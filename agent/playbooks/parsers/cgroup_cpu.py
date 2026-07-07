from __future__ import annotations

import re


def parse_cgroup_cpu_throttle(text: str) -> dict[str, int | float | None]:
    stats: dict[str, int] = {}
    for line in text.splitlines():
        if " " not in line:
            continue
        key, value = line.split(maxsplit=1)
        try:
            stats[key.strip()] = int(value.strip())
        except ValueError:
            continue
    periods = stats.get("nr_periods") or stats.get("cpu.cfs_periods")
    throttled = stats.get("nr_throttled") or stats.get("cpu.cfs_throttled_periods")
    ratio = None
    if periods and periods > 0 and throttled is not None:
        ratio = round(throttled / periods, 4)
    return {
        "nr_periods": periods,
        "nr_throttled": throttled,
        "throttle_ratio": ratio,
    }


def parse_docker_cpu_line(text: str) -> float | None:
    line = text.strip()
    match = re.search(r"([\d.]+)%", line)
    if match:
        return float(match.group(1))
    return None
