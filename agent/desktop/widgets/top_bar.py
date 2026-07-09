from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QWidget

PAGE_TITLES = ["首页", "告警", "问题报告", "设置"]


class TopBar(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("topBar")
        self.setFixedHeight(56)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(12)

        self.page_title = QLabel("首页")
        self.page_title.setObjectName("pageTitle")
        layout.addWidget(self.page_title)
        layout.addStretch()

        host_label = QLabel("当前主机")
        host_label.setObjectName("fieldLabel")
        self.host_combo = QComboBox()
        self.host_combo.setObjectName("hostCombo")
        self.host_combo.setMinimumWidth(240)

        self.inspect_btn = QPushButton("立即巡检")
        self.inspect_btn.setObjectName("primaryButton")
        self.scan_btn = QPushButton("扫描服务")
        self.scan_btn.setObjectName("secondaryButton")
        self.wizard_btn = QPushButton("初始化向导")
        self.wizard_btn.setObjectName("secondaryButton")
        self.wizard_btn.setVisible(False)

        layout.addWidget(host_label)
        layout.addWidget(self.host_combo)
        layout.addWidget(self.inspect_btn)
        layout.addWidget(self.scan_btn)
        layout.addWidget(self.wizard_btn)

    def set_page_index(self, index: int) -> None:
        if 0 <= index < len(PAGE_TITLES):
            self.page_title.setText(PAGE_TITLES[index])

    def set_setup_needed(self, needed: bool) -> None:
        self.wizard_btn.setVisible(needed)
