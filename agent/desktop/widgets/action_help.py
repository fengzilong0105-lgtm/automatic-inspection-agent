from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget


class ActionWithHint(QWidget):
    """Action button with a small '?' badge at the top-right corner."""

    _HINT_SIZE = 14

    def __init__(
        self,
        button: QPushButton,
        hint: str,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.button = button

        variant = "primary" if button.objectName() == "primaryButton" else "secondary"
        self._hint_label = QLabel("?", self)
        self._hint_label.setObjectName("actionHelpHint")
        self._hint_label.setProperty("hintVariant", variant)
        self._hint_label.setToolTip(hint)
        self._hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint_label.setFixedSize(self._HINT_SIZE, self._HINT_SIZE)
        self._hint_label.setCursor(Qt.CursorShape.WhatsThisCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 4, 0)
        layout.setSpacing(0)
        layout.addWidget(self.button)

        self.button.installEventFilter(self)
        self._position_hint()

    def set_hint(self, hint: str) -> None:
        self._hint_label.setToolTip(hint)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_hint()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.button and event.type() == QEvent.Type.Resize:
            self._position_hint()
        return super().eventFilter(watched, event)

    def _position_hint(self) -> None:
        btn_rect = self.button.geometry()
        offset = self._HINT_SIZE // 2
        x = btn_rect.right() - offset
        y = btn_rect.top() - offset
        self._hint_label.move(x, y)
        self._hint_label.raise_()
