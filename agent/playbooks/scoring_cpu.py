from __future__ import annotations

from agent.models import ServiceType
from agent.playbooks.models import (
    CheckResult,
    CheckStatus,
    CpuCategory,
    CpuCollectorState,
    CpuRiskReport,
)


_SCORE_BY_CHECK: dict[str, tuple[int, int]] = {
    "process_cpu_sample": (20, 35),
    "host_load_ratio": (15, 25),
    "host_cpu": (10, 20),
    "java_gc_cpu_ratio": (25, 35),
    "container_cpu_throttled": (20, 40),
    "container_cpu_percent": (15, 25),
    "java_thread_count": (15, 25),
    "restart_stability": (15, 30),
    "java_fgc_activity": (10, 20),
    "jstack_summary": (10, 15),
    "log_cpu_clues": (5, 10),
}


def _category_from_state(state: CpuCollectorState) -> CpuCategory:
    cats = state.categories
    if "gc_cpu_storm" in cats:
        return CpuCategory.GC_CPU_STORM
    if "cgroup_throttled" in cats:
        return CpuCategory.CGROUP_THROTTLED
    if "process_hot" in cats:
        return CpuCategory.PROCESS_HOT
    if "host_saturated" in cats:
        return CpuCategory.HOST_SATURATED
    if "thread_storm" in cats:
        return CpuCategory.THREAD_STORM
    if "restart_storm" in cats:
        return CpuCategory.RESTART_STORM
    proc_avg = state.process_cpu.get("cpu_avg")
    if proc_avg and float(proc_avg) >= 40:
        return CpuCategory.TRAFFIC_PRESSURE
    return CpuCategory.UNKNOWN


def _confidence(state: CpuCollectorState, service_type: ServiceType) -> str:
    samples = int(state.process_cpu.get("count") or 0)
    has_host = state.host.get("load_ratio") is not None or state.host.get("cpu_percent") is not None
    has_java = state.java.get("gc_cpu_ratio") is not None
    if samples >= 3 and has_host:
        if service_type == ServiceType.JAVA and has_java:
            return "verified"
        if service_type != ServiceType.JAVA:
            return "verified"
        return "partial"
    if samples >= 1:
        return "partial"
    return "low"


def _recommendations(state: CpuCollectorState, primary: CpuCategory, service_id: str) -> list[str]:
    recs: list[str] = []
    if primary == CpuCategory.GC_CPU_STORM:
        recs.append(f"联动执行 assess_oom_risk({service_id})，确认是否堆压力导致 GC 占 CPU")
        recs.append("查看 GC 日志中 Full GC 频率与停顿时间")
        recs.append("勿仅扩容 CPU；优先处理堆/GC 根因")
    elif primary == CpuCategory.PROCESS_HOT:
        recs.append("用 top -Hp <pid> 或 jstack 定位热点线程（见 next_commands）")
        recs.append("核对近期发布/流量是否突增")
    elif primary == CpuCategory.HOST_SATURATED:
        proc_rank = state.process_cpu.get("rank")
        if proc_rank and int(proc_rank) > 1:
            recs.append("主机整体负载高，本服务不是 Top CPU；先排查主机上其他进程")
        else:
            recs.append("主机 CPU/负载饱和，考虑迁移服务或扩容主机")
    elif primary == CpuCategory.CGROUP_THROTTLED:
        recs.append("容器 CPU 被 cgroup 限流，评估提高 cpu quota/limits")
        recs.append("优化 CPU 密集逻辑，避免长期顶满 quota")
    elif primary == CpuCategory.THREAD_STORM:
        recs.append("检查线程池配置、异步任务堆积与连接泄漏")
        recs.append("jstack 查看 BLOCKED/RUNNABLE 分布")
    elif primary == CpuCategory.RESTART_STORM:
        recs.append("先解决频繁重启（启动失败/健康检查），再评估 CPU 容量")
    elif primary == CpuCategory.TRAFFIC_PRESSURE:
        recs.append("CPU 偏高但 GC/线程无异常，考虑弹性扩容或限流")
    if not recs:
        recs.append("继续观察 CPU 采样趋势，必要时扩容")
    return recs[:5]


def build_cpu_report(service, state: CpuCollectorState) -> CpuRiskReport:
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
    if confidence in {"partial", "low"} and risk_level in {"high", "critical"}:
        risk_level = "medium"
    if not state.running:
        risk_level = "unknown"

    primary = _category_from_state(state)
    categories: list[CpuCategory] = []
    for key in state.categories:
        try:
            categories.append(CpuCategory(key))
        except ValueError:
            categories.append(CpuCategory.UNKNOWN)

    summary = _build_summary(service.id, risk_level, confidence, primary, state)
    if confidence in {"partial", "low"} and "【待核实】" not in summary:
        summary = f"【待核实】{summary}"

    next_commands = list(dict.fromkeys(state.next_commands))[:6]

    return CpuRiskReport(
        service_id=service.id,
        host_id=service.host_id,
        service_type=service.type,
        running=state.running,
        pid=state.pid,
        risk_level=risk_level,  # type: ignore[arg-type]
        score=score,
        confidence=confidence,  # type: ignore[arg-type]
        primary_category=primary,
        categories=categories or [CpuCategory.UNKNOWN],
        summary=summary,
        checks=state.checks,
        evidence=state.evidence[:12],
        recommendations=_recommendations(state, primary, service.id),
        next_commands=next_commands,
        limitations=state.limitations,
    )


def _build_summary(
    service_id: str,
    risk_level: str,
    confidence: str,
    primary: CpuCategory,
    state: CpuCollectorState,
) -> str:
    labels = {
        "low": "低",
        "medium": "中",
        "high": "高",
        "critical": "严重",
        "unknown": "未知",
    }
    cat_labels = {
        CpuCategory.PROCESS_HOT: "进程 CPU",
        CpuCategory.HOST_SATURATED: "主机负载",
        CpuCategory.CGROUP_THROTTLED: "容器限流",
        CpuCategory.GC_CPU_STORM: "GC CPU",
        CpuCategory.THREAD_STORM: "线程",
        CpuCategory.RESTART_STORM: "重启",
        CpuCategory.TRAFFIC_PRESSURE: "流量压力",
        CpuCategory.UNKNOWN: "综合",
    }
    if not state.running:
        return f"{service_id} 未运行，无法完成运行时 CPU 风险评估"
    top = state.evidence[0] if state.evidence else "未发现明显 CPU 风险信号"
    return (
        f"{service_id} {cat_labels.get(primary, '综合')} 风险{labels.get(risk_level, risk_level)}"
        f"（置信度 {confidence}）：{top}"
    )
