from __future__ import annotations

import re
from typing import TYPE_CHECKING

from agent.models import DiscoveredService, ServiceType

if TYPE_CHECKING:
    from agent.executor.ssh import SSHRemoteExecutor


async def detect_compose(executor: SSHRemoteExecutor, host_id: str) -> list[DiscoveredService]:
    services: list[DiscoveredService] = []
    # 没装 docker 就直接跳过，省掉后面的 find
    has_docker = await executor.run("command -v docker >/dev/null 2>&1 && echo yes || true", timeout=15)
    if has_docker.stdout.strip() != "yes":
        return services

    find_result = await executor.run(
        "find /opt /srv /home /app /data /var/www -maxdepth 4 "
        "\\( -path '*/node_modules/*' -o -path '*/.git/*' \\) -prune -o "
        "-name 'docker-compose*.yml' -print 2>/dev/null | head -10",
        timeout=45,
    )
    compose_files = [line.strip() for line in find_result.stdout.splitlines() if line.strip()]

    for compose_file in compose_files:
        # -a：包含已停止/退出的 compose 服务
        ps = await executor.run(
            f"docker compose -f {compose_file!r} ps -a --format '{{{{.Service}}}}|{{{{.State}}}}|{{{{.Ports}}}}' 2>/dev/null || true",
            timeout=30,
        )
        for line in ps.stdout.splitlines():
            if "|" not in line:
                continue
            svc_name, state, ports = (line.split("|", 2) + ["", "", ""])[:3]
            if not svc_name:
                continue
            running = state.strip().lower() == "running"
            suggested_id = f"{compose_file.split('/')[-2]}-{svc_name}".lower()
            suggested_id = re.sub(r"[^a-z0-9-]+", "-", suggested_id)
            services.append(
                DiscoveredService(
                    suggested_id=suggested_id,
                    suggested_name=svc_name,
                    host_id=host_id,
                    service_type=ServiceType.COMPOSE,
                    compose_file=compose_file,
                    compose_service=svc_name,
                    listen_ports=_parse_ports(ports),
                    confidence=0.8 if running else 0.6,
                    running=running,
                    evidence={"source": "docker compose ps -a", "state": state},
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
