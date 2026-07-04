from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from agent.desktop.widgets.chat_panel import ChatPanel
from agent.services.agent_service import AgentService


class ChatPage(QWidget):
    """Standalone chat page (legacy); chat is embedded on the overview page."""

    def __init__(self, service: AgentService, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(ChatPanel(service), 1)
