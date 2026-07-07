from __future__ import annotations

import json

from agent.executor.ssh import get_executor_registry
from agent.models import ServiceType
from agent.playbooks.collectors import (
    collect_common,
    collect_docker,
    collect_gc_log,
    collect_java,
    collect_process,
)
from agent.playbooks.config import DEFAULT_OOM_THRESHOLDS, OomRiskThresholds
from agent.playbooks.deployment_context import resolve_deployment
from agent.playbooks.models import OomRiskReport
from agent.playbooks.scoring import build_report
from agent.settings import get_settings


async def assess_oom_risk(
    service_id: str,
    thresholds: OomRiskThresholds | None = None,
) -> OomRiskReport:
    """Run the OOM risk playbook for a registered service."""
    thresholds = thresholds or DEFAULT_OOM_THRESHOLDS
    settings = get_settings()
    service = settings.get_service(service_id)
    host = settings.get_host(service.host_id)
    executor = get_executor_registry().get(service.host_id, host)

    state = await resolve_deployment(executor, service)
    await collect_common(executor, service, state, thresholds)

    if state.container_name or service.container_name:
        await collect_docker(executor, state, thresholds)

    if service.type == ServiceType.JAVA:
        await collect_java(executor, state, thresholds)
        await collect_gc_log(executor, service, state, thresholds)

    if state.running and state.pid:
        await collect_process(executor, state, thresholds)

    return build_report(service, state)


async def assess_oom_risk_to_json(service_id: str) -> str:
    report = await assess_oom_risk(service_id)
    return json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)
