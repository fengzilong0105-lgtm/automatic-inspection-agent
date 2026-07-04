from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from agent.langchain.context_builder import build_chat_system_prompt
from agent.langchain.context_limits import resolve_context_limit
from agent.langchain.token_meter import estimate_messages_tokens, estimate_tokens, format_token_count
from agent.settings import get_settings
from agent.store.knowledge import get_knowledge_store


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Conversation:
    id: str
    title: str
    created_at: str
    updated_at: str
    token_count: int
    context_limit: int
    summary: str | None
    status: str
    last_compaction_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "token_count": self.token_count,
            "context_limit": self.context_limit,
            "summary": self.summary,
            "status": self.status,
            "last_compaction_at": self.last_compaction_at,
        }


@dataclass
class ChatMessage:
    id: str
    conversation_id: str
    role: str
    content: str
    raw_content: str | None
    tool_name: str | None
    token_estimate: int
    created_at: str
    archived: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "role": self.role,
            "content": self.content,
            "raw_content": self.raw_content,
            "tool_name": self.tool_name,
            "token_estimate": self.token_estimate,
            "created_at": self.created_at,
            "archived": self.archived,
        }


class ChatStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    token_count INTEGER DEFAULT 0,
                    context_limit INTEGER DEFAULT 524288,
                    summary TEXT,
                    status TEXT DEFAULT 'active'
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    raw_content TEXT,
                    tool_name TEXT,
                    token_estimate INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation
                ON chat_messages(conversation_id, created_at)
                """
            )
            await self._migrate_schema(db)
            await db.commit()

    async def _migrate_schema(self, db: aiosqlite.Connection) -> None:
        conv_cols = {row[1] for row in await (await db.execute("PRAGMA table_info(conversations)")).fetchall()}
        if "last_compaction_at" not in conv_cols:
            await db.execute("ALTER TABLE conversations ADD COLUMN last_compaction_at TEXT")

        msg_cols = {row[1] for row in await (await db.execute("PRAGMA table_info(chat_messages)")).fetchall()}
        if "archived" not in msg_cols:
            await db.execute("ALTER TABLE chat_messages ADD COLUMN archived INTEGER DEFAULT 0")

    async def create_conversation(self, title: str | None = None) -> Conversation:
        now = _utc_now()
        context_limit = await resolve_context_limit()
        conv = Conversation(
            id=str(uuid.uuid4()),
            title=(title or "新对话").strip() or "新对话",
            created_at=now,
            updated_at=now,
            token_count=0,
            context_limit=context_limit,
            summary=None,
            status="active",
        )
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO conversations (
                    id, title, created_at, updated_at, token_count, context_limit, summary, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conv.id,
                    conv.title,
                    conv.created_at,
                    conv.updated_at,
                    conv.token_count,
                    conv.context_limit,
                    conv.summary,
                    conv.status,
                ),
            )
            await db.commit()
        return conv

    async def ensure_conversation(self, conversation_id: str, title: str | None = None) -> str:
        try:
            await self.get_conversation(conversation_id)
            return conversation_id
        except KeyError:
            pass

        now = _utc_now()
        context_limit = await resolve_context_limit()
        conv_title = (title or "新对话").strip() or "新对话"
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO conversations (
                    id, title, created_at, updated_at, token_count, context_limit, summary, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    conv_title,
                    now,
                    now,
                    0,
                    context_limit,
                    None,
                    "active",
                ),
            )
            await db.commit()
        return conversation_id

    async def list_conversations(self, limit: int = 100) -> list[Conversation]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM conversations
                WHERE status = 'active'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_conversation(row) for row in rows]

    async def get_conversation(self, conversation_id: str) -> Conversation:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM conversations WHERE id = ?",
                (conversation_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise KeyError(f"对话不存在: {conversation_id}")
        return self._row_to_conversation(row)

    async def ensure_default_conversation(self) -> Conversation:
        conversations = await self.list_conversations(limit=1)
        if conversations:
            return conversations[0]
        return await self.create_conversation("默认对话")

    async def delete_conversation(self, conversation_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM chat_messages WHERE conversation_id = ?", (conversation_id,))
            await db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            await db.commit()

    async def update_title(self, conversation_id: str, title: str) -> Conversation:
        title = title.strip() or "新对话"
        now = _utc_now()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, now, conversation_id),
            )
            await db.commit()
        return await self.get_conversation(conversation_id)

    async def append_message(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        raw_content: str | None = None,
        tool_name: str | None = None,
    ) -> ChatMessage:
        await self.get_conversation(conversation_id)
        now = _utc_now()
        token_estimate = estimate_tokens(content)
        message = ChatMessage(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            role=role,
            content=content,
            raw_content=raw_content,
            tool_name=tool_name,
            token_estimate=token_estimate,
            created_at=now,
        )
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO chat_messages (
                    id, conversation_id, role, content, raw_content, tool_name,
                    token_estimate, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.conversation_id,
                    message.role,
                    message.content,
                    message.raw_content,
                    message.tool_name,
                    message.token_estimate,
                    message.created_at,
                ),
            )
            await db.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            await db.commit()
        await self.recalculate_token_count(conversation_id)
        conv = await self.get_conversation(conversation_id)
        if conv.title in {"新对话", "默认对话"} and role == "user":
            await self.update_title(conversation_id, content[:30])
        return message

    async def list_messages(self, conversation_id: str, *, include_archived: bool = False) -> list[ChatMessage]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if include_archived:
                cursor = await db.execute(
                    """
                    SELECT * FROM chat_messages
                    WHERE conversation_id = ?
                    ORDER BY created_at ASC
                    """,
                    (conversation_id,),
                )
            else:
                cursor = await db.execute(
                    """
                    SELECT * FROM chat_messages
                    WHERE conversation_id = ? AND COALESCE(archived, 0) = 0
                    ORDER BY created_at ASC
                    """,
                    (conversation_id,),
                )
            rows = await cursor.fetchall()
        return [self._row_to_message(row) for row in rows]

    async def list_active_messages(self, conversation_id: str) -> list[ChatMessage]:
        return await self.list_messages(conversation_id, include_archived=False)

    async def count_user_turns(self, conversation_id: str) -> int:
        messages = await self.list_active_messages(conversation_id)
        return sum(1 for m in messages if m.role == "user")

    async def update_summary(self, conversation_id: str, summary: str) -> Conversation:
        now = _utc_now()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE conversations SET summary = ?, updated_at = ? WHERE id = ?",
                (summary, now, conversation_id),
            )
            await db.commit()
        await self.recalculate_token_count(conversation_id)
        return await self.get_conversation(conversation_id)

    async def update_last_compaction(self, conversation_id: str) -> None:
        now = _utc_now()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE conversations SET last_compaction_at = ?, updated_at = ? WHERE id = ?",
                (now, now, conversation_id),
            )
            await db.commit()

    async def archive_messages_before_turn(self, conversation_id: str, keep_recent: int) -> int:
        messages = await self.list_active_messages(conversation_id)
        user_indices = [i for i, m in enumerate(messages) if m.role == "user"]
        if len(user_indices) <= keep_recent:
            return 0
        cutoff = user_indices[-keep_recent]
        to_archive = [m.id for m in messages[:cutoff]]
        if not to_archive:
            return 0
        placeholders = ",".join("?" for _ in to_archive)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"UPDATE chat_messages SET archived = 1 WHERE id IN ({placeholders})",
                to_archive,
            )
            await db.commit()
        await self.recalculate_token_count(conversation_id)
        return len(to_archive)

    async def update_message_content(
        self,
        message_id: str,
        content: str,
        *,
        raw_content: str | None = None,
    ) -> None:
        token_estimate = estimate_tokens(content)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE chat_messages
                SET content = ?, raw_content = COALESCE(raw_content, ?), token_estimate = ?
                WHERE id = ?
                """,
                (content, raw_content, token_estimate, message_id),
            )
            await db.commit()

    async def clear_messages(self, conversation_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM chat_messages WHERE conversation_id = ?", (conversation_id,))
            await db.execute(
                """
                UPDATE conversations
                SET token_count = 0, summary = NULL, last_compaction_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (_utc_now(), conversation_id),
            )
            await db.commit()

    async def recalculate_token_count(self, conversation_id: str) -> int:
        messages = await self.list_active_messages(conversation_id)
        settings = get_settings()
        knowledge_store = get_knowledge_store()
        await knowledge_store.init()
        knowledge = await knowledge_store.get_entries_for_prompt(settings)
        conv = await self.get_conversation(conversation_id)
        system_tokens = estimate_tokens(build_chat_system_prompt(settings, knowledge=knowledge))
        summary_tokens = estimate_tokens(conv.summary or "")
        message_tokens = estimate_messages_tokens(
            [{"content": m.content, "tool_name": m.tool_name or ""} for m in messages]
        )
        total = system_tokens + summary_tokens + message_tokens
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE conversations SET token_count = ? WHERE id = ?",
                (total, conversation_id),
            )
            await db.commit()
        return total

    async def get_usage(
        self,
        conversation_id: str,
        *,
        new_input: str = "",
        actions_applied: list[str] | None = None,
    ) -> dict:
        from agent.langchain.context_budget import evaluate_budget

        conv = await self.get_conversation(conversation_id)
        budget = await evaluate_budget(conversation_id, new_input, store=self)
        usage = budget.to_usage_dict(
            actions_applied=actions_applied,
            last_compaction=conv.last_compaction_at,
        )
        usage["level_icon"] = _level_icon(budget.level)
        await self._sync_token_count(conversation_id, budget.used)
        return usage

    async def _sync_token_count(self, conversation_id: str, token_count: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE conversations SET token_count = ? WHERE id = ?",
                (token_count, conversation_id),
            )
            await db.commit()

    def _row_to_conversation(self, row: aiosqlite.Row) -> Conversation:
        keys = row.keys()
        return Conversation(
            id=row["id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            token_count=int(row["token_count"] or 0),
            context_limit=int(row["context_limit"] or 524288),
            summary=row["summary"],
            status=row["status"] or "active",
            last_compaction_at=row["last_compaction_at"] if "last_compaction_at" in keys else None,
        )

    def _row_to_message(self, row: aiosqlite.Row) -> ChatMessage:
        keys = row.keys()
        return ChatMessage(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            content=row["content"],
            raw_content=row["raw_content"],
            tool_name=row["tool_name"],
            token_estimate=int(row["token_estimate"] or 0),
            created_at=row["created_at"],
            archived=bool(row["archived"]) if "archived" in keys else False,
        )


def _level_icon(level: str) -> str:
    return {
        "green": "🟢",
        "yellow": "🟡",
        "orange": "🟠",
        "red": "🔴",
        "blocked": "⛔",
    }.get(level, "")


_chat_store: ChatStore | None = None


def get_chat_store() -> ChatStore:
    global _chat_store
    if _chat_store is None:
        _chat_store = ChatStore(get_settings().data_dir / "chat.db")
    return _chat_store
