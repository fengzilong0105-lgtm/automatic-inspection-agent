from __future__ import annotations

import json

from agent.executor.ssh import get_executor_registry
from agent.playbooks.collectors.false_healthy import collect_false_healthy
from agent.playbooks.config import DEFAULT_FALSE_HEALTHY_THRESHOLDS, FalseHealthyThresholds
from agent.playbooks.models import FalseHealthyCollectorState, FalseHealthyReport
from agent.playbooks.scoring_false_healthy import build_false_healthy_report
from agent.settings import get_settings


async def assess_false_healthy(
    service_id: str,
    thresholds: FalseHealthyThresholds | None = None,
) -> FalseHealthyReport:
    """Assess false-healthy risk: health check passes but business/deps are broken."""
    thresholds = thresholds or DEFAULT_FALSE_HEALTHY_THRESHOLDS
    settings = get_settings()
    service = settings.get_service(service_id)
    host = settings.get_host(service.host_id)
    executor = get_executor_registry().get(service.host_id, host)

    status = await executor.service_status(service)
    state = FalseHealthyCollectorState()

    await collect_false_healthy(executor, service, state, status, thresholds)
    return build_false_healthy_report(service, state)


async def assess_false_healthy_to_json(service_id: str) -> str:
    report = await assess_false_healthy(service_id)
    return json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)
