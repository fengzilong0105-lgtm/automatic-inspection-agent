from __future__ import annotations

import re
from collections import Counter


def gc_cpu_ratio_from_jstat(samples: list[dict[str, float]], interval_seconds: float) -> float | None:
    if len(samples) < 2 or interval_seconds <= 0:
        return None
    gcts = [row.get("gct") for row in samples if row.get("gct") is not None]
    if len(gcts) < 2:
        return None
    delta = gcts[-1] - gcts[0]
    window = interval_seconds * (len(gcts) - 1)
    if window <= 0 or delta < 0:
        return None
    return round(delta / window * 100.0, 1)


def parse_jstat_rows(text: str) -> list[dict[str, float]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    rows: list[dict[str, float]] = []
    for line in lines:
        if not re.match(r"^\s*\d", line):
            continue
        parts = line.split()
        if len(parts) < 11:
            continue
        try:
            rows.append(
                {
                    "s0": float(parts[0]),
                    "s1": float(parts[1]),
                    "e": float(parts[2]),
                    "o": float(parts[3]),
                    "m": float(parts[4]),
                    "ygc": float(parts[6]),
                    "fgc": float(parts[8]),
                    "fgct": float(parts[9]),
                    "gct": float(parts[10]),
                }
            )
        except ValueError:
            continue
    return rows


def summarize_jstack(text: str) -> dict[str, object]:
    runnable = 0
    blocked = 0
    waiting = 0
    stacks: list[str] = []
    current: list[str] = []
    state = ""

    for line in text.splitlines():
        header = re.match(r'^"([^"]+)".*?java\.lang\.Thread\.State:\s*(\w+)', line)
        if header:
            if current:
                stacks.append("\n".join(current))
            current = [line]
            state = header.group(2).upper()
            if state == "RUNNABLE":
                runnable += 1
            elif state == "BLOCKED":
                blocked += 1
            elif "WAIT" in state:
                waiting += 1
            continue
        if current and line.strip().startswith("at "):
            current.append(line.strip())
    if current:
        stacks.append("\n".join(current))

    normalized = []
    for stack in stacks:
        lines_only = [ln for ln in stack.splitlines() if ln.strip().startswith("at ")]
        normalized.append("\n".join(lines_only[:4]))

    counter = Counter(normalized)
    top_stacks = counter.most_common(3)
    return {
        "runnable": runnable,
        "blocked": blocked,
        "waiting": waiting,
        "top_stacks": [
            {"count": count, "excerpt": excerpt[:300]} for excerpt, count in top_stacks if excerpt
        ],
    }
