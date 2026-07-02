from __future__ import annotations

from typing import Protocol

from agent.models import CommandResult, HostMetrics, ServiceConfig, ServiceStatus


class Executor(Protocol):
    host_id: str

    async def run(self, cmd: str, timeout: int = 60) -> CommandResult: ...

    async def tail_log(
        self, path: str, lines: int = 200, pattern: str | None = None
    ) -> str: ...

    async def read_file(self, path: str, max_bytes: int = 65536) -> str: ...

    async def service_status(self, service: ServiceConfig) -> ServiceStatus: ...

    async def restart_service(self, service: ServiceConfig) -> CommandResult: ...

    async def get_metrics(self) -> HostMetrics: ...

    async def test_connection(self) -> CommandResult: ...
