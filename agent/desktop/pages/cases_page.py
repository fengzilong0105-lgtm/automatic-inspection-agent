from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from agent.desktop.async_call import AsyncCall
from agent.desktop.formatters import (
    case_status_colors,
    format_case_status,
    format_datetime,
    format_incident_severity,
    incident_severity_colors,
)
from agent.desktop.pages.case_editor_page import CaseEditorPage
from agent.desktop.widgets.card import Card
from agent.desktop.widgets.table_cells import make_badge, make_text_item
from agent.services.agent_service import AgentService


class CasesPage(QWidget):
    def __init__(self, service: AgentService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self._cases: list[dict] = []
        self._pending_open_id: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(0)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_list_view())
        self.editor_page = CaseEditorPage(service)
        self.editor_page.back_requested.connect(self._show_list)
        self.editor_page.case_changed.connect(self._on_editor_case_changed)
        self.stack.addWidget(self.editor_page)
        layout.addWidget(self.stack, 1)

        self._list_bridge = AsyncCall(self)
        self._list_bridge.finished.connect(self._render_list)
        self._list_bridge.failed.connect(
            lambda msg: self.list_status.setText(f"加载失败: {msg}")
        )

    def _build_list_view(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        list_card = Card(padding=0)
        list_header = QWidget()
        list_header.setObjectName("caseTableHeader")
        list_header_layout = QHBoxLayout(list_header)
        list_header_layout.setContentsMargins(16, 12, 16, 12)
        list_title = QLabel("问题报告")
        list_title.setObjectName("sectionTitle")
        self.list_status = QLabel("")
        self.list_status.setObjectName("fieldLabel")
        refresh_btn = QPushButton("刷新")
        refresh_btn.setObjectName("secondaryButton")
        refresh_btn.clicked.connect(self.refresh)
        list_header_layout.addWidget(list_title)
        list_header_layout.addWidget(self.list_status)
        list_header_layout.addStretch()
        list_header_layout.addWidget(refresh_btn)

        self.table = QTableWidget(0, 8)
        self.table.setObjectName("caseTable")
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.setHorizontalHeaderLabels(
            ["更新时间", "标题", "服务", "级别", "状态", "负责人", "发起人", "操作"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        header_view = self.table.horizontalHeader()
        header_view.setStretchLastSection(False)
        header_view.setMinimumSectionSize(64)
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(3, 72)
        self.table.setColumnWidth(4, 96)
        self.table.setColumnWidth(7, 100)

        list_card.content_layout.setSpacing(0)
        list_card.content_layout.addWidget(list_header)
        list_card.content_layout.addWidget(self.table)
        layout.addWidget(list_card, 1)
        return page

    def refresh(self) -> None:
        if self.stack.currentIndex() == 0:
            self.list_status.setText("加载中…")
        self._list_bridge.submit(self.service.list_problem_cases())

    def on_page_shown(self) -> None:
        self.refresh()

    def open_case(self, case_id: str) -> None:
        self._pending_open_id = case_id
        self.refresh()

    def _show_list(self) -> None:
        self.stack.setCurrentIndex(0)
        self.refresh()

    def _open_editor(self, case_id: str) -> None:
        if not case_id:
            return
        self.stack.setCurrentIndex(1)
        self.editor_page.load_case(case_id)

    def _on_editor_case_changed(self, _case_id: str) -> None:
        self.refresh()

    def _render_list(self, cases: list) -> None:
        self._cases = cases
        self.list_status.setText(f"共 {len(cases)} 条")
        self.table.setRowCount(len(cases))

        open_id = self._pending_open_id
        for row, item in enumerate(cases):
            case_id = item.get("id", "")

            updated = format_datetime(item.get("updated_at"))
            title = item.get("title", "") or "-"
            service_id = item.get("service_id", "") or "-"
            initiator = item.get("initiator", "") or "-"
            assignee = item.get("assignee", "") or "-"

            time_item = make_text_item(updated, tooltip=updated)
            time_item.setData(Qt.ItemDataRole.UserRole, case_id)
            self.table.setItem(row, 0, time_item)
            self.table.setItem(row, 1, make_text_item(title, tooltip=title))
            self.table.setItem(row, 2, make_text_item(service_id, tooltip=service_id))

            sev = format_incident_severity(item.get("severity"))
            sev_bg, sev_fg = incident_severity_colors(item.get("severity"))
            self.table.setCellWidget(row, 3, make_badge(sev, sev_bg, sev_fg))

            st = format_case_status(item.get("status"))
            st_bg, st_fg = case_status_colors(item.get("status"))
            self.table.setCellWidget(row, 4, make_badge(st, st_bg, st_fg))

            self.table.setItem(row, 5, make_text_item(assignee, tooltip=assignee))
            self.table.setItem(row, 6, make_text_item(initiator, tooltip=initiator))

            actions = QWidget()
            actions_layout = QHBoxLayout(actions)
            actions_layout.setContentsMargins(4, 2, 4, 2)
            actions_layout.setSpacing(0)
            edit_btn = QPushButton("报告编辑")
            edit_btn.setObjectName("tableActionButton")
            edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            edit_btn.clicked.connect(lambda _checked=False, cid=case_id: self._open_editor(cid))
            actions_layout.addWidget(edit_btn)
            self.table.setCellWidget(row, 7, actions)

        self.table.resizeColumnToContents(0)
        self._pending_open_id = None

        if open_id:
            self._open_editor(open_id)
