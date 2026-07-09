from __future__ import annotations

from typing import Any

from agent.ops.orchestrator import CaseOrchestrator, get_case_orchestrator


async def list_problem_cases(limit: int = 100) -> list[dict[str, Any]]:
    orchestrator = get_case_orchestrator()
    cases = await orchestrator.list_cases(limit=limit)
    return [case.to_dict() for case in cases]


async def get_problem_case(case_id: str) -> dict[str, Any]:
    orchestrator = get_case_orchestrator()
    return (await orchestrator.get_case(case_id)).to_dict()


async def create_problem_case_from_incident(
    incident_id: str,
    *,
    initiator: str | None = None,
) -> dict[str, Any]:
    orchestrator = get_case_orchestrator()
    case = await orchestrator.create_from_incident(incident_id, initiator=initiator)
    return case.to_dict()


async def update_problem_case(case_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    orchestrator = get_case_orchestrator()
    case = await orchestrator.update_case(case_id, payload)
    return case.to_dict()


async def publish_problem_case(case_id: str) -> dict[str, Any]:
    orchestrator = get_case_orchestrator()
    case = await orchestrator.publish_case(case_id)
    return case.to_dict()


async def close_problem_case(
    case_id: str,
    *,
    assignee: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    orchestrator = get_case_orchestrator()
    case = await orchestrator.close_case(case_id, assignee=assignee, note=note)
    return case.to_dict()


async def sync_problem_case_ticket(case_id: str) -> dict[str, Any]:
    orchestrator = get_case_orchestrator()
    case = await orchestrator.sync_ticket(case_id)
    return case.to_dict()


async def init_problem_cases() -> None:
    await get_case_orchestrator().init()
