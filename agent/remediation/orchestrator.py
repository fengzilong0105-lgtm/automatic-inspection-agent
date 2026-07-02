from __future__ import annotations

import asyncio

from agent.executor.ssh import ExecutorRegistry, get_executor_registry
from agent.models import CommandResult, ServiceConfig
from agent.settings import Settings, get_settings
from agent.store.incidents import IncidentStore


class ActionOrchestrator:
    def __init__(
        self,
        settings: Settings | None = None,
        executor_registry: ExecutorRegistry | None = None,
        incident_store: IncidentStore | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.executor_registry = executor_registry or get_executor_registry()
        self.incident_store = incident_store or IncidentStore(self.settings.data_dir / "agent.db")

    async def restart_service(self, service_id: str) -> CommandResult:
        service = self.settings.get_service(service_id)
        max_restarts = self.settings.config.autonomy.max_restart_per_15min
        recent = await self.incident_store.count_recent_restarts(service_id, minutes=15)
        if recent >= max_restarts:
            return CommandResult(
                stdout="",
                stderr=f"重启冷却中：15 分钟内已重启 {recent} 次，上限 {max_restarts}",
                exit_code=429,
            )

        host = self.settings.get_host(service.host_id)
        executor = self.executor_registry.get(service.host_id, host)
        result = await executor.restart_service(service)
        if result.exit_code == 0:
            await self.incident_store.record_restart(service_id)
            await self._wait_for_health(service, executor)
        return result

    async def _wait_for_health(self, service: ServiceConfig, executor) -> None:
        for _ in range(12):
            status = await executor.service_status(service)
            if status.health_ok is not False and status.running:
                return
            await asyncio.sleep(5)
