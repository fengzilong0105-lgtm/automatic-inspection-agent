from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

if TYPE_CHECKING:
    from agent.langchain.chat_graph import ChatAgent


async def get_checkpoint_messages(chat_agent: ChatAgent, thread_id: str) -> list[BaseMessage]:
    graph = await chat_agent._ensure_graph()
    config = {"configurable": {"thread_id": thread_id}}
    state = await graph.aget_state(config)
    if not state or not state.values:
        return []
    return list(state.values.get("messages", []))


async def set_checkpoint_messages(
    chat_agent: ChatAgent, thread_id: str, messages: list[BaseMessage]
) -> None:
    graph = await chat_agent._ensure_graph()
    config = {"configurable": {"thread_id": thread_id}}
    await graph.aupdate_state(config, {"messages": messages})


def split_into_turns(messages: list[BaseMessage]) -> list[list[BaseMessage]]:
    turns: list[list[BaseMessage]] = []
    current: list[BaseMessage] = []
    for msg in messages:
        if isinstance(msg, HumanMessage) and current:
            turns.append(current)
            current = [msg]
        else:
            current.append(msg)
    if current:
        turns.append(current)
    return turns


def flatten_turns(turns: list[list[BaseMessage]]) -> list[BaseMessage]:
    flattened: list[BaseMessage] = []
    for turn in turns:
        flattened.extend(turn)
    return flattened


def count_turns(messages: list[BaseMessage]) -> int:
    return sum(1 for msg in messages if isinstance(msg, HumanMessage))


def replace_tool_message(msg: ToolMessage, content: str) -> ToolMessage:
    return ToolMessage(
        content=content,
        tool_call_id=getattr(msg, "tool_call_id", "") or "",
        name=getattr(msg, "name", None),
    )


def message_content_text(msg: BaseMessage) -> str:
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)


def turns_to_dialogue_text(turns: list[list[BaseMessage]]) -> str:
    lines: list[str] = []
    for turn in turns:
        for msg in turn:
            if isinstance(msg, HumanMessage):
                lines.append(f"用户: {message_content_text(msg)}")
            elif isinstance(msg, AIMessage):
                text = message_content_text(msg)
                if text:
                    lines.append(f"助手: {text}")
            elif isinstance(msg, ToolMessage):
                name = getattr(msg, "name", "tool") or "tool"
                snippet = message_content_text(msg)[:500]
                lines.append(f"工具[{name}]: {snippet}")
    return "\n".join(lines)
