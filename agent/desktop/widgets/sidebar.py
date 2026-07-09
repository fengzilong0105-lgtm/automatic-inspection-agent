from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from agent.brand import PRODUCT_NAME, PRODUCT_SUBTITLE
from agent.desktop.assets import load_logo_pixmap

NAV_ITEMS: list[tuple[str, str]] = [
    ("home", "概览"),
    ("incidents", "告警"),
    ("cases", "问题报告"),
    ("settings", "设置"),
]


class Sidebar(QWidget):
    page_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(200)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 16, 8, 16)
        layout.setSpacing(4)

        brand_wrap = QWidget()
        brand_layout = QHBoxLayout(brand_wrap)
        brand_layout.setContentsMargins(8, 4, 8, 8)
        brand_layout.setSpacing(10)

        logo = QLabel()
        logo.setObjectName("sidebarLogo")
        logo_pixmap = load_logo_pixmap(36)
        if logo_pixmap is not None:
            logo.setPixmap(logo_pixmap)
            logo.setFixedSize(36, 36)

        brand_text = QVBoxLayout()
        brand_text.setSpacing(2)
        brand_name = QLabel(PRODUCT_NAME)
        brand_name.setObjectName("sidebarBrand")
        brand_sub = QLabel(PRODUCT_SUBTITLE)
        brand_sub.setObjectName("sidebarSubtitle")
        brand_text.addWidget(brand_name)
        brand_text.addWidget(brand_sub)

        brand_layout.addWidget(logo)
        brand_layout.addLayout(brand_text, 1)
        layout.addWidget(brand_wrap)
        layout.addSpacing(8)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[QPushButton] = []

        for index, (_key, label) in enumerate(NAV_ITEMS):
            btn = QPushButton(label)
            btn.setObjectName("navButton")
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked=False, i=index: self._on_nav(i))
            self._group.addButton(btn, index)
            self._buttons.append(btn)
            layout.addWidget(btn)

        layout.addStretch()
        self._buttons[0].setChecked(True)

    def _on_nav(self, index: int) -> None:
        self._buttons[index].setChecked(True)
        self.page_changed.emit(index)

    def set_current_index(self, index: int) -> None:
        if 0 <= index < len(self._buttons):
            self._buttons[index].setChecked(True)
