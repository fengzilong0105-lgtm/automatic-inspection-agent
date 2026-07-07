from __future__ import annotations

import json

from agent.executor.ssh import get_executor_registry
from agent.models import ServiceType
from agent.playbooks.collectors.cpu_common import collect_cpu_common
from agent.playbooks.collectors.cpu_docker import collect_cpu_docker
from agent.playbooks.collectors.cpu_java import collect_cpu_java
from agent.playbooks.collectors.cpu_jstack import collect_cpu_jstack
from agent.playbooks.config import DEFAULT_CPU_THRESHOLDS, CpuRiskThresholds
from agent.playbooks.deployment_context import resolve_cpu_deployment
from agent.playbooks.models import CpuRiskReport
from agent.playbooks.scoring_cpu import build_cpu_report
from agent.settings import get_settings


async def assess_cpu_risk(
    service_id: str,
    thresholds: CpuRiskThresholds | None = None,
) -> CpuRiskReport:
    """Run the CPU risk playbook for a registered service."""
    thresholds = thresholds or DEFAULT_CPU_THRESHOLDS
    settings = get_settings()
    service = settings.get_service(service_id)
    host = settings.get_host(service.host_id)
    executor = get_executor_registry().get(service.host_id, host)

    state = await resolve_cpu_deployment(executor, service)

    if state.container_name or service.container_name:
        await collect_cpu_docker(executor, state, thresholds)

    await collect_cpu_common(executor, service, state, thresholds)

    if service.type == ServiceType.JAVA:
        await collect_cpu_java(executor, state, thresholds)

    await collect_cpu_jstack(executor, state, thresholds)

    return build_cpu_report(service, state)


async def assess_cpu_risk_to_json(service_id: str) -> str:
    report = await assess_cpu_risk(service_id)
    return json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)
