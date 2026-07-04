from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import Future
from typing import Any, Awaitable, Callable, TypeVar

from agent.executor.ssh import get_executor_registry
from agent.feishu.notifier import FeishuNotifier
from agent.langchain.chat_graph import ChatAgent
from agent.models import ServiceConfig
from agent.monitor.loop import MonitorLoop
from agent.remediation.orchestrator import ActionOrchestrator
from agent.remediation.write_orchestrator import WriteOrchestrator
from agent.settings import get_settings
from agent.store.incidents import IncidentStore

logger = logging.getLogger(__name__)

T = TypeVar("T")

_runtime: BackgroundRuntime | None = None


class BackgroundRuntime:
    """Dedicated asyncio loop for monitor, SSH, and LLM work."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self.incident_store: IncidentStore | None = None
        self.monitor: MonitorLoop | None = None
        self.chat_agent = ChatAgent()
        self.action_orchestrator = ActionOrchestrator()
        self.write_orchestrator = WriteOrchestrator()
        self.feishu = FeishuNotifier()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._thread_main, name="agent-runtime", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=30):
            raise RuntimeError("Agent runtime failed to start within 30 seconds")

    def stop(self) -> None:
        if not self._loop or not self._loop.is_running():
            return
        future = asyncio.run_coroutine_threadsafe(self._async_shutdown(), self._loop)
        try:
            future.result(timeout=15)
        except Exception:
            logger.exception("Error stopping agent runtime")
        self._loop.call_soon_threadsafe(self._loop.stop)

    def run(self, coro: Awaitable[T]) -> Future[T]:
        if not self._loop:
            raise RuntimeError("Agent runtime is not started")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_startup())
            self._ready.set()
            self._loop.run_forever()
        finally:
            try:
                self._loop.run_until_complete(self._async_shutdown())
            except Exception:
                logger.exception("Runtime shutdown error")
            self._loop.close()

    async def _async_startup(self) -> None:
        settings = get_settings()
        self.incident_store = IncidentStore(settings.data_dir / "agent.db")
        await self.incident_store.init()

        async def on_alert(incident):
            await self.feishu.send_incident_card(incident)

        self.monitor = MonitorLoop(on_alert=on_alert, incident_store=self.incident_store)
        await self.monitor.start()
        logger.info("Background runtime started")

    async def _async_shutdown(self) -> None:
        if self.monitor:
            await self.monitor.stop()
        await get_executor_registry().close_all()
        logger.info("Background runtime stopped")


def get_runtime() -> BackgroundRuntime:
    global _runtime
    if _runtime is None:
        _runtime = BackgroundRuntime()
    return _runtime


def shutdown_runtime() -> None:
    global _runtime
    if _runtime is not None:
        _runtime.stop()
        _runtime = None
