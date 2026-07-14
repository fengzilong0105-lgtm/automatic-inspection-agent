from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable

from agent.executor.ssh import ExecutorRegistry, get_executor_registry
from agent.incident.rules import RuleEngine
from agent.models import Incident
from agent.settings import Settings, get_settings
from agent.store.incidents import IncidentStore

logger = logging.getLogger(__name__)

AlertCallback = Callable[[Incident], Awaitable[None]]


class MonitorLoop:
    def __init__(
        self,
        settings: Settings | None = None,
        executor_registry: ExecutorRegistry | None = None,
        incident_store: IncidentStore | None = None,
        on_alert: AlertCallback | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.executor_registry = executor_registry or get_executor_registry()
        self.incident_store = incident_store or IncidentStore(self.settings.data_dir / "agent.db")
        self.on_alert = on_alert
        self.rule_engine = RuleEngine()
        self._task: asyncio.Task | None = None
        self._health_fail_streak: dict[str, int] = {}
        self._recent_alert_keys: set[str] = set()

    def forget_services(self, service_ids: list[str]) -> None:
        """Drop in-memory monitor state for removed services."""
        for service_id in service_ids:
            self._health_fail_streak.pop(service_id, None)
        prefixes = tuple(f"{sid}:" for sid in service_ids)
        if prefixes:
            self._recent_alert_keys = {
                key for key in self._recent_alert_keys if not key.startswith(prefixes)
            }

    async def start(self) -> None:
        await self.incident_store.init()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def run_once(self) -> list[Incident]:
        created: list[Incident] = []
        for service in self.settings.get_enabled_services():
            try:
                host = self.settings.get_host(service.host_id)
                executor = self.executor_registry.get(service.host_id, host)
                status = await executor.service_status(service)
                log_tail = ""
                if service.log_path:
                    log_tail = await executor.tail_log(service.log_path, lines=100, pattern="ERROR|Exception|OOM")

                streak = self._health_fail_streak.get(service.id, 0)
                if status.health_ok is False:
                    streak += 1
                else:
                    streak = 0
                self._health_fail_streak[service.id] = streak

                alerts = self.rule_engine.evaluate(service, status, log_tail, streak)
                for alert in alerts:
                    key = f"{service.id}:{alert['title']}"
                    if key in self._recent_alert_keys:
                        continue
                    self._recent_alert_keys.add(key)
                    incident = await self.incident_store.create_incident(
                        service_id=service.id,
                        host_id=service.host_id,
                        title=alert["title"],
                        severity=alert["severity"],
                        summary=alert["summary"],
                        log_snippet=alert.get("log_snippet", ""),
                    )
                    created.append(incident)
                    if self.on_alert:
                        await self.on_alert(incident)
            except Exception as exc:
                logger.exception("Monitor failed for service %s: %s", service.id, exc)
        return created

    async def _run_forever(self) -> None:
        interval = self.settings.config.monitor.interval_seconds
        while True:
            await self.run_once()
            await asyncio.sleep(interval)
