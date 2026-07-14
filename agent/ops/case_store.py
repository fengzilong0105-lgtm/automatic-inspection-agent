from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

import aiosqlite

from agent.ops.models import ProblemCase, ProblemCaseSource, ProblemCaseStatus


def _utc_now() -> datetime:
    return datetime.utcnow()


class CaseStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS problem_cases (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    service_id TEXT NOT NULL,
                    host_id TEXT NOT NULL,
                    initiator TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_ref TEXT,
                    evidence TEXT,
                    analysis TEXT,
                    impact TEXT,
                    recommendations TEXT,
                    report_markdown TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    incident_id TEXT,
                    feishu_doc_token TEXT,
                    feishu_doc_url TEXT,
                    feishu_bitable_record_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    published_at TEXT
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_problem_cases_incident
                ON problem_cases(incident_id)
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_problem_cases_status
                ON problem_cases(status, updated_at)
                """
            )
            await self._ensure_columns(db)
            await db.commit()

    async def _ensure_columns(self, db: aiosqlite.Connection) -> None:
        cursor = await db.execute("PRAGMA table_info(problem_cases)")
        rows = await cursor.fetchall()
        existing = {row[1] for row in rows}
        migrations = {
            "assignee": "TEXT DEFAULT ''",
            "ticket_status": "TEXT DEFAULT ''",
            "close_note": "TEXT DEFAULT ''",
            "closed_at": "TEXT",
        }
        for column, ddl in migrations.items():
            if column not in existing:
                await db.execute(f"ALTER TABLE problem_cases ADD COLUMN {column} {ddl}")

    async def create(self, case: ProblemCase) -> ProblemCase:
        await self._upsert(case)
        return case

    async def update(self, case: ProblemCase) -> ProblemCase:
        case.updated_at = _utc_now()
        await self._upsert(case)
        return case

    async def get(self, case_id: str) -> ProblemCase | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM problem_cases WHERE id = ?", (case_id,))
            row = await cursor.fetchone()
        return self._row_to_case(row) if row else None

    async def list_cases(self, limit: int = 100) -> list[ProblemCase]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM problem_cases
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_case(row) for row in rows]

    async def delete_by_host(self, host_id: str) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM problem_cases WHERE host_id = ?", (host_id,))
            await db.commit()
            return int(cursor.rowcount or 0)

    async def find_open_by_incident(self, incident_id: str) -> ProblemCase | None:
        open_statuses = (
            ProblemCaseStatus.DRAFT.value,
            ProblemCaseStatus.REVIEWING.value,
            ProblemCaseStatus.PUBLISHED.value,
            ProblemCaseStatus.TICKET_CREATED.value,
        )
        placeholders = ",".join("?" for _ in open_statuses)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"""
                SELECT * FROM problem_cases
                WHERE incident_id = ? AND status IN ({placeholders})
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (incident_id, *open_statuses),
            )
            row = await cursor.fetchone()
        return self._row_to_case(row) if row else None

    async def find_open_by_source(
        self, source: str, source_ref: str
    ) -> ProblemCase | None:
        open_statuses = (
            ProblemCaseStatus.DRAFT.value,
            ProblemCaseStatus.REVIEWING.value,
            ProblemCaseStatus.PUBLISHED.value,
            ProblemCaseStatus.TICKET_CREATED.value,
        )
        placeholders = ",".join("?" for _ in open_statuses)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"""
                SELECT * FROM problem_cases
                WHERE source = ? AND source_ref = ? AND status IN ({placeholders})
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (source, source_ref, *open_statuses),
            )
            row = await cursor.fetchone()
        return self._row_to_case(row) if row else None

    async def _upsert(self, case: ProblemCase) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO problem_cases (
                    id, title, description, severity, service_id, host_id, initiator,
                    source, source_ref, evidence, analysis, impact, recommendations,
                    report_markdown, status, incident_id, feishu_doc_token, feishu_doc_url,
                    feishu_bitable_record_id, assignee, ticket_status, close_note,
                    created_at, updated_at, published_at, closed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    description=excluded.description,
                    severity=excluded.severity,
                    service_id=excluded.service_id,
                    host_id=excluded.host_id,
                    initiator=excluded.initiator,
                    source=excluded.source,
                    source_ref=excluded.source_ref,
                    evidence=excluded.evidence,
                    analysis=excluded.analysis,
                    impact=excluded.impact,
                    recommendations=excluded.recommendations,
                    report_markdown=excluded.report_markdown,
                    status=excluded.status,
                    incident_id=excluded.incident_id,
                    feishu_doc_token=excluded.feishu_doc_token,
                    feishu_doc_url=excluded.feishu_doc_url,
                    feishu_bitable_record_id=excluded.feishu_bitable_record_id,
                    assignee=excluded.assignee,
                    ticket_status=excluded.ticket_status,
                    close_note=excluded.close_note,
                    updated_at=excluded.updated_at,
                    published_at=excluded.published_at,
                    closed_at=excluded.closed_at
                """,
                (
                    case.id,
                    case.title,
                    case.description,
                    case.severity,
                    case.service_id,
                    case.host_id,
                    case.initiator,
                    case.source.value,
                    case.source_ref,
                    json.dumps(case.evidence, ensure_ascii=False),
                    case.analysis,
                    case.impact,
                    json.dumps(case.recommendations, ensure_ascii=False),
                    case.report_markdown,
                    case.status.value,
                    case.incident_id,
                    case.feishu_doc_token,
                    case.feishu_doc_url,
                    case.feishu_bitable_record_id,
                    case.assignee,
                    case.ticket_status,
                    case.close_note,
                    case.created_at.isoformat(),
                    case.updated_at.isoformat(),
                    case.published_at.isoformat() if case.published_at else None,
                    case.closed_at.isoformat() if case.closed_at else None,
                ),
            )
            await db.commit()

    def _row_to_case(self, row: aiosqlite.Row) -> ProblemCase:
        published_at = row["published_at"]
        closed_at = row["closed_at"] if "closed_at" in row.keys() else None
        return ProblemCase(
            id=row["id"],
            title=row["title"],
            description=row["description"] or "",
            severity=row["severity"],
            service_id=row["service_id"],
            host_id=row["host_id"],
            initiator=row["initiator"],
            source=ProblemCaseSource(row["source"]),
            source_ref=row["source_ref"] or "",
            evidence=json.loads(row["evidence"] or "{}"),
            analysis=row["analysis"] or "",
            impact=row["impact"] or "",
            recommendations=json.loads(row["recommendations"] or "[]"),
            report_markdown=row["report_markdown"] or "",
            status=ProblemCaseStatus(row["status"]),
            incident_id=row["incident_id"],
            feishu_doc_token=row["feishu_doc_token"],
            feishu_doc_url=row["feishu_doc_url"],
            feishu_bitable_record_id=row["feishu_bitable_record_id"],
            assignee=row["assignee"] if "assignee" in row.keys() else "",
            ticket_status=row["ticket_status"] if "ticket_status" in row.keys() else "",
            close_note=row["close_note"] if "close_note" in row.keys() else "",
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            published_at=datetime.fromisoformat(published_at) if published_at else None,
            closed_at=datetime.fromisoformat(closed_at) if closed_at else None,
        )


_case_store: CaseStore | None = None


def get_case_store() -> CaseStore:
    global _case_store
    if _case_store is None:
        from agent.settings import get_settings

        _case_store = CaseStore(get_settings().data_dir / "agent.db")
    return _case_store


def new_case_id() -> str:
    return str(uuid.uuid4())
