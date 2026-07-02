from __future__ import annotations

import pytest

from agent.incident.rules import RuleEngine
from agent.models import IncidentSeverity, ServiceConfig, ServiceStatus, ServiceType


@pytest.mark.asyncio
async def test_rule_engine_detects_down_service():
    engine = RuleEngine()
    service = ServiceConfig(id="api", host_id="h1", type=ServiceType.JAVA, systemd_unit="api.service")
    status = ServiceStatus(service_id="api", running=False, detail="inactive")
    alerts = engine.evaluate(service, status, "", 0)
    assert alerts
    assert alerts[0]["severity"] == IncidentSeverity.P0


def test_slug_from_java_path():
    from agent.discovery.java import _slug_from_path

    assert _slug_from_path("/opt/order-api/app.jar") == "app"
