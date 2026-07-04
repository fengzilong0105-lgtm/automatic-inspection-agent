from __future__ import annotations

from concurrent.futures import Future
from typing import Callable, TypeVar

from PySide6.QtCore import QObject, Signal

T = TypeVar("T")


class AsyncCall(QObject):
    """Bridge BackgroundRuntime futures to Qt signals."""

    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pending: Future | None = None

    def submit(self, future: Future[T], on_success: Callable[[T], None] | None = None) -> None:
        self._pending = future
        future.add_done_callback(lambda f: self._dispatch(f, on_success))

    def _dispatch(self, future: Future, on_success: Callable | None) -> None:
        try:
            result = future.result()
            if on_success:
                on_success(result)
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))
