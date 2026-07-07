from __future__ import annotations

import shlex

from agent.models import ServiceConfig
from agent.playbooks.config import DEFAULT_OOM_THRESHOLDS, OomRiskThresholds
from agent.playbooks.jvm_flags import parse_gc_log_stats
from agent.playbooks.models import CheckResult, CheckStatus, CollectorState


async def collect_gc_log(
    executor,
    service: ServiceConfig,
    state: CollectorState,
    thresholds: OomRiskThresholds = DEFAULT_OOM_THRESHOLDS,
) -> None:
    paths: list[str] = []
    for path in state.jvm_flags.get("gc_log_paths") or []:
        if path and path != "__verbose_gc_stdout__":
            paths.append(path)
    if service.deploy_dir:
        for candidate in (
            f"{service.deploy_dir}/gc.log",
            f"{service.deploy_dir}/logs/gc.log",
            f"{service.deploy_dir}/log/gc.log",
        ):
            if candidate not in paths:
                paths.append(candidate)

    gc_text = ""
    used_path = ""
    for path in paths[:3]:
        quoted = shlex.quote(path)
        check = await executor.run(f"test -r {quoted} && echo yes || true")
        if check.stdout.strip() != "yes":
            continue
        tail = await executor.run(f"tail -n {thresholds.gc_log_tail_lines} {quoted} 2>/dev/null")
        if tail.stdout.strip():
            gc_text = tail.stdout
            used_path = path
            break

    if not gc_text:
        state.limitations.append("未找到可读的 GC 日志")
        state.add_check(
            CheckResult(
                id="gc_log_full_gc",
                name="GC 日志 Full GC",
                status=CheckStatus.SKIP,
                detail="无 GC 日志路径或未配置",
                source="gc_log",
            )
        )
        return

    stats = parse_gc_log_stats(gc_text)
    state.gc_log = {"path": used_path, **stats}
    full_gc = stats.get("full_gc_count", 0)
    max_pause = stats.get("max_pause_seconds", 0.0)
    alloc_fail = stats.get("allocation_failure_count", 0)

    if full_gc >= thresholds.full_gc_count_fail or alloc_fail >= 3:
        fg_status = CheckStatus.FAIL
        state.critical = True
        state.categories.add("heap")
    elif full_gc >= thresholds.full_gc_count_warn:
        fg_status = CheckStatus.WARN
        state.categories.add("heap")
    else:
        fg_status = CheckStatus.PASS

    state.add_check(
        CheckResult(
            id="gc_log_full_gc",
            name="GC 日志 Full GC",
            status=fg_status,
            detail=f"窗口内 Full GC {full_gc} 次，Allocation Failure {alloc_fail} 次",
            source="gc_log",
            metrics=stats,
        )
    )
    if fg_status in {CheckStatus.WARN, CheckStatus.FAIL}:
        state.evidence.append(f"GC 日志 {used_path}: Full GC {full_gc} 次")

    pause_status = CheckStatus.PASS
    if max_pause >= 10:
        pause_status = CheckStatus.FAIL
        state.categories.add("heap")
    elif max_pause >= 3:
        pause_status = CheckStatus.WARN
        state.categories.add("heap")
    state.add_check(
        CheckResult(
            id="gc_log_pause",
            name="GC 最长停顿",
            status=pause_status,
            detail=f"最长停顿约 {max_pause:.1f}s",
            source="gc_log",
            metrics={"max_pause_seconds": max_pause},
        )
    )
