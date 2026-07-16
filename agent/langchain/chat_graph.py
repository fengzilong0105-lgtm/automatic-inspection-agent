from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.prebuilt import ToolNode, create_react_agent

from agent.langchain.checkpointer import get_checkpointer
from agent.langchain.context_builder import build_chat_system_prompt
from agent.langchain.context_messages import build_summary_message
from agent.langchain.llm_factory import get_llm
from agent.langchain.session_context import chat_session_id
from agent.langchain.tools import (
    _extract_tool_output_text,
    build_readonly_tools,
    parse_file_op_pending,
)
from agent.remediation.orchestrator import ActionOrchestrator
from agent.remediation.pending_writes import get_pending_file_op_store
from agent.settings import Settings, get_settings
from agent.store.chat import get_chat_store
from agent.store.knowledge import get_knowledge_store

# 询问「如何重启」类问题，不应触发执行重启
_HOWTO_RESTART = re.compile(
    r"(如何|怎么|怎样|咋).{0,30}重启"
    r"|重启.{0,20}(如何|怎么|怎样|方法|命令|步骤|方式|教程)"
    r"|how\s+(?:do\s+(?:i|you|we)\s+)?restart"
    r"|what\s+(?:is|are)\s+the\s+restart",
    re.I,
)
# 明确的重启执行意图（须带服务名，或单独说「重启」且非询问）
_ACTION_RESTART = re.compile(
    r"(?:请|帮我|帮忙|麻烦|立刻|马上|现在|直接)?(?:执行)?(?:重启|restart)\s+([a-zA-Z0-9_.-]+)"
    r"|^确认重启"
    r"|(?:^|[^\w])([a-zA-Z0-9_.-]+)\s*(?:请)?(?:重启|restart)(?:吧|一下|服务)?\s*(?:[？?]|$|[，。！!])",
    re.I,
)
_BARE_RESTART = re.compile(
    r"^(?:请|帮我|帮忙)?(?:执行)?(?:重启|restart)(?:吧|一下|服务)?[？?！!。.]*$",
    re.I,
)
_CORRUPT_HISTORY_MARKERS = (
    "INVALID_CHAT_HISTORY",
    "tool_calls that do not have a corresponding ToolMessage",
)


def _is_corrupt_history_error(exc: Exception) -> bool:
    text = str(exc)
    return any(marker in text for marker in _CORRUPT_HISTORY_MARKERS)


def _stream_event_data(event: dict, key: str, default=None):
    """Read a field from astream_events payload; data may be dict or non-dict."""
    data = event.get("data")
    if isinstance(data, dict):
        return data.get(key, default)
    return default


def _extract_chunk_text(chunk) -> str:
    content = getattr(chunk, "content", None)
    if not content:
        return ""
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


def _resolve_service_id(settings: Settings, token: str) -> str | None:
    ref = (token or "").strip()
    if not ref:
        return None
    services = settings.config.services
    for service in services:
        if service.id == ref:
            return service.id
    lowered = ref.lower()
    for service in services:
        if lowered in service.id.lower() or lowered in service.name.lower():
            return service.id
    return None


def detect_restart_intent(settings: Settings, text: str) -> str | None:
    """仅在用户明确要求执行重启时返回 service_id；「如何重启」类问题返回 None。"""
    stripped = (text or "").strip()
    if not stripped:
        return None
    if _HOWTO_RESTART.search(stripped):
        return None

    action = _ACTION_RESTART.search(stripped)
    if action:
        token = action.group(1) or action.group(2)
        if token:
            return _resolve_service_id(settings, token)
        return None

    if _BARE_RESTART.match(stripped) and settings.config.active_service_id:
        return settings.config.active_service_id

    return None


