from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ProblemCaseSource(str, Enum):
    INSPECTION = "inspection"
    INCIDENT = "incident"
    CHAT = "chat"
    MANUAL = "manual"


class ProblemCaseStatus(str, Enum):
    DRAFT = "draft"
    REVIEWING = "reviewing"
    PUBLISHED = "published"
    TICKET_CREATED = "ticket_created"
    CLOSED = "closed"


class EvidenceBundle(BaseModel):
    service_id: str
    host_id: str
    collected_at: datetime = Field(default_factory=datetime.utcnow)
    incident: dict[str, Any] | None = None
    service_status: dict[str, Any] | None = None
    deployment_info: dict[str, Any] | None = None
    log_tail: str | None = None
    playbook_reports: list[dict[str, Any]] = Field(default_factory=list)
    chat_excerpt: str | None = None
    tool_outputs: list[dict[str, Any]] = Field(default_factory=list)


class ProblemCaseDraft(BaseModel):
    """LLM structured output for report composition."""

    title: str
    description: str
    impact: str
    analysis: str
    recommendations: list[str] = Field(default_factory=list)


class ProblemCase(BaseModel):
    id: str
    title: str
    description: str
    severity: str
    service_id: str
    host_id: str
    initiator: str
    source: ProblemCaseSource
    source_ref: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    analysis: str = ""
    impact: str = ""
    recommendations: list[str] = Field(default_factory=list)
    report_markdown: str = ""
    status: ProblemCaseStatus = ProblemCaseStatus.DRAFT
    incident_id: str | None = None
    feishu_doc_token: str | None = None
    feishu_doc_url: str | None = None
    feishu_bitable_record_id: str | None = None
    assignee: str = ""
    ticket_status: str = ""
    close_note: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    published_at: datetime | None = None
    closed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["source"] = self.source.value
        data["status"] = self.status.value
        return data
