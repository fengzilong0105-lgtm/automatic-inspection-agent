from __future__ import annotations

import json
import re
from dataclasses import dataclass

_AT_TAG_RE = re.compile(r"@_user_\d+\s*")
_AT_ALL_RE = re.compile(r"@_all\b\s*", re.I)


@dataclass
class ParsedFeishuMessage:
    chat_id: str
    message_id: str
    user_id: str
    text: str
    message_type: str
    has_bot_mention: bool
    is_from_app: bool


def parse_p2_message(event: object) -> ParsedFeishuMessage | None:
    """Extract fields from lark P2ImMessageReceiveV1 event."""
    try:
        root = event.event if hasattr(event, "event") else None
        if root is None:
            return None
        message = root.message
        sender = root.sender
    except AttributeError:
        return None

    if message is None:
        return None

    chat_id = getattr(message, "chat_id", "") or ""
    message_id = getattr(message, "message_id", "") or ""
    message_type = getattr(message, "message_type", "") or ""
    raw_content = getattr(message, "content", "") or ""

    sender_type = getattr(sender, "sender_type", "") or ""
    is_from_app = sender_type == "app"

    user_id = ""
    sender_id = getattr(sender, "sender_id", None)
    if sender_id is not None:
        user_id = (
            getattr(sender_id, "open_id", "")
            or getattr(sender_id, "user_id", "")
            or getattr(sender_id, "union_id", "")
            or ""
        )

    text = _extract_text(raw_content, message_type)
    mentions = getattr(message, "mentions", None) or []
    has_bot_mention = bool(mentions)

    return ParsedFeishuMessage(
        chat_id=chat_id,
        message_id=message_id,
        user_id=user_id,
        text=text,
        message_type=message_type,
        has_bot_mention=has_bot_mention,
        is_from_app=is_from_app,
    )


def _extract_text(raw_content: str, message_type: str) -> str:
    if message_type != "text":
        return ""
    try:
        payload = json.loads(raw_content)
        text = str(payload.get("text", ""))
    except json.JSONDecodeError:
        text = raw_content
    text = _AT_TAG_RE.sub("", text)
    text = _AT_ALL_RE.sub("", text)
    return text.strip()


def strip_command_prefix(text: str) -> str:
    text = text.strip()
    if text.startswith("/ops"):
        text = text[4:].strip()
    elif text.startswith("/"):
        text = text[1:].strip()
    return text
