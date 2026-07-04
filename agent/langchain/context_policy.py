from __future__ import annotations

from agent.models import ChatPolicyConfig

LEVEL_GREEN = "green"
LEVEL_YELLOW = "yellow"
LEVEL_ORANGE = "orange"
LEVEL_RED = "red"
LEVEL_BLOCKED = "blocked"


def resolve_level(
    ratio: float,
    policy: ChatPolicyConfig,
    *,
    overflow_reason: str | None = None,
) -> str:
    if ratio >= 1.0:
        return LEVEL_BLOCKED
    if ratio >= policy.red_threshold:
        return LEVEL_RED
    if ratio >= policy.orange_threshold:
        return LEVEL_ORANGE
    if ratio >= policy.yellow_threshold:
        return LEVEL_YELLOW
    return LEVEL_GREEN


def decide_actions(
    *,
    level: str,
    overflow_reason: str | None,
    turn_count: int,
    policy: ChatPolicyConfig,
) -> list[str]:
    actions: list[str] = []

    if turn_count >= policy.summary_trigger_turns and turn_count % policy.summary_trigger_turns == 0:
        actions.append("rolling_summary")

    if level == LEVEL_GREEN:
        return _dedupe(actions)

    if overflow_reason == "current_turn":
        actions.append("aggressive_compress_current")
        return _dedupe(actions)

    if level == LEVEL_YELLOW:
        actions.append("compress_old_tools")
    elif level == LEVEL_ORANGE:
        actions.extend(["compress_old_tools", "rolling_summary"])
    elif level in {LEVEL_RED, LEVEL_BLOCKED}:
        actions.extend(["compress_old_tools", "rolling_summary", "shrink_window"])

    if level == LEVEL_BLOCKED and overflow_reason == "accumulated":
        return _dedupe(actions)

    return _dedupe(actions)


def escalation_actions_for_blocked() -> list[str]:
    return ["compress_old_tools", "rolling_summary", "shrink_window"]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered
