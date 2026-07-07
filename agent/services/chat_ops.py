from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.langchain.chat_graph import ChatAgent
from agent.langchain.checkpointer import delete_checkpointer_thread
from agent.langchain.context_compactor import prepare_conversation_context
from agent.langchain.context_policy import LEVEL_BLOCKED
from agent.langchain.memory_extractor import process_turn_memories, save_memory_suggestion
from agent.settings import get_settings
from agent.store.chat import ChatStore, get_chat_store
from agent.store.knowledge import get_knowledge_store


async def _run_pre_message_compaction(
    chat_agent: ChatAgent,
    conversation_id: str,
    message: str,
    store: ChatStore,
) -> dict[str, Any] | None:
    budget, applied, notices = await prepare_conversation_context(
        chat_agent, conversation_id, message, store=store
    )
    for notice in notices:
        await store.append_message(conversation_id, role="system", content=notice)

    if budget.level == LEVEL_BLOCKED:
        if budget.overflow_reason == "current_turn":
            error_msg = "本次查询范围过大，请指定服务名/时间段/关键字"
        else:
            error_msg = "上下文已满，建议新建对话；重要信息已写入 AI 记忆。"
        await store.append_message(conversation_id, role="system", content=error_msg)
        return {
            "type": "error",
            "message": error_msg,
            "usage": await store.get_usage(conversation_id, actions_applied=applied),
            "actions_applied": applied,
            "level": budget.level,
        }
    return {"budget": budget, "applied": applied, "notices": notices}


async def list_conversations(store: ChatStore | None = None) -> list[dict[str, Any]]:
    store = store or get_chat_store()
    return [item.to_dict() for item in await store.list_conversations()]


async def load_chat_workspace(conversation_id: str | None = None, store: ChatStore | None = None) -> dict[str, Any]:
    store = store or get_chat_store()
    await store.init()
    if conversation_id:
        conv = await store.get_conversation(conversation_id)
    else:
        conv = await store.ensure_default_conversation()
    conversations = await store.list_conversations()
    messages = await store.list_messages(conv.id)
    usage = await store.get_usage(conv.id)
    return {
        "conversation": conv.to_dict(),
        "conversations": [item.to_dict() for item in conversations],
        "messages": [item.to_dict() for item in messages],
        "usage": usage,
    }


async def create_conversation_workspace(title: str | None = None, store: ChatStore | None = None) -> dict[str, Any]:
    store = store or get_chat_store()
    await store.init()
    conv = await store.create_conversation(title)
    conversations = await store.list_conversations()
    usage = await store.get_usage(conv.id)
    return {
        "conversation": conv.to_dict(),
        "conversations": [item.to_dict() for item in conversations],
        "messages": [],
        "usage": usage,
    }


async def ensure_default_conversation(store: ChatStore | None = None) -> dict[str, Any]:
    store = store or get_chat_store()
    return (await store.ensure_default_conversation()).to_dict()


async def get_conversation_messages(
    conversation_id: str, store: ChatStore | None = None
) -> list[dict[str, Any]]:
    store = store or get_chat_store()
    return [item.to_dict() for item in await store.list_messages(conversation_id)]


async def get_conversation_usage(conversation_id: str, store: ChatStore | None = None) -> dict[str, Any]:
    store = store or get_chat_store()
    return await store.get_usage(conversation_id)


async def delete_conversation(conversation_id: str, store: ChatStore | None = None) -> dict[str, Any]:
    store = store or get_chat_store()
    await store.delete_conversation(conversation_id)
    await delete_checkpointer_thread(conversation_id)
    return {"deleted": conversation_id}


async def clear_conversation(conversation_id: str, store: ChatStore | None = None) -> dict[str, Any]:
    store = store or get_chat_store()
    await store.clear_messages(conversation_id)
    await delete_checkpointer_thread(conversation_id)
    usage = await store.get_usage(conversation_id)
    return {"cleared": True, "session_id": conversation_id, "usage": usage}


