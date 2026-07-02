from __future__ import annotations

import re
from typing import TYPE_CHECKING

from agent.executor.middleware_probe import probe_middleware_process
from agent.executor.systemd_probe import probe_systemd_unit
from agent.models import DiscoveredService, ServiceType

if TYPE_CHECKING:
    from agent.executor.ssh import SSHRemoteExecutor

_MIDDLEWARE_HINTS = ("nginx", "redis", "mysql", "mariadb", "postgres", "rabbitmq", "kafka", "zookeeper")


async def detect_middleware(executor: SSHRemoteExecutor, host_id: str) -> list[DiscoveredService]:
    services: list[DiscoveredService] = []
    seen_ids: set[str] = set()

    units = await executor.run("systemctl list-units --type=service --state=running --no-pager --no-legend")
    for line in units.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        unit = line.split()[0]
        lowered = unit.lower()
        if not any(hint in lowered for hint in _MIDDLEWARE_HINTS):
            continue
        suggested_id = re.sub(r"\.service$", "", unit)
        probe = await probe_systemd_unit(executor, unit)
        process_probe = await probe_middleware_process(executor, suggested_id)
        pid = probe.get("main_pid") or (
            process_probe["matches"][0]["pid"] if process_probe.get("matches") else None
        )
        confidence = 0.85 if probe["running"] and pid else 0.75 if probe["running"] else 0.65
        services.append(
            DiscoveredService(
                suggested_id=suggested_id,
                suggested_name=suggested_id,
                host_id=host_id,
                service_type=ServiceType.MIDDLEWARE,
                pid=pid,
                systemd_unit=unit if unit.endswith(".service") else f"{unit}.service",
                confidence=confidence,
                evidence={
                    "source": "systemd",
                    "unit": unit,
                    "systemd_detail": probe["detail"],
                    "process_detail": process_probe["detail"],
                },
            )
        )
        seen_ids.add(suggested_id)

    for hint in _MIDDLEWARE_HINTS:
        if hint in seen_ids:
            continue
        process_probe = await probe_middleware_process(executor, hint)
        if not process_probe["running"]:
            continue
        pid = process_probe["matches"][0]["pid"] if process_probe.get("matches") else None
        services.append(
            DiscoveredService(
                suggested_id=hint,
                suggested_name=hint,
                host_id=host_id,
                service_type=ServiceType.MIDDLEWARE,
                pid=pid,
                confidence=0.8 if pid else 0.7,
                evidence={"source": "process", "detail": process_probe["detail"]},
            )
        )
        seen_ids.add(hint)

    docker = await executor.run(
        "docker ps --format '{{.Names}}|{{.Image}}' 2>/dev/null | grep -Ei 'nginx|redis|mysql|postgres|kafka' || true"
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
                evidence={"source": "docker middleware", "image": image},
            )
        )
        seen_ids.add(name)
    return services
