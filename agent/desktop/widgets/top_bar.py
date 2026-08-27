from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

PAGE_TITLES = ["首页", "告警", "问题报告", "设置"]

_ROW_HEIGHT = 36
_POPUP_PAD = 8

_POPUP_QSS = """
QFrame#hostComboPopup {
    background: transparent;
    border: none;
}
QListWidget#hostComboList {
    background: transparent;
    border: none;
    outline: none;
}
QListWidget#hostComboList::item {
    color: #262626;
    padding: 6px 10px;
    border-radius: 8px;
    min-height: 28px;
}
QListWidget#hostComboList::item:selected {
    background-color: #E6F4FF;
    color: #1890FF;
}
QListWidget#hostComboList::item:hover {
    background-color: #F5F7FA;
}
"""


class _HostPopup(QFrame):
    """Rounded host picker popup — QPainter on translucent window (Windows-safe)."""

    picked = Signal(int)

    _RADIUS = 12

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setObjectName("hostComboPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(_POPUP_QSS)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(_POPUP_PAD, _POPUP_PAD, _POPUP_PAD, _POPUP_PAD)
        outer.setSpacing(0)

        self.list = QListWidget()
        self.list.setObjectName("hostComboList")
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.itemClicked.connect(self._on_pick)
        outer.addWidget(self.list)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -2, -2)
        shadow = rect.adjusted(2, 3, 2, 3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 30))
        painter.drawRoundedRect(shadow, self._RADIUS, self._RADIUS)
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        painter.setPen(QPen(QColor("#E8ECF0"), 1))
        painter.drawRoundedRect(rect, self._RADIUS, self._RADIUS)

    def _on_pick(self, item: QListWidgetItem) -> None:
        index = item.data(Qt.ItemDataRole.UserRole)
        if index is not None:
            self.picked.emit(int(index))
        self.hide()

    def open_at(self, pos: QPoint, *, width: int, hosts: list[tuple[str, str]], current: int) -> None:
        self.list.clear()
        for i, (label, _) in enumerate(hosts):
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, i)
            item.setSizeHint(QSize(0, _ROW_HEIGHT))
            self.list.addItem(item)
            if i == current:
                self.list.setCurrentItem(item)

        visible_rows = min(len(hosts), 8)
        list_h = visible_rows * _ROW_HEIGHT + 4
        self.list.setFixedHeight(list_h)
        self.setFixedWidth(max(width, 220))
        self.adjustSize()
        self.move(pos)
        self.show()
        self.raise_()
        self.activateWindow()


class HostSelector(QWidget):
    currentIndexChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hosts: list[tuple[str, str]] = []
        self._current = -1

        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._button = QPushButton("请选择主机  ▾")
        self._button.setObjectName("hostCombo")
        self._button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._button.clicked.connect(self._toggle_popup)
        layout.addWidget(self._button)

        self._popup = _HostPopup()
        self._popup.picked.connect(self._pick)

    def _label_with_caret(self, label: str) -> str:
        return f"{label}  ▾"

    def _fit_button_width(self) -> None:
        fm = QFontMetrics(self._button.font())
        text = self._button.text()
        w = fm.horizontalAdvance(text) + 28  # padding L+R
        self._button.setFixedWidth(max(w, 120))
        self.adjustSize()

    def _toggle_popup(self) -> None:
        if not self._hosts:
            return
        if self._popup.isVisible():
            self._popup.hide()
            return
        pos = self._button.mapToGlobal(QPoint(0, self._button.height() + 6))
        self._popup.open_at(
            pos,
            width=self._button.width(),
            hosts=self._hosts,
            current=self._current,
        )

    def clear(self) -> None:
        self._hosts.clear()
        self._current = -1
        self._button.setText("请选择主机  ▾")
        self._fit_button_width()

    def addItem(self, label: str, userData: object = None) -> None:
        host_id = str(userData or "")
        self._hosts.append((label, host_id))

    def _pick(self, index: int) -> None:
        if index < 0 or index >= len(self._hosts) or index == self._current:
            self._popup.hide()
            return
        self._current = index
        self._button.setText(self._label_with_caret(self._hosts[index][0]))
        self._fit_button_width()
        self.currentIndexChanged.emit(index)

    def findData(self, data: object) -> int:
        sid = str(data)
        for i, (_, host_id) in enumerate(self._hosts):
            if host_id == sid:
                return i
        return -1

    def setCurrentIndex(self, index: int) -> None:
        if 0 <= index < len(self._hosts):
            self._current = index
            self._button.setText(self._label_with_caret(self._hosts[index][0]))
            self._fit_button_width()

    def currentData(self) -> str | None:
        if 0 <= self._current < len(self._hosts):
            return self._hosts[self._current][1]
        return None

    def currentText(self) -> str:
        if 0 <= self._current < len(self._hosts):
            return self._hosts[self._current][0]
        return ""

    def count(self) -> int:
        return len(self._hosts)


class TopBar(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("topBar")
        self.setFixedHeight(56)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 16, 0)
        layout.setSpacing(12)

        self.page_title = QLabel("首页")
        self.page_title.setObjectName("pageTitle")
        layout.addWidget(self.page_title)
        layout.addStretch()

        host_label = QLabel("当前主机")
        host_label.setObjectName("fieldLabel")
        self.host_combo = HostSelector()

        self.inspect_btn = QPushButton("立即巡检")
        self.inspect_btn.setObjectName("primaryButton")
        self.scan_btn = QPushButton("扫描服务")
        self.scan_btn.setObjectName("secondaryButton")
        self.wizard_btn = QPushButton("初始化向导")
        self.wizard_btn.setObjectName("secondaryButton")
        self.wizard_btn.setVisible(False)

        layout.addWidget(host_label)
        layout.addWidget(self.host_combo, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.inspect_btn)
        layout.addWidget(self.scan_btn)
        layout.addWidget(self.wizard_btn)

    def set_page_index(self, index: int) -> None:
        if 0 <= index < len(PAGE_TITLES):
            self.page_title.setText(PAGE_TITLES[index])

    def set_setup_needed(self, needed: bool) -> None:
        self.wizard_btn.setVisible(needed)
