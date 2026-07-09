from __future__ import annotations

import re
from typing import Any

from agent.feishu.client import FeishuAPIError, feishu_api_request

_BLOCK_BATCH_SIZE = 40
_MAX_TEXT_CHARS = 4000

_HEADING_KEYS = {
    1: "heading1",
    2: "heading2",
    3: "heading3",
    4: "heading4",
    5: "heading5",
    6: "heading6",
    7: "heading7",
    8: "heading8",
    9: "heading9",
}


def build_doc_url(document_id: str, *, tenant_subdomain: str = "") -> str:
    subdomain = tenant_subdomain.strip()
    if subdomain:
        return f"https://{subdomain}.feishu.cn/docx/{document_id}"
    return f"https://feishu.cn/docx/{document_id}"


def _text_elements(content: str) -> list[dict[str, Any]]:
    return [{"text_run": {"content": content}}]


def _text_block(content: str) -> dict[str, Any]:
    return {
        "block_type": 2,
        "text": {"elements": _text_elements(content), "style": {}},
    }


def _heading_block(level: int, content: str) -> dict[str, Any]:
    level = max(1, min(level, 9))
    key = _HEADING_KEYS[level]
    return {
        "block_type": level + 2,
        key: {"elements": _text_elements(content), "style": {}},
    }


def _bullet_block(content: str) -> dict[str, Any]:
    return {
        "block_type": 12,
        "bullet": {"elements": _text_elements(content), "style": {}},
    }


def _code_block(content: str) -> dict[str, Any]:
    return {
        "block_type": 14,
        "code": {"elements": _text_elements(content), "style": {}},
    }


def _split_long_text(content: str) -> list[str]:
    if len(content) <= _MAX_TEXT_CHARS:
        return [content]
    chunks: list[str] = []
    start = 0
    while start < len(content):
        chunks.append(content[start : start + _MAX_TEXT_CHARS])
        start += _MAX_TEXT_CHARS
    return chunks


def markdown_to_blocks(markdown: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    lines = markdown.splitlines()
    in_code = False
    code_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                blocks.append(_code_block("\n".join(code_lines)))
                code_lines = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not stripped:
            continue

        heading_match = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading_match:
            level = len(heading_match.group(1))
            blocks.append(_heading_block(level, heading_match.group(2).strip()))
            continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            blocks.append(_bullet_block(stripped[2:].strip()))
            continue
        if re.match(r"^- \[[ xX]\]\s+", stripped):
            blocks.append(_bullet_block(stripped))
            continue

        for chunk in _split_long_text(line):
            blocks.append(_text_block(chunk))

    if in_code and code_lines:
        blocks.append(_code_block("\n".join(code_lines)))

    if not blocks:
        blocks.append(_text_block("（空报告）"))
    return blocks


class FeishuDocClient:
    def __init__(self, *, app_id: str, app_secret: str, tenant_subdomain: str = "") -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.tenant_subdomain = tenant_subdomain

    async def create_document(
        self,
        title: str,
        *,
        folder_token: str = "",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"title": title[:800]}
        if folder_token:
            body["folder_token"] = folder_token
        data = await feishu_api_request(
            "POST",
            "/docx/v1/documents",
            app_id=self.app_id,
            app_secret=self.app_secret,
            json_body=body,
        )
        document = data.get("document") or {}
        document_id = document.get("document_id")
        if not document_id:
            raise FeishuAPIError("飞书创建文档成功但未返回 document_id")
        return {
            "document_id": str(document_id),
            "revision_id": document.get("revision_id"),
            "title": document.get("title") or title,
            "url": build_doc_url(str(document_id), tenant_subdomain=self.tenant_subdomain),
        }

    async def append_blocks(
        self,
        document_id: str,
        blocks: list[dict[str, Any]],
        *,
        index: int = -1,
    ) -> None:
        if not blocks:
            return
        for offset in range(0, len(blocks), _BLOCK_BATCH_SIZE):
            batch = blocks[offset : offset + _BLOCK_BATCH_SIZE]
            await feishu_api_request(
                "POST",
                f"/docx/v1/documents/{document_id}/blocks/{document_id}/children",
                app_id=self.app_id,
                app_secret=self.app_secret,
                params={"document_revision_id": -1},
                json_body={"children": batch, "index": index},
            )

    async def write_markdown(self, document_id: str, markdown: str) -> None:
        blocks = markdown_to_blocks(markdown)
        await self.append_blocks(document_id, blocks)

    async def create_document_with_markdown(
        self,
        title: str,
        markdown: str,
        *,
        folder_token: str = "",
    ) -> dict[str, Any]:
        created = await self.create_document(title, folder_token=folder_token)
        await self.write_markdown(created["document_id"], markdown)
        return created
