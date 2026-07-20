from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from agent.desktop.async_call import AsyncCall
from agent.desktop.pages.services_list_view import HomePageLogic, ServicesListView
from agent.desktop.widgets.card import Card
from agent.desktop.widgets.chat_panel import ChatPanel
from agent.desktop.widgets.host_metrics_bar import HostMetricsBar
from agent.services.agent_service import AgentService


class _ServiceChip(QLabel):
    clicked = Signal()

    def __init__(self, text: str = "", *, accent: str = "default", parent=None) -> None:
        super().__init__(text, parent)
        self.setObjectName("serviceChip")
        self.setProperty("accent", accent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def set_accent(self, accent: str) -> None:
        self.setProperty("accent", accent)
        self.style().unpolish(self)
        self.style().polish(self)


class HomePage(QWidget):
    go_incidents = Signal()

    def __init__(self, service: AgentService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.summary: list[dict] = []
        self._host_id = ""
        self._mode = "refresh"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(0)

        self.stack = QStackedWidget()
        self.overview = self._build_overview()
        self.services_view = ServicesListView()
        self.services_view.back_btn.clicked.connect(self._back_to_overview)
        self.services_view.enable_service.connect(self._enable_service)
        self.services_view.remove_service.connect(self._remove_service)
        self.stack.addWidget(self.overview)
        self.stack.addWidget(self.services_view)
        layout.addWidget(self.stack, 1)

        self._bridge = AsyncCall(self)
        self._bridge.finished.connect(self._on_async_done)
        self._bridge.failed.connect(self._on_error)

    def _build_overview(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        summary_card = Card(padding=8)
        summary_layout = summary_card.content_layout
        summary_layout.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(10)

        self.host_hint = QLabel("服务")
        self.host_hint.setObjectName("serviceStripTitle")
        row.addWidget(self.host_hint)

        self.chip_ok = _ServiceChip("正常 0", accent="success")
        self.chip_bad = _ServiceChip("异常 0", accent="danger")
        self.chip_disabled = _ServiceChip("停用 0")
        self.chip_ok.clicked.connect(lambda: self._open_service_list("ok"))
        self.chip_bad.clicked.connect(lambda: self._open_service_list("bad"))
        self.chip_disabled.clicked.connect(lambda: self._open_service_list("disabled"))
        row.addWidget(self.chip_ok)
        row.addWidget(self.chip_bad)
        row.addWidget(self.chip_disabled)

        self.status_label = QLabel("")
        self.status_label.setObjectName("fieldLabel")
        row.addWidget(self.status_label, 1)

        self.incidents_btn = QPushButton("告警")
        self.incidents_btn.setObjectName("alertButtonCompact")
        self.incidents_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.incidents_btn.clicked.connect(self.go_incidents.emit)
        row.addWidget(self.incidents_btn)
        summary_layout.addLayout(row)
        layout.addWidget(summary_card)

        self.metrics_bar = HostMetricsBar(self.service)
        layout.addWidget(self.metrics_bar)

        chat_card = Card()
        self.chat_panel = ChatPanel(self.service)
        chat_card.content_layout.addWidget(self.chat_panel)
        layout.addWidget(chat_card, 1)

        return page

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.stack.currentIndex() == 0 and self._host_id:
            self.metrics_bar.resume()

    def hideEvent(self, event) -> None:
        self.metrics_bar.pause()
        super().hideEvent(event)

    def _back_to_overview(self) -> None:
        self.stack.setCurrentIndex(0)
        if self._host_id and self.isVisible():
            self.metrics_bar.resume()

    def _open_service_list(self, filter_kind: str) -> None:
        self.metrics_bar.pause()
        self.services_view.show_list(filter_kind, self.summary)
        self.stack.setCurrentIndex(1)

    def set_active_host(self, host_id: str, host_name: str = "") -> None:
        self._host_id = host_id or ""
        title = "服务"
        if host_name:
            title = f"服务 · {host_name}"
        self.host_hint.setText(title)
        if self.stack.currentIndex() != 0:
            self.stack.setCurrentIndex(0)
        self.metrics_bar.set_host(self._host_id)
        if not self._host_id:
            self.clear_summary("请先选择或新建服务器")
            return
        self.refresh()
        if self.isVisible():
            self.metrics_bar.resume()

    def clear_summary(self, message: str = "") -> None:
        self.summary = []
        self.chip_ok.setText("正常 0")
        self.chip_bad.setText("异常 0")
        self.chip_disabled.setText("停用 0")
        self.status_label.setText(message or "暂无服务")

    def refresh(self) -> None:
        if not self._host_id:
            self.clear_summary("请先选择或新建服务器")
            return
        self._mode = "refresh"
        self.status_label.setText("正在检测服务状态…")
        self._bridge.submit(self.service.status_summary(self._host_id))

    def run_inspection(self) -> None:
        self._mode = "inspect"
        self.status_label.setText("正在巡检…")
        self._bridge.submit(self.service.run_inspection())

    def scan_services(self) -> None:
        if not self._host_id:
            self.status_label.setText("请先选择主机")
            return
        self._mode = "scan"
        self.status_label.setText("正在扫描服务…")
        self._bridge.submit(self.service.scan_host(self._host_id))

    def _on_async_done(self, result) -> None:
        if self._mode == "refresh":
            self.summary = result
            self._render_summary()
        elif self._mode == "inspect":
            created = result.get("created", 0)
            self.status_label.setText(f"巡检完成，新建告警 {created} 条")
            self.refresh()
        elif self._mode == "scan":
            services = self.service.discovered_to_services(self._host_id, result)
            self.service.register_services(services)
            stopped = sum(1 for item in result if not item.get("running", True))
            text = f"扫描完成，注册 {len(services)} 个服务"
            if stopped:
                text += f"（{stopped} 个未运行，默认停用巡检）"
            self.status_label.setText(text)
            self.refresh()

    def _on_error(self, msg: str) -> None:
        self.status_label.setText(f"操作失败: {msg}")

    def _enable_service(self, service_id: str) -> None:
        if not service_id:
            return
        try:
            self.service.set_service_enabled(service_id, True)
        except Exception as exc:
            self.status_label.setText(f"启用失败: {exc}")
            return
        self.status_label.setText(f"已启用 {service_id}，正在刷新状态…")
        self.refresh()

    def _remove_service(self, service_id: str) -> None:
        if not service_id:
            return
        answer = QMessageBox.question(
            self,
            "移除服务",
            f"确定将「{service_id}」从服务列表移除吗？\n（不会影响服务器上的文件，重新扫描可再次发现）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.remove_service(service_id)
        except Exception as exc:
            self.status_label.setText(f"移除失败: {exc}")
            return
        self.status_label.setText(f"已移除 {service_id}，正在刷新…")
        self.refresh()

    def _render_summary(self) -> None:
        ok = sum(1 for item in self.summary if HomePageLogic.is_ok(item))
        bad = sum(1 for item in self.summary if HomePageLogic.is_bad(item))
        disabled = sum(1 for item in self.summary if item.get("disabled"))
        pending = sum(
            1
            for item in self.summary
            if not item.get("disabled") and item.get("status", {}).get("running") is None
        )
        total = len(self.summary)
        self.chip_ok.setText(f"正常 {ok}")
        self.chip_bad.setText(f"异常 {bad}")
        self.chip_disabled.setText(f"停用 {disabled}")
        self.chip_bad.set_accent("danger" if bad else "default")
        parts = [f"共 {total} 个"]
        if disabled:
            parts.append(f"{disabled} 停用")
        if pending:
            parts.append(f"{pending} 待检测")
        self.status_label.setText(" · ".join(parts))

        if self.stack.currentIndex() == 1:
            self.services_view.show_list(self.services_view._filter, self.summary)
        elif self.isVisible() and self._host_id:
            self.metrics_bar.resume()
