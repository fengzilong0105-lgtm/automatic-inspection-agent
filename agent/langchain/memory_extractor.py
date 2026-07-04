from __future__ import annotations

import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from agent.langchain.llm_factory import get_llm
from agent.settings import get_settings
from agent.store.knowledge import KnowledgeEntry, get_knowledge_store

logger = logging.getLogger(__name__)

_REMEMBER_MARKER = re.compile(
    r"【可记住】\s*(?P<category>preference|service_fact|ops_note)\s*[/:：]\s*"
    r"(?P<key>[^\n:=：]+?)\s*[:：=]\s*(?P<value>.+?)(?=\n【可记住】|\Z)",
    re.DOTALL,
)


class ExtractedMemoryItem(BaseModel):
    category: Literal["preference", "service_fact", "ops_note"]
    key: str
    value: str
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class ExtractedMemories(BaseModel):
    items: list[ExtractedMemoryItem] = Field(default_factory=list)


def parse_rememberable_markers(text: str) -> list[dict]:
    """Parse assistant-marked memories like 【可记住】service_fact/road_control.log_path: /path."""
    suggestions: list[dict] = []
    for match in _REMEMBER_MARKER.finditer(text or ""):
        suggestions.append(
            {
                "category": match.group("category").strip(),
                "key": match.group("key").strip(),
                "value": match.group("value").strip(),
                "confidence": 1.0,
                "source": "assistant_marker",
            }
        )
    return suggestions


async def auto_extract_memories(
    *,
    user_text: str,
    assistant_text: str,
    conversation_id: str,
) -> list[KnowledgeEntry]:
    """Extract stable facts/preferences from a completed turn using LLM."""
    settings = get_settings()
    if not settings.config.chat.memory.auto_extract:
        return []

    if not user_text.strip() or not assistant_text.strip():
        return []

    prompt = (
        "从以下对话轮次中提取可长期记住的稳定事实，仅在有明确依据时输出。\n"
        "分类说明：\n"
        "- preference: 用户明确表达的回答偏好\n"
        "- service_fact: 已确认的服务路径、启动方式、配置位置\n"
        "- ops_note: 运维注意事项（如需 sudo、特殊路径权限等）\n"
        "不要编造；没有可记住内容则返回空列表。\n\n"
        f"用户: {user_text.strip()}\n\n"
        f"助手: {assistant_text.strip()}"
    )
    try:
        llm = get_llm("chat_qa")
        structured = llm.with_structured_output(ExtractedMemories)
        result: ExtractedMemories = await structured.ainvoke(
            [
                {
                    "role": "system",
                    "content": "你是运维知识提取器，只输出对话中明确出现且可跨会话复用的事实。",
                },
                {"role": "user", "content": prompt},
            ]
        )
    except Exception as exc:
        logger.warning("auto_extract_memories failed: %s", exc)
        return []

    store = get_knowledge_store()
    saved: list[KnowledgeEntry] = []
    for item in result.items:
        key = item.key.strip()
        value = item.value.strip()
        if not key or not value:
            continue
        try:
            entry = await store.upsert_entry(
                category=item.category,
                key=key,
                value=value,
                source_conv_id=conversation_id,
                confidence=item.confidence,
            )
            saved.append(entry)
        except ValueError:
            continue
    return saved


async def save_memory_suggestion(
    *,
    category: str,
    key: str,
    value: str,
    conversation_id: str | None = None,
    confidence: float = 1.0,
) -> KnowledgeEntry:
    store = get_knowledge_store()
    return await store.upsert_entry(
        category=category,
        key=key,
        value=value,
        source_conv_id=conversation_id,
        confidence=confidence,
    )


async def process_turn_memories(
    *,
    user_text: str,
    assistant_text: str,
    conversation_id: str,
) -> dict:
    """Run auto-extract and parse assistant markers after a chat turn."""
    suggestions = parse_rememberable_markers(assistant_text)
    auto_saved = await auto_extract_memories(
        user_text=user_text,
        assistant_text=assistant_text,
        conversation_id=conversation_id,
    )
    return {
        "memory_suggestions": suggestions,
        "auto_saved": [item.to_dict() for item in auto_saved],
    }
