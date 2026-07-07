from __future__ import annotations

from agent.playbooks.config import DEFAULT_OOM_THRESHOLDS, OomRiskThresholds
from agent.playbooks.jvm_flags import usage_ratio
from agent.playbooks.models import CheckResult, CheckStatus, CollectorState


async def collect_process(
    executor,
    state: CollectorState,
    thresholds: OomRiskThresholds = DEFAULT_OOM_THRESHOLDS,
) -> None:
    if not state.pid:
        state.add_check(
            CheckResult(
                id="process_rss",
                name="进程内存 RSS",
                status=CheckStatus.SKIP,
                detail="无 PID",
                source="live_probe",
            )
        )
        return

    pid = state.pid
    status_result = await executor.run(f"awk '/VmRSS|VmHWM|Threads/{{print $1,$2}}' /proc/{pid}/status 2>/dev/null")
    rss_kb = None
    hwm_kb = None
    threads = None
    for line in status_result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        key, value = parts
        if key == "VmRSS:":
            rss_kb = int(value)
        elif key == "VmHWM:":
            hwm_kb = int(value)
        elif key == "Threads:":
            threads = int(value)

    state.proc = {
        "rss_bytes": rss_kb * 1024 if rss_kb is not None else None,
        "hwm_bytes": hwm_kb * 1024 if hwm_kb is not None else None,
        "threads": threads,
    }

    rss = state.proc.get("rss_bytes")
    heap_max = state.jvm_flags.get("heap_max_bytes")
    ratio = usage_ratio(rss, heap_max) if heap_max else None

    detail_parts = []
    if rss is not None:
        detail_parts.append(f"RSS {rss / (1024**2):.0f} MB")
    if threads is not None:
        detail_parts.append(f"线程 {threads}")
    if ratio is not None:
        detail_parts.append(f"RSS/-Xmx {ratio:.0f}%")

    status = CheckStatus.PASS
    if ratio is not None and ratio >= thresholds.rss_xmx_warn_ratio * 100:
        status = CheckStatus.WARN
        state.categories.add("native")
    if threads is not None and threads > 800:
        status = CheckStatus.WARN

    state.add_check(
        CheckResult(
            id="process_rss",
            name="进程内存 RSS",
            status=status,
            detail="，".join(detail_parts) or "无法读取 /proc",
            source="live_probe",
            metrics={"rss_bytes": rss, "rss_xmx_ratio": ratio, "threads": threads},
        )
    )
    if status == CheckStatus.WARN and detail_parts:
        state.evidence.append("进程 RSS: " + "，".join(detail_parts))
