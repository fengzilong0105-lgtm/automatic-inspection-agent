from __future__ import annotations

import shlex

from agent.playbooks.config import DEFAULT_CPU_THRESHOLDS, CpuRiskThresholds
from agent.playbooks.models import CheckResult, CheckStatus, CpuCollectorState
from agent.playbooks.parsers.jvm_cpu import summarize_jstack


async def collect_cpu_jstack(
    executor,
    state: CpuCollectorState,
    thresholds: CpuRiskThresholds = DEFAULT_CPU_THRESHOLDS,
) -> None:
    if not thresholds.allow_jstack or not state.running or not state.pid:
        return

    pid = state.pid
    if thresholds.jstack_on_warn:
        warn_ids = {
            "process_cpu_sample",
            "java_gc_cpu_ratio",
            "java_thread_count",
            "container_cpu_throttled",
        }
        if not any(
            c.id in warn_ids and c.status in {CheckStatus.WARN, CheckStatus.FAIL}
            for c in state.checks
        ):
            return

    if state.container_name:
        cmd = f"docker exec {shlex.quote(state.container_name)} jstack {pid} 2>/dev/null | head -n 400"
    else:
        cmd = f"jstack {pid} 2>/dev/null | head -n 400"
    result = await executor.run(cmd, timeout=60)
    text = result.stdout or ""
    if not text.strip():
        state.limitations.append("jstack 不可用或未输出")
        return

    summary = summarize_jstack(text)
    state.jstack = summary
    runnable = int(summary.get("runnable") or 0)
    blocked = int(summary.get("blocked") or 0)
    top_stacks = summary.get("top_stacks") or []

    detail_parts = [f"RUNNABLE={runnable}", f"BLOCKED={blocked}"]
    if top_stacks:
        top = top_stacks[0]
        detail_parts.append(f"重复栈 x{top.get('count')}")

    status = CheckStatus.PASS
    if runnable >= 50 and top_stacks and top_stacks[0].get("count", 0) >= 10:
        status = CheckStatus.WARN
        state.categories.add("process_hot")
        state.evidence.append("jstack: 大量 RUNNABLE 线程共享相同栈，疑似热点循环")
    elif blocked >= 20:
        status = CheckStatus.WARN
        state.evidence.append("jstack: BLOCKED 线程偏多，可能存在锁竞争")

    state.add_check(
        CheckResult(
            id="jstack_summary",
            name="jstack 摘要",
            status=status,
            detail="，".join(detail_parts),
            source="jvm_tool",
            metrics=summary,
        )
    )
