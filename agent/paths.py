from __future__ import annotations

import os
import sys
from pathlib import Path

from agent.brand import ORG_NAME

APP_NAME = ORG_NAME


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def get_bundle_root() -> Path:
    """Packaged code root (PyInstaller _MEIPASS) or project root in dev."""
    if is_frozen():
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def get_app_root() -> Path:
    """Directory containing the executable or project root."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def get_user_base_dir() -> Path:
    if is_frozen():
        return Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
    return get_app_root()


def get_data_dir() -> Path:
    return get_user_base_dir() / "data"


def get_log_dir() -> Path:
    return get_user_base_dir() / "logs"


def get_static_dir() -> Path:
    return get_bundle_root() / "agent" / "web" / "static"