class ChatAgent:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.orchestrator = ActionOrchestrator()
        self._memory = None
        self._graph = None
        self._graph_fingerprint: tuple | None = None
        self._checkpointer_ready = False

    def _config_fingerprint(self) -> tuple:
        config = self.settings.config
        return (
            tuple(h.id for h in config.hosts),
            tuple(s.id for s in config.services),
            config.llm.default.provider,
            config.llm.default.model,
            config.llm.ollama_base_url,
        )

    async def _knowledge_fingerprint(self) -> str:
        store = get_knowledge_store()
        await store.init()
        return await store.get_fingerprint()

    async def _ensure_checkpointer(self):
        if self._memory is None:
            self._memory = await get_checkpointer()
            self._checkpointer_ready = True
        return self._memory

    async def _ensure_graph(self):
        fingerprint = self._config_fingerprint()
        knowledge_fp = await self._knowledge_fingerprint()
        graph_key = (fingerprint, knowledge_fp)
        memory = await self._ensure_checkpointer()
        if self._graph is None or self._graph_fingerprint != graph_key:
            knowledge_store = get_knowledge_store()
            knowledge = await knowledge_store.get_entries_for_prompt(self.settings)
            tools = build_readonly_tools()
            tool_node = ToolNode(tools, handle_tool_errors=True)
            self._graph = create_react_agent(
                get_llm("chat_qa"),
                tool_node,
                checkpointer=memory,
                prompt=SystemMessage(
                    content=build_chat_system_prompt(self.settings, knowledge=knowledge)
                ),
            )
            self._graph_fingerprint = graph_key
        return self._graph

    def invalidate_graph(self) -> None:
        """Force graph rebuild on next invoke (e.g. after knowledge changes)."""
        self._graph = None
        self._graph_fingerprint = None

    async def clear_session(self, session_id: str) -> None:
        memory = await self._ensure_checkpointer()
        await memory.adelete_thread(session_id)

    async def _build_inputs(self, session_id: str, text: str) -> dict:
        conv = await get_chat_store().get_conversation(session_id)
        messages = []
        summary_msg = build_summary_message(conv.summary)
        if summary_msg:
            messages.append(summary_msg)
        messages.append(HumanMessage(content=text))
        return {"messages": messages}

    async def _invoke_graph(self, session_id: str, text: str) -> str:
        token = chat_session_id.set(session_id)
        try:
            config = {"configurable": {"thread_id": session_id}}
            inputs = await self._build_inputs(session_id, text)
            graph = await self._ensure_graph()
            final_message = ""
            async for event in graph.astream(inputs, config=config, stream_mode="values"):
                messages = event.get("messages", [])
                if messages:
                    last = messages[-1]
                    if isinstance(last, AIMessage) and last.content:
                        final_message = str(last.content)
            return final_message or "已完成查询。"
        finally:
            chat_session_id.reset(token)

    def _detect_restart_intent(self, text: str) -> str | None:
        return detect_restart_intent(self.settings, text)

    async def handle_message(
        self,
        session_id: str,
        text: str,
        service_id: str | None = None,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        restart_target = self._detect_restart_intent(text)
        if restart_target and not confirmed:
            service = self.settings.get_service(restart_target)
            return {
                "type": "confirm_restart",
                "service_id": restart_target,
                "message": f"确认重启服务 `{restart_target}` 吗？（类型: {service.type.value}）",
                "requires_confirm": True,
            }
        if restart_target and confirmed:
            result = await self.orchestrator.restart_service(restart_target)
            ok = result.exit_code == 0
            return {
                "type": "restart_result",
                "service_id": restart_target,
                "success": ok,
                "message": result.stdout or result.stderr or ("重启成功" if ok else "重启失败"),
            }

        try:
            final_message = await self._invoke_graph(session_id, text)
        except Exception as exc:
            if _is_corrupt_history_error(exc):
                await self.clear_session(session_id)
                try:
                    final_message = await self._invoke_graph(session_id, text)
                    return {
                        "type": "message",
                        "message": final_message,
                        "history_reset": True,
                        "requires_confirm": False,
                    }
                except Exception as retry_exc:
                    return {
                        "type": "error",
                        "message": f"对话处理失败（已重置会话后重试）: {retry_exc}",
                        "requires_confirm": False,
                    }
            return {
                "type": "error",
                "message": f"对话处理失败: {exc}",
                "requires_confirm": False,
            }

        return {"type": "message", "message": final_message, "requires_confirm": False}

    async def stream_message(
        self, session_id: str, text: str, confirmed: bool = False
    ):
        restart_target = self._detect_restart_intent(text)
        if restart_target and not confirmed:
            service = self.settings.get_service(restart_target)
            yield {
                "event": "confirm_restart",
                "data": {
                    "service_id": restart_target,
                    "message": (
                        f"确认重启服务 `{restart_target}` 吗？（类型: {service.type.value}）"
                    ),
                    "requires_confirm": True,
                },
            }
            return
        if restart_target and confirmed:
            result = await self.orchestrator.restart_service(restart_target)
            ok = result.exit_code == 0
            yield {
                "event": "restart_result",
                "data": {
                    "service_id": restart_target,
                    "success": ok,
                    "message": result.stdout or result.stderr or ("重启成功" if ok else "重启失败"),
                },
            }
            return

        confirm_write_emitted = False
        pending_confirm: dict | None = None

        async def _run_stream():
            nonlocal pending_confirm
            token = chat_session_id.set(session_id)
            try:
                config = {"configurable": {"thread_id": session_id}}
                inputs = await self._build_inputs(session_id, text)
                graph = await self._ensure_graph()
                async for event in graph.astream_events(inputs, config=config, version="v2"):
                    kind = event.get("event")
                    if kind == "on_chat_model_stream":
                        chunk = _stream_event_data(event, "chunk")
                        delta = _extract_chunk_text(chunk)
                        if delta:
                            yield {"event": "delta", "data": delta}
                    elif kind == "on_tool_start":
                        tool_name = event.get("name") or "tool"
                        yield {"event": "tool_start", "data": str(tool_name)}
                    elif kind == "on_tool_end":
                        output = _stream_event_data(event, "output")
                        yield {"event": "tool_end", "data": _extract_tool_output_text(output)[:800]}
                        pending = parse_file_op_pending(output)
                        if pending:
                            # 等本轮全部文字流式输出完再弹确认框，避免边说边弹、双重引导
                            pending_confirm = pending
            finally:
                chat_session_id.reset(token)

        def _fallback_confirm_write() -> dict | None:
            if pending_confirm:
                return pending_confirm
            pending_item = get_pending_file_op_store().latest_for_session(session_id)
            if not pending_item:
                return None
            host = self.settings.get_host(pending_item.host_id)
            host_label = f"{host.id} ({host.ssh.host})"
            return get_pending_file_op_store().to_confirm_payload(pending_item, host_label)

        def _emit_confirm_after_stream():
            nonlocal confirm_write_emitted
            if confirm_write_emitted:
                return None
            fallback = _fallback_confirm_write()
            if fallback:
                confirm_write_emitted = True
                return {"event": "confirm_write", "data": fallback}
            return None

        try:
            async for item in _run_stream():
                yield item
            confirm_event = _emit_confirm_after_stream()
            if confirm_event:
                yield confirm_event
        except Exception as exc:
            if _is_corrupt_history_error(exc):
                await self.clear_session(session_id)
                yield {"event": "history_reset", "data": "上下文已自动重置，正在重试…"}
                confirm_write_emitted = False
                pending_confirm = None
                try:
                    async for item in _run_stream():
                        yield item
                    confirm_event = _emit_confirm_after_stream()
                    if confirm_event:
                        yield confirm_event
                except Exception as retry_exc:
                    yield {"event": "error", "data": f"对话处理失败（已重置后重试）: {retry_exc}"}
                    return
            else:
                yield {"event": "error", "data": f"对话处理失败: {exc}"}
                return
        yield {"event": "done", "data": ""}
