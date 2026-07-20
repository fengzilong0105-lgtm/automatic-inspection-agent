from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from agent.desktop.async_call import AsyncCall
from agent.desktop.widgets.card import Card
from agent.services.agent_service import AgentService

POLL_MS = 4000
LEVEL_OK = "ok"
LEVEL_WARN = "warn"
LEVEL_DANGER = "danger"


def _level(percent: float | None) -> str:
    if percent is None:
        return LEVEL_OK
    if percent >= 85:
        return LEVEL_DANGER
    if percent >= 70:
        return LEVEL_WARN
    return LEVEL_OK


def _fmt_bytes(value: int | float | None) -> str:
    if value is None:
        return "—"
    num = float(value)
    for unit in ("B", "K", "M", "G", "T"):
        if abs(num) < 1024 or unit == "T":
            if unit == "B":
                return f"{int(num)}{unit}"
            return f"{num:.1f}{unit}"
        num /= 1024
    return "—"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.0f}%"


class _MetricCell(QWidget):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        self.title = QLabel(title)
        self.title.setObjectName("metricsCellTitle")
        self.value = QLabel("—")
        self.value.setObjectName("metricsCellValue")
        head.addWidget(self.title)
        head.addWidget(self.value, 1)
        layout.addLayout(head)

        self.bar = QProgressBar()
        self.bar.setObjectName("metricsBar")
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        layout.addWidget(self.bar)

        self.hint = QLabel("")
        self.hint.setObjectName("metricsCellHint")
        layout.addWidget(self.hint)

    def set_data(
        self,
        percent: float | None,
        *,
        value_text: str | None = None,
        hint: str = "",
    ) -> None:
        level = _level(percent)
        self.bar.setProperty("level", level)
        self.value.setProperty("level", level)
        self.bar.style().unpolish(self.bar)
        self.bar.style().polish(self.bar)
        self.value.style().unpolish(self.value)
        self.value.style().polish(self.value)

        if percent is None:
            self.bar.setValue(0)
            self.value.setText(value_text or "—")
        else:
            self.bar.setValue(max(0, min(100, int(round(percent)))))
            self.value.setText(value_text or _fmt_pct(percent))
        self.hint.setText(hint)
        self.hint.setVisible(bool(hint))


