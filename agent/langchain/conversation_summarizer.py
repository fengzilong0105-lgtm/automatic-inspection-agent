from __future__ import annotations

import logging

from langchain_core.messages import BaseMessage

from agent.langchain.llm_factory import get_llm
from agent.langchain.thread_messages import split_into_turns, turns_to_dialogue_text

logger = logging.getLogger(__name__)

_SUMMARY_PROMPT = """请将以下对话早期内容压缩为 500 字以内的摘要，保留：
1. 用户偏好
2. 服务路径/配置
3. 已确认故障结论
4. 未解决问题
不要编造；若信息不足则只写已知部分。

{existing}对话内容：
{dialogue}
"""


async def summarize_dialogue(existing_summary: str | None, dialogue: str) -> str:
    if not dialogue.strip():
        return existing_summary or ""

    existing_block = ""
    if existing_summary:
        existing_block = f"已有摘要：\n{existing_summary}\n\n请在已有摘要基础上合并更新。\n"

    prompt = _SUMMARY_PROMPT.format(existing=existing_block, dialogue=dialogue[:12000])
    try:
        llm = get_llm("chat_qa")
        result = await llm.ainvoke(
            [
                {"role": "system", "content": "你是运维对话摘要助手，输出简洁中文摘要。"},
                {"role": "user", "content": prompt},
            ]
        )
        content = getattr(result, "content", str(result))
        if isinstance(content, list):
            content = "".join(
                block.get("text", "") if isinstance(block, dict) else str(block) for block in content
            )
        summary = str(content).strip()
        return summary[:2000]
    except Exception as exc:
        logger.warning("summarize_dialogue failed: %s", exc)
        fallback = dialogue[:500]
        if existing_summary:
            return f"{existing_summary}\n{fallback}"
        return fallback


async def summarize_turns(
    existing_summary: str | None,
    turns: list[list[BaseMessage]],
) -> str:
    if not turns:
        return existing_summary or ""
    dialogue = turns_to_dialogue_text(turns)
    return await summarize_dialogue(existing_summary, dialogue)


async def summarize_messages(
    existing_summary: str | None,
    messages: list[BaseMessage],
    *,
    keep_recent_turns: int,
) -> str:
    turns = split_into_turns(messages)
    if len(turns) <= keep_recent_turns:
        return existing_summary or ""
    early_turns = turns[:-keep_recent_turns]
    return await summarize_turns(existing_summary, early_turns)


async def summarize_db_messages(
    existing_summary: str | None,
    messages: list,
) -> str:
    if not messages:
        return existing_summary or ""
    dialogue = "\n".join(
        f"{getattr(m, 'role', 'msg')}: {str(getattr(m, 'content', ''))[:400]}"
        for m in messages
    )
    return await summarize_dialogue(existing_summary, dialogue)
