from __future__ import annotations

import argparse
import ctypes
import logging
import multiprocessing
import sys
import threading
import time
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

from agent.paths import get_log_dir, is_frozen

from agent.brand import ORG_NAME, PRODUCT_NAME

_MUTEX_NAME = f"Global\\{ORG_NAME}_SingleInstance_v1"


def _ensure_single_instance() -> bool:
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if kernel32.GetLastError() == 183:
        return False
    return True


def _setup_logging() -> None:
    handlers: list[logging.Handler] = []
    if is_frozen():
        log_dir = get_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_dir / "agent.log",
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
        )
    else:
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=handlers,
        force=True,
    )


def _run_web_mode(args: argparse.Namespace) -> int:
    import uvicorn

    from agent.settings import Settings, get_settings, reset_settings
    from agent.web.routes import create_app

    if args.config:
        import agent.settings as settings_module

        reset_settings()
        settings_module._settings = Settings(Path(args.config))

    settings = get_settings()
    host = args.host or ("127.0.0.1" if is_frozen() else settings.config.web.host)
    port = args.port or settings.config.web.port
    app = create_app()

    if not args.no_browser:
        def open_browser() -> None:
            import urllib.error
            import urllib.request

            url = f"http://{host}:{port}"
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen(url, timeout=1.0) as response:
                        if response.status < 500:
                            webbrowser.open(url)
                            return
                except (urllib.error.URLError, TimeoutError, OSError):
                    time.sleep(0.4)

        threading.Thread(target=open_browser, daemon=True).start()

    uvicorn.run(app, host=host, port=port, log_config=None)
    return 0


def _run_desktop_mode() -> int:
    from agent.desktop.app import run_desktop_app

    return run_desktop_app()


def main() -> None:
    multiprocessing.freeze_support()

    parser = argparse.ArgumentParser(description=PRODUCT_NAME)
    parser.add_argument("--web", action="store_true", help="Launch legacy Web UI (dev only)")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if not _ensure_single_instance():
        if args.web:
            print("Agent is already running.")
        else:
            from PySide6.QtWidgets import QApplication, QMessageBox

            app = QApplication(sys.argv)
            QMessageBox.information(None, PRODUCT_NAME, "应用已在运行中。")
        return

    _setup_logging()

    if args.web:
        _run_web_mode(args)
    else:
        sys.exit(_run_desktop_mode())


if __name__ == "__main__":
    main()
