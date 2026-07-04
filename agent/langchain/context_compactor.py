from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from langchain_core.messages import ToolMessage

from agent.langchain.context_budget import BudgetReport, evaluate_budget
from agent.langchain.context_policy import LEVEL_BLOCKED, escalation_actions_for_blocked
from agent.langchain.conversation_summarizer import summarize_db_messages, summarize_turns
from agent.langchain.thread_messages import (
    flatten_turns,
    get_checkpoint_messages,
    message_content_text,
    replace_tool_message,
    set_checkpoint_messages,
    split_into_turns,
)
from agent.langchain.tool_compress import aggressive_compress_tool_output, compress_tool_output
from agent.settings import get_settings
from agent.store.chat import ChatStore, get_chat_store

if TYPE_CHECKING:
    from agent.langchain.chat_graph import ChatAgent

logger = logging.getLogger(__name__)

_COMPACTION_NOTICES = {
    "compress_old_tools": "上下文 Usage {percent}%，已自动压缩较早的工具返回。",
    "rolling_summary": "上下文 Usage {percent}%，已对早期对话生成滚动摘要。",
    "shrink_window": "上下文 Usage {percent}%，已缩窗仅保留最近 {keep} 轮；建议新建对话。",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ContextCompactor:
    def __init__(self, store: ChatStore | None = None) -> None:
        self.store = store or get_chat_store()

    async def execute(
        self,
        actions: list[str],
        conversation_id: str,
        chat_agent: ChatAgent,
    ) -> list[str]:
        applied: list[str] = []
        for action in actions:
            try:
                if action == "compress_old_tools":
                    await self.compress_old_tool_messages(conversation_id, chat_agent)
                    applied.append(action)
                elif action == "rolling_summary":
                    await self.apply_rolling_summary(conversation_id, chat_agent)
                    applied.append(action)
                elif action == "shrink_window":
                    await self.shrink_recent_window(conversation_id, chat_agent)
                    applied.append(action)
                elif action == "aggressive_compress_current":
                    changed = await self.aggressive_compress_checkpoint_tools(conversation_id, chat_agent)
                    if changed:
                        applied.append(action)
            except Exception as exc:
                logger.warning("compaction action %s failed: %s", action, exc)
        if applied:
            await self.store.update_last_compaction(conversation_id)
            await self.store.recalculate_token_count(conversation_id)
        return applied

    async def compress_old_tool_messages(
        self,
        conversation_id: str,
        chat_agent: ChatAgent,
        *,
        keep_recent: int | None = None,
    ) -> int:
        policy = get_settings().config.chat.policy
        keep_recent = keep_recent if keep_recent is not None else policy.shrink_keep_turns
        messages = await get_checkpoint_messages(chat_agent, conversation_id)
        if not messages:
            return await self._compress_db_tool_messages(conversation_id, keep_recent)

        turns = split_into_turns(messages)
        if len(turns) <= keep_recent:
            return 0

        updated: list = []
        changed = 0
        for turn_idx, turn in enumerate(turns):
            is_old = turn_idx < len(turns) - keep_recent
            for msg in turn:
                if is_old and isinstance(msg, ToolMessage):
                    tool_name = getattr(msg, "name", None) or "tool"
                    raw = message_content_text(msg)
                    compressed = compress_tool_output(tool_name, raw)
                    if compressed != raw:
                        msg = replace_tool_message(msg, compressed)
                        changed += 1
                updated.append(msg)

        if changed:
            await set_checkpoint_messages(chat_agent, conversation_id, updated)
        await self._compress_db_tool_messages(conversation_id, keep_recent)
        return changed

    async def _compress_db_tool_messages(self, conversation_id: str, keep_recent: int) -> int:
        messages = await self.store.list_active_messages(conversation_id)
        user_indices = [i for i, m in enumerate(messages) if m.role == "user"]
        if len(user_indices) <= keep_recent:
            return 0
        cutoff = user_indices[-keep_recent]
        changed = 0
        for msg in messages[:cutoff]:
            if msg.role != "tool" or not msg.tool_name:
                continue
            compressed = compress_tool_output(msg.tool_name, msg.content)
            if compressed != msg.content:
                await self.store.update_message_content(msg.id, compressed, raw_content=msg.raw_content or msg.content)
                changed += 1
        return changed

    async def apply_rolling_summary(
        self,
        conversation_id: str,
        chat_agent: ChatAgent,
    ) -> str:
        policy = get_settings().config.chat.policy
        keep_recent = policy.keep_recent_turns
        conv = await self.store.get_conversation(conversation_id)

        checkpoint_messages = await get_checkpoint_messages(chat_agent, conversation_id)
        if checkpoint_messages:
            turns = split_into_turns(checkpoint_messages)
            if len(turns) > keep_recent:
                early_turns = turns[:-keep_recent]
                summary = await summarize_turns(conv.summary, early_turns)
                remaining = flatten_turns(turns[-keep_recent:])
                await set_checkpoint_messages(chat_agent, conversation_id, remaining)
            else:
                summary = conv.summary or ""
        else:
            db_messages = await self.store.list_active_messages(conversation_id)
            user_indices = [i for i, m in enumerate(db_messages) if m.role == "user"]
            if len(user_indices) <= keep_recent:
                return conv.summary or ""
            cutoff = user_indices[-keep_recent]
            early = db_messages[:cutoff]
            summary = await summarize_db_messages(conv.summary, early)

        if summary and summary != (conv.summary or ""):
            await self.store.update_summary(conversation_id, summary)
        await self.store.archive_messages_before_turn(conversation_id, keep_recent)
        return summary or conv.summary or ""

    async def shrink_recent_window(
        self,
        conversation_id: str,
        chat_agent: ChatAgent,
        *,
        keep: int | None = None,
    ) -> int:
        policy = get_settings().config.chat.policy
        keep = keep if keep is not None else policy.shrink_keep_turns
        messages = await get_checkpoint_messages(chat_agent, conversation_id)
        if messages:
            turns = split_into_turns(messages)
            if len(turns) > keep:
                remaining = flatten_turns(turns[-keep:])
                await set_checkpoint_messages(chat_agent, conversation_id, remaining)
        await self.store.archive_messages_before_turn(conversation_id, keep)
        return keep

    async def aggressive_compress_checkpoint_tools(
        self,
        conversation_id: str,
        chat_agent: ChatAgent,
    ) -> bool:
        messages = await get_checkpoint_messages(chat_agent, conversation_id)
        if not messages:
            return False
        turns = split_into_turns(messages)
        if not turns:
            return False
        last_turn = turns[-1]
        updated_turn: list = []
        changed = False
        for msg in last_turn:
            if isinstance(msg, ToolMessage):
                tool_name = getattr(msg, "name", None) or "tool"
                raw = message_content_text(msg)
                compressed = aggressive_compress_tool_output(tool_name, raw)
                if compressed != raw:
                    msg = replace_tool_message(msg, compressed)
                    changed = True
            updated_turn.append(msg)
        if not changed:
            return False
        turns[-1] = updated_turn
        await set_checkpoint_messages(chat_agent, conversation_id, flatten_turns(turns))
        return True

    def aggressive_compress_current_tool(self, tool_name: str, output: str) -> str:
        return aggressive_compress_tool_output(tool_name, output)


async def prepare_conversation_context(
    chat_agent: ChatAgent,
    conversation_id: str,
    user_text: str,
    *,
    store: ChatStore | None = None,
) -> tuple[BudgetReport, list[str], list[str]]:
    """Evaluate budget, run compaction if needed. Returns (budget, applied_actions, system_notices)."""
    store = store or get_chat_store()
    compactor = ContextCompactor(store)
    notices: list[str] = []

    budget = await evaluate_budget(conversation_id, user_text, store=store)
    applied = await compactor.execute(budget.actions, conversation_id, chat_agent)
    notices.extend(_notices_for_actions(applied, budget))

    if budget.level == LEVEL_BLOCKED and budget.overflow_reason == "accumulated":
        for action in escalation_actions_for_blocked():
            if action in applied:
                continue
            extra = await compactor.execute([action], conversation_id, chat_agent)
            applied.extend(extra)
            budget = await evaluate_budget(conversation_id, user_text, store=store)
            notices.extend(_notices_for_actions(extra, budget))
            if budget.level != LEVEL_BLOCKED:
                break

    budget = await evaluate_budget(conversation_id, user_text, store=store)

    if budget.level == LEVEL_BLOCKED and budget.overflow_reason == "current_turn":
        if "aggressive_compress_current" not in applied:
            extra = await compactor.execute(["aggressive_compress_current"], conversation_id, chat_agent)
            applied.extend(extra)
            budget = await evaluate_budget(conversation_id, user_text, store=store)

    return budget, applied, notices


def _notices_for_actions(actions: list[str], budget: BudgetReport) -> list[str]:
    policy = get_settings().config.chat.policy
    notices: list[str] = []
    for action in actions:
        template = _COMPACTION_NOTICES.get(action)
        if template:
            notices.append(
                template.format(percent=budget.percent, keep=policy.shrink_keep_turns)
            )
    return notices
