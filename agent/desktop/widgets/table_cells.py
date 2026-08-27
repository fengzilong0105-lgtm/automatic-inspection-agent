from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTableWidgetItem, QWidget

# QTableWidget::item vertical padding (4+4) + 28px button + 2px border ≈ 38; 44 leaves headroom.
TABLE_ACTION_ROW_HEIGHT = 44


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


def make_table_action_button(
    text: str,
    *,
    object_name: str = "tableActionButton",
    on_click: Callable[[bool], None] | None = None,
) -> QPushButton:
    btn = QPushButton(text)
    btn.setObjectName(object_name)
    btn.setFixedHeight(28)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click is not None:
        btn.clicked.connect(on_click)
    return btn


def make_table_action_cell(*buttons: QPushButton) -> QWidget:
    """Cell container for table row actions — sized to match TABLE_ACTION_ROW_HEIGHT."""
    wrap = QWidget()
    wrap.setAutoFillBackground(False)
    layout = QHBoxLayout(wrap)
    layout.setContentsMargins(6, 0, 8, 0)
    layout.setSpacing(6)
    layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    for btn in buttons:
        layout.addWidget(btn)
    return wrap


def make_text_item(text: str, *, tooltip: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if tooltip:
        item.setToolTip(tooltip)
    return item
