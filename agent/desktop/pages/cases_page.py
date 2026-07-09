from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTextEdit,
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
    is_case_closed,
)
from agent.desktop.widgets.card import Card
from agent.desktop.widgets.form_rows import style_input
from agent.desktop.widgets.table_cells import make_badge, make_text_item
from agent.services.agent_service import AgentService


class CasesPage(QWidget):
    def __init__(self, service: AgentService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self._cases: list[dict] = []
        self._current_case_id: str | None = None
        self._pending_case_id: str | None = None
        self._current_doc_url: str | None = None
        self._current_case: dict = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setObjectName("casesSplitter")

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

        self.table = QTableWidget(0, 7)
        self.table.setObjectName("caseTable")
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.setHorizontalHeaderLabels(
            ["更新时间", "标题", "服务", "级别", "状态", "负责人", "发起人"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._on_row_selected)

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
        self.table.setColumnWidth(3, 72)
        self.table.setColumnWidth(4, 96)

        list_card.content_layout.setSpacing(0)
        list_card.content_layout.addWidget(list_header)
        list_card.content_layout.addWidget(self.table)

        editor_card = Card()
        editor_title = QLabel("报告编辑")
        editor_title.setObjectName("sectionTitle")
        self.editor_status = QLabel("选择一条报告进行预览与编辑")
        self.editor_status.setObjectName("fieldLabel")

        self.title_input = QLineEdit()
        self.initiator_input = QLineEdit()
        self.assignee_input = QLineEdit()
        self.assignee_input.setPlaceholderText("处理负责人")
        self.description_input = QTextEdit()
        self.description_input.setPlaceholderText("问题描述")
        self.description_input.setMaximumHeight(80)
        self.markdown_input = QTextEdit()
        self.markdown_input.setPlaceholderText("报告 Markdown 正文")
        self.markdown_input.setMinimumHeight(220)
        for field in (self.title_input, self.initiator_input, self.assignee_input):
            style_input(field)
        style_input(self.description_input)
        style_input(self.markdown_input)

        meta_form = QFormLayout()
        meta_form.setSpacing(8)
        meta_form.addRow("标题", self.title_input)
        meta_form.addRow("发起人", self.initiator_input)
        meta_form.addRow("负责人", self.assignee_input)
        meta_form.addRow("描述", self.description_input)

        save_btn = QPushButton("保存")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self._save_current)
        self.save_btn = save_btn
        self.publish_btn = QPushButton("发布到飞书")
        self.publish_btn.setObjectName("secondaryButton")
        self.publish_btn.clicked.connect(self._publish_current)
        self.open_doc_btn = QPushButton("打开飞书文档")
        self.open_doc_btn.setObjectName("secondaryButton")
        self.open_doc_btn.clicked.connect(self._open_doc_url)
        self.open_doc_btn.setVisible(False)
        self.sync_ticket_btn = QPushButton("同步工单")
        self.sync_ticket_btn.setObjectName("secondaryButton")
        self.sync_ticket_btn.clicked.connect(self._sync_ticket)
        self.close_btn = QPushButton("关闭案例")
        self.close_btn.setObjectName("secondaryButton")
        self.close_btn.clicked.connect(self._close_current)
        editor_actions = QHBoxLayout()
        editor_actions.addWidget(self.editor_status)
        editor_actions.addStretch()
        editor_actions.addWidget(self.open_doc_btn)
        editor_actions.addWidget(self.sync_ticket_btn)
        editor_actions.addWidget(self.close_btn)
        editor_actions.addWidget(self.publish_btn)
        editor_actions.addWidget(save_btn)

        editor_card.content_layout.addWidget(editor_title)
        editor_card.content_layout.addLayout(meta_form)
        editor_card.content_layout.addWidget(QLabel("Markdown 报告"))
        editor_card.content_layout.addWidget(self.markdown_input, 1)
        editor_card.content_layout.addLayout(editor_actions)

        splitter.addWidget(list_card)
        splitter.addWidget(editor_card)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)
        layout.addWidget(splitter, 1)

        self._list_bridge = AsyncCall(self)
        self._list_bridge.finished.connect(self._render_list)
        self._list_bridge.failed.connect(
            lambda msg: self.list_status.setText(f"加载失败: {msg}")
        )

        self._load_bridge = AsyncCall(self)
        self._load_bridge.finished.connect(self._render_editor)
        self._load_bridge.failed.connect(
            lambda msg: self.editor_status.setText(f"加载失败: {msg}")
        )

        self._save_bridge = AsyncCall(self)
        self._save_bridge.finished.connect(self._on_saved)
        self._save_bridge.failed.connect(
            lambda msg: self.editor_status.setText(f"保存失败: {msg}")
        )

        self._publish_bridge = AsyncCall(self)
        self._publish_bridge.finished.connect(self._on_published)
        self._publish_bridge.failed.connect(self._on_publish_failed)

        self._sync_bridge = AsyncCall(self)
        self._sync_bridge.finished.connect(self._on_synced)
        self._sync_bridge.failed.connect(
            lambda msg: self.editor_status.setText(f"同步失败: {msg}")
        )

        self._close_bridge = AsyncCall(self)
        self._close_bridge.finished.connect(self._on_closed)
        self._close_bridge.failed.connect(self._on_close_failed)

        self._set_editor_enabled(False)

    def refresh(self) -> None:
        self.list_status.setText("加载中…")
        self._list_bridge.submit(self.service.list_problem_cases())

    def open_case(self, case_id: str) -> None:
        self._pending_case_id = case_id
        self.refresh()

    def _set_editor_enabled(self, enabled: bool) -> None:
        for widget in (
            self.title_input,
            self.initiator_input,
            self.assignee_input,
            self.description_input,
            self.markdown_input,
        ):
            widget.setEnabled(enabled)

    def _render_list(self, cases: list) -> None:
        self._cases = cases
        self.list_status.setText(f"共 {len(cases)} 条")
        self.table.setRowCount(len(cases))

        target_row = -1
        for row, item in enumerate(cases):
            case_id = item.get("id", "")
            if case_id and case_id == self._pending_case_id:
                target_row = row

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

        self.table.resizeColumnToContents(0)
        self._pending_case_id = None

        if target_row >= 0:
            self.table.selectRow(target_row)
        elif self.table.rowCount() > 0 and self._current_case_id is None:
            self.table.selectRow(0)

    def _on_row_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        if not item:
            return
        case_id = item.data(Qt.ItemDataRole.UserRole)
        if not case_id:
            return
        self._current_case_id = str(case_id)
        self.editor_status.setText("加载中…")
        self._load_bridge.submit(self.service.get_problem_case(self._current_case_id))

    def _apply_action_state(self, case: dict) -> None:
        closed = is_case_closed(case)
        has_ticket = bool(case.get("feishu_bitable_record_id"))
        has_doc = bool(self._current_doc_url)
        self.publish_btn.setText("更新飞书" if has_doc else "发布到飞书")
        self.save_btn.setEnabled(not closed)
        self.publish_btn.setEnabled(not closed)
        self.close_btn.setEnabled(not closed)
        self.sync_ticket_btn.setEnabled(has_ticket and not closed)
        self._set_editor_enabled(not closed)

    def _render_editor(self, case: dict) -> None:
        self._current_case = case
        self._current_case_id = case.get("id")
        self._current_doc_url = case.get("feishu_doc_url") or None
        self.title_input.setText(case.get("title", "") or "")
        self.initiator_input.setText(case.get("initiator", "") or "")
        self.assignee_input.setText(case.get("assignee", "") or "")
        self.description_input.setPlainText(case.get("description", "") or "")
        self.markdown_input.setPlainText(case.get("report_markdown", "") or "")
        status = format_case_status(case.get("status"))
        ticket_status = case.get("ticket_status") or "-"
        updated = format_datetime(case.get("updated_at"))
        doc_hint = ""
        if self._current_doc_url:
            doc_hint = " · 已发布飞书文档"
        self.editor_status.setText(
            f"状态：{status} · 工单：{ticket_status} · 更新于 {updated}{doc_hint}"
        )
        self._apply_action_state(case)
        self.open_doc_btn.setVisible(bool(self._current_doc_url))

    def _publish_current(self) -> None:
        if not self._current_case_id:
            self.editor_status.setText("请先选择一条报告")
            return
        if is_case_closed(self._current_case):
            self.editor_status.setText("案例已关闭，无法再次发布")
            return
        has_doc = bool(self._current_doc_url)
        if has_doc:
            prompt = (
                "将用当前报告内容覆盖更新已有飞书文档，并同步 Bitable 工单字段。"
                "文档链接不变，确认继续？"
            )
            title = "更新飞书"
        else:
            prompt = "将创建飞书文档、写入 Bitable 工单并发送群通知，确认继续？"
            title = "发布到飞书"
        answer = QMessageBox.question(
            self,
            title,
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.editor_status.setText("正在保存并更新飞书…" if has_doc else "正在保存并发布到飞书…")
        self.publish_btn.setEnabled(False)
        payload = {
            "title": self.title_input.text().strip(),
            "initiator": self.initiator_input.text().strip(),
            "assignee": self.assignee_input.text().strip(),
            "description": self.description_input.toPlainText().strip(),
            "report_markdown": self.markdown_input.toPlainText(),
        }
        self._publish_bridge.submit(
            self.service.publish_problem_case(self._current_case_id, payload)
        )

    def _on_published(self, case: dict) -> None:
        self._render_editor(case)
        if self._current_case_id:
            self._pending_case_id = self._current_case_id
        self.refresh()
        doc_url = case.get("feishu_doc_url") or ""
        record_id = case.get("feishu_bitable_record_id") or ""
        if doc_url or record_id:
            action = "更新完成" if self._current_doc_url and doc_url == self._current_doc_url else "发布完成"
            lines = [f"{action}："]
            if doc_url:
                lines.append(f"文档: {doc_url}")
            if record_id:
                lines.append(f"工单记录: {record_id}")
            QMessageBox.information(self, "发布成功", "\n".join(lines))

    def _open_doc_url(self) -> None:
        if self._current_doc_url:
            QDesktopServices.openUrl(QUrl(self._current_doc_url))

    def _on_publish_failed(self, msg: str) -> None:
        self.editor_status.setText(f"发布失败: {msg}")
        self._apply_action_state(self._current_case)

    def _sync_ticket(self) -> None:
        if not self._current_case_id:
            self.editor_status.setText("请先选择一条报告")
            return
        if is_case_closed(self._current_case):
            self.editor_status.setText("案例已关闭，无法同步工单")
            return
        self.editor_status.setText("正在从 Bitable 同步工单状态…")
        self._sync_bridge.submit(self.service.sync_problem_case_ticket(self._current_case_id))

    def _on_synced(self, case: dict) -> None:
        self._render_editor(case)
        if self._current_case_id:
            self._pending_case_id = self._current_case_id
        self.refresh()
        QMessageBox.information(
            self,
            "同步完成",
            f"工单状态：{case.get('ticket_status') or '-'}\n"
            f"负责人：{case.get('assignee') or '-'}",
        )

    def _close_current(self) -> None:
        if not self._current_case_id:
            self.editor_status.setText("请先选择一条报告")
            return
        if is_case_closed(self._current_case):
            self.editor_status.setText("案例已关闭")
            return
        answer = QMessageBox.question(
            self,
            "关闭案例",
            "将关闭本地案例、回写 Bitable 工单为「已关闭」，并联动解决关联告警。确认继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.editor_status.setText("正在关闭案例…")
        self.close_btn.setEnabled(False)
        self._close_bridge.submit(
            self.service.close_problem_case(
                self._current_case_id,
                assignee=self.assignee_input.text().strip() or None,
            )
        )

    def _on_closed(self, case: dict) -> None:
        self._render_editor(case)
        if self._current_case_id:
            self._pending_case_id = self._current_case_id
        self.refresh()
        QMessageBox.information(self, "案例已关闭", "案例已关闭，关联告警已标记为已解决。")

    def _on_close_failed(self, msg: str) -> None:
        self.editor_status.setText(f"关闭失败: {msg}")
        self._apply_action_state(self._current_case)

    def _save_current(self) -> None:
        if not self._current_case_id:
            self.editor_status.setText("请先选择一条报告")
            return
        payload = {
            "title": self.title_input.text().strip(),
            "initiator": self.initiator_input.text().strip(),
            "assignee": self.assignee_input.text().strip(),
            "description": self.description_input.toPlainText().strip(),
            "report_markdown": self.markdown_input.toPlainText(),
        }
        self.editor_status.setText("保存中…")
        self._save_bridge.submit(
            self.service.update_problem_case(self._current_case_id, payload)
        )

    def _on_saved(self, case: dict) -> None:
        self._render_editor(case)
        if self._current_case_id:
            self._pending_case_id = self._current_case_id
        self.refresh()
