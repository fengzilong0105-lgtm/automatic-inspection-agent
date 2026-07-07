from __future__ import annotations

from agent.models import ServiceType
from agent.playbooks.models import (
    CheckResult,
    CheckStatus,
    CollectorState,
    OomCategory,
    OomRiskReport,
)


_SCORE_BY_CHECK: dict[str, tuple[int, int]] = {
    # check_id: (warn_score, fail_score)
    "log_oom_hits": (0, 35),
    "container_oom_killed": (0, 40),
    "dmesg_oom_killer": (20, 30),
    "java_old_gen_usage": (25, 35),
    "gc_log_full_gc": (20, 30),
    "gc_log_pause": (10, 20),
    "container_memory_limit": (25, 35),
    "host_memory": (15, 25),
    "java_metaspace_usage": (20, 20),
    "jstat_fgc_storm": (15, 25),
    "process_rss": (10, 10),
}


def _category_from_checks(state: CollectorState) -> OomCategory:
    cats = state.categories
    if "heap" in cats:
        return OomCategory.HEAP
    if "metaspace" in cats:
        return OomCategory.METASPACE
    if "cgroup" in cats:
        return OomCategory.CGROUP
    if "host_oom_killer" in cats:
        return OomCategory.HOST_KILLER
    if "native" in cats:
        return OomCategory.NATIVE
    return OomCategory.UNKNOWN


def _confidence(state: CollectorState, service_type: ServiceType) -> str:
    has_jvm = any(c.id == "java_old_gen_usage" and c.status != CheckStatus.UNKNOWN for c in state.checks)
    has_gc = any(c.id == "gc_log_full_gc" and c.status != CheckStatus.SKIP for c in state.checks)
    if service_type == ServiceType.JAVA:
        if has_jvm and (has_gc or state.host.get("memory_percent") is not None):
            return "verified"
        if has_jvm or has_gc or state.proc.get("rss_bytes"):
            return "partial"
        return "low"
    if state.docker.get("usage_bytes") or state.host.get("memory_percent") is not None:
        return "verified"
    return "partial"


def _recommendations(state: CollectorState, primary: OomCategory) -> list[str]:
    recs: list[str] = []
    if primary == OomCategory.HEAP:
        recs.append("排查堆内存持续增长（泄漏、缓存无界、大对象）")
        recs.append("查看 GC 日志或考虑在维护窗口做 heap dump（需人工确认）")
    if primary == OomCategory.METASPACE:
        recs.append("检查类加载是否异常（热部署、动态代理、Groovy 等）")
    if primary == OomCategory.CGROUP:
        recs.append("评估调高容器 memory limit 或优化进程内存占用")
    if primary == OomCategory.HOST_KILLER:
        recs.append("释放主机内存或迁移部分服务，避免 OOM Killer")
    if primary == OomCategory.NATIVE:
        recs.append("RSS 高但堆使用率正常时，排查堆外/Direct/线程栈内存")
    if not state.jvm_flags.get("heap_dump_on_oom"):
        recs.append("建议配置 -XX:+HeapDumpOnOutOfMemoryError 便于事后分析")
    if not recs:
        recs.append("继续观察内存与 GC 趋势，必要时扩容或限流")
    return recs[:5]


def build_report(service, state: CollectorState) -> OomRiskReport:
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
    elif not state.running:
        risk_level = "unknown"
    else:
        risk_level = "low"

    confidence = _confidence(state, service.type)

    if service.type == ServiceType.JAVA and confidence in {"partial", "low"}:
        if risk_level in {"high", "critical"}:
            risk_level = "medium"
    if not state.running:
        risk_level = "unknown"

    primary = _category_from_checks(state)
    categories = []
    for key in state.categories:
        try:
            categories.append(OomCategory(key))
        except ValueError:
            categories.append(OomCategory.UNKNOWN)

    summary = _build_summary(service.id, risk_level, confidence, primary, state)
    if confidence in {"partial", "low"} and "【待核实】" not in summary:
        summary = f"【待核实】{summary}"

    return OomRiskReport(
        service_id=service.id,
        host_id=service.host_id,
        service_type=service.type,
        running=state.running,
        pid=state.pid,
        risk_level=risk_level,  # type: ignore[arg-type]
        score=score,
        confidence=confidence,  # type: ignore[arg-type]
        primary_category=primary,
        categories=categories or [OomCategory.UNKNOWN],
        summary=summary,
        checks=state.checks,
        evidence=state.evidence[:12],
        recommendations=_recommendations(state, primary),
        limitations=state.limitations,
    )


def _build_summary(
    service_id: str,
    risk_level: str,
    confidence: str,
    primary: OomCategory,
    state: CollectorState,
) -> str:
    labels = {
        "low": "低",
        "medium": "中",
        "high": "高",
        "critical": "严重",
        "unknown": "未知",
    }
    cat_labels = {
        OomCategory.HEAP: "堆内存",
        OomCategory.METASPACE: "Metaspace",
        OomCategory.CGROUP: "容器内存",
        OomCategory.HOST_KILLER: "主机 OOM Killer",
        OomCategory.NATIVE: "Native/堆外",
        OomCategory.UNKNOWN: "综合",
    }
    top_evidence = state.evidence[0] if state.evidence else "未发现明显风险信号"
    if not state.running:
        return f"{service_id} 未运行，仅完成离线/配置级检查"
    return (
        f"{service_id} {cat_labels.get(primary, '综合')} OOM 风险{labels.get(risk_level, risk_level)}"
        f"（置信度 {confidence}）：{top_evidence}"
    )
