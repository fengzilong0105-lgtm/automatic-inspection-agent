from __future__ import annotations

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from agent.desktop.async_call import AsyncCall
from agent.desktop.formatters import format_case_status, format_datetime, is_case_closed
from agent.desktop.widgets.action_help import ActionWithHint
from agent.desktop.widgets.card import Card
from agent.desktop.widgets.form_rows import style_input
from agent.services.agent_service import AgentService


class CaseEditorPage(QWidget):
    back_requested = Signal()
    case_changed = Signal(str)

    def __init__(self, service: AgentService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self._current_case_id: str | None = None
        self._current_doc_url: str | None = None
        self._current_case: dict = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)

        header = QHBoxLayout()
        self.back_btn = QPushButton("← 返回列表")
        self.back_btn.setObjectName("secondaryButton")
        self.back_btn.clicked.connect(self.back_requested.emit)
        self.page_title = QLabel("报告编辑")
        self.page_title.setObjectName("sectionTitle")
        self.editor_status = QLabel("")
        self.editor_status.setObjectName("fieldLabel")
        header.addWidget(self.back_btn)
        header.addWidget(self.page_title)
        header.addWidget(self.editor_status)
        header.addStretch()
        outer.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(12)

        editor_card = Card()
        self.title_input = QLineEdit()
        self.initiator_input = QLineEdit()
        self.assignee_input = QLineEdit()
        self.assignee_input.setPlaceholderText("处理负责人")
        self.description_input = QTextEdit()
        self.description_input.setPlaceholderText("问题描述")
        self.description_input.setMaximumHeight(100)
        self.markdown_input = QTextEdit()
        self.markdown_input.setPlaceholderText("报告 Markdown 正文")
        self.markdown_input.setMinimumHeight(360)
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

        editor_card.content_layout.addLayout(meta_form)
        editor_card.content_layout.addWidget(QLabel("Markdown 报告"))
        editor_card.content_layout.addWidget(self.markdown_input)
        body_layout.addWidget(editor_card, 1)

        actions_card = Card()
        actions_row = QHBoxLayout()
        actions_row.setSpacing(10)
        self.save_btn = QPushButton("保存")
        self.save_btn.setObjectName("primaryButton")
        self.save_btn.clicked.connect(self._save_current)
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

        self._save_hint = ActionWithHint(
            self.save_btn,
            "将标题、描述、Markdown 等内容保存到本地。\n"
            "若已关联飞书 Bitable 工单，填写负责人后会同步回表格。",
        )
        self._publish_hint = ActionWithHint(
            self.publish_btn,
            "创建飞书文档、写入 Bitable 工单行，并向运维群发送通知。",
        )
        self._open_doc_hint = ActionWithHint(
            self.open_doc_btn,
            "在浏览器中打开已发布的飞书报告文档（需先发布）。",
        )
        self._sync_hint = ActionWithHint(
            self.sync_ticket_btn,
            "从飞书 Bitable 拉取最新工单状态与负责人；\n"
            "若工单已关闭，则自动结案本地案例。",
        )
        self._close_hint = ActionWithHint(
            self.close_btn,
            "关闭本地案例，将 Bitable 工单标为「已关闭」，\n"
            "并联动解决关联告警。",
        )

        actions_row.addWidget(self._save_hint)
        actions_row.addWidget(self._publish_hint)
        actions_row.addWidget(self._open_doc_hint)
        actions_row.addWidget(self._sync_hint)
        actions_row.addWidget(self._close_hint)
        actions_row.addStretch()
        actions_card.content_layout.addLayout(actions_row)
        body_layout.addWidget(actions_card)

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

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

    def load_case(self, case_id: str) -> None:
        self._current_case_id = case_id
        self.editor_status.setText("加载中…")
        self._set_editor_enabled(False)
        self._load_bridge.submit(self.service.get_problem_case(case_id))

    def _set_editor_enabled(self, enabled: bool) -> None:
        for widget in (
            self.title_input,
            self.initiator_input,
            self.assignee_input,
            self.description_input,
            self.markdown_input,
        ):
            widget.setEnabled(enabled)

    def _apply_action_state(self, case: dict) -> None:
        closed = is_case_closed(case)
        has_ticket = bool(case.get("feishu_bitable_record_id"))
        has_doc = bool(case.get("feishu_doc_url"))
        self.publish_btn.setText("更新飞书" if has_doc else "发布到飞书")
        if has_doc:
            self._publish_hint.set_hint(
                "用当前内容覆盖已有飞书文档，并同步 Bitable 工单字段（文档链接不变）。",
            )
        else:
            self._publish_hint.set_hint(
                "创建飞书文档、写入 Bitable 工单行，并向运维群发送通知。",
            )
        self.save_btn.setEnabled(not closed)
        self.publish_btn.setEnabled(not closed)
        self.close_btn.setEnabled(not closed)
        self.sync_ticket_btn.setEnabled(has_ticket and not closed)
        self._set_editor_enabled(not closed)

    def _render_editor(self, case: dict) -> None:
        self._current_case = case
        self._current_case_id = case.get("id")
        self._current_doc_url = case.get("feishu_doc_url") or None
        title = case.get("title", "") or "未命名报告"
        self.page_title.setText(f"报告编辑 · {title[:48]}")
        self.title_input.setText(title)
        self.initiator_input.setText(case.get("initiator", "") or "")
        self.assignee_input.setText(case.get("assignee", "") or "")
        self.description_input.setPlainText(case.get("description", "") or "")
        self.markdown_input.setPlainText(case.get("report_markdown", "") or "")
        status = format_case_status(case.get("status"))
        ticket_status = case.get("ticket_status") or "-"
        updated = format_datetime(case.get("updated_at"))
        doc_hint = " · 已发布飞书文档" if self._current_doc_url else ""
        self.editor_status.setText(
            f"状态：{status} · 工单：{ticket_status} · 更新于 {updated}{doc_hint}"
        )
        self._apply_action_state(case)
        has_doc = bool(self._current_doc_url)
        self.open_doc_btn.setVisible(has_doc)
        self._open_doc_hint.setVisible(has_doc)

    def _edit_payload(self) -> dict:
        return {
            "title": self.title_input.text().strip(),
            "initiator": self.initiator_input.text().strip(),
            "assignee": self.assignee_input.text().strip(),
            "description": self.description_input.toPlainText().strip(),
            "report_markdown": self.markdown_input.toPlainText(),
        }

    def _save_current(self) -> None:
        if not self._current_case_id:
            return
        self.editor_status.setText("保存中…")
        self._save_bridge.submit(
            self.service.update_problem_case(self._current_case_id, self._edit_payload())
        )

    def _on_saved(self, case: dict) -> None:
        self._render_editor(case)
        self.case_changed.emit(str(case.get("id", "")))

    def _publish_current(self) -> None:
        if not self._current_case_id:
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
            dlg_title = "更新飞书"
        else:
            prompt = "将创建飞书文档、写入 Bitable 工单并发送群通知，确认继续？"
            dlg_title = "发布到飞书"
        answer = QMessageBox.question(
            self,
            dlg_title,
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.editor_status.setText("正在保存并更新飞书…" if has_doc else "正在保存并发布到飞书…")
        self.publish_btn.setEnabled(False)
        self._publish_bridge.submit(
            self.service.publish_problem_case(self._current_case_id, self._edit_payload())
        )

    def _on_published(self, case: dict) -> None:
        prev_url = self._current_doc_url
        self._render_editor(case)
        self.case_changed.emit(str(case.get("id", "")))
        doc_url = case.get("feishu_doc_url") or ""
        record_id = case.get("feishu_bitable_record_id") or ""
        if doc_url or record_id:
            action = "更新完成" if prev_url and doc_url == prev_url else "发布完成"
            lines = [f"{action}："]
            if doc_url:
                lines.append(f"文档: {doc_url}")
            if record_id:
                lines.append(f"工单记录: {record_id}")
            QMessageBox.information(self, "发布成功", "\n".join(lines))

    def _on_publish_failed(self, msg: str) -> None:
        self.editor_status.setText(f"发布失败: {msg}")
        self._apply_action_state(self._current_case)

    def _open_doc_url(self) -> None:
        if self._current_doc_url:
            QDesktopServices.openUrl(QUrl(self._current_doc_url))

    def _sync_ticket(self) -> None:
        if not self._current_case_id or is_case_closed(self._current_case):
            return
        self.editor_status.setText("正在从 Bitable 同步工单状态…")
        self._sync_bridge.submit(self.service.sync_problem_case_ticket(self._current_case_id))

    def _on_synced(self, case: dict) -> None:
        self._render_editor(case)
        self.case_changed.emit(str(case.get("id", "")))
        QMessageBox.information(
            self,
            "同步完成",
            f"工单状态：{case.get('ticket_status') or '-'}\n"
            f"负责人：{case.get('assignee') or '-'}",
        )

    def _close_current(self) -> None:
        if not self._current_case_id or is_case_closed(self._current_case):
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
        self.case_changed.emit(str(case.get("id", "")))
        QMessageBox.information(self, "案例已关闭", "案例已关闭，关联告警已标记为已解决。")

    def _on_close_failed(self, msg: str) -> None:
        self.editor_status.setText(f"关闭失败: {msg}")
        self._apply_action_state(self._current_case)
