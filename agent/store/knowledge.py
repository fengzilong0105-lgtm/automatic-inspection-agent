from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

from agent.langchain.token_meter import estimate_tokens
from agent.settings import Settings, get_settings

VALID_CATEGORIES = {"preference", "service_fact", "ops_note"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class KnowledgeEntry:
    id: str
    category: str
    key: str
    value: str
    source_conv_id: str | None
    confidence: float
    created_at: str
    updated_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "category": self.category,
            "key": self.key,
            "value": self.value,
            "source_conv_id": self.source_conv_id,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class KnowledgeStore:
    def __init__(self, db_path) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_entries (
                    id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    source_conv_id TEXT,
                    confidence REAL DEFAULT 1.0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(category, key)
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_knowledge_updated
                ON knowledge_entries(updated_at DESC)
                """
            )
            await db.commit()

    async def list_entries(self, limit: int = 500) -> list[KnowledgeEntry]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM knowledge_entries
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_entry(row) for row in rows]

    async def get_entry(self, entry_id: str) -> KnowledgeEntry:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM knowledge_entries WHERE id = ?",
                (entry_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise KeyError(f"记忆条目不存在: {entry_id}")
        return self._row_to_entry(row)

    async def upsert_entry(
        self,
        *,
        category: str,
        key: str,
        value: str,
        source_conv_id: str | None = None,
        confidence: float = 1.0,
        entry_id: str | None = None,
    ) -> KnowledgeEntry:
        category = category.strip()
        key = key.strip()
        value = value.strip()
        if category not in VALID_CATEGORIES:
            raise ValueError(f"无效分类: {category}")
        if not key or not value:
            raise ValueError("key 和 value 不能为空")

        now = _utc_now()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id FROM knowledge_entries WHERE category = ? AND key = ?",
                (category, key),
            )
            existing = await cursor.fetchone()
            if existing:
                entry_id = existing["id"]
                await db.execute(
                    """
                    UPDATE knowledge_entries
                    SET value = ?, source_conv_id = ?, confidence = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (value, source_conv_id, confidence, now, entry_id),
                )
            else:
                entry_id = entry_id or str(uuid.uuid4())
                await db.execute(
                    """
                    INSERT INTO knowledge_entries (
                        id, category, key, value, source_conv_id, confidence, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (entry_id, category, key, value, source_conv_id, confidence, now, now),
                )
            await db.commit()
        return await self.get_entry(entry_id)

    async def update_entry(
        self,
        entry_id: str,
        *,
        category: str | None = None,
        key: str | None = None,
        value: str | None = None,
    ) -> KnowledgeEntry:
        current = await self.get_entry(entry_id)
        return await self.upsert_entry(
            category=category or current.category,
            key=key or current.key,
            value=value or current.value,
            source_conv_id=current.source_conv_id,
            confidence=current.confidence,
            entry_id=entry_id,
        )

    async def delete_entry(self, entry_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM knowledge_entries WHERE id = ?",
                (entry_id,),
            )
            await db.commit()
            if cursor.rowcount == 0:
                raise KeyError(f"记忆条目不存在: {entry_id}")

    async def get_fingerprint(self) -> str:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*), MAX(updated_at) FROM knowledge_entries"
            )
            row = await cursor.fetchone()
        count = int(row[0] or 0)
        updated = row[1] or ""
        return f"{count}:{updated}"

    async def get_entries_for_prompt(self, settings: Settings | None = None) -> list[KnowledgeEntry]:
        settings = settings or get_settings()
        max_tokens = settings.config.chat.memory.max_inject_tokens
        active_service_id = settings.config.active_service_id or ""
        service_prefix = f"{active_service_id}." if active_service_id else ""

        all_entries = await self.list_entries(limit=500)
        preferences = [e for e in all_entries if e.category == "preference"]
        service_facts = [e for e in all_entries if e.category == "service_fact"]
        ops_notes = [e for e in all_entries if e.category == "ops_note"]

        prioritized_facts: list[KnowledgeEntry] = []
        if service_prefix:
            prioritized_facts.extend(
                e for e in service_facts if e.key.startswith(service_prefix)
            )
        recent_facts = service_facts[:20]
        for entry in recent_facts:
            if entry not in prioritized_facts:
                prioritized_facts.append(entry)

        selected = preferences + prioritized_facts + ops_notes
        if not selected:
            return []

        # Trim by token budget, drop oldest first.
        while selected:
            text = self._format_entries(selected)
            if estimate_tokens(text) <= max_tokens:
                return selected
            selected.pop()
        return selected

    def _format_entries(self, entries: list[KnowledgeEntry]) -> str:
        lines = []
        for entry in entries:
            lines.append(f"- [{entry.category}] {entry.key}: {entry.value}")
        return "\n".join(lines)

    def _row_to_entry(self, row: aiosqlite.Row) -> KnowledgeEntry:
        return KnowledgeEntry(
            id=row["id"],
            category=row["category"],
            key=row["key"],
            value=row["value"],
            source_conv_id=row["source_conv_id"],
            confidence=float(row["confidence"] or 1.0),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


_knowledge_store: KnowledgeStore | None = None


def get_knowledge_store() -> KnowledgeStore:
    global _knowledge_store
    if _knowledge_store is None:
        _knowledge_store = KnowledgeStore(get_settings().data_dir / "chat.db")
    return _knowledge_store
