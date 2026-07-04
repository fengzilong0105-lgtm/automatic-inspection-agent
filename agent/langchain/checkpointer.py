from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

logger = logging.getLogger(__name__)

_saver: AsyncSqliteSaver | None = None
_conn: aiosqlite.Connection | None = None


async def get_checkpointer() -> AsyncSqliteSaver:
    """Return the process-wide AsyncSqliteSaver, creating it on first use."""
    global _saver, _conn
    if _saver is not None:
        return _saver

    from agent.settings import get_settings

    db_path: Path = get_settings().data_dir / "chat_checkpoints.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _conn = await aiosqlite.connect(str(db_path))
    _saver = AsyncSqliteSaver(_conn)
    await _saver.setup()
    logger.info("LangGraph checkpointer ready: %s", db_path)
    return _saver


async def close_checkpointer() -> None:
    global _saver, _conn
    if _conn is not None:
        await _conn.close()
    _saver = None
    _conn = None


async def delete_checkpointer_thread(thread_id: str) -> None:
    saver = await get_checkpointer()
    await saver.adelete_thread(thread_id)
