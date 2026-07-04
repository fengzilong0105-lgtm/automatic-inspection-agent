from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from agent.desktop.widgets.card import Card


class ServicesListView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._filter = "ok"
        self._summary: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        card = Card(padding=0)
        toolbar = QWidget()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(16, 12, 16, 8)
        self.back_btn = QPushButton("← 返回")
        self.back_btn.setObjectName("secondaryButton")
        self.title_label = QLabel("正常服务")
        self.title_label.setObjectName("sectionTitle")
        self.count_label = QLabel("")
        self.count_label.setObjectName("fieldLabel")
        toolbar_layout.addWidget(self.back_btn)
        toolbar_layout.addWidget(self.title_label)
        toolbar_layout.addWidget(self.count_label)
        toolbar_layout.addStretch()

        search_row = QWidget()
        search_layout = QHBoxLayout(search_row)
        search_layout.setContentsMargins(16, 0, 16, 8)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("按服务名称或 ID 搜索…")
        search_layout.addWidget(self.search_input)

        self.table = QTableWidget(0, 5)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setHorizontalHeaderLabels(["服务", "类型", "运行", "健康", "详情"])
        self.table.horizontalHeader().setStretchLastSection(True)

        card.content_layout.setSpacing(0)
        card.content_layout.addWidget(toolbar)
        card.content_layout.addWidget(search_row)
        card.content_layout.addWidget(self.table)
        layout.addWidget(card, 1)

        self.search_input.textChanged.connect(self._render_table)

    def show_list(self, filter_kind: str, summary: list[dict]) -> None:
        self._filter = filter_kind
        self._summary = summary
        self.title_label.setText("正常服务" if filter_kind == "ok" else "异常服务")
        self.search_input.clear()
        self._render_table()

    def filtered_items(self) -> list[dict]:
        query = self.search_input.text().strip().lower()
        items = [item for item in self._summary if self._match_filter(item)]
        if not query:
            return items
        return [
            item
            for item in items
            if query in (item.get("service", {}).get("name") or "").lower()
            or query in (item.get("service", {}).get("id") or "").lower()
        ]

    def _match_filter(self, item: dict) -> bool:
        if self._filter == "ok":
            return HomePageLogic.is_ok(item)
        return HomePageLogic.is_bad(item)

    def _render_table(self) -> None:
        items = self.filtered_items()
        self.count_label.setText(f"共 {len(items)} 个")
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            svc = item.get("service", {})
            status = item.get("status", {})
            running = status.get("running")
            health = status.get("health_ok")
            self.table.setItem(row, 0, QTableWidgetItem(svc.get("name") or svc.get("id", "")))
            self.table.setItem(row, 1, QTableWidgetItem(str(svc.get("type", ""))))
            self.table.setItem(
                row,
                2,
                QTableWidgetItem(
                    "运行中" if running else "已停止" if running is False else "未知"
                ),
            )
            health_text = "正常" if health is True else "异常" if health is False else "未检测"
            self.table.setItem(row, 3, QTableWidgetItem(health_text))
            self.table.setItem(row, 4, QTableWidgetItem(status.get("detail", "")))


class HomePageLogic:
    @staticmethod
    def is_ok(item: dict) -> bool:
        status = item.get("status", {})
        if status.get("running") is not True:
            return False
        if status.get("health_ok") is False:
            return False
        return True

    @staticmethod
    def is_bad(item: dict) -> bool:
        status = item.get("status", {})
        running = status.get("running")
        if running is None:
            return False
        return not HomePageLogic.is_ok(item)
