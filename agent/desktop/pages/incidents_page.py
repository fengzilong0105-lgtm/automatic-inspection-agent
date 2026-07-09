from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from agent.desktop.async_call import AsyncCall
from agent.desktop.formatters import (
    format_datetime,
    format_incident_severity,
    format_incident_status,
    incident_severity_colors,
    incident_status_colors,
)
from agent.desktop.widgets.card import Card
from agent.desktop.widgets.stat_card import StatCard
from agent.desktop.widgets.table_cells import make_badge, make_text_item
from agent.services.agent_service import AgentService


class IncidentsPage(QWidget):
    case_created = Signal(str)

    def __init__(self, service: AgentService, parent=None) -> None:
        super().__init__(parent)
        self.service = service

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        summary_row = QHBoxLayout()
        summary_row.setSpacing(12)
        self.stat_total = StatCard("告警总数", "0", accent="primary")
        self.stat_open = StatCard("未处理", "0", accent="danger")
        self.stat_p0 = StatCard("P0 严重", "0", accent="danger")
        self.stat_p1 = StatCard("P1 重要", "0", accent="warning")
        for card in (self.stat_total, self.stat_open, self.stat_p0, self.stat_p1):
            summary_row.addWidget(card, 1)
        layout.addLayout(summary_row)

        card = Card(padding=0)
        card.setObjectName("incidentCard")
        header = QWidget()
        header.setObjectName("incidentTableHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 12, 16, 12)
        title = QLabel("告警记录")
        title.setObjectName("sectionTitle")
        self.status = QLabel("")
        self.status.setObjectName("fieldLabel")
        refresh_btn = QPushButton("刷新")
        refresh_btn.setObjectName("secondaryButton")
        refresh_btn.clicked.connect(self.refresh)
        report_btn = QPushButton("生成报告")
        report_btn.setObjectName("primaryButton")
        report_btn.clicked.connect(self._generate_report)
        header_layout.addWidget(title)
        header_layout.addWidget(self.status)
        header_layout.addStretch()
        header_layout.addWidget(report_btn)
        header_layout.addWidget(refresh_btn)

        self.table = QTableWidget(0, 5)
        self.table.setObjectName("incidentTable")
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.setHorizontalHeaderLabels(["时间", "服务", "标题", "级别", "状态"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)

        header_view = self.table.horizontalHeader()
        header_view.setStretchLastSection(False)
        header_view.setMinimumSectionSize(64)
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(3, 80)
        self.table.setColumnWidth(4, 96)

        card.content_layout.setSpacing(0)
        card.content_layout.addWidget(header)
        card.content_layout.addWidget(self.table)
        layout.addWidget(card, 1)

        self._bridge = AsyncCall(self)
        self._bridge.finished.connect(self._render)
        self._bridge.failed.connect(lambda msg: self.status.setText(f"加载失败: {msg}"))

        self._report_bridge = AsyncCall(self)
        self._report_bridge.finished.connect(self._on_report_created)
        self._report_bridge.failed.connect(
            lambda msg: self.status.setText(f"生成报告失败: {msg}")
        )

    def _selected_incident_id(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if not item:
            return None
        incident_id = item.data(Qt.ItemDataRole.UserRole)
        return str(incident_id) if incident_id else None

    def _generate_report(self) -> None:
        incident_id = self._selected_incident_id()
        if not incident_id:
            self.status.setText("请先选择一条告警")
            return
        self.status.setText("正在生成报告（调用 LLM，请稍候）…")
        self._report_bridge.submit(
            self.service.create_problem_case_from_incident(incident_id)
        )

    def _on_report_created(self, case: dict) -> None:
        case_id = case.get("id", "")
        title = case.get("title", "")
        self.status.setText(f"报告已就绪：{title}")
        if case_id:
            self.case_created.emit(str(case_id))

    def refresh(self) -> None:
        self.status.setText("加载中…")
        self._bridge.submit(self.service.list_incidents())

    def _render(self, incidents: list) -> None:
        self.status.setText(f"共 {len(incidents)} 条")
        open_count = 0
        p0_count = 0
        p1_count = 0
        for item in incidents:
            status_text = format_incident_status(item.get("status"))
            severity_text = format_incident_severity(item.get("severity"))
            if status_text == "未处理":
                open_count += 1
            if severity_text == "P0":
                p0_count += 1
            elif severity_text == "P1":
                p1_count += 1

        self.stat_total.set_value(str(len(incidents)))
        self.stat_open.set_value(str(open_count))
        self.stat_p0.set_value(str(p0_count))
        self.stat_p1.set_value(str(p1_count))

        self.table.setRowCount(len(incidents))
        for row, item in enumerate(incidents):
            created = format_datetime(item.get("created_at"))
            service_id = item.get("service_id", "") or "-"
            title = item.get("title", "") or "-"
            summary = item.get("summary", "") or ""
            tooltip = f"{title}\n{summary}".strip()

            time_item = make_text_item(created, tooltip=created)
            time_item.setData(Qt.ItemDataRole.UserRole, item.get("id", ""))
            self.table.setItem(row, 0, time_item)
            self.table.setItem(row, 1, make_text_item(service_id, tooltip=service_id))
            self.table.setItem(row, 2, make_text_item(title, tooltip=tooltip))

            sev = format_incident_severity(item.get("severity"))
            sev_bg, sev_fg = incident_severity_colors(item.get("severity"))
            self.table.setCellWidget(row, 3, make_badge(sev, sev_bg, sev_fg))

            st = format_incident_status(item.get("status"))
            st_bg, st_fg = incident_status_colors(item.get("status"))
            self.table.setCellWidget(row, 4, make_badge(st, st_bg, st_fg))

        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(1)
