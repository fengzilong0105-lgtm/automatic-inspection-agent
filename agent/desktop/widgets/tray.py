from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from agent.brand import PRODUCT_NAME


class TrayController(QObject):
    """Windows system tray icon for background / restore workflow."""

    show_requested = Signal()
    quit_requested = Signal()

    def __init__(self, icon: QIcon, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tray = QSystemTrayIcon(icon if not icon.isNull() else QIcon(), parent)
        self._tray.setToolTip(PRODUCT_NAME)

        menu = QMenu()
        show_action = menu.addAction("打开控制台")
        show_action.triggered.connect(self.show_requested.emit)
        menu.addSeparator()
        quit_action = menu.addAction("退出")
        quit_action.triggered.connect(self.quit_requested.emit)
        self._tray.setContextMenu(menu)

        self._tray.activated.connect(self._on_activated)

    @property
    def available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self) -> None:
        if self.available:
            self._tray.show()

    def hide(self) -> None:
        self._tray.hide()

    def notify(self, title: str, message: str, msec: int = 3000) -> None:
        if self.available and self._tray.isVisible():
            self._tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, msec)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_requested.emit()
