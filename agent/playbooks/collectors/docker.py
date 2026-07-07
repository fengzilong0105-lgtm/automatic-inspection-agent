from __future__ import annotations

import re
import shlex

from agent.playbooks.config import DEFAULT_OOM_THRESHOLDS, OomRiskThresholds
from agent.playbooks.jvm_flags import usage_ratio
from agent.playbooks.models import CheckResult, CheckStatus, CollectorState


async def collect_docker(
    executor,
    state: CollectorState,
    thresholds: OomRiskThresholds = DEFAULT_OOM_THRESHOLDS,
) -> None:
    container = state.container_name
    if not container:
        return

    quoted = shlex.quote(container)
    inspect = await executor.run(
        f"docker inspect -f 'limit={{.HostConfig.Memory}} "
        f"oomkilled={{.State.OOMKilled}} "
        f"restarts={{.RestartCount}}' {quoted} 2>/dev/null"
    )
    text = inspect.stdout.strip()
    limit_bytes = None
    oom_killed = False
    restarts = 0
    for part in text.split():
        if part.startswith("limit="):
            raw = part.split("=", 1)[1]
            if raw.isdigit() and int(raw) > 0:
                limit_bytes = int(raw)
        elif part.startswith("oomkilled="):
            oom_killed = part.split("=", 1)[1].lower() == "true"
        elif part.startswith("restarts="):
            try:
                restarts = int(part.split("=", 1)[1])
            except ValueError:
                pass

    stats = await executor.run(
        f"docker stats --no-stream --format '{{{{.MemUsage}}}}' {quoted} 2>/dev/null"
    )
    usage_bytes = None
    usage_line = stats.stdout.strip()
    match = re.match(r"([\d.]+)([KMGT]?i?B)\s*/\s*([\d.]+)([KMGT]?i?B)", usage_line, re.I)
    if match:
        usage_bytes = _to_bytes(float(match.group(1)), match.group(2))
        if limit_bytes is None:
            limit_bytes = _to_bytes(float(match.group(3)), match.group(4))

    state.docker = {
        "container": container,
        "limit_bytes": limit_bytes,
        "usage_bytes": usage_bytes,
        "oom_killed": oom_killed,
        "restarts": restarts,
    }

    if oom_killed:
        state.add_check(
            CheckResult(
                id="container_oom_killed",
                name="容器 OOMKilled",
                status=CheckStatus.FAIL,
                detail="容器历史上曾被 OOM Kill",
                source="live_probe",
            )
        )
        state.categories.add("cgroup")
        state.critical = True
        state.evidence.append("docker inspect: OOMKilled=true")
    else:
        state.add_check(
            CheckResult(
                id="container_oom_killed",
                name="容器 OOMKilled",
                status=CheckStatus.PASS,
                detail="未发现 OOMKilled",
                source="live_probe",
            )
        )

    ratio = usage_ratio(usage_bytes, limit_bytes)
    if ratio is not None:
        if ratio >= thresholds.container_mem_fail:
            status = CheckStatus.FAIL
            state.critical = True
        elif ratio >= thresholds.container_mem_warn:
            status = CheckStatus.WARN
        else:
            status = CheckStatus.PASS
        detail = f"容器内存 {usage_bytes // (1024**2)}MB / {limit_bytes // (1024**2)}MB ({ratio:.0f}%)"
        state.add_check(
            CheckResult(
                id="container_memory_limit",
                name="容器内存上限",
                status=status,
                detail=detail,
                source="live_probe",
                metrics={"usage_ratio_percent": ratio},
            )
        )
        if status in {CheckStatus.WARN, CheckStatus.FAIL}:
            state.categories.add("cgroup")
            state.evidence.append(detail)
    elif limit_bytes == 0:
        state.add_check(
            CheckResult(
                id="container_memory_limit",
                name="容器内存上限",
                status=CheckStatus.SKIP,
                detail="容器未设置 memory limit",
                source="live_probe",
            )
        )


def _to_bytes(value: float, unit: str) -> int:
    unit = unit.upper().replace("IB", "B").replace("I", "")
    mult = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    for key, factor in sorted(mult.items(), key=lambda item: -len(item[0])):
        if unit.startswith(key):
            return int(value * factor)
    return int(value)
