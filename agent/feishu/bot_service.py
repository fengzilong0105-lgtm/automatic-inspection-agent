from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from agent.feishu.client import FeishuAPIError, send_feishu_text
from agent.feishu.message_parser import ParsedFeishuMessage, parse_p2_message, strip_command_prefix
from agent.langchain.chat_graph import detect_restart_intent
from agent.runtime.background import get_runtime
from agent.services.chat_ops import clear_conversation, handle_chat_message
from agent.settings import get_settings
from agent.store.chat import get_chat_store

if TYPE_CHECKING:
    from agent.feishu.runner import FeishuBotRunner

logger = logging.getLogger(__name__)

_WRITE_INTENT = re.compile(
    r"(写文件|删文件|删除文件|覆盖|写入|write_remote|delete_remote|帮我改|帮我写|帮我删)",
    re.I,
)

_HELP_TEXT = (
    "【SteadyOps 飞书机器人 · 只读模式】\n"
    "请 @机器人 后发送指令，例如：\n"
    "· road_control 状态怎么样？\n"
    "· 查一下 prod-01 最近 ERROR 日志\n"
    "· 立即巡检\n"
    "· 列出所有服务\n\n"
    "发送「重置」可清空飞书对话上下文。\n"
    "暂不支持：重启、写文件、删文件（请使用桌面端）。"
)


class FeishuBotService:
    def __init__(self, runner: FeishuBotRunner) -> None:
        self.runner = runner

    async def handle_event(self, event: object) -> None:
        parsed = parse_p2_message(event)
        if parsed is None:
            return
        if parsed.is_from_app:
            return
        if parsed.message_type != "text":
            await self._reply(parsed.chat_id, "暂仅支持文本消息。")
            return

        settings = get_settings()
        feishu = settings.config.feishu
        bot_cfg = feishu.bot

        command_chat_id = (bot_cfg.command_chat_id or feishu.alert_chat_id or "").strip()
        if command_chat_id and parsed.chat_id != command_chat_id:
            logger.debug("Ignore message from chat %s (expected %s)", parsed.chat_id, command_chat_id)
            return

        text = strip_command_prefix(parsed.text)
        if not text:
            return

        if bot_cfg.require_at_mention and not parsed.has_bot_mention:
            return

        if text.lower() in {"help", "帮助", "?"}:
            await self._reply(parsed.chat_id, _HELP_TEXT)
            return

        conv_id = await self._ensure_conversation(parsed)
        if text.lower() in {"reset", "重置", "清空对话"}:
            await clear_conversation(conv_id)
            await self._reply(parsed.chat_id, "飞书对话上下文已清空，请重新提问。")
            return

        blocked = self._readonly_block_message(text)
        if blocked:
            await self._reply(parsed.chat_id, blocked)
            return

        await self._reply(parsed.chat_id, "收到，正在处理…")

        try:
            result = await handle_chat_message(
                get_runtime().chat_agent,
                conversation_id=conv_id,
                message=text,
                confirmed=False,
            )
            reply = self._format_result(result)
            if result.get("type") == "error":
                logger.warning("Feishu command error: %s", result.get("message"))
        except Exception as exc:
            logger.exception("Feishu command failed")
            reply = f"处理失败: {exc}"

        await self._reply(parsed.chat_id, reply)

    def _readonly_block_message(self, text: str) -> str | None:
        settings = get_settings()
        if detect_restart_intent(settings, text):
            return "飞书机器人处于只读模式，暂不支持重启操作，请使用桌面端确认执行。"
        if _WRITE_INTENT.search(text):
            return "飞书机器人处于只读模式，暂不支持写文件/删文件操作，请使用桌面端。"
        return None

    async def _ensure_conversation(self, parsed: ParsedFeishuMessage) -> str:
        store = get_chat_store()
        conv_id = f"feishu:{parsed.chat_id}:{parsed.user_id or 'unknown'}"
        title = f"飞书 {parsed.user_id[:12] or '用户'}"
        return await store.ensure_conversation(conv_id, title=title)

    def _format_result(self, result: dict[str, Any]) -> str:
        msg_type = result.get("type", "message")
        if msg_type == "confirm_restart":
            return "飞书机器人处于只读模式，暂不支持重启操作，请使用桌面端。"
        if msg_type == "error":
            return str(result.get("message", "对话失败"))
        body = str(result.get("message") or result.get("reply") or "已完成。")
        parts: list[str] = []
        for notice in result.get("compaction_notices") or []:
            parts.append(notice)
        parts.append(body)
        return self._truncate("\n\n".join(parts))

    async def _reply(self, chat_id: str, text: str) -> None:
        settings = get_settings()
        feishu = settings.config.feishu
        chunks = self._split_message(text)
        for chunk in chunks:
            try:
                await send_feishu_text(
                    app_id=feishu.app_id,
                    app_secret=feishu.app_secret,
                    chat_id=chat_id,
                    text=chunk,
                )
            except FeishuAPIError as exc:
                logger.error("Feishu reply failed: %s", exc)
                break

    @staticmethod
    def _truncate(text: str, limit: int = 12000) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 20] + "\n…(内容过长已截断)"

    @staticmethod
    def _split_message(text: str, limit: int = 3800) -> list[str]:
        if len(text) <= limit:
            return [text]
        chunks: list[str] = []
        start = 0
        while start < len(text):
            chunks.append(text[start : start + limit])
            start += limit
        return chunks
