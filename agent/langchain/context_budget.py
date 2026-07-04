from __future__ import annotations

from dataclasses import dataclass, field

from agent.langchain.context_builder import build_chat_system_prompt
from agent.langchain.context_policy import decide_actions, resolve_level
from agent.langchain.token_meter import estimate_messages_tokens, estimate_tokens
from agent.settings import get_settings
from agent.store.chat import ChatStore, get_chat_store
from agent.store.knowledge import get_knowledge_store


@dataclass
class BudgetReport:
    used: int
    limit: int
    percent: float
    level: str
    overflow_reason: str | None
    actions: list[str] = field(default_factory=list)
    summary_tokens: int = 0
    recent_turns: int = 0
    system_prompt_tokens: int = 0
    knowledge_tokens: int = 0
    message_tokens: int = 0
    new_input_tokens: int = 0
    tool_reserve: int = 0

    def to_usage_dict(self, *, actions_applied: list[str] | None = None, last_compaction: str | None = None) -> dict:
        from agent.langchain.token_meter import format_token_count

        hint = _level_hint(self.level, actions_applied or [])
        return {
            "used": self.used,
            "limit": self.limit,
            "percent": self.percent,
            "level": self.level,
            "used_label": format_token_count(self.used),
            "limit_label": format_token_count(self.limit),
            "summary_tokens": self.summary_tokens,
            "recent_turns": self.recent_turns,
            "overflow_reason": self.overflow_reason,
            "actions_applied": actions_applied or [],
            "last_compaction": last_compaction,
            "hint": hint,
        }


def _level_hint(level: str, actions_applied: list[str]) -> str:
    if level == "green":
        return ""
    if "shrink_window" in actions_applied or level == "red":
        return "建议新建对话；重要信息已写入 AI 记忆"
    if "rolling_summary" in actions_applied or level == "orange":
        return "已自动摘要早期对话"
    if "compress_old_tools" in actions_applied or level == "yellow":
        return "已压缩历史工具结果"
    if level == "blocked":
        return "上下文已满，请缩小查询范围或新建对话"
    return ""


async def evaluate_budget(
    conversation_id: str,
    new_input: str = "",
    *,
    store: ChatStore | None = None,
) -> BudgetReport:
    store = store or get_chat_store()
    settings = get_settings()
    policy = settings.config.chat.policy

    conv = await store.get_conversation(conversation_id)
    limit = conv.context_limit
    messages = await store.list_active_messages(conversation_id)
    turn_count = await store.count_user_turns(conversation_id)

    knowledge_store = get_knowledge_store()
    await knowledge_store.init()
    knowledge = await knowledge_store.get_entries_for_prompt(settings)

    system_prompt = build_chat_system_prompt(settings, knowledge=knowledge)
    system_prompt_tokens = estimate_tokens(system_prompt)
    knowledge_text = "\n".join(f"- [{e.category}] {e.key}: {e.value}" for e in knowledge)
    knowledge_tokens = estimate_tokens(knowledge_text)
    summary_tokens = estimate_tokens(conv.summary or "")
    message_tokens = estimate_messages_tokens(
        [{"content": m.content, "tool_name": m.tool_name or ""} for m in messages]
    )
    new_input_tokens = estimate_tokens(new_input)
    tool_reserve = policy.tool_reserve_tokens

    base_without_history = system_prompt_tokens + summary_tokens + new_input_tokens
    total = base_without_history + message_tokens + tool_reserve
    percent = round(total / limit * 100, 1) if limit else 0.0

    if base_without_history + tool_reserve >= limit:
        overflow_reason: str | None = "current_turn"
    elif total >= limit:
        overflow_reason = "accumulated"
    else:
        overflow_reason = None

    level = resolve_level(percent / 100.0, policy, overflow_reason=overflow_reason)
    actions = decide_actions(
        level=level,
        overflow_reason=overflow_reason,
        turn_count=turn_count,
        policy=policy,
    )

    return BudgetReport(
        used=total,
        limit=limit,
        percent=percent,
        level=level,
        overflow_reason=overflow_reason,
        actions=actions,
        summary_tokens=summary_tokens,
        recent_turns=turn_count,
        system_prompt_tokens=system_prompt_tokens,
        knowledge_tokens=knowledge_tokens,
        message_tokens=message_tokens,
        new_input_tokens=new_input_tokens,
        tool_reserve=tool_reserve,
    )
