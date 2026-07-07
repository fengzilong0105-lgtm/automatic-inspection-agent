from __future__ import annotations

import re
from typing import Any


def _parse_memory_value(token: str) -> int | None:
    token = token.strip().upper()
    match = re.match(r"^(\d+(?:\.\d+)?)([KMG])?$", token)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2) or ""
    multiplier = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3}.get(unit, 1)
    return int(value * multiplier)


def parse_jvm_flags(cmdline: str) -> dict[str, Any]:
    flags: dict[str, Any] = {
        "heap_max_bytes": None,
        "heap_init_bytes": None,
        "metaspace_max_bytes": None,
        "direct_max_bytes": None,
        "heap_dump_on_oom": False,
        "nmt_enabled": False,
        "gc_log_paths": [],
    }
    if not cmdline:
        return flags

    for match in re.finditer(r"-Xmx(\d+(?:\.\d+)?[kKmMgG]?)", cmdline):
        flags["heap_max_bytes"] = _parse_memory_value(match.group(1))
    for match in re.finditer(r"-Xms(\d+(?:\.\d+)?[kKmMgG]?)", cmdline):
        flags["heap_init_bytes"] = _parse_memory_value(match.group(1))
    for match in re.finditer(
        r"-XX:MaxHeapSize=(\d+(?:\.\d+)?[kKmMgG]?)", cmdline, re.I
    ):
        flags["heap_max_bytes"] = _parse_memory_value(match.group(1))
    for match in re.finditer(
        r"-XX:MaxMetaspaceSize=(\d+(?:\.\d+)?[kKmMgG]?)", cmdline, re.I
    ):
        flags["metaspace_max_bytes"] = _parse_memory_value(match.group(1))
    for match in re.finditer(
        r"-XX:MaxDirectMemorySize=(\d+(?:\.\d+)?[kKmMgG]?)", cmdline, re.I
    ):
        flags["direct_max_bytes"] = _parse_memory_value(match.group(1))

    if re.search(r"-XX:\+HeapDumpOnOutOfMemoryError", cmdline, re.I):
        flags["heap_dump_on_oom"] = True
    if re.search(r"-XX:NativeMemoryTracking=(summary|detail)", cmdline, re.I):
        flags["nmt_enabled"] = True

    gc_paths: list[str] = []
    for match in re.finditer(r"-Xloggc:([^\s]+)", cmdline):
        gc_paths.append(match.group(1))
    for match in re.finditer(r"-Xlog:gc(?:\*:file=)?(?:file=)?([^\s:]+)", cmdline):
        path = match.group(1)
        if path and not path.startswith("tags"):
            gc_paths.append(path)
    for match in re.finditer(r"-verbose:gc\b", cmdline):
        gc_paths.append("__verbose_gc_stdout__")
    flags["gc_log_paths"] = list(dict.fromkeys(gc_paths))
    return flags


def parse_gc_heap_info(text: str) -> dict[str, Any]:
    """Best-effort parse of `jcmd <pid> GC.heap_info` output."""
    result: dict[str, Any] = {
        "heap_used_bytes": None,
        "heap_max_bytes": None,
        "old_gen_used_bytes": None,
        "old_gen_max_bytes": None,
        "metaspace_used_bytes": None,
        "metaspace_max_bytes": None,
        "raw_excerpt": text[:2000],
    }
    if not text:
        return result

    heap_total = re.search(r"heap\s+total\s+(\d+)K,\s*used\s+(\d+)K", text, re.I)
    if heap_total:
        result["heap_max_bytes"] = int(heap_total.group(1)) * 1024
        result["heap_used_bytes"] = int(heap_total.group(2)) * 1024

    old_patterns = [
        r"old\s+gen(?:eration)?\s+total\s+(\d+)K,\s*used\s+(\d+)K",
        r"Old\s+Generation\s+total\s+(\d+)K,\s*used\s+(\d+)K",
        r"tenured\s+total\s+(\d+)K,\s*used\s+(\d+)K",
    ]
    for pattern in old_patterns:
        match = re.search(pattern, text, re.I)
        if match:
            result["old_gen_max_bytes"] = int(match.group(1)) * 1024
            result["old_gen_used_bytes"] = int(match.group(2)) * 1024
            break

    meta = re.search(
        r"Metaspace\s+used\s+(\d+)K,\s*capacity\s+(\d+)K,\s*committed\s+(\d+)K,\s*reserved\s+(\d+)K",
        text,
        re.I,
    )
    if meta:
        result["metaspace_used_bytes"] = int(meta.group(1)) * 1024
        result["metaspace_max_bytes"] = int(meta.group(4)) * 1024
    return result


def parse_jstat_gcutil(text: str) -> dict[str, Any]:
    """Parse `jstat -gcutil` tabular output; use last data row."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    data_rows = [
        line
        for line in lines
        if re.match(r"^\s*\d", line) and "O" not in line.split()[0]
    ]
    if not data_rows:
        data_rows = [line for line in lines if re.match(r"^\s*\d", line)]
    if not data_rows:
        return {"raw_excerpt": text[:1000]}

    last = data_rows[-1].split()
    keys = ["S0", "S1", "E", "O", "M", "CCS", "YGC", "YGCT", "FGC", "FGCT", "GCT"]
    parsed: dict[str, Any] = {"raw_excerpt": text[:1000]}
    for idx, key in enumerate(keys):
        if idx < len(last):
            try:
                parsed[key.lower()] = float(last[idx])
            except ValueError:
                parsed[key.lower()] = last[idx]
    if len(data_rows) >= 2:
        first = data_rows[0].split()
        if len(first) > 8 and len(last) > 8:
            try:
                parsed["fgc_delta"] = float(last[8]) - float(first[8])
            except ValueError:
                pass
    return parsed


def parse_gc_log_stats(text: str) -> dict[str, Any]:
    lower = text.lower()
    full_gc = len(re.findall(r"full gc", lower))
    allocation_fail = len(re.findall(r"allocation failure|promotion failed|to-space exhausted", lower))
    pauses = [float(v) for v in re.findall(r"(\d+(?:\.\d+)?)\s*secs?\]", lower)]
    pauses_ms = [float(v) for v in re.findall(r"(\d+(?:\.\d+)?)ms", text)]
    max_pause = max(pauses + [p / 1000 for p in pauses_ms], default=0.0)
    return {
        "full_gc_count": full_gc,
        "allocation_failure_count": allocation_fail,
        "max_pause_seconds": max_pause,
        "line_count": len(text.splitlines()),
    }


def usage_ratio(used: int | float | None, total: int | float | None) -> float | None:
    if used is None or total is None or total <= 0:
        return None
    return float(used) / float(total) * 100.0