class HostMetricsBar(Card):
    """Collapsed host CPU/mem/disk strip with expandable Top5 / disk details."""

    def __init__(self, service: AgentService, parent=None) -> None:
        super().__init__(parent, padding=10)
        self.service = service
        self._host_id = ""
        self._expanded = False
        self._active = False
        self._fetching = False

        root = self.content_layout
        root.setSpacing(8)

        summary = QHBoxLayout()
        summary.setSpacing(16)
        self.cpu_cell = _MetricCell("CPU")
        self.mem_cell = _MetricCell("内存")
        self.disk_cell = _MetricCell("磁盘")
        summary.addWidget(self.cpu_cell, 1)
        summary.addWidget(self.mem_cell, 1)
        summary.addWidget(self.disk_cell, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("metricsStatus")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.toggle_btn = QPushButton("展开详情 ▾")
        self.toggle_btn.setObjectName("metricsToggle")
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.clicked.connect(self._toggle_expanded)
        self.toggle_btn.setEnabled(False)

        right = QVBoxLayout()
        right.setSpacing(4)
        right.addWidget(self.status_label)
        right.addWidget(self.toggle_btn, 0, Qt.AlignmentFlag.AlignRight)
        right.addStretch()

        summary.addLayout(right, 0)
        root.addLayout(summary)

        self.detail = QWidget()
        self.detail.setObjectName("metricsDetail")
        self.detail.setVisible(False)
        self.detail.setMaximumHeight(220)
        detail_layout = QVBoxLayout(self.detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("metricsTabs")
        self.cpu_table = self._make_process_table(["PID", "进程", "CPU%", "用户"])
        self.mem_table = self._make_process_table(["PID", "进程", "内存%", "RSS", "用户"])
        self.disk_table = self._make_disk_table()
        self.tabs.addTab(self.cpu_table, "CPU Top5")
        self.tabs.addTab(self.mem_table, "内存 Top5")
        self.tabs.addTab(self.disk_table, "磁盘详情")
        detail_layout.addWidget(self.tabs)
        root.addWidget(self.detail)

        self._bridge = AsyncCall(self)
        self._bridge.failed.connect(self._on_error)
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)

        self.set_empty("请先选择主机")

    @staticmethod
    def _make_process_table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return table

    @staticmethod
    def _make_disk_table() -> QTableWidget:
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["挂载点", "使用率", "已用 / 总量", "类型"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return table

    def set_host(self, host_id: str) -> None:
        self._host_id = host_id or ""
        if not self._host_id:
            self.pause()
            self.set_empty("请先选择主机")
            return
        self.toggle_btn.setEnabled(True)
        self.status_label.setText("采集中…")
        if self._active or self.isVisible():
            self.resume()
        else:
            self._poll()

    def set_empty(self, message: str) -> None:
        self.cpu_cell.set_data(None)
        self.mem_cell.set_data(None)
        self.disk_cell.set_data(None, hint="")
        self.status_label.setText(message)
        self.toggle_btn.setEnabled(False)
        self._fill_process_table(self.cpu_table, [], kind="cpu")
        self._fill_process_table(self.mem_table, [], kind="mem")
        self._fill_disk_table([])

    def resume(self) -> None:
        self._active = True
        if not self._host_id:
            return
        if not self._timer.isActive():
            self._timer.start()
        self._poll()

    def pause(self) -> None:
        self._active = False
        self._timer.stop()

    def _toggle_expanded(self) -> None:
        self._expanded = not self._expanded
        self.detail.setVisible(self._expanded)
        self.toggle_btn.setText("收起详情 ▴" if self._expanded else "展开详情 ▾")
        if self._expanded and self._host_id:
            self._poll(force=True)

    def _poll(self, *, force: bool = False) -> None:
        if not self._host_id:
            return
        if self._fetching and not force:
            return
        self._fetching = True
        future = self.service.get_host_resources(self._host_id, include_top=self._expanded)
        self._bridge.submit(future, on_success=self._on_data)

    def _on_data(self, data: dict[str, Any]) -> None:
        self._fetching = False
        if not self._host_id:
            return

        cpu = data.get("cpu_percent")
        mem = data.get("memory_percent")
        disk = data.get("disk_percent")
        load = (data.get("load_avg") or "").strip()
        mem_hint = ""
        used = data.get("memory_used_bytes")
        total = data.get("memory_total_bytes")
        if used is not None and total is not None:
            mem_hint = f"{_fmt_bytes(used)} / {_fmt_bytes(total)}"

        disks = data.get("disks") or []
        disk_mount = data.get("disk_mount") or ""
        disk_hint = disk_mount
        if disks:
            disk_hint = f"{disk_mount or disks[0].get('mount', '')} · {len(disks)} 个挂载点"

        self.cpu_cell.set_data(cpu, hint=f"load {load}" if load else "")
        self.mem_cell.set_data(mem, hint=mem_hint)
        self.disk_cell.set_data(disk, hint=disk_hint)
        self.status_label.setText("")

        if self._expanded:
            self._fill_process_table(self.cpu_table, data.get("top_cpu") or [], kind="cpu")
            self._fill_process_table(self.mem_table, data.get("top_memory") or [], kind="mem")
            self._fill_disk_table(disks)

    def _on_error(self, message: str) -> None:
        self._fetching = False
        self.status_label.setText(f"主机不可达：{message}")
        for cell in (self.cpu_cell, self.mem_cell, self.disk_cell):
            cell.setProperty("level", LEVEL_DANGER)

    def _fill_process_table(self, table: QTableWidget, rows: list[dict], *, kind: str) -> None:
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            values = [
                str(row.get("pid", "")),
                str(row.get("name") or ""),
            ]
            if kind == "cpu":
                values.append(_fmt_pct(row.get("cpu_percent")))
                values.append(str(row.get("user") or ""))
            else:
                values.append(_fmt_pct(row.get("memory_percent")))
                values.append(_fmt_bytes(row.get("rss_bytes")))
                values.append(str(row.get("user") or ""))
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table.setItem(i, col, item)

    def _fill_disk_table(self, disks: list[dict]) -> None:
        self.disk_table.setRowCount(len(disks))
        for i, disk in enumerate(disks):
            used = disk.get("used_bytes")
            total = disk.get("total_bytes")
            values = [
                str(disk.get("mount") or ""),
                _fmt_pct(disk.get("percent")),
                f"{_fmt_bytes(used)} / {_fmt_bytes(total)}",
                str(disk.get("fstype") or ""),
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col == 1:
                    level = _level(disk.get("percent"))
                    if level == LEVEL_DANGER:
                        item.setForeground(Qt.GlobalColor.red)
                    elif level == LEVEL_WARN:
                        item.setForeground(Qt.GlobalColor.darkYellow)
                self.disk_table.setItem(i, col, item)
