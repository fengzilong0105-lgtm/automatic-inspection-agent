from __future__ import annotations

from datetime import datetime

from agent.brand import PRODUCT_NAME
from agent.feishu.bitable_client import FeishuBitableClient
from agent.feishu.client import FeishuAPIError, send_feishu_text
from agent.feishu.doc_client import FeishuDocClient
from agent.ops.models import ProblemCase, ProblemCaseStatus
from agent.ops.ticket_sync import TICKET_STATUS_PENDING
from agent.settings import get_settings


class CasePublishError(RuntimeError):
    pass


def _validate_publish_ready(case: ProblemCase) -> None:
    if not (case.title or "").strip():
        raise CasePublishError("报告标题不能为空")
    if not (case.report_markdown or "").strip():
        raise CasePublishError("报告正文不能为空")

    settings = get_settings()
    feishu = settings.config.feishu
    if not feishu.enabled:
        raise CasePublishError("飞书未启用，请在设置中开启飞书告警")
    if not feishu.app_id or not feishu.app_secret:
        raise CasePublishError("飞书 App ID / App Secret 未配置")


def _resolve_notify_chat_id() -> str:
    settings = get_settings()
    ops_feishu = settings.config.ops_report.feishu
    return (ops_feishu.notify_chat_id or settings.config.feishu.alert_chat_id or "").strip()


async def _ensure_feishu_document(case: ProblemCase) -> ProblemCase:
    if case.feishu_doc_token and case.feishu_doc_url:
        return case

    settings = get_settings()
    feishu_cfg = settings.config.feishu
    ops_feishu = settings.config.ops_report.feishu

    client = FeishuDocClient(
        app_id=feishu_cfg.app_id,
        app_secret=feishu_cfg.app_secret,
        tenant_subdomain=ops_feishu.tenant_subdomain,
    )
    created = await client.create_document_with_markdown(
        case.title.strip(),
        case.report_markdown,
        folder_token=ops_feishu.archive_folder_token,
    )
    now = datetime.utcnow()
    return case.model_copy(
        update={
            "feishu_doc_token": created["document_id"],
            "feishu_doc_url": created["url"],
            "status": ProblemCaseStatus.PUBLISHED,
            "published_at": case.published_at or now,
            "updated_at": now,
        }
    )


async def _create_bitable_ticket(case: ProblemCase) -> ProblemCase:
    if case.feishu_bitable_record_id:
        return case

    settings = get_settings()
    feishu_cfg = settings.config.feishu
    ops_feishu = settings.config.ops_report.feishu
    if not ops_feishu.bitable_app_token or not ops_feishu.bitable_table_id:
        raise CasePublishError(
            "Bitable 未配置，请在设置中填写 Bitable App Token 与 Table ID"
        )
    if not case.feishu_doc_url:
        raise CasePublishError("飞书文档链接缺失，无法创建工单")

    client = FeishuBitableClient(
        app_id=feishu_cfg.app_id,
        app_secret=feishu_cfg.app_secret,
    )
    record_id = await client.create_ticket_record(
        case,
        app_token=ops_feishu.bitable_app_token,
        table_id=ops_feishu.bitable_table_id,
        doc_url=case.feishu_doc_url,
    )
    now = datetime.utcnow()
    return case.model_copy(
        update={
            "feishu_bitable_record_id": record_id,
            "status": ProblemCaseStatus.TICKET_CREATED,
            "ticket_status": TICKET_STATUS_PENDING,
            "updated_at": now,
        }
    )


async def _send_publish_notification(case: ProblemCase) -> None:
    chat_id = _resolve_notify_chat_id()
    if not chat_id:
        return

    settings = get_settings()
    feishu_cfg = settings.config.feishu
    record_hint = ""
    if case.feishu_bitable_record_id:
        record_hint = f"\n工单记录 ID: {case.feishu_bitable_record_id}"

    text = (
        f"【{PRODUCT_NAME} 问题报告已发布】\n"
        f"问题: {case.title}\n"
        f"级别: {case.severity} | 服务: {case.service_id} | 主机: {case.host_id}\n"
        f"发起人: {case.initiator}\n"
        f"报告链接: {case.feishu_doc_url or '-'}\n"
        f"Case ID: {case.id}"
        f"{record_hint}"
    )
    await send_feishu_text(
        app_id=feishu_cfg.app_id,
        app_secret=feishu_cfg.app_secret,
        chat_id=chat_id,
        text=text,
    )


async def publish_case(case: ProblemCase) -> ProblemCase:
    """M3: Feishu doc + Bitable ticket row + group notification."""
    _validate_publish_ready(case)

    if (
        case.status == ProblemCaseStatus.TICKET_CREATED
        and case.feishu_bitable_record_id
        and case.feishu_doc_url
    ):
        return case

    try:
        updated = case
        updated = await _ensure_feishu_document(updated)
        updated = await _create_bitable_ticket(updated)
        await _send_publish_notification(updated)
    except FeishuAPIError as exc:
        raise CasePublishError(str(exc)) from exc

    return updated


async def publish_case_document(case: ProblemCase) -> ProblemCase:
    """M2 compatibility: doc only."""
    _validate_publish_ready(case)

    if case.feishu_doc_token and case.feishu_doc_url:
        return case

    try:
        return await _ensure_feishu_document(case)
    except FeishuAPIError as exc:
        raise CasePublishError(str(exc)) from exc
