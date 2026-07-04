from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QTableWidgetItem, QWidget, QHBoxLayout


def make_badge(text: str, bg: str, fg: str) -> QWidget:
    wrap = QWidget()
    layout = QHBoxLayout(wrap)
    layout.setContentsMargins(4, 2, 4, 2)
    label = QLabel(text)
    label.setObjectName("tableBadge")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setStyleSheet(
        f"background-color: {bg}; color: {fg}; border-radius: 4px;"
        "padding: 2px 8px; font-size: 12px; font-weight: 600;"
    )
    layout.addWidget(label)
    layout.addStretch()
    return wrap


def make_text_item(text: str, *, tooltip: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if tooltip:
        item.setToolTip(tooltip)
    return item
