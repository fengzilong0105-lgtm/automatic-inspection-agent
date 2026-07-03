from __future__ import annotations

from agent.executor.write_policy import is_path_allowed
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
                stderr="文件操作请求不存在、已过期或会话不匹配",
                exit_code=404,
            )
        if not is_path_allowed(pending.path, self.settings.config.autonomy):
            return CommandResult(
                stdout="",
                stderr=f"路径未被允许: {pending.path}",
                exit_code=403,
            )
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
