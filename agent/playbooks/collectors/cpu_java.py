from __future__ import annotations

import shlex

from agent.playbooks.config import DEFAULT_CPU_THRESHOLDS, CpuRiskThresholds
from agent.playbooks.models import CheckResult, CheckStatus, CpuCollectorState
from agent.playbooks.parsers.jvm_cpu import gc_cpu_ratio_from_jstat, parse_jstat_rows


async def _run_jstat(executor, pid: int, thresholds: CpuRiskThresholds, container: str | None) -> str:
    interval_s = max(1, thresholds.jstat_interval_ms // 1000)
    if container:
        cmd = (
            f"docker exec {shlex.quote(container)} jstat -gcutil {pid} "
            f"{interval_s * 1000} {thresholds.jstat_samples} 2>/dev/null"
        )
    else:
        cmd = f"jstat -gcutil {pid} {interval_s * 1000} {thresholds.jstat_samples} 2>/dev/null"
    result = await executor.run(cmd, timeout=max(30, interval_s * thresholds.jstat_samples + 10))
    return (result.stdout or "").strip()


async def collect_cpu_java(
    executor,
    state: CpuCollectorState,
    thresholds: CpuRiskThresholds = DEFAULT_CPU_THRESHOLDS,
) -> None:
    if not state.running or not state.pid:
        state.limitations.append("Java 服务未运行，跳过 JVM CPU 分析")
        return

    pid = state.pid
    status_result = await executor.run(
        f"awk '/Threads/{{print $2}}' /proc/{pid}/status 2>/dev/null"
    )
    threads = None
    try:
        threads = int((status_result.stdout or "").strip())
    except ValueError:
        pass

    if threads is not None:
        state.java["threads"] = threads
        if threads >= thresholds.java_threads_fail:
            t_status = CheckStatus.FAIL
            state.categories.add("thread_storm")
            state.critical = True
        elif threads >= thresholds.java_threads_warn:
            t_status = CheckStatus.WARN
            state.categories.add("thread_storm")
        else:
            t_status = CheckStatus.PASS
        t_detail = f"线程数 {threads}"
        state.add_check(
            CheckResult(
                id="java_thread_count",
                name="Java 线程数",
                status=t_status,
                detail=t_detail,
                source="jvm_tool",
                metrics={"threads": threads},
            )
        )
        if t_status in {CheckStatus.WARN, CheckStatus.FAIL}:
            state.evidence.append(t_detail)
            state.next_commands.append(f"jstack {pid} | head -200")

    jstat_text = await _run_jstat(executor, pid, thresholds, state.container_name)
    if not jstat_text:
        state.limitations.append("jstat 不可用，无法计算 GC CPU 占比")
        state.add_check(
            CheckResult(
                id="java_gc_cpu_ratio",
                name="GC CPU 占比",
                status=CheckStatus.UNKNOWN,
                detail="jstat 执行失败",
                source="jvm_tool",
            )
        )
        return

    rows = parse_jstat_rows(jstat_text)
    interval_s = max(1, thresholds.jstat_interval_ms // 1000)
    gc_ratio = gc_cpu_ratio_from_jstat(rows, float(interval_s))
    fgc_delta = None
    if len(rows) >= 2:
        fgc_delta = rows[-1].get("fgc", 0) - rows[0].get("fgc", 0)

    state.java["jstat_rows"] = rows
    state.java["gc_cpu_ratio"] = gc_ratio
    state.java["fgc_delta"] = fgc_delta

    if gc_ratio is None:
        state.add_check(
            CheckResult(
                id="java_gc_cpu_ratio",
                name="GC CPU 占比",
                status=CheckStatus.UNKNOWN,
                detail="无法从 jstat 计算 GC CPU 占比",
                source="jvm_tool",
            )
        )
        return

    if gc_ratio >= thresholds.java_gc_cpu_fail:
        gc_status = CheckStatus.FAIL
        state.categories.add("gc_cpu_storm")
        state.critical = True
    elif gc_ratio >= thresholds.java_gc_cpu_warn:
        gc_status = CheckStatus.WARN
        state.categories.add("gc_cpu_storm")
    else:
        gc_status = CheckStatus.PASS
    gc_detail = f"采样窗口 GC CPU 占比约 {gc_ratio}%"
    state.add_check(
        CheckResult(
            id="java_gc_cpu_ratio",
            name="GC CPU 占比",
            status=gc_status,
            detail=gc_detail,
            source="jvm_tool",
            metrics={"gc_cpu_ratio": gc_ratio, "fgc_delta": fgc_delta},
        )
    )
    if gc_status in {CheckStatus.WARN, CheckStatus.FAIL}:
        state.evidence.append(gc_detail)
        state.next_commands.append(f"jstat -gcutil {pid} 5000 5")
        state.next_commands.append("建议联动 assess_oom_risk 交叉验证堆压力")

    if fgc_delta is not None and fgc_delta >= 3:
        state.add_check(
            CheckResult(
                id="java_fgc_activity",
                name="Full GC 活动",
                status=CheckStatus.WARN if fgc_delta < 5 else CheckStatus.FAIL,
                detail=f"采样期间 Full GC 增加 {fgc_delta:.0f} 次",
                source="jvm_tool",
                metrics={"fgc_delta": fgc_delta},
            )
        )
        state.categories.add("gc_cpu_storm")
