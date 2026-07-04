from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget


class WordWrapLabel(QLabel):
    """QLabel that reserves correct height for wrapped text on resize."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_height()

    def setText(self, text: str) -> None:
        super().setText(text)
        QTimer.singleShot(0, self._sync_height)

    def _sync_height(self) -> None:
        width = self.width()
        if width <= 0 and self.parentWidget() is not None:
            width = max(self.parentWidget().width() - 48, 160)
        if width <= 0:
            return
        height = self._wrapped_height(width)
        self.setFixedHeight(height)
        self.updateGeometry()

    def _wrapped_height(self, width: int) -> int:
        margins = self.contentsMargins()
        text_width = max(40, width - margins.left() - margins.right())
        bounds = self.fontMetrics().boundingRect(
            0,
            0,
            text_width,
            0,
            int(Qt.TextFlag.TextWordWrap),
            self.text(),
        )
        return max(32, bounds.height() + margins.top() + margins.bottom() + 8)
