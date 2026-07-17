from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from agent.desktop.widgets.card import Card
from agent.desktop.widgets.table_cells import make_text_item

_FILTER_TITLES = {
    "ok": "正常服务",
    "bad": "异常服务",
    "disabled": "停用巡检的服务",
}


class ServicesListView(QWidget):
    enable_service = Signal(str)
    remove_service = Signal(str)

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

        self.table = QTableWidget(0, 6)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setHorizontalHeaderLabels(["服务", "类型", "运行", "健康", "详情", "操作"])
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        # 服务名拉宽，避免 zstd-jni-1.5.6-4 一类被截成 zstd-...
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 240)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 72)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 72)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(3, 72)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        # 操作列固定，保证「启用巡检」「移除」完整可见
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(5, 200)

        card.content_layout.setSpacing(0)
        card.content_layout.addWidget(toolbar)
        card.content_layout.addWidget(search_row)
        card.content_layout.addWidget(self.table)
        layout.addWidget(card, 1)

        self.search_input.textChanged.connect(self._render_table)

    def show_list(self, filter_kind: str, summary: list[dict]) -> None:
        self._filter = filter_kind
        self._summary = summary
        self.title_label.setText(_FILTER_TITLES.get(filter_kind, "服务列表"))
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
        if self._filter == "disabled":
            return bool(item.get("disabled"))
        if item.get("disabled"):
            return False
        if self._filter == "ok":
            return HomePageLogic.is_ok(item)
        return HomePageLogic.is_bad(item)

    def _render_table(self) -> None:
        items = self.filtered_items()
        self.count_label.setText(f"共 {len(items)} 个")
        self.table.setRowCount(len(items))
        # 每次渲染后重新钉死列宽，避免 Stretch 挤掉操作列
        self.table.setColumnWidth(0, 240)
        self.table.setColumnWidth(5, 200)
        for row, item in enumerate(items):
            svc = item.get("service", {})
            status = item.get("status", {})
            running = status.get("running")
            health = status.get("health_ok")
            name = svc.get("name") or svc.get("id", "")
            self.table.setItem(row, 0, make_text_item(name, tooltip=name))
            self.table.setItem(row, 1, make_text_item(str(svc.get("type", ""))))
            if item.get("disabled"):
                running_text = "未检测"
            else:
                running_text = "运行中" if running else "已停止" if running is False else "未知"
            self.table.setItem(row, 2, make_text_item(running_text))
            health_text = "正常" if health is True else "异常" if health is False else "未检测"
            self.table.setItem(row, 3, make_text_item(health_text))
            detail = status.get("detail", "") or ""
            self.table.setItem(row, 4, make_text_item(detail, tooltip=detail))

            self.table.setRowHeight(row, 46)
            if item.get("disabled"):
                service_id = svc.get("id", "")
                actions = QWidget()
                actions.setAutoFillBackground(False)
                actions_layout = QHBoxLayout(actions)
                actions_layout.setContentsMargins(6, 4, 6, 4)
                actions_layout.setSpacing(6)
                enable_btn = QPushButton("启用巡检")
                enable_btn.setObjectName("tableActionButton")
                enable_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                enable_btn.clicked.connect(
                    lambda _checked=False, sid=service_id: self.enable_service.emit(sid)
                )
                remove_btn = QPushButton("移除")
                remove_btn.setObjectName("tableActionButtonDanger")
                remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                remove_btn.clicked.connect(
                    lambda _checked=False, sid=service_id: self.remove_service.emit(sid)
                )
                actions_layout.addWidget(enable_btn)
                actions_layout.addWidget(remove_btn)
                self.table.setCellWidget(row, 5, actions)
            else:
                self.table.removeCellWidget(row, 5)
                self.table.setItem(row, 5, QTableWidgetItem(""))


class HomePageLogic:
    @staticmethod
    def is_ok(item: dict) -> bool:
        if item.get("disabled"):
            return False
        status = item.get("status", {})
        if status.get("running") is not True:
            return False
        if status.get("health_ok") is False:
            return False
        return True

    @staticmethod
    def is_bad(item: dict) -> bool:
        if item.get("disabled"):
            return False
        status = item.get("status", {})
        running = status.get("running")
        if running is None:
            return False
        return not HomePageLogic.is_ok(item)
