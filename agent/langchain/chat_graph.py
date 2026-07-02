from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import ToolNode, create_react_agent

from agent.langchain.context_builder import build_chat_system_prompt
from agent.langchain.llm_factory import get_llm
from agent.langchain.tools import build_readonly_tools
from agent.remediation.orchestrator import ActionOrchestrator
from agent.settings import get_settings

_RESTART_PATTERN = re.compile(r"(重启|restart)\s*([a-zA-Z0-9_-]+)?", re.I)
_CORRUPT_HISTORY_MARKERS = (
    "INVALID_CHAT_HISTORY",
    "tool_calls that do not have a corresponding ToolMessage",
)


def _is_corrupt_history_error(exc: Exception) -> bool:
    text = str(exc)
    return any(marker in text for marker in _CORRUPT_HISTORY_MARKERS)


class ChatAgent:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.orchestrator = ActionOrchestrator()
        self._memory = MemorySaver()
        self._graph = None
        self._graph_fingerprint: tuple | None = None

    def _config_fingerprint(self) -> tuple:
        config = self.settings.config
        return (
            tuple(h.id for h in config.hosts),
            tuple(s.id for s in config.services),
            config.llm.default.provider,
            config.llm.default.model,
            config.llm.ollama_base_url,
        )

    def _ensure_graph(self):
        fingerprint = self._config_fingerprint()
        if self._graph is None or self._graph_fingerprint != fingerprint:
            if self._graph_fingerprint is not None:
                self._memory = MemorySaver()
            tools = build_readonly_tools()
            tool_node = ToolNode(tools, handle_tool_errors=True)
            self._graph = create_react_agent(
                get_llm("chat_qa"),
                tool_node,
                checkpointer=self._memory,
                prompt=SystemMessage(content=build_chat_system_prompt(self.settings)),
            )
            self._graph_fingerprint = fingerprint
        return self._graph

    async def clear_session(self, session_id: str) -> None:
        await self._memory.adelete_thread(session_id)

    async def _invoke_graph(self, session_id: str, text: str) -> str:
        config = {"configurable": {"thread_id": session_id}}
        inputs = {"messages": [HumanMessage(content=text)]}
        graph = self._ensure_graph()
        final_message = ""
        async for event in graph.astream(inputs, config=config, stream_mode="values"):
            messages = event.get("messages", [])
            if messages:
                last = messages[-1]
                if isinstance(last, AIMessage) and last.content:
                    final_message = str(last.content)
        return final_message or "已完成查询。"

    def _detect_restart_intent(self, text: str) -> str | None:
        match = _RESTART_PATTERN.search(text)
        if not match:
            return None
        service_id = match.group(2)
        if service_id:
            return service_id
        if self.settings.config.active_service_id:
            return self.settings.config.active_service_id
        return None

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

    async def stream_message(self, session_id: str, text: str):
        config = {"configurable": {"thread_id": session_id}}
        inputs = {"messages": [HumanMessage(content=text)]}
        graph = self._ensure_graph()
        async for event in graph.astream_events(inputs, config=config, version="v2"):
            kind = event.get("event")
            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and getattr(chunk, "content", None):
                    yield {"event": "delta", "data": chunk.content}
            elif kind == "on_tool_end":
                output = event.get("data", {}).get("output")
                yield {"event": "tool", "data": str(output)[:2000]}
        yield {"event": "done", "data": ""}
