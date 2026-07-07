from __future__ import annotations

import shlex

from agent.playbooks.config import DEFAULT_OOM_THRESHOLDS, OomRiskThresholds
from agent.playbooks.jvm_flags import (
    parse_gc_heap_info,
    parse_jstat_gcutil,
    parse_jvm_flags,
    usage_ratio,
)
from agent.playbooks.models import CheckResult, CheckStatus, CollectorState


async def _run_jcmd(executor, pid: int, subcommand: str, container: str | None = None) -> str:
    if container:
        cmd = f"docker exec {shlex.quote(container)} jcmd {pid} {subcommand} 2>/dev/null"
    else:
        cmd = f"jcmd {pid} {subcommand} 2>/dev/null"
    result = await executor.run(cmd)
    return (result.stdout or "").strip()


async def _run_jstat(executor, pid: int, samples: int, interval_ms: int, container: str | None) -> str:
    interval_s = max(1, interval_ms // 1000)
    if container:
        cmd = (
            f"docker exec {shlex.quote(container)} jstat -gcutil {pid} "
            f"{interval_s * 1000} {samples} 2>/dev/null"
        )
    else:
        cmd = f"jstat -gcutil {pid} {interval_s * 1000} {samples} 2>/dev/null"
    result = await executor.run(cmd, timeout=max(30, interval_s * samples + 10))
    return (result.stdout or "").strip()


async def collect_java(
    executor,
    state: CollectorState,
    thresholds: OomRiskThresholds = DEFAULT_OOM_THRESHOLDS,
) -> None:
    if not state.running or not state.pid:
        state.limitations.append("Java 服务未运行，跳过 JVM 采集")
        return

    state.jvm_flags = parse_jvm_flags(state.cmdline)
    state.add_check(
        CheckResult(
            id="jvm_flags",
            name="JVM 启动参数",
            status=CheckStatus.PASS,
            detail=_format_jvm_flags(state.jvm_flags),
            source="config_registry",
            metrics=dict(state.jvm_flags),
        )
    )

    pid = state.pid
    container = state.container_name
    heap_info_text = ""
    if thresholds.prefer_jcmd:
        heap_info_text = await _run_jcmd(executor, pid, "GC.heap_info", container)
    if heap_info_text:
        state.java_heap = parse_gc_heap_info(heap_info_text)
        _apply_heap_checks(state, thresholds)
    elif thresholds.allow_jstat:
        jstat_text = await _run_jstat(
            executor, pid, thresholds.jstat_samples, thresholds.jstat_interval_ms, container
        )
        if jstat_text:
            state.jstat = parse_jstat_gcutil(jstat_text)
            _apply_jstat_checks(state, thresholds)
        else:
            state.limitations.append("jcmd/jstat 不可用，无法获取 JVM 堆使用率")
            state.add_check(
                CheckResult(
                    id="java_old_gen_usage",
                    name="老年代使用率",
                    status=CheckStatus.UNKNOWN,
                    detail="jcmd/jstat 执行失败",
                    source="jvm_tool",
                )
            )
    else:
        state.limitations.append("未采集 JVM 堆指标")

    meta_used = state.java_heap.get("metaspace_used_bytes")
    meta_max = state.java_heap.get("metaspace_max_bytes") or state.jvm_flags.get(
        "metaspace_max_bytes"
    )
    meta_ratio = usage_ratio(meta_used, meta_max)
    if meta_ratio is not None:
        if meta_ratio >= thresholds.metaspace_warn:
            meta_status = CheckStatus.WARN
            state.categories.add("metaspace")
            state.evidence.append(f"Metaspace 使用率 {meta_ratio:.0f}%")
        else:
            meta_status = CheckStatus.PASS
        state.java_metaspace = {"used_bytes": meta_used, "max_bytes": meta_max, "ratio": meta_ratio}
        state.add_check(
            CheckResult(
                id="java_metaspace_usage",
                name="Metaspace 使用率",
                status=meta_status,
                detail=f"{meta_ratio:.0f}%",
                source="jvm_tool",
                metrics={"ratio_percent": meta_ratio},
            )
        )


def _format_jvm_flags(flags: dict) -> str:
    parts = []
    if flags.get("heap_max_bytes"):
        parts.append(f"-Xmx≈{flags['heap_max_bytes'] // (1024**2)}M")
    if flags.get("metaspace_max_bytes"):
        parts.append(f"MaxMetaspace≈{flags['metaspace_max_bytes'] // (1024**2)}M")
    if flags.get("gc_log_paths"):
        parts.append(f"GC日志 {len(flags['gc_log_paths'])} 处")
    return "，".join(parts) if parts else "未解析到关键 JVM 参数"


def _apply_heap_checks(state: CollectorState, thresholds: OomRiskThresholds) -> None:
    old_used = state.java_heap.get("old_gen_used_bytes")
    old_max = state.java_heap.get("old_gen_max_bytes")
    old_ratio = usage_ratio(old_used, old_max)

    heap_used = state.java_heap.get("heap_used_bytes")
    heap_max = state.java_heap.get("heap_max_bytes") or state.jvm_flags.get("heap_max_bytes")
    heap_ratio = usage_ratio(heap_used, heap_max)

    ratio = old_ratio if old_ratio is not None else heap_ratio
    if ratio is None:
        state.add_check(
            CheckResult(
                id="java_old_gen_usage",
                name="老年代/堆使用率",
                status=CheckStatus.UNKNOWN,
                detail="GC.heap_info 未能解析使用率",
                source="jvm_tool",
            )
        )
        return

    if ratio >= thresholds.old_gen_fail:
        status = CheckStatus.FAIL
        state.critical = True
        state.categories.add("heap")
    elif ratio >= thresholds.old_gen_warn:
        status = CheckStatus.WARN
        state.categories.add("heap")
    else:
        status = CheckStatus.PASS

    label = "老年代" if old_ratio is not None else "堆"
    detail = f"{label}使用率 {ratio:.0f}%"
    state.add_check(
        CheckResult(
            id="java_old_gen_usage",
            name="老年代/堆使用率",
            status=status,
            detail=detail,
            source="jvm_tool",
            metrics={"ratio_percent": ratio},
        )
    )
    if status in {CheckStatus.WARN, CheckStatus.FAIL}:
        state.evidence.append(f"jcmd GC.heap_info: {detail}")


def _apply_jstat_checks(state: CollectorState, thresholds: OomRiskThresholds) -> None:
    old_pct = state.jstat.get("o")
    meta_pct = state.jstat.get("m")
    fgc_delta = state.jstat.get("fgc_delta")

    if old_pct is not None:
        if old_pct >= thresholds.old_gen_fail:
            status = CheckStatus.FAIL
            state.critical = True
            state.categories.add("heap")
        elif old_pct >= thresholds.old_gen_warn:
            status = CheckStatus.WARN
            state.categories.add("heap")
        else:
            status = CheckStatus.PASS
        state.add_check(
            CheckResult(
                id="java_old_gen_usage",
                name="老年代使用率",
                status=status,
                detail=f"Old Gen {old_pct:.0f}% (jstat)",
                source="jvm_tool",
                metrics={"old_gen_percent": old_pct},
            )
        )
        if status in {CheckStatus.WARN, CheckStatus.FAIL}:
            state.evidence.append(f"jstat Old Gen {old_pct:.0f}%")

    if meta_pct is not None and meta_pct >= thresholds.metaspace_warn:
        state.categories.add("metaspace")
        state.add_check(
            CheckResult(
                id="java_metaspace_usage",
                name="Metaspace 使用率",
                status=CheckStatus.WARN,
                detail=f"Metaspace {meta_pct:.0f}% (jstat)",
                source="jvm_tool",
                metrics={"metaspace_percent": meta_pct},
            )
        )
        state.evidence.append(f"jstat Metaspace {meta_pct:.0f}%")

    if fgc_delta is not None and fgc_delta >= 3:
        state.add_check(
            CheckResult(
                id="jstat_fgc_storm",
                name="Full GC 频率",
                status=CheckStatus.WARN if fgc_delta < 5 else CheckStatus.FAIL,
                detail=f"采样期间 Full GC 增加 {fgc_delta:.0f} 次",
                source="jvm_tool",
                metrics={"fgc_delta": fgc_delta},
            )
        )
        state.categories.add("heap")
        state.evidence.append(f"jstat 采样期间 FGC +{fgc_delta:.0f}")
