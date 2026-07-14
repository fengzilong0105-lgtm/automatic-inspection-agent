from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from agent.desktop.widgets.host_editor_dialog import HostEditorDialog
from agent.desktop.widgets.table_cells import make_text_item
from agent.services.agent_service import AgentService


class HostsPanel(QWidget):
    hosts_changed = Signal()

    def __init__(self, service: AgentService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self._hosts: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("服务器管理")
        title.setObjectName("sectionTitle")
        self.status_label = QLabel("")
        self.status_label.setObjectName("fieldLabel")
        self.add_btn = QPushButton("新建服务器")
        self.add_btn.setObjectName("primaryButton")
        self.add_btn.clicked.connect(self._add_host)
        header.addWidget(title)
        header.addWidget(self.status_label)
        header.addStretch()
        header.addWidget(self.add_btn)
        layout.addLayout(header)

        self.table = QTableWidget(0, 5)
        self.table.setObjectName("hostsTable")
        self.table.setHorizontalHeaderLabels(["名称", "主机 ID", "地址", "用户", "操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(4, 176)
        layout.addWidget(self.table)

        self.reload()

    def reload(self) -> None:
        data = self.service.list_hosts()
        self._hosts = list(data.get("hosts", []))
        self.status_label.setText(f"共 {len(self._hosts)} 台")
        self.table.setRowCount(len(self._hosts))

        for row, host in enumerate(self._hosts):
            ssh = host.get("ssh", {})
            sudo_hint = " · sudo" if ssh.get("use_sudo_su") else ""
            address = f"{ssh.get('host', '')}:{ssh.get('port', 22)}{sudo_hint}"

            self.table.setItem(row, 0, make_text_item(host.get("name", "")))
            self.table.setItem(row, 1, make_text_item(host.get("id", "")))
            self.table.setItem(row, 2, make_text_item(address))
            self.table.setItem(row, 3, make_text_item(ssh.get("user", "")))

            actions = QWidget()
            actions.setAutoFillBackground(False)
            actions_layout = QHBoxLayout(actions)
            actions_layout.setContentsMargins(6, 4, 6, 4)
            actions_layout.setSpacing(6)
            edit_btn = QPushButton("编辑")
            edit_btn.setObjectName("tableActionButton")
            edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            edit_btn.clicked.connect(lambda _checked=False, h=host: self._edit_host(h))
            actions_layout.addWidget(edit_btn)
            delete_btn = QPushButton("删除")
            delete_btn.setObjectName("tableActionButtonDanger")
            delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            delete_btn.clicked.connect(lambda _checked=False, h=host: self._delete_host(h))
            actions_layout.addWidget(delete_btn)
            self.table.setRowHeight(row, 46)
            self.table.setCellWidget(row, 4, actions)

    def _add_host(self) -> None:
        dialog = HostEditorDialog(self.service, is_new=True, parent=self.window())
        if dialog.exec() != HostEditorDialog.DialogCode.Accepted:
            return
        saved = dialog.saved_host()
        if not saved:
            return
        self.reload()
        self.hosts_changed.emit()
        host_id = saved.get("id", "")
        answer = QMessageBox.question(
            self,
            "扫描服务",
            "新服务器已保存。是否立即扫描并注册该主机上的服务？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes and host_id:
            self.service.set_active_host(host_id)
            self.hosts_changed.emit()
            try:
                future = self.service.scan_host(host_id)
                discovered = future.result(timeout=180)
                services = self.service.discovered_to_services(host_id, discovered)
                self.service.register_services(services)
                QMessageBox.information(
                    self,
                    "扫描完成",
                    f"已注册 {len(services)} 个服务。",
                )
            except Exception as exc:
                QMessageBox.warning(self, "扫描失败", str(exc))

    def _edit_host(self, host: dict) -> None:
        dialog = HostEditorDialog(
            self.service,
            host=host,
            is_new=False,
            parent=self.window(),
        )
        if dialog.exec() == HostEditorDialog.DialogCode.Accepted:
            self.reload()
            self.hosts_changed.emit()

    def _delete_host(self, host: dict) -> None:
        host_id = host.get("id", "")
        name = host.get("name", host_id)
        try:
            bound = [
                s.get("id", "")
                for s in self.service.list_services().get("services", [])
                if s.get("host_id") == host_id
            ]
        except Exception:
            bound = []

        last_hint = (
            "\n这是当前唯一服务器，删除后可重新新建录入。"
            if len(self._hosts) <= 1
            else ""
        )
        if bound:
            message = (
                f"确定删除「{name}」？\n"
                f"将同时清除该主机下已注册的 {len(bound)} 个服务。\n"
                f"删除后可重新录入，视为新服务器。{last_hint}"
            )
        else:
            message = f"确定删除「{name}」？{last_hint}"

        answer = QMessageBox.question(
            self,
            "删除服务器",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            result = self.service.delete_host(host_id)
            removed = result.get("removed_services") or []
            incidents = int(result.get("removed_incidents") or 0)
            cases = int(result.get("removed_problem_cases") or 0)
            self.reload()
            self.hosts_changed.emit()
            parts = [f"已删除服务器「{name}」"]
            if removed:
                parts.append(f"关联服务 {len(removed)} 个")
            if incidents:
                parts.append(f"告警 {incidents} 条")
            if cases:
                parts.append(f"问题报告 {cases} 份")
            detail = "，".join(parts) if len(parts) == 1 else parts[0] + "；已清理 " + "、".join(parts[1:])
            warning = result.get("purge_warning")
            if warning:
                detail += f"\n\n部分运行时数据清理失败：{warning}"
            QMessageBox.information(self, "删除成功", detail + "。")
        except Exception as exc:
            QMessageBox.critical(self, "删除失败", str(exc))
