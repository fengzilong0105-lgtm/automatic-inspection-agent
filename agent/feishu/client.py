from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

FEISHU_API_BASE = "https://open.feishu.cn/open-apis"


class FeishuAPIError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, log_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.log_id = log_id


async def get_tenant_access_token(app_id: str, app_secret: str) -> str:
    url = f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(url, json={"app_id": app_id, "app_secret": app_secret})
        response.raise_for_status()
        data = response.json()

    if data.get("code") != 0:
        raise FeishuAPIError(
            f"获取 tenant_access_token 失败: {data.get('msg', data)}",
            code=data.get("code"),
        )
    token = data.get("tenant_access_token")
    if not token:
        raise FeishuAPIError("飞书返回为空 tenant_access_token")
    return str(token)


async def feishu_api_request(
    method: str,
    path: str,
    *,
    app_id: str,
    app_secret: str,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 30,
) -> dict[str, Any]:
    if not app_id or not app_secret:
        raise FeishuAPIError("App ID 或 App Secret 未配置")

    token = await get_tenant_access_token(app_id, app_secret)
    url = f"{FEISHU_API_BASE}{path}"
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(
            method,
            url,
            headers=headers,
            json=json_body,
            params=params,
        )
        if response.status_code >= 400:
            hint = _http_error_hint(path, response.status_code, response.text[:500])
            raise FeishuAPIError(f"HTTP {response.status_code}: {response.text[:500]}. {hint}".strip())
        data = response.json()

    if data.get("code") != 0:
        msg = str(data.get("msg") or data)
        hint = _doc_error_hint(data.get("code"), msg)
        raise FeishuAPIError(f"飞书 API 失败: {msg}. {hint}".strip(), code=data.get("code"))
    return data.get("data") or {}


async def send_feishu_text(
    *,
    app_id: str,
    app_secret: str,
    chat_id: str,
    text: str,
) -> dict[str, Any]:
    if not app_id or not app_secret:
        raise FeishuAPIError("App ID 或 App Secret 未配置")
    if not chat_id:
        raise FeishuAPIError("告警 Chat ID 未配置")

    token = await get_tenant_access_token(app_id, app_secret)
    url = f"{FEISHU_API_BASE}/im/v1/messages"
    params = {"receive_id_type": "chat_id"}
    payload = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(url, params=params, json=payload, headers=headers)
        if response.status_code >= 400:
            raise FeishuAPIError(f"HTTP {response.status_code}: {response.text[:300]}")
        data = response.json()

    if data.get("code") != 0:
        msg = str(data.get("msg") or data)
        hint = _error_hint(data.get("code"), msg)
        raise FeishuAPIError(f"发送消息失败: {msg}. {hint}".strip(), code=data.get("code"))

    return data.get("data") or {}


def _http_error_hint(path: str, status: int, body: str) -> str:
    text = body.lower()
    if status == 403 and "/bitable/" in path:
        return (
            "多维表格写入被拒绝：除开放平台开通 bitable:app 权限外，"
            "还需在该多维表格内「… → 添加文档应用 → 选择本应用 → 可编辑」。"
            "若开启高级权限，请给应用管理权限。"
        )
    if status == 403 and ("docx" in path or "/drive/" in path):
        return "云文档权限不足：请为应用开通 docx:document，并将应用加入目标文件夹（可编辑）。"
    return _doc_error_hint(None, text)


def _error_hint(code: Any, msg: str) -> str:
    text = msg.lower()
    if code in (99991663, 99991664) or "permission" in text or "scope" in text:
        return "请确认应用已开通 im:message 权限并已发布。"
    if "chat_id" in text or "receive_id" in text:
        return "请确认 Chat ID 正确，且机器人已被拉入该群。"
    if "app" in text and ("secret" in text or "invalid" in text):
        return "请检查 App ID / App Secret 是否正确。"
    return "请确认机器人已加入目标群，且应用具备发消息权限。"


def _doc_error_hint(code: Any, msg: str) -> str:
    text = msg.lower()
    if code in (99991663, 99991664) or "permission" in text or "scope" in text:
        return "请确认应用已开通 docx:document 权限并已发布版本。"
    if code in (1770039, 1770040) or "folder" in text:
        return "请检查 archive_folder_token 是否正确，并为应用开通文件夹权限。"
    if "bitable" in text or "base:" in text or "table" in text:
        return (
            "请确认应用已开通 bitable:app 权限并已发布；"
            "并在目标多维表格内「添加文档应用」授予本应用可编辑权限。"
        )
    if code == 99991400:
        return "飞书接口限频，请稍后重试。"
    return _error_hint(code, msg)
