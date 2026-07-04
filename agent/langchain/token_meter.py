from __future__ import annotations

import re

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")


def estimate_tokens(text: str) -> int:
    """Lightweight token estimate without calling the model tokenizer."""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = max(0, len(text) - cjk)
    # Chinese-heavy text: ~1.5 chars/token; Latin-heavy: ~4 chars/token.
    return max(1, int(cjk / 1.5 + other / 4))


def estimate_messages_tokens(messages: list[dict[str, str]]) -> int:
    total = 0
    for item in messages:
        total += estimate_tokens(item.get("content", ""))
        if item.get("tool_name"):
            total += 4
    return total


def format_token_count(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1000:
        return f"{count / 1000:.1f}K"
    return str(count)