async def prepare_stream_chat(
    chat_agent: ChatAgent,
    conversation_id: str,
    message: str,
    *,
    confirmed: bool = False,
    store: ChatStore | None = None,
) -> dict[str, Any]:
    """Prepare streaming chat: compaction + optional user message append."""
    store = store or get_chat_store()
    await store.init()
    await get_knowledge_store().init()
    await store.get_conversation(conversation_id)
    applied: list[str] = []
    prep = None
    if not confirmed:
        prep = await _run_pre_message_compaction(chat_agent, conversation_id, message, store)
        if prep and prep.get("type") == "error":
            return prep
        if prep:
            applied = prep.get("applied", [])
        await store.append_message(conversation_id, role="user", content=message)
    return {"ok": True, "applied": applied, "notices": prep.get("notices", []) if prep else []}


async def handle_chat_message(
    chat_agent: ChatAgent,
    *,
    conversation_id: str,
    message: str,
    confirmed: bool = False,
    store: ChatStore | None = None,
) -> dict[str, Any]:
    store = store or get_chat_store()
    await store.init()
    await get_knowledge_store().init()
    await store.get_conversation(conversation_id)

    applied: list[str] = []
    compaction_notices: list[str] = []
    if not confirmed:
        prep = await _run_pre_message_compaction(chat_agent, conversation_id, message, store)
        if prep and prep.get("type") == "error":
            return prep
        if prep:
            applied = prep.get("applied", [])
            compaction_notices = prep.get("notices", [])
        await store.append_message(conversation_id, role="user", content=message)

    result = await chat_agent.handle_message(
        session_id=conversation_id,
        text=message,
        confirmed=confirmed,
    )
    if result.get("type") == "message" and result.get("message"):
        await store.append_message(conversation_id, role="assistant", content=result["message"])
        memory_info = await process_turn_memories(
            user_text=message,
            assistant_text=result["message"],
            conversation_id=conversation_id,
        )
        if memory_info.get("auto_saved"):
            chat_agent.invalidate_graph()
        result.update(memory_info)
    elif result.get("type") == "error":
        await store.append_message(
            conversation_id,
            role="system",
            content=result.get("message", "对话失败"),
        )
    result["actions_applied"] = applied
    result["compaction_notices"] = compaction_notices
    result["usage"] = await store.get_usage(conversation_id, actions_applied=applied)
    return result


def _emit_stream_event(on_event: Callable[[dict[str, Any]], None] | None, event: dict[str, Any]) -> None:
    if on_event is not None:
        on_event(event)


