from __future__ import annotations

import re
from typing import TYPE_CHECKING

from agent.models import DiscoveredService, ServiceConfig, ServiceType
from agent.discovery import compose, docker, java, middleware

if TYPE_CHECKING:
    from agent.executor.ssh import SSHRemoteExecutor


async def scan_host(executor: SSHRemoteExecutor, host_id: str) -> list[DiscoveredService]:
    discovered: list[DiscoveredService] = []
    discovered.extend(await java.detect_java(executor, host_id))
    discovered.extend(await docker.detect_docker(executor, host_id))
    discovered.extend(await compose.detect_compose(executor, host_id))
    discovered.extend(await middleware.detect_middleware(executor, host_id))
    return _deduplicate(discovered)


def _deduplicate(services: list[DiscoveredService]) -> list[DiscoveredService]:
    by_pid: dict[int, DiscoveredService] = {}
    for svc in services:
        if svc.pid is None:
            continue
        existing = by_pid.get(svc.pid)
        if existing is None or svc.confidence > existing.confidence:
            by_pid[svc.pid] = svc

    merged = list(by_pid.values()) + [svc for svc in services if svc.pid is None]
    by_key: dict[str, DiscoveredService] = {}
    for svc in merged:
        key = f"{svc.host_id}:{svc.suggested_id}"
        existing = by_key.get(key)
        if existing is None or svc.confidence > existing.confidence:
            by_key[key] = svc
    return list(by_key.values())


def to_service_config(item: DiscoveredService) -> ServiceConfig:
    return ServiceConfig(
        id=item.suggested_id,
        host_id=item.host_id,
        name=item.suggested_name,
        type=item.service_type,
        enabled=True,
        jar_path=item.jar_path,
        deploy_dir=item.deploy_dir,
        systemd_unit=item.systemd_unit,
        container_name=item.container_name,
        compose_file=item.compose_file,
        compose_service=item.compose_service,
        health_url=item.health_url,
        log_path=item.log_path,
        config_files=[c.model_copy() for c in item.config_files],
        active_profile=item.spring_profile,
        listen_ports=item.listen_ports,
    )
