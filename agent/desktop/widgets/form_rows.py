from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QWidget

_FIELD_LABEL_WIDTH = 100


def style_input(widget: QLineEdit | QComboBox) -> None:
    widget.setMinimumHeight(36)
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


def labeled_field_row(label_text: str, widget: QWidget, *, label_width: int = _FIELD_LABEL_WIDTH) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(12)
    label = QLabel(label_text)
    label.setFixedWidth(label_width)
    label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    row.addWidget(label, 0)
    row.addWidget(widget, 1)
    return row
