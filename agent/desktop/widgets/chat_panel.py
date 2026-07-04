from __future__ import annotations

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
    format_system_message,
    format_user_message,
)
from agent.services.agent_service import AgentService


class ChatPanel(QWidget):
    """Embeddable AI chat panel with multi-conversation support."""

    def __init__(self, service: AgentService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.pending_restart: dict | None = None
        self.pending_write: dict | None = None
        self.pending_memory: dict | None = None
        self.conversation_id: str | None = None
        self._switching_conversation = False

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
        clear_btn = QPushButton("清空对话")
        clear_btn.setObjectName("secondaryButton")
        head.addWidget(title)
        head.addWidget(self.conversation_combo, 1)
        head.addWidget(self.new_conv_btn)
        head.addWidget(self.usage_label)
        head.addWidget(clear_btn)
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
        send_btn = QPushButton("发送")
        send_btn.setObjectName("primaryButton")
        input_row.addWidget(self.input, 1)
        input_row.addWidget(send_btn)

        layout.addWidget(self.history, 1)
        layout.addWidget(self.confirm_frame)
        layout.addLayout(input_row)

        self._bridge = AsyncCall(self)
        self._bridge.finished.connect(self._on_async_finished)
        self._bridge.failed.connect(self._append_system)

        send_btn.clicked.connect(self.send_message)
        self.input.returnPressed.connect(self.send_message)
        clear_btn.clicked.connect(self.clear_chat)
        self.new_conv_btn.clicked.connect(self.create_conversation)
        self.conversation_combo.currentIndexChanged.connect(self._on_conversation_changed)
        self.confirm_btn.clicked.connect(self.confirm_pending)
        self.cancel_btn.clicked.connect(self._hide_confirm)

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
        self._hide_confirm()
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
        self._bridge.submit(
            self.service.chat_message(text, session_id=self.conversation_id, confirmed=False)
        )

    def clear_chat(self) -> None:
        if not self.conversation_id:
            return
        self._mode = "clear"
        self._bridge.submit(self.service.chat_clear(self.conversation_id))

    def confirm_pending(self) -> None:
        if not self.conversation_id:
            return
        if self.pending_restart:
            self._mode = "restart"
            self._bridge.submit(
                self.service.confirm_restart(self.pending_restart["service_id"])
            )
        elif self.pending_write:
            self._mode = "write"
            self._bridge.submit(
                self.service.confirm_write(
                    self.pending_write.get("write_id") or self.pending_write.get("op_id"),
                    self.conversation_id,
                )
            )
        elif self.pending_memory:
            self._mode = "memory"
            mem = self.pending_memory
            self._bridge.submit(
                self.service.confirm_memory(
                    mem["category"],
                    mem["key"],
                    mem["value"],
                    self.conversation_id,
                )
            )

    def _on_reply(self, result: dict) -> None:
        if self._mode == "clear":
            self.history.clear()
            self._set_usage(result.get("usage"))
            self._hide_confirm()
            self._mode = "chat"
            return
        if self._mode == "restart":
            ok = result.get("success")
            self._append_system(
                "重启成功" if ok else f"重启失败: {result.get('stderr') or result.get('stdout')}"
            )
            self._hide_confirm()
            self._mode = "chat"
            return
        if self._mode == "write":
            ok = result.get("success")
            self._append_system(
                "写操作成功" if ok else f"写操作失败: {result.get('stderr') or result.get('stdout')}"
            )
            self._hide_confirm()
            self._mode = "chat"
            return
        if self._mode == "memory":
            self._append_system("已记住该条信息")
            self._hide_confirm()
            self._mode = "chat"
            return

        msg_type = result.get("type", "message")
        if msg_type == "confirm_restart":
            self.pending_restart = {"service_id": result.get("service_id", "")}
            self.pending_write = None
            self._append_assistant(result.get("message", "确认重启？"))
            self.confirm_label.setText(f"待确认：重启服务 {self.pending_restart['service_id']}")
            self.confirm_frame.show()
            self.confirm_btn.show()
            self.cancel_btn.show()
            return
        if msg_type == "error":
            self._append_system(result.get("message", str(result)))
            self._set_usage(result.get("usage"))
            return

        for notice in result.get("compaction_notices") or []:
            self._append_system(notice)

        self._append_assistant(result.get("message") or result.get("reply") or str(result))
        self._set_usage(result.get("usage"))
        auto_saved = result.get("auto_saved") or []
        if auto_saved:
            self._append_system(f"已自动记住 {len(auto_saved)} 条信息")
        suggestions = result.get("memory_suggestions") or []
        if suggestions:
            self.pending_memory = suggestions[0]
            self.pending_restart = None
            self.pending_write = None
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
        self._mode = "pending"
        self.confirm_btn.setText("确认执行")
        self._bridge.submit(self.service.pending_file_op(self.conversation_id))

    def _on_pending_op(self, data: dict) -> None:
        if data.get("pending"):
            self.pending_write = data
            self.pending_restart = None
            self.confirm_label.setText("待确认：执行文件写操作")
            self.confirm_frame.show()
            self.confirm_btn.show()
            self.cancel_btn.show()

    def _hide_confirm(self) -> None:
        self.pending_restart = None
        self.pending_write = None
        self.pending_memory = None
        self.confirm_label.setText("")
        self.confirm_btn.setText("确认执行")
        self.confirm_frame.hide()
        self.confirm_btn.hide()
        self.cancel_btn.hide()

    def _append_user(self, text: str) -> None:
        self._append_html(format_user_message(text))

    def _append_assistant(self, text: str) -> None:
        self._append_html(format_assistant_message(text))

    def _append_system(self, text: str) -> None:
        self._append_html(format_system_message(text))
