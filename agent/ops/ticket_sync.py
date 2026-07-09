from __future__ import annotations

from typing import Any

from agent.feishu.bitable_client import FeishuBitableClient
from agent.feishu.client import FeishuAPIError
from agent.ops.models import ProblemCase
from agent.settings import get_settings

TICKET_STATUS_PENDING = "待处理"
TICKET_STATUS_IN_PROGRESS = "处理中"
TICKET_STATUS_CLOSED = "已关闭"

VALID_TICKET_STATUSES = {
    TICKET_STATUS_PENDING,
    TICKET_STATUS_IN_PROGRESS,
    TICKET_STATUS_CLOSED,
}


def _bitable_client() -> tuple[FeishuBitableClient, Any]:
    settings = get_settings()
    feishu_cfg = settings.config.feishu
    ops_feishu = settings.config.ops_report.feishu
    client = FeishuBitableClient(
        app_id=feishu_cfg.app_id,
        app_secret=feishu_cfg.app_secret,
    )
    return client, ops_feishu


def parse_bitable_record_fields(
    fields: dict[str, Any],
    *,
    field_names: dict[str, str] | None = None,
) -> tuple[str, str]:
    names = field_names or {}
    status_key = names.get("status", "状态")
    assignee_key = names.get("assignee", "负责人")

    status = _normalize_text(fields.get(status_key))
    assignee = _normalize_text(fields.get(assignee_key))

    if status not in VALID_TICKET_STATUSES:
        status = status or ""
    return assignee, status


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text") or item.get("name")
                if text:
                    parts.append(str(text).strip())
            elif item:
                parts.append(str(item).strip())
        return " ".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        for key in ("text", "name", "value"):
            if value.get(key):
                return str(value[key]).strip()
    return str(value).strip()


def apply_ticket_fields_to_case(
    case: ProblemCase,
    *,
    assignee: str,
    ticket_status: str,
) -> ProblemCase:
    updates: dict[str, Any] = {}
    if assignee:
        updates["assignee"] = assignee
    if ticket_status:
        updates["ticket_status"] = ticket_status
    if not updates:
        return case
    return case.model_copy(update=updates)


async def pull_ticket_from_bitable(case: ProblemCase) -> ProblemCase:
    if not case.feishu_bitable_record_id:
        raise ValueError("未关联 Bitable 工单记录")

    client, ops_feishu = _bitable_client()
    if not ops_feishu.bitable_app_token or not ops_feishu.bitable_table_id:
        raise ValueError("Bitable app_token / table_id 未配置")

    try:
        fields = await client.get_record(
            ops_feishu.bitable_app_token,
            ops_feishu.bitable_table_id,
            case.feishu_bitable_record_id,
        )
    except FeishuAPIError as exc:
        raise ValueError(str(exc)) from exc

    assignee, ticket_status = parse_bitable_record_fields(fields)
    return apply_ticket_fields_to_case(case, assignee=assignee, ticket_status=ticket_status)


async def push_ticket_to_bitable(
    case: ProblemCase,
    *,
    status: str,
    assignee: str | None = None,
) -> None:
    if not case.feishu_bitable_record_id:
        return

    client, ops_feishu = _bitable_client()
    if not ops_feishu.bitable_app_token or not ops_feishu.bitable_table_id:
        raise ValueError("Bitable app_token / table_id 未配置")

    try:
        await client.update_ticket_status(
            app_token=ops_feishu.bitable_app_token,
            table_id=ops_feishu.bitable_table_id,
            record_id=case.feishu_bitable_record_id,
            status=status,
            assignee=assignee or case.assignee or None,
        )
    except FeishuAPIError as exc:
        raise ValueError(str(exc)) from exc
