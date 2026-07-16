from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from agent.desktop.async_call import AsyncCall
from agent.desktop.markdown_render import (
    format_assistant_message,
    format_assistant_status,
    format_assistant_streaming,
    format_system_message,
    format_user_message,
)
from agent.services.agent_service import AgentService


class _StreamEventBridge(QObject):
    """Thread-safe bridge from background runtime to Qt UI."""

    event = Signal(object)


class ChatPanel(QWidget):
    """Embeddable AI chat panel with multi-conversation support."""

    memory_updated = Signal()

    def __init__(self, service: AgentService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.pending_restart: dict | None = None
        self.pending_write: dict | None = None
        self.pending_memory: dict | None = None
        self._confirming: dict | None = None  # snapshot while confirm request in flight
        self._confirm_token = 0  # invalidate in-flight confirm after cancel / new click
        self.conversation_id: str | None = None
        self._switching_conversation = False
        self._stream_buffer = ""
        self._stream_status = "正在思考…"
        self._stream_start_pos: int | None = None
        self._stream_bridge = _StreamEventBridge(self)
        self._stream_bridge.event.connect(self._on_stream_event)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("对话运维")
        title.setObjectName("sectionTitle")
        self.conversation_combo = QComboBox()
        self.conversation_combo.setMinimumWidth(180)
        self.new_conv_btn = QPushButton("新建对话")
        self.new_conv_btn.setObjectName("secondaryButton")
        self.usage_label = QLabel("上下文 --")
        self.usage_label.setObjectName("mutedText")
        self.clear_btn = QPushButton("清空对话")
        self.clear_btn.setObjectName("secondaryButton")
        head.addWidget(title)
        head.addWidget(self.conversation_combo, 1)
        head.addWidget(self.new_conv_btn)
        head.addWidget(self.usage_label)
        head.addWidget(self.clear_btn)
        layout.addLayout(head)

        self.history = QTextEdit()
        self.history.setObjectName("chatHistory")
        self.history.setReadOnly(True)
        self.history.setPlaceholderText("例如：road_control 状态怎么样？最近有什么报错？")

        self.confirm_frame = QFrame()
        self.confirm_frame.setObjectName("confirmBanner")
        confirm_layout = QHBoxLayout(self.confirm_frame)
        confirm_layout.setContentsMargins(12, 8, 12, 8)
        self.confirm_label = QLabel("")
        self.confirm_btn = QPushButton("确认执行")
        self.confirm_btn.setObjectName("primaryButton")
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("secondaryButton")
        self.confirm_btn.hide()
        self.cancel_btn.hide()
        confirm_layout.addWidget(self.confirm_label, 1)
        confirm_layout.addWidget(self.confirm_btn)
        confirm_layout.addWidget(self.cancel_btn)
        self.confirm_frame.hide()

        input_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入消息，Enter 发送")
        self.send_btn = QPushButton("发送")
        self.send_btn.setObjectName("primaryButton")
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.send_btn)

        layout.addWidget(self.history, 1)
        layout.addWidget(self.confirm_frame)
        layout.addLayout(input_row)

        self._thinking_timer = QTimer(self)
        self._thinking_timer.setInterval(450)
        self._thinking_dots = 0
        self._thinking_timer.timeout.connect(self._animate_thinking)

        self._bridge = AsyncCall(self)
        self._bridge.finished.connect(self._on_async_finished)
        self._bridge.failed.connect(self._on_async_failed)
        # 与对话流分离，避免确认结果被聊天回调误吃掉
        self._action_bridge = AsyncCall(self)
        self._action_bridge.finished.connect(self._on_confirm_finished)
        self._action_bridge.failed.connect(self._on_confirm_failed)

        self.send_btn.clicked.connect(self.send_message)
        self.input.returnPressed.connect(self.send_message)
        self.clear_btn.clicked.connect(self.clear_chat)
        self.new_conv_btn.clicked.connect(self.create_conversation)
        self.conversation_combo.currentIndexChanged.connect(self._on_conversation_changed)
        self.confirm_btn.clicked.connect(self.confirm_pending)
        self.cancel_btn.clicked.connect(self._cancel_confirm)

        self._confirm_watchdog = QTimer(self)
        self._confirm_watchdog.setSingleShot(True)
        self._confirm_watchdog.timeout.connect(self._on_confirm_watchdog)

        self._mode = "bootstrap"
        self._bridge.submit(self.service.load_chat_workspace())

    def _scroll_to_bottom(self) -> None:
        cursor = self.history.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.history.setTextCursor(cursor)
        self.history.ensureCursorVisible()

    def _append_html(self, html: str) -> None:
        self.history.append(html)
        self._scroll_to_bottom()

    def _set_chat_busy(self, busy: bool) -> None:
        self.input.setEnabled(not busy)
        self.send_btn.setEnabled(not busy)
        self.new_conv_btn.setEnabled(not busy)
        self.clear_btn.setEnabled(not busy)
        self.conversation_combo.setEnabled(not busy)

    def _replace_stream_html(self, html: str) -> None:
        if self._stream_start_pos is None:
            self._append_html(html)
            return
        cursor = self.history.textCursor()
        cursor.setPosition(self._stream_start_pos)
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertHtml(html)
        self._scroll_to_bottom()

    def _begin_stream(self, status: str) -> None:
        self._stream_buffer = ""
        self._stream_status = status
        cursor = self.history.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._stream_start_pos = cursor.position()
        cursor.insertHtml(format_assistant_status(status))
        self._scroll_to_bottom()
        self._thinking_timer.start()

    def _update_stream(self, text: str, status: str | None) -> None:
        if status is not None:
            self._stream_status = status
        self._stream_buffer = text
        if text:
            self._thinking_timer.stop()
            html = format_assistant_streaming(text, status)
        else:
            if not self._thinking_timer.isActive():
                self._thinking_timer.start()
            html = format_assistant_status(status or self._stream_status)
        self._replace_stream_html(html)

    def _finalize_stream(self, text: str) -> None:
        self._thinking_timer.stop()
        if self._stream_start_pos is not None:
            self._replace_stream_html(format_assistant_message(text))
            self._stream_start_pos = None
        else:
            self._append_assistant(text)
        self._stream_buffer = ""
        self._stream_status = "正在思考…"

    def _cancel_stream(self) -> None:
        self._thinking_timer.stop()
        if self._stream_start_pos is not None:
            cursor = self.history.textCursor()
            cursor.setPosition(self._stream_start_pos)
            cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            self._stream_start_pos = None
        self._stream_buffer = ""
        self._stream_status = "正在思考…"

    def _animate_thinking(self) -> None:
        if self._stream_buffer or self._stream_start_pos is None:
            return
        self._thinking_dots = (self._thinking_dots + 1) % 4
        dots = "·" * (self._thinking_dots + 1)
        self._replace_stream_html(format_assistant_status(f"{self._stream_status}{dots}"))

    def _emit_stream_event(self, event: dict) -> None:
        self._stream_bridge.event.emit(event)

    def _on_stream_event(self, event: dict) -> None:
        evt = event.get("event")
        if evt == "delta":
            self._update_stream(self._stream_buffer + str(event.get("data") or ""), None)
        elif evt == "tool_start":
            tool = str(event.get("data") or "tool")
            self._update_stream(self._stream_buffer, f"正在调用工具: {tool}…")
        elif evt == "tool_end":
            self._update_stream(self._stream_buffer, "正在整理回答…")
        elif evt == "confirm_write":
            data = event.get("data") or {}
            if isinstance(data, dict) and (data.get("op_id") or data.get("write_id")):
                if self._confirming:
                    return
                # 只缓存，等本轮流式结束（_on_reply）再展示横幅，避免边输出边弹确认
                self.pending_write = data
                self.pending_restart = None
                self.pending_memory = None
        elif evt == "history_reset":
            prefix = "（上下文已自动重置）\n\n"
            if not self._stream_buffer.startswith(prefix):
                self._update_stream(prefix + self._stream_buffer, str(event.get("data") or "上下文已重置…"))
            else:
                self._update_stream(self._stream_buffer, str(event.get("data") or "上下文已重置…"))
        elif evt == "compaction":
            self._append_system(str(event.get("data") or ""))

    def _set_usage(self, usage: dict | None) -> None:
        if not usage:
            self.usage_label.setText("上下文 --")
            return
        icon = usage.get("level_icon") or ""
        hint = usage.get("hint") or ""
        text = (
            f"{icon} 上下文 {usage.get('used_label', usage.get('used'))} / "
            f"{usage.get('limit_label', usage.get('limit'))} "
            f"({usage.get('percent', 0)}%)"
        )
        if hint:
            text += f" · {hint}"
        self.usage_label.setText(text.strip())

    def _apply_workspace(self, payload: dict) -> None:
        conv = payload.get("conversation") or {}
        self.conversation_id = conv.get("id")
        conversations = payload.get("conversations", [])
        self._switching_conversation = True
        self.conversation_combo.clear()
        active_index = 0
        for idx, item in enumerate(conversations):
            self.conversation_combo.addItem(item.get("title") or "新对话", item.get("id"))
            if item.get("id") == self.conversation_id:
                active_index = idx
        self.conversation_combo.setCurrentIndex(active_index)
        self._switching_conversation = False
        self._render_messages(payload.get("messages", []))
        self._set_usage(payload.get("usage"))

    def _on_async_finished(self, result) -> None:
        if self._mode in {"bootstrap", "switch", "create"}:
            self._apply_workspace(result)
            self._mode = "chat"
            return
        if self._mode == "pending":
            self._on_pending_op(result)
        else:
            self._on_reply(result)

    def _on_async_failed(self, message: str) -> None:
        self._cancel_stream()
        self._set_chat_busy(False)
        self._append_system(message)

    def _render_messages(self, messages: list[dict]) -> None:
        self.history.clear()
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "user":
                self._append_html(format_user_message(content))
            elif role == "assistant":
                self._append_html(format_assistant_message(content))
            elif role in {"system", "tool"}:
                prefix = f"[{msg.get('tool_name')}] " if msg.get("tool_name") else ""
                self._append_html(format_system_message(prefix + content))

    def _on_conversation_changed(self, index: int) -> None:
        if index < 0 or self._switching_conversation:
            return
        conv_id = self.conversation_combo.itemData(index)
        if not conv_id or conv_id == self.conversation_id:
            return
        self.conversation_id = conv_id
        if self._confirming:
            self._confirm_token += 1
            self._confirming = None
        self._hide_confirm()
        self._cancel_stream()
        self._mode = "switch"
        self._bridge.submit(self.service.load_chat_workspace(conv_id))

    def create_conversation(self) -> None:
        self._mode = "create"
        self._bridge.submit(self.service.create_conversation_workspace())

    def send_message(self) -> None:
        if not self.conversation_id:
            return
        text = self.input.text().strip()
        if not text:
            return
        self._append_user(text)
        self.input.clear()
        self._mode = "chat"
        self._set_chat_busy(True)
        self._begin_stream("正在思考")
        self._bridge.submit(
            self.service.chat_stream(
                text,
                session_id=self.conversation_id,
                confirmed=False,
                on_event=self._emit_stream_event,
            )
        )

    def clear_chat(self) -> None:
        if not self.conversation_id:
            return
        self._mode = "clear"
        self._bridge.submit(self.service.chat_clear(self.conversation_id))

    def confirm_pending(self) -> None:
        if not self.conversation_id:
            return
        if self._confirming:
            return
        if self.pending_restart:
            self._begin_confirm({"kind": "restart", **self.pending_restart})
            self._action_bridge.submit(
                self.service.confirm_restart(self.pending_restart["service_id"])
            )
        elif self.pending_write:
            op_id = self.pending_write.get("write_id") or self.pending_write.get("op_id")
            if not op_id:
                self._append_system("确认失败：缺少操作 ID")
                return
            self._begin_confirm({"kind": "write", **self.pending_write})
            session_id = (
                self.pending_write.get("session_id")
                or self.conversation_id
            )
            self._action_bridge.submit(self.service.confirm_write(op_id, session_id))
        elif self.pending_memory:
            mem = self.pending_memory
            self._begin_confirm({"kind": "memory", **mem})
            self._action_bridge.submit(
                self.service.confirm_memory(
                    mem["category"],
                    mem["key"],
                    mem["value"],
                    self.conversation_id,
                )
            )

    def _begin_confirm(self, payload: dict) -> None:
        self._confirm_token += 1
        self._confirming = {**payload, "_token": self._confirm_token}
        self.confirm_label.setText("正在执行已确认的操作，请稍候…")
        self.confirm_btn.setText("执行中…")
        self.confirm_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.confirm_frame.show()
        self.confirm_btn.show()
        self.cancel_btn.show()
        self._confirm_watchdog.start(90_000)

    def _on_confirm_watchdog(self) -> None:
        if not self._confirming:
            return
        self.confirm_label.setText(
            "执行时间较长，仍在等待远端结果…可点「取消」结束等待（远端命令可能仍在跑）"
        )

    def _cancel_confirm(self) -> None:
        """取消待确认，并丢弃后端挂起项，避免下轮对话再次弹出。"""
        if self._confirming:
            self._confirm_token += 1
            self._confirming = None
            self._confirm_watchdog.stop()
            self._append_system(
                "已取消等待确认结果。若命令已在远端开始执行，稍后仍可能产生输出。"
            )
        pending_write = self.pending_write
        op_id = None
        session_id = self.conversation_id
        if pending_write:
            op_id = pending_write.get("write_id") or pending_write.get("op_id")
            session_id = pending_write.get("session_id") or self.conversation_id
        self._hide_confirm()
        if pending_write and self.conversation_id:
            # fire-and-forget：不要走 _action_bridge，以免结果被当成确认回调
            self.service.cancel_pending_file_op(
                op_id, session_id or self.conversation_id
            )

    def _on_confirm_finished(self, result: dict) -> None:
        confirming = self._confirming or {}
        token = confirming.get("_token")
        self._confirm_watchdog.stop()
        self._confirming = None
        self.confirm_btn.setEnabled(True)
        self.confirm_btn.setText("确认执行")

        # 用户已点取消 / 新确认覆盖时，忽略过期回调，避免卡死或重复续聊
        if token is not None and token != self._confirm_token:
            return

        kind = confirming.get("kind")
        if kind == "restart":
            ok = bool(result.get("success"))
            detail = str(result.get("stderr") or result.get("stdout") or "")
            self._append_system("重启成功" if ok else f"重启失败: {detail}")
            self._hide_confirm()
            self._auto_continue_after_confirm("重启服务", detail, success=ok)
            return

        if kind == "write":
            ok = bool(result.get("success"))
            action = confirming.get("action") or "write"
            if action == "command":
                label = "命令执行成功" if ok else "命令执行失败"
            elif action == "delete":
                label = "删除成功" if ok else "删除失败"
            else:
                label = "写操作成功" if ok else "写操作失败"
            detail = str(result.get("stderr") or result.get("stdout") or "")
            self._append_system(label if ok or not detail else f"{label}: {detail}")
            if result.get("stdout"):
                out = str(result.get("stdout"))
                self._append_system(out if len(out) <= 2000 else out[:2000] + "…")
            self._hide_confirm()
            action_label = {
                "command": "远程命令",
                "delete": "删除文件",
                "write": "写入文件",
            }.get(action, "写操作")
            self._auto_continue_after_confirm(action_label, detail, success=ok)
            return

        if kind == "memory":
            self._append_system("已记住该条信息")
            self._hide_confirm()
            self.memory_updated.emit()
            return

        # 未知 kind：至少解锁横幅，避免一直灰着
        self._hide_confirm()

    def _on_confirm_failed(self, message: str) -> None:
        confirming = self._confirming or {}
        token = confirming.get("_token")
        self._confirm_watchdog.stop()
        self._confirming = None
        self.confirm_btn.setEnabled(True)
        self.confirm_btn.setText("确认执行")
        if token is not None and token != self._confirm_token:
            return
        self._append_system(f"确认执行失败: {message}")
        self._hide_confirm()
        self._auto_continue_after_confirm("待确认操作", message, success=False)

    def _auto_continue_after_confirm(
        self, action_label: str, detail: str, *, success: bool = True
    ) -> None:
        """确认后无论成败都回传结果给 Agent，避免用户点了确认却没有后续。"""
        if not self.conversation_id:
            return
        summary = (detail or "").strip()
        if len(summary) > 1200:
            summary = summary[:1200] + "…"
        if success:
            followup = (
                f"【系统】用户已确认，「{action_label}」已执行成功。"
                "请立即继续你计划中的下一步；若下一步仍需确认，请再次调用对应工具。"
            )
        else:
            followup = (
                f"【系统】用户已确认，「{action_label}」已提交执行但失败。"
                "请根据错误输出修正后重试；不要再说「请再点一次确认」或假装命令未执行。"
                "若错误含 sudo/密码，请明确提示用户到「设置→服务器管理」重填密码并「测试 SSH」。"
            )
        if summary:
            followup += f"\n执行输出：\n```text\n{summary}\n```"
        self._mode = "chat"
        self._set_chat_busy(True)
        self._begin_stream("正在继续…" if success else "正在根据失败结果继续…")
        self._bridge.submit(
            self.service.chat_stream(
                followup,
                session_id=self.conversation_id,
                confirmed=False,
                on_event=self._emit_stream_event,
            )
        )

    def _on_reply(self, result: dict) -> None:
        self._set_chat_busy(False)

        if self._mode == "clear":
            self._cancel_stream()
            self.history.clear()
            self._set_usage(result.get("usage"))
            self._hide_confirm()
            self._mode = "chat"
            return

        msg_type = result.get("type", "message")
        if msg_type == "confirm_restart":
            self._cancel_stream()
            self.pending_restart = {"service_id": result.get("service_id", "")}
            self.pending_write = None
            self._append_assistant(result.get("message", "确认重启？"))
            self.confirm_label.setText(f"待确认：重启服务 {self.pending_restart['service_id']}")
            self.confirm_frame.show()
            self.confirm_btn.show()
            self.cancel_btn.show()
            return
        if msg_type == "error":
            self._cancel_stream()
            self._append_system(result.get("message", str(result)))
            self._set_usage(result.get("usage"))
            return

        for notice in result.get("compaction_notices") or []:
            self._append_system(notice)

        reply_text = result.get("message") or result.get("reply") or ""
        if reply_text:
            self._finalize_stream(reply_text)
        elif self._stream_buffer:
            self._finalize_stream(self._stream_buffer)
        else:
            self._cancel_stream()
        self._set_usage(result.get("usage"))
        auto_saved = result.get("auto_saved") or []
        if auto_saved:
            self._append_system(f"已自动记住 {len(auto_saved)} 条信息")
            self.memory_updated.emit()

        # 确认执行进行中时不要重绘「待确认」横幅，否则按钮会一直灰着像卡住
        if self._confirming:
            self._mode = "chat"
            return

        # 已有待确认写操作时，优先保留，不被记忆建议冲掉
        if self.pending_write:
            self._show_pending_write_banner(self.pending_write)
            self._mode = "chat"
            return

        suggestions = result.get("memory_suggestions") or []
        if suggestions:
            self.pending_memory = suggestions[0]
            self.pending_restart = None
            mem = self.pending_memory
            self.confirm_label.setText(
                f"待确认记忆：[{mem.get('category')}] {mem.get('key')}: {mem.get('value')}"
            )
            self.confirm_btn.setText("记住这条")
            self.confirm_frame.show()
            self.confirm_btn.show()
            self.cancel_btn.show()
            self._mode = "chat"
            return

        # 流式事件已带上本轮 pending；不再去 store 捞历史残留
        self._mode = "chat"

    def _show_pending_write_banner(self, data: dict) -> None:
        if not data:
            return
        if self._confirming:
            return
        action = data.get("action") or "write"
        if action == "command":
            preview = (data.get("command") or data.get("content_preview") or "").strip()
            if len(preview) > 80:
                preview = preview[:80] + "…"
            self.confirm_label.setText(f"待确认：执行命令 {preview}")
        elif action == "delete":
            self.confirm_label.setText(f"待确认：删除 {data.get('path') or ''}")
        else:
            self.confirm_label.setText(f"待确认：写入 {data.get('path') or ''}")
        self.confirm_btn.setText("确认执行")
        self.confirm_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        self.confirm_frame.show()
        self.confirm_btn.show()
        self.cancel_btn.show()

    def _on_pending_op(self, data: dict) -> None:
        if self._confirming:
            self._mode = "chat"
            return
        if data.get("pending"):
            self.pending_write = data
            self.pending_restart = None
            self._show_pending_write_banner(data)
        self._mode = "chat"

    def _hide_confirm(self) -> None:
        self._confirm_watchdog.stop()
        self.pending_restart = None
        self.pending_write = None
        self.pending_memory = None
        self.confirm_label.setText("")
        self.confirm_btn.setText("确认执行")
        self.confirm_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        self.confirm_frame.hide()
        self.confirm_btn.hide()
        self.cancel_btn.hide()

    def _append_user(self, text: str) -> None:
        self._append_html(format_user_message(text))

    def _append_assistant(self, text: str) -> None:
        self._append_html(format_assistant_message(text))

    def _append_system(self, text: str) -> None:
        self._append_html(format_system_message(text))
