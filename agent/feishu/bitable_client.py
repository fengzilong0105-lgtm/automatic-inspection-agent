from __future__ import annotations

from datetime import datetime
from typing import Any

from agent.feishu.client import FeishuAPIError, feishu_api_request
from agent.ops.models import ProblemCase, ProblemCaseSource

# 默认列名需与飞书多维表格「SteadyOps 运维工单」表结构一致（见 docs/ops-report-workflow.md §5.2）
DEFAULT_BITABLE_FIELDS = {
    "title": "问题名称",
    "description": "问题描述",
    "severity": "严重级别",
    "service_id": "服务",
    "host_id": "主机",
    "initiator": "发起人",
    "source": "来源",
    "status": "状态",
    "report_url": "报告链接",
    "case_id": "SteadyOps Case ID",
    "incident_id": "Incident ID",
    "assignee": "负责人",
    "created_at": "创建时间",
    "published_at": "发布时间",
}

_SOURCE_LABELS = {
    ProblemCaseSource.INSPECTION: "巡检",
    ProblemCaseSource.INCIDENT: "告警",
    ProblemCaseSource.CHAT: "AI对话",
    ProblemCaseSource.MANUAL: "手动",
}


def _to_epoch_ms(value: datetime | None) -> int | None:
    if not value:
        return None
    return int(value.timestamp() * 1000)


def build_ticket_fields(case: ProblemCase, *, doc_url: str) -> dict[str, Any]:
    """Map ProblemCase to Bitable fields dict (column name → value)."""
    names = DEFAULT_BITABLE_FIELDS
    fields: dict[str, Any] = {
        names["title"]: case.title,
        names["description"]: case.description,
        names["severity"]: case.severity,
        names["service_id"]: case.service_id,
        names["host_id"]: case.host_id,
        names["initiator"]: case.initiator,
        names["source"]: _SOURCE_LABELS.get(case.source, case.source.value),
        names["status"]: "待处理",
        names["report_url"]: {"link": doc_url, "text": case.title or "查看报告"},
        names["case_id"]: case.id,
    }
    if case.assignee:
        fields[names["assignee"]] = case.assignee
    if case.ticket_status:
        fields[names["status"]] = case.ticket_status
    if case.incident_id:
        fields[names["incident_id"]] = case.incident_id

    created_ms = _to_epoch_ms(case.created_at)
    if created_ms is not None:
        fields[names["created_at"]] = created_ms

    published_ms = _to_epoch_ms(case.published_at or datetime.utcnow())
    if published_ms is not None:
        fields[names["published_at"]] = published_ms

    return fields


class FeishuBitableClient:
    def __init__(self, *, app_id: str, app_secret: str) -> None:
        self.app_id = app_id
        self.app_secret = app_secret

    async def create_record(
        self,
        app_token: str,
        table_id: str,
        fields: dict[str, Any],
    ) -> str:
        if not app_token or not table_id:
            raise FeishuAPIError("Bitable app_token 或 table_id 未配置")

        data = await feishu_api_request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
            app_id=self.app_id,
            app_secret=self.app_secret,
            json_body={"fields": fields},
        )
        record = data.get("record") or {}
        record_id = record.get("record_id")
        if not record_id:
            raise FeishuAPIError("Bitable 创建记录成功但未返回 record_id")
        return str(record_id)

    async def create_ticket_record(
        self,
        case: ProblemCase,
        *,
        app_token: str,
        table_id: str,
        doc_url: str,
    ) -> str:
        fields = build_ticket_fields(case, doc_url=doc_url)
        return await self.create_record(app_token, table_id, fields)

    async def get_record(
        self,
        app_token: str,
        table_id: str,
        record_id: str,
    ) -> dict[str, Any]:
        data = await feishu_api_request(
            "GET",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            app_id=self.app_id,
            app_secret=self.app_secret,
        )
        record = data.get("record") or {}
        fields = record.get("fields") or {}
        if not isinstance(fields, dict):
            return {}
        return fields

    async def update_record(
        self,
        app_token: str,
        table_id: str,
        record_id: str,
        fields: dict[str, Any],
    ) -> None:
        await feishu_api_request(
            "PUT",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            app_id=self.app_id,
            app_secret=self.app_secret,
            json_body={"fields": fields},
        )

    async def update_ticket_status(
        self,
        *,
        app_token: str,
        table_id: str,
        record_id: str,
        status: str,
        assignee: str | None = None,
    ) -> None:
        names = DEFAULT_BITABLE_FIELDS
        fields: dict[str, Any] = {names["status"]: status}
        if assignee:
            fields[names["assignee"]] = assignee
        await self.update_record(app_token, table_id, record_id, fields)
