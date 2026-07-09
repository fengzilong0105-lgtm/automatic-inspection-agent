from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from agent.desktop.assets import load_app_icon
from agent.desktop.pages.cases_page import CasesPage
from agent.desktop.pages.home_page import HomePage
from agent.desktop.pages.incidents_page import IncidentsPage
from agent.desktop.pages.settings_page import SettingsPage
from agent.desktop.widgets.sidebar import Sidebar
from agent.desktop.widgets.top_bar import TopBar
from agent.desktop.widgets.tray import TrayController
from agent.brand import PRODUCT_NAME
from agent.services.agent_service import AgentService


class MainWindow(QMainWindow):
    def __init__(self, service: AgentService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self._force_quit = False
        self._tray_hint_shown = False
        self.setWindowTitle(PRODUCT_NAME)
        app_icon = load_app_icon()
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)
        self.resize(1280, 800)

        self.tray = TrayController(app_icon, self)
        self.tray.show_requested.connect(self.show_from_tray)
        self.tray.quit_requested.connect(self.quit_application)
        if self.tray.available:
            self.tray.show()

        root = QWidget()
        root.setObjectName("centralRoot")
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.sidebar = Sidebar()
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self.top_bar = TopBar()
        self.top_bar.host_combo.currentIndexChanged.connect(self._on_host_changed)
        self.top_bar.wizard_btn.clicked.connect(self.open_setup_wizard)
        self.top_bar.inspect_btn.clicked.connect(self._run_inspection)
        self.top_bar.scan_btn.clicked.connect(self._run_scan)

        self.stack = QStackedWidget()
        self.stack.setObjectName("pageHost")
        self.home_page = HomePage(service)
        self.incidents_page = IncidentsPage(service)
        self.cases_page = CasesPage(service)
        self.settings_page = SettingsPage(service)
        self.stack.addWidget(self.home_page)
        self.stack.addWidget(self.incidents_page)
        self.stack.addWidget(self.cases_page)
        self.stack.addWidget(self.settings_page)

        self.home_page.go_incidents.connect(lambda: self._on_page_changed(1))
        self.home_page.chat_panel.memory_updated.connect(self.settings_page.load_memory)
        self.incidents_page.case_created.connect(self._on_case_created)
        self.settings_page.hosts_changed.connect(self.reload_hosts)

        self.sidebar.page_changed.connect(self._on_page_changed)

        right_layout.addWidget(self.top_bar)
        right_layout.addWidget(self.stack, 1)

        root_layout.addWidget(self.sidebar)
        root_layout.addWidget(right, 1)
        self.setCentralWidget(root)

        self.status = QStatusBar()
        self.setStatusBar(self.status)

        self.reload_hosts()
        self._update_setup_ui()

    def _update_setup_ui(self) -> None:
        status = self.service.setup_status()
        self.top_bar.set_setup_needed(bool(status.get("setup_needed")))

    def _on_page_changed(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.top_bar.set_page_index(index)
        self.sidebar.set_current_index(index)
        if index == 0 and self.home_page.stack.currentIndex() != 0:
            self.home_page.stack.setCurrentIndex(0)
        if index == 1:
            self.incidents_page.refresh()
        elif index == 2:
            self.cases_page.on_page_shown()
        elif index == 3:
            self.settings_page.load_memory()
            self.settings_page.reload_hosts()

    def _on_case_created(self, case_id: str) -> None:
        self._on_page_changed(2)
        self.cases_page.open_case(case_id)
        self.status.showMessage(f"问题报告已生成：{case_id[:8]}…", 5000)

    def _run_inspection(self) -> None:
        self._on_page_changed(0)
        self.home_page.run_inspection()

    def _run_scan(self) -> None:
        self._on_page_changed(0)
        self.home_page.scan_services()

    def reload_hosts(self) -> None:
        data = self.service.list_hosts()
        active = data.get("active_host_id") or ""
        combo = self.top_bar.host_combo
        combo.blockSignals(True)
        combo.clear()
        hosts = data.get("hosts", [])
        for host in hosts:
            label = f"{host.get('name')} ({host.get('ssh', {}).get('host', '')})"
            combo.addItem(label, host.get("id"))
        if active:
            idx = combo.findData(active)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        combo.blockSignals(False)
        self._on_host_changed()

    def _on_host_changed(self) -> None:
        host_id = self.top_bar.host_combo.currentData() or ""
        if host_id:
            self.service.set_active_host(host_id)
            name = self.top_bar.host_combo.currentText()
            self.home_page.set_active_host(host_id, name)
            self.status.showMessage(f"当前主机: {host_id}")
        else:
            self.status.showMessage("未配置主机")

    def open_setup_wizard(self) -> None:
        from agent.desktop.setup_wizard import SetupWizard

        wizard = SetupWizard(self.service, self)
        if wizard.exec():
            self.reload_hosts()
            self._update_setup_ui()
            self.settings_page.load_form()
            self.settings_page.reload_hosts()
            self.incidents_page.refresh()

    def show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_application(self) -> None:
        self._force_quit = True
        self.tray.hide()
        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._force_quit or not self.tray.available:
            event.accept()
            return

        event.ignore()
        self.hide()
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray.notify(
                PRODUCT_NAME,
                "应用仍在后台运行。点击右下角托盘图标可重新打开控制台。",
            )

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if (
            event.type() == event.Type.WindowStateChange
            and self.isMinimized()
            and self.tray.available
            and not self._force_quit
        ):
            self.hide()
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.tray.notify(
                    PRODUCT_NAME,
                    "应用仍在后台运行。点击右下角托盘图标可重新打开控制台。",
                )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.stack.currentIndex() == 1:
            self.incidents_page.refresh()
