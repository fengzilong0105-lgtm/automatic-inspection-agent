from __future__ import annotations

from contextvars import ContextVar

chat_session_id: ContextVar[str] = ContextVar("chat_session_id", default="default")