async def run_chat_stream(
    chat_agent: ChatAgent,
    *,
    conversation_id: str,
    message: str,
    confirmed: bool = False,
    store: ChatStore | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Stream chat events to a UI callback and return the final turn result."""
    store = store or get_chat_store()
    prep = await prepare_stream_chat(
        chat_agent,
        conversation_id,
        message,
        confirmed=confirmed,
        store=store,
    )
    applied = prep.get("applied", [])
    compaction_notices = list(prep.get("notices") or [])
    for notice in compaction_notices:
        _emit_stream_event(on_event, {"event": "compaction", "data": notice})
    if prep.get("type") == "error":
        _emit_stream_event(on_event, {"event": "error", "data": prep.get("message")})
        usage = prep.get("usage") or await store.get_usage(conversation_id, actions_applied=applied)
        _emit_stream_event(on_event, {"event": "usage", "data": usage})
        return {
            "type": "error",
            "message": prep.get("message"),
            "actions_applied": applied,
            "compaction_notices": compaction_notices,
            "usage": usage,
        }

    assistant_parts: list[str] = []
    async for chunk in chat_agent.stream_message(
        conversation_id, message, confirmed=confirmed
    ):
        event = chunk.get("event")
        _emit_stream_event(on_event, chunk)
        if event == "delta":
            assistant_parts.append(str(chunk.get("data", "")))
        elif event == "confirm_restart":
            usage = await store.get_usage(conversation_id, actions_applied=applied)
            data = chunk.get("data")
            data_dict = data if isinstance(data, dict) else {}
            return {
                "type": "confirm_restart",
                "service_id": data_dict.get("service_id", ""),
                "message": data_dict.get("message", "确认重启？"),
                "requires_confirm": True,
                "actions_applied": applied,
                "compaction_notices": compaction_notices,
                "usage": usage,
            }
        elif event == "error":
            await store.append_message(
                conversation_id,
                role="system",
                content=str(chunk.get("data") or "对话失败"),
            )
            usage = await store.get_usage(conversation_id, actions_applied=applied)
            return {
                "type": "error",
                "message": chunk.get("data"),
                "actions_applied": applied,
                "compaction_notices": compaction_notices,
                "usage": usage,
            }

    result: dict[str, Any] = {
        "type": "message",
        "requires_confirm": False,
        "actions_applied": applied,
        "compaction_notices": compaction_notices,
    }
    if assistant_parts:
        assistant_text = "".join(assistant_parts)
        await store.append_message(
            conversation_id,
            role="assistant",
            content=assistant_text,
        )
        memory_info = await process_turn_memories(
            user_text=message,
            assistant_text=assistant_text,
            conversation_id=conversation_id,
        )
        if memory_info.get("auto_saved"):
            chat_agent.invalidate_graph()
        result["message"] = assistant_text
        result.update(memory_info)
        _emit_stream_event(on_event, {"event": "memory", "data": memory_info})
    else:
        result["message"] = ""

    usage = await store.get_usage(conversation_id, actions_applied=applied)
    result["usage"] = usage
    _emit_stream_event(on_event, {"event": "usage", "data": usage})
    return result


async def confirm_memory_suggestion(
    *,
    category: str,
    key: str,
    value: str,
    conversation_id: str | None = None,
    chat_agent: ChatAgent | None = None,
) -> dict[str, Any]:
    entry = await save_memory_suggestion(
        category=category,
        key=key,
        value=value,
        conversation_id=conversation_id,
    )
    if chat_agent is not None:
        chat_agent.invalidate_graph()
    return entry.to_dict()


async def list_knowledge_entries() -> list[dict[str, Any]]:
    store = get_knowledge_store()
    await store.init()
    return [item.to_dict() for item in await store.list_entries()]


async def create_knowledge_entry(
    *,
    category: str,
    key: str,
    value: str,
    source_conv_id: str | None = None,
    chat_agent: ChatAgent | None = None,
) -> dict[str, Any]:
    store = get_knowledge_store()
    await store.init()
    entry = await store.upsert_entry(
        category=category,
        key=key,
        value=value,
        source_conv_id=source_conv_id,
    )
    if chat_agent is not None:
        chat_agent.invalidate_graph()
    return entry.to_dict()


async def update_knowledge_entry(
    entry_id: str,
    *,
    category: str | None = None,
    key: str | None = None,
    value: str | None = None,
    chat_agent: ChatAgent | None = None,
) -> dict[str, Any]:
    store = get_knowledge_store()
    await store.init()
    entry = await store.update_entry(entry_id, category=category, key=key, value=value)
    if chat_agent is not None:
        chat_agent.invalidate_graph()
    return entry.to_dict()


async def delete_knowledge_entry(entry_id: str, chat_agent: ChatAgent | None = None) -> dict[str, Any]:
    store = get_knowledge_store()
    await store.init()
    await store.delete_entry(entry_id)
    if chat_agent is not None:
        chat_agent.invalidate_graph()
    return {"deleted": entry_id}


async def get_chat_memory_settings() -> dict[str, Any]:
    settings = get_settings()
    return {
        "auto_extract": settings.config.chat.memory.auto_extract,
        "max_inject_tokens": settings.config.chat.memory.max_inject_tokens,
        "tool_compression": settings.config.chat.tool_compression.model_dump(),
    }


async def save_chat_memory_settings(*, auto_extract: bool) -> dict[str, Any]:
    settings = get_settings()
    chat = settings.config.chat.model_copy(
        update={"memory": settings.config.chat.memory.model_copy(update={"auto_extract": auto_extract})}
    )
    settings.save(settings.config.model_copy(update={"chat": chat}))
    return await get_chat_memory_settings()
