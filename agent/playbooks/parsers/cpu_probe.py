from __future__ import annotations

import re
from statistics import mean


def parse_ps_cpu_samples(text: str) -> list[float]:
    values: list[float] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith("average:"):
            continue
        parts = line.split()
        for token in parts:
            try:
                value = float(token.replace(",", "."))
                if 0 <= value <= 10000:
                    values.append(value)
                    break
            except ValueError:
                continue
    return values


def parse_pidstat_cpu(text: str) -> list[float]:
    values: list[float] = []
    for line in text.splitlines():
        if "%CPU" in line or not line.strip():
            continue
        parts = line.split()
        if len(parts) < 7:
            continue
        try:
            values.append(float(parts[-3]))
        except ValueError:
            continue
    return values


def summarize_cpu_samples(values: list[float]) -> dict[str, float | int | list[float]]:
    if not values:
        return {"samples": [], "cpu_avg": 0.0, "cpu_max": 0.0, "count": 0}
    return {
        "samples": values,
        "cpu_avg": round(mean(values), 1),
        "cpu_max": round(max(values), 1),
        "count": len(values),
    }


def parse_load_ratio(load_avg: str | None, cpu_cores: int | None) -> float | None:
    if not load_avg or not cpu_cores or cpu_cores <= 0:
        return None
    first = load_avg.split()[0]
    try:
        return float(first) / cpu_cores
    except ValueError:
        return None


def parse_ps_top_rank(text: str, pid: int) -> dict[str, object]:
    lines = [line for line in text.splitlines() if line.strip()]
    rank = None
    top_pid = None
    top_cpu = None
    for idx, line in enumerate(lines[1:], start=1):
        parts = line.split(None, 10)
        if len(parts) < 11:
            continue
        try:
            row_pid = int(parts[1])
            cpu = float(parts[2])
        except ValueError:
            continue
        if idx == 1:
            top_pid = row_pid
            top_cpu = cpu
        if row_pid == pid:
            rank = idx
    return {
        "rank": rank,
        "is_top1": rank == 1,
        "top_pid": top_pid,
        "top_cpu": top_cpu,
    }
