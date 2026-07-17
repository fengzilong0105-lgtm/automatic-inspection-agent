from __future__ import annotations

import re
from typing import TYPE_CHECKING

from agent.executor.middleware_probe import _MIDDLEWARE_PATTERNS
from agent.models import DiscoveredService, ServiceType

if TYPE_CHECKING:
    from agent.executor.ssh import SSHRemoteExecutor

_MIDDLEWARE_HINTS = ("nginx", "redis", "mysql", "mariadb", "postgres", "rabbitmq", "kafka", "zookeeper")


async def detect_middleware(executor: SSHRemoteExecutor, host_id: str) -> list[DiscoveredService]:
    """Detect middleware via systemd units + process table in 3 SSH round trips.

    以前逐 unit 调 systemctl show / is-active（每个 unit 6+ 次往返），跳板机上非常慢；
    现在直接解析 list-units --all 的状态列，并复用一次 ps 输出做进程匹配。
    """
    services: list[DiscoveredService] = []
    seen_ids: set[str] = set()

    # --all：包含 inactive/failed 的 unit，探测未运行的中间件
    units = await executor.run(
        "systemctl list-units --type=service --all --no-pager --no-legend --plain 2>/dev/null || true",
        timeout=30,
    )
    ps = await executor.run("ps -eo pid,cmd 2>/dev/null | grep -v grep || true", timeout=30)
    ps_lines: list[tuple[int, str]] = []
    for line in ps.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if parts and parts[0].isdigit():
            ps_lines.append((int(parts[0]), parts[1] if len(parts) > 1 else ""))

    def _match_process(service_id: str) -> tuple[int | None, str]:
        pattern = _MIDDLEWARE_PATTERNS.get(service_id.lower())
        if not pattern:
            for key, regex in _MIDDLEWARE_PATTERNS.items():
                if key in service_id.lower():
                    pattern = regex
                    break
        if not pattern:
            return None, ""
        for pid, cmd in ps_lines:
            if pattern.search(cmd):
                return pid, cmd
        return None, ""

    for line in units.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        cols = line.split(None, 4)
        if len(cols) < 3:
            continue
        unit, _load, active = cols[0], cols[1], cols[2]
        sub = cols[3] if len(cols) > 3 else ""
        lowered = unit.lower()
        if not any(hint in lowered for hint in _MIDDLEWARE_HINTS):
            continue
        running = active == "active"
        suggested_id = re.sub(r"\.service$", "", unit)
        pid, proc_cmd = _match_process(suggested_id) if running else (None, "")
        confidence = 0.85 if running and pid else 0.75 if running else 0.6
        services.append(
            DiscoveredService(
                suggested_id=suggested_id,
                suggested_name=suggested_id,
                host_id=host_id,
                service_type=ServiceType.MIDDLEWARE,
                pid=pid,
                systemd_unit=unit if unit.endswith(".service") else f"{unit}.service",
                confidence=confidence,
                running=running,
                evidence={
                    "source": "systemd",
                    "unit": unit,
                    "state": f"{active}/{sub}",
                    "process_detail": proc_cmd[:160],
                },
            )
        )
        seen_ids.add(suggested_id)

    # 没被 systemd 管理但进程在跑的中间件
    for hint in _MIDDLEWARE_HINTS:
        if hint in seen_ids:
            continue
        pid, proc_cmd = _match_process(hint)
        if pid is None:
            continue
        services.append(
            DiscoveredService(
                suggested_id=hint,
                suggested_name=hint,
                host_id=host_id,
                service_type=ServiceType.MIDDLEWARE,
                pid=pid,
                confidence=0.8,
                running=True,
                evidence={"source": "process", "detail": proc_cmd[:160]},
            )
        )
        seen_ids.add(hint)

    docker = await executor.run(
        "docker ps --format '{{.Names}}|{{.Image}}' 2>/dev/null | grep -Ei 'nginx|redis|mysql|postgres|kafka' || true",
        timeout=20,
    )
    for line in docker.stdout.splitlines():
        if "|" not in line:
            continue
        name, image = line.split("|", 1)
        if name in seen_ids:
            continue
        services.append(
            DiscoveredService(
                suggested_id=name,
                suggested_name=name,
                host_id=host_id,
                service_type=ServiceType.MIDDLEWARE,
                container_name=name,
                confidence=0.75,
                running=True,
                evidence={"source": "docker middleware", "image": image},
            )
        )
        seen_ids.add(name)
    return services
