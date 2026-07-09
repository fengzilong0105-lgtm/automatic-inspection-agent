from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from agent.ops.case_store import CaseStore, get_case_store
from agent.ops.evidence_collector import collect_from_chat, collect_from_incident, collect_from_service
from agent.models import IncidentStatus
from agent.ops.models import ProblemCase, ProblemCaseSource, ProblemCaseStatus
from agent.ops.report_composer import apply_case_edits, compose_problem_case
from agent.ops.ticket_sync import (
    TICKET_STATUS_CLOSED,
    TICKET_STATUS_IN_PROGRESS,
    pull_ticket_from_bitable,
    push_ticket_to_bitable,
)
from agent.feishu.publisher import (
    CasePublishError,
    _create_bitable_ticket,
    _ensure_feishu_document,
    _send_publish_notification,
)
from agent.store.incidents import IncidentStore

logger = logging.getLogger(__name__)


class CaseOrchestrator:
    def __init__(
        self,
        case_store: CaseStore | None = None,
        incident_store: IncidentStore | None = None,
    ) -> None:
        from agent.settings import get_settings

        settings = get_settings()
        self.case_store = case_store or get_case_store()
        self.incident_store = incident_store or IncidentStore(settings.data_dir / "agent.db")

    async def init(self) -> None:
        await self.case_store.init()
        await self.incident_store.init()

    async def list_cases(self, limit: int = 100) -> list[ProblemCase]:
        await self.init()
        return await self.case_store.list_cases(limit=limit)

    async def get_case(self, case_id: str) -> ProblemCase:
        await self.init()
        case = await self.case_store.get(case_id)
        if not case:
            raise KeyError(f"问题报告不存在: {case_id}")
        return case

    async def create_from_incident(
        self,
        incident_id: str,
        *,
        initiator: str | None = None,
    ) -> ProblemCase:
        await self.init()
        incident = await self.incident_store.get_incident(incident_id)
        if not incident:
            raise KeyError(f"告警不存在: {incident_id}")

        existing = await self.case_store.find_open_by_incident(incident_id)
        if existing:
            return existing

        evidence = await collect_from_incident(incident)
        case = await compose_problem_case(
            evidence,
            source=ProblemCaseSource.INCIDENT,
            source_ref=incident_id,
            initiator=initiator,
            incident_id=incident_id,
            severity=incident.severity.value,
        )
        await self.case_store.create(case)
        return case

    async def create_from_chat(
        self,
        conversation_id: str,
        service_id: str,
        *,
        hint: str | None = None,
        initiator: str | None = None,
    ) -> ProblemCase:
        await self.init()
        existing = await self.case_store.find_open_by_source(
            ProblemCaseSource.CHAT.value, conversation_id
        )
        if existing:
            return existing

        evidence = await collect_from_chat(conversation_id, service_id, hint=hint)
        case = await compose_problem_case(
            evidence,
            source=ProblemCaseSource.CHAT,
            source_ref=conversation_id,
            initiator=initiator,
        )
        await self.case_store.create(case)
        return case

    async def create_from_service(
        self,
        service_id: str,
        *,
        hint: str | None = None,
        initiator: str | None = None,
    ) -> ProblemCase:
        await self.init()
        evidence = await collect_from_service(service_id, hint=hint)
        case = await compose_problem_case(
            evidence,
            source=ProblemCaseSource.MANUAL,
            source_ref=service_id,
            initiator=initiator,
        )
        await self.case_store.create(case)
        return case

    async def update_case(self, case_id: str, payload: dict[str, Any]) -> ProblemCase:
        await self.init()
        case = await self.get_case(case_id)
        if case.status == ProblemCaseStatus.DRAFT and payload.get("status") is None:
            payload = {**payload, "status": ProblemCaseStatus.REVIEWING.value}
        updated = apply_case_edits(case, payload)
        await self.case_store.update(updated)

        if updated.feishu_bitable_record_id and (
            "assignee" in payload or "ticket_status" in payload
        ):
            status = updated.ticket_status or (
                TICKET_STATUS_IN_PROGRESS if updated.assignee else TICKET_STATUS_PENDING
            )
            try:
                await push_ticket_to_bitable(
                    updated,
                    status=status,
                    assignee=updated.assignee or None,
                )
                if status != updated.ticket_status:
                    updated = updated.model_copy(update={"ticket_status": status})
                    await self.case_store.update(updated)
            except ValueError as exc:
                logger.warning("工单字段回写 Bitable 失败 case=%s: %s", case_id, exc)

        return updated

    async def publish_case(self, case_id: str) -> ProblemCase:
        await self.init()
        case = await self.get_case(case_id)
        updated = case
        try:
            updated = await _ensure_feishu_document(updated)
            if updated.feishu_doc_token != case.feishu_doc_token:
                await self.case_store.update(updated)

            updated = await _create_bitable_ticket(updated)
            await self.case_store.update(updated)

            try:
                await _send_publish_notification(updated)
            except Exception as exc:
                logger.warning("发布通知发送失败（工单已创建）: %s", exc)
        except CasePublishError as exc:
            if updated.feishu_doc_token != case.feishu_doc_token:
                await self.case_store.update(updated)
            if updated.feishu_doc_token and not updated.feishu_bitable_record_id:
                raise ValueError(
                    f"{exc}（飞书文档已创建并保存，修复 Bitable 权限后可再次点击「发布到飞书」补建工单。）"
                ) from exc
            raise ValueError(str(exc)) from exc
        return updated

    async def sync_ticket(self, case_id: str) -> ProblemCase:
        await self.init()
        case = await self.get_case(case_id)
        try:
            synced = await pull_ticket_from_bitable(case)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

        synced = synced.model_copy(update={"updated_at": datetime.utcnow()})
        if (
            synced.ticket_status == TICKET_STATUS_CLOSED
            and case.status != ProblemCaseStatus.CLOSED
        ):
            return await self._finalize_close(
                synced,
                note="从 Bitable 同步：工单已关闭",
                push_bitable=False,
            )

        await self.case_store.update(synced)
        return synced

    async def close_case(
        self,
        case_id: str,
        *,
        assignee: str | None = None,
        note: str | None = None,
    ) -> ProblemCase:
        await self.init()
        case = await self.get_case(case_id)
        if case.status == ProblemCaseStatus.CLOSED:
            return case

        if assignee:
            case = apply_case_edits(case, {"assignee": assignee})

        try:
            if case.feishu_bitable_record_id:
                await push_ticket_to_bitable(
                    case,
                    status=TICKET_STATUS_CLOSED,
                    assignee=case.assignee or None,
                )
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

        return await self._finalize_close(case, note=note, push_bitable=False)

    async def _finalize_close(
        self,
        case: ProblemCase,
        *,
        note: str | None,
        push_bitable: bool,
    ) -> ProblemCase:
        if push_bitable and case.feishu_bitable_record_id:
            await push_ticket_to_bitable(
                case,
                status=TICKET_STATUS_CLOSED,
                assignee=case.assignee or None,
            )

        now = datetime.utcnow()
        closed = case.model_copy(
            update={
                "status": ProblemCaseStatus.CLOSED,
                "ticket_status": TICKET_STATUS_CLOSED,
                "close_note": (note or case.close_note or "").strip(),
                "closed_at": now,
                "updated_at": now,
            }
        )
        await self.case_store.update(closed)

        if closed.incident_id:
            await self.incident_store.update_status(
                closed.incident_id, IncidentStatus.RESOLVED
            )
        return closed


_orchestrator: CaseOrchestrator | None = None


def get_case_orchestrator() -> CaseOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = CaseOrchestrator()
    return _orchestrator
