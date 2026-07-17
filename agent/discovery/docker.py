from __future__ import annotations

from typing import TYPE_CHECKING

from agent.models import DiscoveredService, ServiceType

if TYPE_CHECKING:
    from agent.executor.ssh import SSHRemoteExecutor


async def detect_docker(executor: SSHRemoteExecutor, host_id: str) -> list[DiscoveredService]:
    services: list[DiscoveredService] = []
    # -a：把已停止的容器也纳入（标记 running=False，注册后默认不巡检）
    result = await executor.run(
        "docker ps -a --format '{{.ID}}|{{.Names}}|{{.Image}}|{{.State}}|{{.Ports}}' 2>/dev/null || true",
        timeout=30,
    )
    for line in result.stdout.splitlines():
        if not line.strip() or "|" not in line:
            continue
        cid, name, image, state, ports = (line.split("|", 4) + ["", "", "", "", ""])[:5]
        running = state.strip().lower() == "running"
        listen_ports = _parse_ports(ports)
        services.append(
            DiscoveredService(
                suggested_id=name,
                suggested_name=name,
                host_id=host_id,
                service_type=ServiceType.DOCKER,
                container_name=name,
                listen_ports=listen_ports,
                confidence=0.85 if running else 0.6,
                running=running,
                evidence={
                    "source": "docker ps -a",
                    "image": image,
                    "container_id": cid,
                    "state": state,
                },
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
