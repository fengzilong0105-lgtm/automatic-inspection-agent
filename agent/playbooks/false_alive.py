from __future__ import annotations

import json

from agent.executor.ssh import get_executor_registry
from agent.playbooks.collectors.false_alive import collect_false_alive
from agent.playbooks.config import DEFAULT_FALSE_ALIVE_THRESHOLDS, FalseAliveThresholds
from agent.playbooks.deployment_context import resolve_cpu_deployment
from agent.playbooks.models import FalseAliveCollectorState, FalseAliveReport
from agent.playbooks.scoring_false_alive import build_false_alive_report
from agent.settings import get_settings


async def assess_false_alive(
    service_id: str,
    thresholds: FalseAliveThresholds | None = None,
) -> FalseAliveReport:
    """Assess false-alive risk: process running but service unreachable."""
    thresholds = thresholds or DEFAULT_FALSE_ALIVE_THRESHOLDS
    settings = get_settings()
    service = settings.get_service(service_id)
    host = settings.get_host(service.host_id)
    executor = get_executor_registry().get(service.host_id, host)

    deployment = await resolve_cpu_deployment(executor, service)
    status = await executor.service_status(service)

    state = FalseAliveCollectorState(
        pid=deployment.pid,
        cmdline=deployment.cmdline,
        container_name=deployment.container_name,
        systemd_unit=service.systemd_unit,
    )

    await collect_false_alive(executor, service, state, status, thresholds)
    return build_false_alive_report(service, state)


async def assess_false_alive_to_json(service_id: str) -> str:
    report = await assess_false_alive(service_id)
    return json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)
