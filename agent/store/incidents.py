from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

import aiosqlite

from agent.models import Incident, IncidentSeverity, IncidentStatus


class IncidentStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    service_id TEXT NOT NULL,
                    host_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT,
                    log_snippet TEXT,
                    diagnosis TEXT,
                    suggestions TEXT,
                    metadata TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS restart_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    service_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.commit()

    async def create_incident(
        self,
        *,
        service_id: str,
        host_id: str,
        title: str,
        severity: IncidentSeverity,
        summary: str = "",
        log_snippet: str = "",
        metadata: dict | None = None,
    ) -> Incident:
        now = datetime.utcnow()
        incident = Incident(
            id=str(uuid.uuid4()),
            service_id=service_id,
            host_id=host_id,
            title=title,
            severity=severity,
            status=IncidentStatus.OPEN,
            summary=summary,
            log_snippet=log_snippet,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
        await self._upsert(incident)
        return incident

    async def _upsert(self, incident: Incident) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO incidents (
                    id, service_id, host_id, title, severity, status, summary,
                    log_snippet, diagnosis, suggestions, metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    summary=excluded.summary,
                    log_snippet=excluded.log_snippet,
                    diagnosis=excluded.diagnosis,
                    suggestions=excluded.suggestions,
                    metadata=excluded.metadata,
                    updated_at=excluded.updated_at
                """,
                (
                    incident.id,
                    incident.service_id,
                    incident.host_id,
                    incident.title,
                    incident.severity.value,
                    incident.status.value,
                    incident.summary,
                    incident.log_snippet,
                    incident.diagnosis,
                    json.dumps(incident.suggestions, ensure_ascii=False),
                    json.dumps(incident.metadata, ensure_ascii=False),
                    incident.created_at.isoformat(),
                    incident.updated_at.isoformat(),
                ),
            )
            await db.commit()

    async def list_incidents(self, limit: int = 50) -> list[Incident]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM incidents ORDER BY created_at DESC LIMIT ?", (limit,)
            )
            rows = await cursor.fetchall()
        return [self._row_to_incident(row) for row in rows]

    async def get_incident(self, incident_id: str) -> Incident | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,))
            row = await cursor.fetchone()
        return self._row_to_incident(row) if row else None

    async def update_diagnosis(
        self, incident_id: str, diagnosis: str, suggestions: list[str]
    ) -> None:
        incident = await self.get_incident(incident_id)
        if not incident:
            return
        incident.diagnosis = diagnosis
        incident.suggestions = suggestions
        incident.status = IncidentStatus.DIAGNOSING
        incident.updated_at = datetime.utcnow()
        await self._upsert(incident)

    async def record_restart(self, service_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO restart_history (service_id, created_at) VALUES (?, ?)",
                (service_id, datetime.utcnow().isoformat()),
            )
            await db.commit()

    async def count_recent_restarts(self, service_id: str, minutes: int = 15) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT COUNT(*) FROM restart_history
                WHERE service_id = ? AND datetime(created_at) >= datetime('now', ?)
                """,
                (service_id, f"-{minutes} minutes"),
            )
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    def _row_to_incident(self, row: aiosqlite.Row) -> Incident:
        return Incident(
            id=row["id"],
            service_id=row["service_id"],
            host_id=row["host_id"],
            title=row["title"],
            severity=IncidentSeverity(row["severity"]),
            status=IncidentStatus(row["status"]),
            summary=row["summary"] or "",
            log_snippet=row["log_snippet"] or "",
            diagnosis=row["diagnosis"],
            suggestions=json.loads(row["suggestions"] or "[]"),
            metadata=json.loads(row["metadata"] or "{}"),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
