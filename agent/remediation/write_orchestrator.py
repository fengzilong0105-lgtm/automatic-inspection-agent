from __future__ import annotations

from agent.executor.ssh import get_executor_registry
from agent.models import CommandResult
from agent.remediation.pending_writes import PendingFileOp, get_pending_file_op_store
from agent.settings import Settings, get_settings


class WriteOrchestrator:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.executor_registry = get_executor_registry()
        self.pending_store = get_pending_file_op_store()

    async def execute_pending_write(self, write_id: str, session_id: str) -> CommandResult:
        return await self.execute_pending_op(write_id, session_id)

    async def execute_pending_op(self, op_id: str, session_id: str) -> CommandResult:
        pending = self.pending_store.pop(op_id, session_id)
        if not pending:
            return CommandResult(
                stdout="",
                stderr=(
                    "操作请求不存在或已过期（可能已确认过，或对话已切换）。"
                    "请让助手重新提交该操作后再确认。"
                ),
                exit_code=404,
            )
        if pending.action == "command":
            return await self._run_command(pending)
        if pending.action == "delete":
            return await self._delete(pending)
        return await self._write(pending)

    async def _write(self, pending: PendingFileOp) -> CommandResult:
        host = self.settings.get_host(pending.host_id)
        executor = self.executor_registry.get(pending.host_id, host)
        return await executor.write_file(pending.path, pending.content or "")

    async def _delete(self, pending: PendingFileOp) -> CommandResult:
        host = self.settings.get_host(pending.host_id)
        executor = self.executor_registry.get(pending.host_id, host)
        return await executor.delete_file(pending.path)

    async def _run_command(self, pending: PendingFileOp) -> CommandResult:
        command = (pending.command or "").strip()
        if not command:
            return CommandResult(stdout="", stderr="待确认命令为空", exit_code=400)
        host = self.settings.get_host(pending.host_id)
        executor = self.executor_registry.get(pending.host_id, host)
        return await executor.run(command, timeout=pending.timeout_seconds)
