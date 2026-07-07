from __future__ import annotations

import re
import shlex

from agent.playbooks.config import DEFAULT_CPU_THRESHOLDS, CpuRiskThresholds
from agent.playbooks.models import CheckResult, CheckStatus, CpuCollectorState
from agent.playbooks.parsers.cgroup_cpu import parse_cgroup_cpu_throttle, parse_docker_cpu_line


async def collect_cpu_docker(
    executor,
    state: CpuCollectorState,
    thresholds: CpuRiskThresholds = DEFAULT_CPU_THRESHOLDS,
) -> None:
    container = state.container_name
    if not container:
        return

    quoted = shlex.quote(container)
    stats = await executor.run(
        f"docker stats --no-stream --format '{{{{.CPUPerc}}}}|{{{{.PIDs}}}}' {quoted} 2>/dev/null"
    )
    cpu_percent = None
    if stats.stdout.strip():
        cpu_part = stats.stdout.strip().split("|")[0]
        cpu_percent = parse_docker_cpu_line(cpu_part)

    inspect = await executor.run(
        f"docker inspect -f 'restarts={{.RestartCount}}' {quoted} 2>/dev/null"
    )
    restarts = 0
    for part in (inspect.stdout or "").split():
        if part.startswith("restarts="):
            try:
                restarts = int(part.split("=", 1)[1])
            except ValueError:
                pass
    state.docker["restarts"] = restarts
    state.docker["cpu_percent"] = cpu_percent

    if cpu_percent is not None:
        if cpu_percent >= thresholds.container_cpu_fail:
            cpu_status = CheckStatus.FAIL
            state.categories.add("process_hot")
            state.critical = True
        elif cpu_percent >= thresholds.container_cpu_warn:
            cpu_status = CheckStatus.WARN
            state.categories.add("process_hot")
        else:
            cpu_status = CheckStatus.PASS
        detail = f"容器 CPU {cpu_percent:.1f}%"
        state.add_check(
            CheckResult(
                id="container_cpu_percent",
                name="容器 CPU",
                status=cpu_status,
                detail=detail,
                source="live_probe",
                metrics={"cpu_percent": cpu_percent},
            )
        )
        if cpu_status in {CheckStatus.WARN, CheckStatus.FAIL}:
            state.evidence.append(detail)

    throttle_cmds = [
        f"docker exec {quoted} cat /sys/fs/cgroup/cpu.stat 2>/dev/null",
        f"docker exec {quoted} cat /sys/fs/cgroup/cpu/cpu.stat 2>/dev/null",
    ]
    throttle_text = ""
    for cmd in throttle_cmds:
        result = await executor.run(cmd)
        if (result.stdout or "").strip():
            throttle_text = result.stdout
            break
    if not throttle_text:
        cid_result = await executor.run(f"docker inspect -f '{{{{.Id}}}}' {quoted}")
        cid = (cid_result.stdout or "").strip()[:12]
        if cid:
            host_paths = [
                f"/sys/fs/cgroup/system.slice/docker-{cid}.scope/cpu.stat",
                f"/sys/fs/cgroup/cpu/system.slice/docker-{cid}.scope/cpu.stat",
            ]
            for path in host_paths:
                result = await executor.run(f"cat {shlex.quote(path)} 2>/dev/null")
                if (result.stdout or "").strip():
                    throttle_text = result.stdout
                    break

    throttle = parse_cgroup_cpu_throttle(throttle_text)
    state.docker.update(throttle)
    ratio = throttle.get("throttle_ratio")
    if ratio is None:
        state.add_check(
            CheckResult(
                id="container_cpu_throttled",
                name="容器 CPU 限流",
                status=CheckStatus.SKIP,
                detail="无法读取 cgroup cpu.stat",
                source="live_probe",
            )
        )
        return

    if ratio >= thresholds.throttle_ratio_fail:
        t_status = CheckStatus.FAIL
        state.categories.add("cgroup_throttled")
        state.critical = True
    elif ratio >= thresholds.throttle_ratio_warn:
        t_status = CheckStatus.WARN
        state.categories.add("cgroup_throttled")
    else:
        t_status = CheckStatus.PASS
    detail = f"CPU 限流占比 {ratio * 100:.1f}%（{throttle.get('nr_throttled')}/{throttle.get('nr_periods')}）"
    state.add_check(
        CheckResult(
            id="container_cpu_throttled",
            name="容器 CPU 限流",
            status=t_status,
            detail=detail,
            source="live_probe",
            metrics={"throttle_ratio": ratio},
        )
    )
    if t_status in {CheckStatus.WARN, CheckStatus.FAIL}:
        state.evidence.append(detail)
        state.next_commands.append(f"docker stats --no-stream {quoted}")
