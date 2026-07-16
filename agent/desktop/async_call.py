from __future__ import annotations

from concurrent.futures import Future
from typing import Any, Callable, TypeVar

from PySide6.QtCore import QObject, Qt, Signal

T = TypeVar("T")


class AsyncCall(QObject):
    """Bridge BackgroundRuntime futures to Qt signals on the GUI thread."""

    finished = Signal(object)
    failed = Signal(str)
    # Always queued onto the thread that owns this QObject (GUI).
    _deliver_ok = Signal(int, object)
    _deliver_err = Signal(int, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pending: Future | None = None
        self._token = 0
        self._on_success: dict[int, Callable[[Any], None]] = {}
        self._deliver_ok.connect(self._on_ok, Qt.ConnectionType.QueuedConnection)
        self._deliver_err.connect(self._on_err, Qt.ConnectionType.QueuedConnection)

    def submit(self, future: Future[T], on_success: Callable[[T], None] | None = None) -> None:
        self._token += 1
        token = self._token
        self._pending = future
        if on_success is not None:
            self._on_success[token] = on_success
        future.add_done_callback(lambda f, t=token: self._dispatch(f, t))

    def _dispatch(self, future: Future, token: int) -> None:
        # Runs on the runtime / worker thread — only emit, never touch widgets.
        try:
            self._deliver_ok.emit(token, future.result())
        except Exception as exc:
            self._deliver_err.emit(token, str(exc))

    def _on_ok(self, token: int, result: object) -> None:
        cb = self._on_success.pop(token, None)
        if token != self._token:
            return
        if cb:
            cb(result)
        self.finished.emit(result)

    def _on_err(self, token: int, message: str) -> None:
        self._on_success.pop(token, None)
        if token != self._token:
            return
        self.failed.emit(message)
