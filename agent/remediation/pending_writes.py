from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Literal

from agent.executor.write_policy import content_preview, validate_write_content

FileOpAction = Literal["write", "delete"]


@dataclass
class PendingFileOp:
    op_id: str
    session_id: str
    host_id: str
    action: FileOpAction
    path: str
    content: str | None
    created_at: float


class PendingFileOpStore:
    def __init__(self, ttl_seconds: int = 1800) -> None:
        self._ttl = ttl_seconds
        self._pending: dict[str, PendingFileOp] = {}

    def _purge_expired(self) -> None:
        now = time.time()
        expired = [oid for oid, item in self._pending.items() if now - item.created_at > self._ttl]
        for oid in expired:
            self._pending.pop(oid, None)

    def _check_session_limit(self, session_id: str) -> None:
        session_items = [item for item in self._pending.values() if item.session_id == session_id]
        if len(session_items) >= 5:
            raise ValueError("当前会话待确认文件操作过多，请先确认或取消已有请求")

    def create_write(self, session_id: str, host_id: str, path: str, content: str) -> PendingFileOp:
        self._purge_expired()
        self._check_session_limit(session_id)
        validated = validate_write_content(content)
        op_id = secrets.token_urlsafe(12)
        item = PendingFileOp(
            op_id=op_id,
            session_id=session_id,
            host_id=host_id,
            action="write",
            path=path,
            content=validated,
            created_at=time.time(),
        )
        self._pending[op_id] = item
        return item

    def create_delete(self, session_id: str, host_id: str, path: str) -> PendingFileOp:
        self._purge_expired()
        self._check_session_limit(session_id)
        op_id = secrets.token_urlsafe(12)
        item = PendingFileOp(
            op_id=op_id,
            session_id=session_id,
            host_id=host_id,
            action="delete",
            path=path,
            content=None,
            created_at=time.time(),
        )
        self._pending[op_id] = item
        return item

    def get(self, op_id: str, session_id: str) -> PendingFileOp | None:
        self._purge_expired()
        item = self._pending.get(op_id)
        if not item or item.session_id != session_id:
            return None
        return item

    def pop(self, op_id: str, session_id: str) -> PendingFileOp | None:
        item = self.get(op_id, session_id)
        if item:
            self._pending.pop(op_id, None)
        return item

    def latest_for_session(self, session_id: str) -> PendingFileOp | None:
        self._purge_expired()
        items = [item for item in self._pending.values() if item.session_id == session_id]
        if not items:
            return None
        return max(items, key=lambda item: item.created_at)

    def to_confirm_payload(self, item: PendingFileOp, host_label: str) -> dict:
        action_label = "写入" if item.action == "write" else "删除"
        payload = {
            "op_id": item.op_id,
            "write_id": item.op_id,
            "action": item.action,
            "host_id": item.host_id,
            "host_label": host_label,
            "path": item.path,
            "requires_confirm": True,
        }
        if item.action == "write" and item.content is not None:
            payload["content_preview"] = content_preview(item.content)
            payload["content_bytes"] = len(item.content.encode("utf-8"))
            payload["message"] = (
                f"确认写入/修改 `{item.path}` 吗？（主机: {host_label}，"
                f"{payload['content_bytes']} 字节）"
            )
        else:
            payload["content_preview"] = ""
            payload["message"] = f"确认删除 `{item.path}` 吗？（主机: {host_label}）"
        return payload


_pending_file_op_store = PendingFileOpStore()


def get_pending_file_op_store() -> PendingFileOpStore:
    return _pending_file_op_store


def get_pending_write_store() -> PendingFileOpStore:
    return get_pending_file_op_store()
