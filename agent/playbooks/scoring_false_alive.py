from __future__ import annotations

from agent.models import ServiceType
from agent.playbooks.models import (
    CheckResult,
    CheckStatus,
    FalseAliveCategory,
    FalseAliveCollectorState,
    FalseAliveReport,
)


_SCORE_BY_CHECK: dict[str, tuple[int, int]] = {
    "health_vs_running": (0, 40),
    "port_listening": (0, 40),
    "tcp_connect": (10, 25),
    "systemd_main_pid": (15, 30),
    "pid_cmdline_match": (0, 35),
    "docker_health": (0, 40),
    "systemd_active_state": (10, 15),
}


def _category_from_state(state: FalseAliveCollectorState) -> FalseAliveCategory:
    cats = state.categories
    if "port_dead" in cats:
        return FalseAliveCategory.PORT_DEAD
    if "health_dead" in cats:
        return FalseAliveCategory.HEALTH_DEAD
    if "wrong_process" in cats:
        return FalseAliveCategory.WRONG_PROCESS
    if "systemd_mismatch" in cats:
        return FalseAliveCategory.SYSTEMD_MISMATCH
    if "docker_unhealthy" in cats:
        return FalseAliveCategory.DOCKER_UNHEALTHY
    return FalseAliveCategory.UNKNOWN


def _confidence(state: FalseAliveCollectorState) -> str:
    has_health = state.health_ok is not None or any(
        c.id == "health_vs_running" and c.status != CheckStatus.SKIP for c in state.checks
    )
    has_port = bool(state.port_results) or any(
        c.id == "port_listening" and c.status != CheckStatus.SKIP for c in state.checks
    )
    if has_health and has_port:
        return "verified"
    if has_health or has_port:
        return "partial"
    return "low"


def _recommendations(state: FalseAliveCollectorState, primary: FalseAliveCategory, service_id: str) -> list[str]:
    recs: list[str] = []
    if primary == FalseAliveCategory.HEALTH_DEAD:
        recs.append("进程存活但 health 失败：优先 jstack / 查 GC 与 ERROR 日志，确认是否卡死")
        recs.append("可联动 assess_cpu_risk / assess_oom_risk 排查资源与 GC 问题")
    if primary == FalseAliveCategory.PORT_DEAD:
        recs.append("进程在但端口未监听：查启动是否完成、是否 bind 失败、是否假启动")
        recs.append(f"ss -lntp | grep {service_id} 相关进程")
    if primary == FalseAliveCategory.WRONG_PROCESS:
        recs.append("疑似错进程/孤儿进程：核对 jar_path 与 systemd unit 指向")
    if primary == FalseAliveCategory.SYSTEMD_MISMATCH:
        recs.append("systemd MainPID 与探测 PID 不一致：确认是否有多实例或残留进程")
    if primary == FalseAliveCategory.DOCKER_UNHEALTHY:
        recs.append("容器 unhealthy：docker logs + inspect Health.Log")
    if not recs:
        recs.append("未发现明显假活信号，继续观察端口与健康检查")
    recs.append("确认根因后重启服务（需在对话中明确确认）")
    return recs[:5]


def build_false_alive_report(service, state: FalseAliveCollectorState) -> FalseAliveReport:
    if not state.running:
        return FalseAliveReport(
            service_id=service.id,
            host_id=service.host_id,
            service_type=service.type,
            running=False,
            pid=state.pid,
            health_ok=state.health_ok,
            risk_level="unknown",
            score=0,
            confidence="low",
            primary_category=FalseAliveCategory.UNKNOWN,
            categories=[FalseAliveCategory.UNKNOWN],
            summary=f"{service.id} 未运行，不属于假活（属于真宕机）",
            checks=state.checks,
            evidence=state.evidence,
            recommendations=["先排查服务为何未启动：systemctl status / 启动日志"],
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
    categories: list[FalseAliveCategory] = []
    for key in state.categories:
        try:
            categories.append(FalseAliveCategory(key))
        except ValueError:
            categories.append(FalseAliveCategory.UNKNOWN)

    summary = _build_summary(service.id, risk_level, confidence, primary, state)
    if confidence in {"partial", "low"} and risk_level != "low" and "【待核实】" not in summary:
        summary = f"【待核实】{summary}"

    return FalseAliveReport(
        service_id=service.id,
        host_id=service.host_id,
        service_type=service.type,
        running=state.running,
        pid=state.pid,
        health_ok=state.health_ok,
        risk_level=risk_level,  # type: ignore[arg-type]
        score=score,
        confidence=confidence,  # type: ignore[arg-type]
        primary_category=primary,
        categories=categories or [FalseAliveCategory.UNKNOWN],
        summary=summary,
        checks=state.checks,
        evidence=state.evidence[:12],
        recommendations=_recommendations(state, primary, service.id),
        next_commands=list(dict.fromkeys(state.next_commands))[:6],
        limitations=state.limitations,
    )


def _build_summary(
    service_id: str,
    risk_level: str,
    confidence: str,
    primary: FalseAliveCategory,
    state: FalseAliveCollectorState,
) -> str:
    labels = {
        "low": "低",
        "medium": "中",
        "high": "高",
        "critical": "严重",
        "unknown": "未知",
    }
    cat_labels = {
        FalseAliveCategory.PORT_DEAD: "端口不可达",
        FalseAliveCategory.HEALTH_DEAD: "健康检查失败",
        FalseAliveCategory.WRONG_PROCESS: "进程身份异常",
        FalseAliveCategory.SYSTEMD_MISMATCH: "systemd PID 不一致",
        FalseAliveCategory.DOCKER_UNHEALTHY: "容器 unhealthy",
        FalseAliveCategory.UNKNOWN: "综合",
    }
    if state.evidence:
        top = state.evidence[0]
    else:
        top = "进程在运行且未发现明显假活信号"
    return (
        f"{service_id} 假活风险{labels.get(risk_level, risk_level)}"
        f"（{cat_labels.get(primary, '综合')}，置信度 {confidence}）：{top}"
    )
