from __future__ import annotations

from agent.playbooks.models import (
    CheckResult,
    CheckStatus,
    FalseHealthyCategory,
    FalseHealthyCollectorState,
    FalseHealthyReport,
)


_SCORE_BY_CHECK: dict[str, tuple[int, int]] = {
    "deep_health_parse": (0, 45),
    "business_probe": (0, 50),
    "log_error_vs_health": (0, 40),
    "log_business_errors": (10, 25),
    "health_latency": (10, 20),
}


def _category_from_state(state: FalseHealthyCollectorState) -> FalseHealthyCategory:
    cats = state.categories
    if "business_probe_fail" in cats:
        return FalseHealthyCategory.BUSINESS_PROBE_FAIL
    if "component_down" in cats:
        return FalseHealthyCategory.COMPONENT_DOWN
    if "readiness_down" in cats:
        return FalseHealthyCategory.READINESS_DOWN
    if "log_error_surge" in cats:
        return FalseHealthyCategory.LOG_ERROR_SURGE
    if "log_business_error" in cats:
        return FalseHealthyCategory.LOG_BUSINESS_ERROR
    if "health_slow" in cats:
        return FalseHealthyCategory.HEALTH_SLOW
    return FalseHealthyCategory.UNKNOWN


def _confidence(state: FalseHealthyCollectorState) -> str:
    has_deep = any(c.id == "deep_health_parse" and c.status == CheckStatus.PASS for c in state.checks) or bool(
        state.down_components
    )
    has_deep_fail = any(c.id == "deep_health_parse" and c.status == CheckStatus.FAIL for c in state.checks)
    has_log = any(c.id == "log_error_vs_health" and c.status != CheckStatus.SKIP for c in state.checks)
    has_biz = state.business_probe_ok is not None

    signals = sum(
        [
            state.health_ok is True,
            has_deep or has_deep_fail,
            has_log,
            has_biz,
        ]
    )
    if signals >= 3:
        return "verified"
    if signals >= 2:
        return "partial"
    return "low"


def _recommendations(state: FalseHealthyCollectorState, primary: FalseHealthyCategory) -> list[str]:
    recs: list[str] = []
    if primary == FalseHealthyCategory.COMPONENT_DOWN:
        recs.append("Actuator 组件 DOWN：优先排查对应依赖（DB/Redis/磁盘等）连通与配置")
        for item in state.down_components[:3]:
            recs.append(f"检查 {item.get('path')} ({item.get('status')})")
    if primary == FalseHealthyCategory.READINESS_DOWN:
        recs.append("readiness 异常但浅健康可能仍 UP：检查流量是否应该切走")
    if primary == FalseHealthyCategory.BUSINESS_PROBE_FAIL:
        recs.append("业务探针失败：核对 business_probe_url 与真实业务链路")
    if primary == FalseHealthyCategory.LOG_ERROR_SURGE:
        recs.append("health 通过但 ERROR 激增：查最近异常栈与依赖超时")
        recs.append("可联动 assess_oom_risk / assess_cpu_risk 排除资源问题")
    if primary == FalseHealthyCategory.HEALTH_SLOW:
        recs.append("health 响应过慢：可能是 GC/线程池/依赖阻塞的前兆")
    if not recs:
        recs.append("未发现明显假健康信号；若业务仍异常，配置 business_probe_url 或 health_deep_url")
    recs.append("确认根因后再考虑重启（需在对话中明确确认）")
    return recs[:6]


