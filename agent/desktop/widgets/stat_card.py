from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel

from agent.desktop.widgets.card import Card


class StatCard(Card):
    def __init__(
        self,
        title: str,
        value: str = "0",
        *,
        hint: str = "",
        accent: str = "default",
        parent=None,
    ) -> None:
        super().__init__(parent, padding=14)
        self.setProperty("accent", accent)
        self.setProperty("clickable", False)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("statTitle")

        self.value_label = QLabel(value)
        self.value_label.setObjectName("statValue")

        layout = self.content_layout
        layout.setSpacing(4)
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        if hint:
            self.hint_label = QLabel(hint)
            self.hint_label.setObjectName("statHint")
            layout.addWidget(self.hint_label)

        self._refresh_style()

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)

    def _refresh_style(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)


class ClickableStatCard(StatCard):
    clicked = Signal()

    def __init__(
        self,
        title: str,
        value: str = "0",
        *,
        hint: str = "点击查看列表",
        accent: str = "default",
        parent=None,
    ) -> None:
        super().__init__(title, value, hint=hint, accent=accent, parent=parent)
        self.setProperty("clickable", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        for child in self.findChildren(QLabel):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._refresh_style()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)
