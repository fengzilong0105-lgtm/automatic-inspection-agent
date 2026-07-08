from __future__ import annotations

import logging
import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

from agent.desktop.assets import load_app_icon
from agent.desktop.main_window import MainWindow
from agent.desktop.setup_wizard import SetupWizard
from agent.brand import ORG_NAME
from agent.desktop.theme import FONT_FAMILY, FONT_SIZE, load_stylesheet
from agent.runtime.background import get_runtime, shutdown_runtime
from agent.services.agent_service import AgentService
from agent.settings import get_settings


def run_desktop_app() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(ORG_NAME)
    app.setOrganizationName(ORG_NAME)
    # Keep process alive when main window is hidden to tray.
    app.setQuitOnLastWindowClosed(False)
    app_icon = load_app_icon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    app.setStyle("Fusion")
    app.setFont(QFont(FONT_FAMILY, FONT_SIZE))
    app.setStyleSheet(load_stylesheet())

    runtime = get_runtime()
    runtime.start()
    service = AgentService(runtime)

    settings = get_settings()
    logging.getLogger(__name__).info("Data directory: %s", settings.data_dir)

    window: MainWindow | None = None
    try:
        if settings.is_setup_needed():
            wizard = SetupWizard(service)
            if wizard.exec() != SetupWizard.DialogCode.Accepted:
                shutdown_runtime()
                return 0

        window = MainWindow(service)
        window.show()
        code = app.exec()
    except Exception as exc:
        logging.exception("Desktop app failed")
        QMessageBox.critical(None, "启动失败", str(exc))
        code = 1
    finally:
        if window is not None:
            window.tray.hide()
        shutdown_runtime()

    return code