def build_false_healthy_report(service, state: FalseHealthyCollectorState) -> FalseHealthyReport:
    if not state.running:
        return FalseHealthyReport(
            service_id=service.id,
            host_id=service.host_id,
            service_type=service.type,
            running=False,
            health_ok=state.health_ok,
            risk_level="unknown",
            score=0,
            confidence="low",
            primary_category=FalseHealthyCategory.UNKNOWN,
            categories=[FalseHealthyCategory.UNKNOWN],
            summary=f"{service.id} 未运行，无法评估假健康",
            checks=state.checks,
            evidence=state.evidence,
            recommendations=["先恢复服务运行"],
            next_commands=state.next_commands[:6],
            limitations=state.limitations,
        )

    if state.health_ok is False:
        return FalseHealthyReport(
            service_id=service.id,
            host_id=service.host_id,
            service_type=service.type,
            running=True,
            health_ok=False,
            risk_level="unknown",
            score=0,
            confidence="verified",
            primary_category=FalseHealthyCategory.UNKNOWN,
            categories=[FalseHealthyCategory.UNKNOWN],
            summary=f"{service.id} 健康检查未通过，属于真不健康（非假健康）",
            checks=state.checks,
            evidence=state.evidence,
            recommendations=["排查 health_url 失败原因；若进程也不可用，可调用 assess_false_alive"],
            next_commands=state.next_commands[:6],
            limitations=state.limitations,
        )

    score = 0
    for check in state.checks:
        weights = _SCORE_BY_CHECK.get(check.id)
        if not weights:
            continue
        warn_score, fail_score = weights
        if check.status == CheckStatus.FAIL:
            score += fail_score
        elif check.status == CheckStatus.WARN:
            score += warn_score
    score = min(score, 100)

    if state.critical:
        risk_level = "critical"
    elif score >= 50:
        risk_level = "high"
    elif score >= 25:
        risk_level = "medium"
    else:
        risk_level = "low"

    confidence = _confidence(state)
    if confidence in {"partial", "low"} and risk_level in {"high", "critical"}:
        risk_level = "medium"

    primary = _category_from_state(state)
    categories: list[FalseHealthyCategory] = []
    for key in state.categories:
        try:
            categories.append(FalseHealthyCategory(key))
        except ValueError:
            categories.append(FalseHealthyCategory.UNKNOWN)

    summary = _build_summary(service.id, risk_level, confidence, primary, state)
    if confidence in {"partial", "low"} and risk_level not in {"low", "unknown"} and "【待核实】" not in summary:
        summary = f"【待核实】{summary}"

    return FalseHealthyReport(
        service_id=service.id,
        host_id=service.host_id,
        service_type=service.type,
        running=state.running,
        health_ok=state.health_ok,
        risk_level=risk_level,  # type: ignore[arg-type]
        score=score,
        confidence=confidence,  # type: ignore[arg-type]
        primary_category=primary,
        categories=categories or [FalseHealthyCategory.UNKNOWN],
        summary=summary,
        checks=state.checks,
        evidence=state.evidence[:12],
        recommendations=_recommendations(state, primary),
        next_commands=list(dict.fromkeys(state.next_commands))[:6],
        limitations=state.limitations,
    )


def _build_summary(
    service_id: str,
    risk_level: str,
    confidence: str,
    primary: FalseHealthyCategory,
    state: FalseHealthyCollectorState,
) -> str:
    labels = {
        "low": "低",
        "medium": "中",
        "high": "高",
        "critical": "严重",
        "unknown": "未知",
    }
    cat_labels = {
        FalseHealthyCategory.COMPONENT_DOWN: "组件 DOWN",
        FalseHealthyCategory.READINESS_DOWN: "readiness 异常",
        FalseHealthyCategory.BUSINESS_PROBE_FAIL: "业务探针失败",
        FalseHealthyCategory.LOG_ERROR_SURGE: "日志 ERROR 激增",
        FalseHealthyCategory.LOG_BUSINESS_ERROR: "业务异常日志",
        FalseHealthyCategory.HEALTH_SLOW: "health 响应慢",
        FalseHealthyCategory.UNKNOWN: "综合",
    }
    if state.evidence:
        top = state.evidence[0]
    elif state.health_ok is True:
        top = "浅健康通过且未发现明显假健康信号"
    else:
        top = "健康信号不完整，需补充 health_url / log_path"
    return (
        f"{service_id} 假健康风险{labels.get(risk_level, risk_level)}"
        f"（{cat_labels.get(primary, '综合')}，置信度 {confidence}）：{top}"
    )
