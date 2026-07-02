from __future__ import annotations

from typing import TYPE_CHECKING

from agent.models import DiscoveredService, ServiceType

if TYPE_CHECKING:
    from agent.executor.ssh import SSHRemoteExecutor


async def detect_docker(executor: SSHRemoteExecutor, host_id: str) -> list[DiscoveredService]:
    services: list[DiscoveredService] = []
    result = await executor.run(
        "docker ps --format '{{.ID}}|{{.Names}}|{{.Image}}|{{.Ports}}' 2>/dev/null || true"
    )
    for line in result.stdout.splitlines():
        if not line.strip() or "|" not in line:
            continue
        cid, name, image, ports = (line.split("|", 3) + ["", "", "", ""])[:4]
        listen_ports = _parse_ports(ports)
        services.append(
            DiscoveredService(
                suggested_id=name,
                suggested_name=name,
                host_id=host_id,
                service_type=ServiceType.DOCKER,
                container_name=name,
                listen_ports=listen_ports,
                confidence=0.85,
                evidence={"source": "docker ps", "image": image, "container_id": cid},
            )
        )
    return services


def _parse_ports(ports: str) -> list[int]:
    found: list[int] = []
    for part in ports.split(","):
        part = part.strip()
        if "->" in part:
            host = part.split("->", 1)[0]
            if ":" in host:
                maybe = host.rsplit(":", 1)[-1]
                if maybe.isdigit():
                    found.append(int(maybe))
    return found
