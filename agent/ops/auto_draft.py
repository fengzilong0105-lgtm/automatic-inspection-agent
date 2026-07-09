from __future__ import annotations

import logging

from agent.feishu.notifier import FeishuNotifier
from agent.models import Incident, IncidentSeverity
from agent.ops.orchestrator import get_case_orchestrator
from agent.settings import get_settings

logger = logging.getLogger(__name__)


async def maybe_auto_draft_from_incident(incident: Incident):
    settings = get_settings()
    if not settings.config.ops_report.auto_draft_on_incident:
        return None
    if incident.severity not in {IncidentSeverity.P0, IncidentSeverity.P1}:
        return None

    try:
        orchestrator = get_case_orchestrator()
        return await orchestrator.create_from_incident(incident.id)
    except Exception as exc:
        logger.exception("P0/P1 自动起草报告失败 incident=%s: %s", incident.id, exc)
        return None


async def handle_incident_alert(incident: Incident, notifier: FeishuNotifier) -> None:
    await notifier.send_incident_card(incident)

    case = await maybe_auto_draft_from_incident(incident)
    if not case:
        return

    try:
        await notifier.send_text(
            "【问题报告草稿】\n"
            f"告警 [{incident.severity.value}] 已自动生成报告草稿：{case.title}\n"
            f"Case ID: {case.id}\n"
            "请在 SteadyOps【问题报告】页确认内容后发布，不会自动发布到飞书。"
        )
    except Exception as exc:
        logger.warning("自动起草通知发送失败 case=%s: %s", case.id, exc)
