from __future__ import annotations

from PySide6.QtWidgets import (
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
from agent.services.agent_service import AgentService

SESSION_ID = "desktop-default"


class ChatPanel(QWidget):
    """Embeddable AI chat panel for the overview page."""

    def __init__(self, service: AgentService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.pending_restart: dict | None = None
        self.pending_write: dict | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("对话运维")
        title.setObjectName("sectionTitle")
        clear_btn = QPushButton("清空对话")
        clear_btn.setObjectName("secondaryButton")
        head.addWidget(title)
        head.addStretch()
        head.addWidget(clear_btn)
        layout.addLayout(head)

        self.history = QTextEdit()
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
        self.confirm_btn.clicked.connect(self.confirm_pending)
        self.cancel_btn.clicked.connect(self._hide_confirm)

        self._mode = "chat"

    def _on_async_finished(self, result) -> None:
        if self._mode == "pending":
            self._on_pending_op(result)
        else:
            self._on_reply(result)

    def send_message(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self._append_user(text)
        self.input.clear()
        self._mode = "chat"
        self._bridge.submit(
            self.service.chat_message(text, session_id=SESSION_ID, confirmed=False)
        )

    def clear_chat(self) -> None:
        self._bridge.submit(self.service.chat_clear(SESSION_ID))
        self.history.clear()
        self._hide_confirm()

    def confirm_pending(self) -> None:
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
                    SESSION_ID,
                )
            )

    def _on_reply(self, result: dict) -> None:
        if self._mode == "restart":
            ok = result.get("success")
            self._append_system(
                "重启成功" if ok else f"重启失败: {result.get('stderr') or result.get('stdout')}"
            )
            self._hide_confirm()
            return
        if self._mode == "write":
            ok = result.get("success")
            self._append_system(
                "写操作成功" if ok else f"写操作失败: {result.get('stderr') or result.get('stdout')}"
            )
            self._hide_confirm()
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
            return

        self._append_assistant(result.get("message") or result.get("reply") or str(result))
        self._mode = "pending"
        self._bridge.submit(self.service.pending_file_op(SESSION_ID))

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
        self.confirm_label.setText("")
        self.confirm_frame.hide()
        self.confirm_btn.hide()
        self.cancel_btn.hide()

    def _append_user(self, text: str) -> None:
        self.history.append(f"<p style='color:#1890FF;margin:4px 0;'><b>你</b> {text}</p>")

    def _append_assistant(self, text: str) -> None:
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.history.append(
            f"<p style='color:#262626;margin:4px 0;'><b>Agent</b> {escaped}</p>"
        )

    def _append_system(self, text: str) -> None:
        self.history.append(f"<p style='color:#8C8C8C;margin:4px 0;'><i>{text}</i></p>")
