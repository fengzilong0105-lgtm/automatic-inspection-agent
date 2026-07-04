from __future__ import annotations

from langchain_core.messages import SystemMessage


def build_summary_message(summary: str | None) -> SystemMessage | None:
    if not summary:
        return None
    return SystemMessage(content=f"【本对话早期摘要】\n{summary}")
