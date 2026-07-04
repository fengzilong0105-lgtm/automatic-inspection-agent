from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap

from agent.paths import get_bundle_root


def get_assets_dir() -> Path:
    return get_bundle_root() / "agent" / "desktop" / "assets"


def get_logo_path() -> Path:
    return get_assets_dir() / "logo.png"


def get_icon_path() -> Path:
    return get_assets_dir() / "icon.ico"


def load_app_icon() -> QIcon:
    logo = get_logo_path()
    if logo.is_file():
        return QIcon(str(logo))
    ico = get_icon_path()
    if ico.is_file():
        return QIcon(str(ico))
    return QIcon()


def load_logo_pixmap(size: int = 40) -> QPixmap | None:
    path = get_logo_path()
    if not path.is_file():
        return None
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return None
    return pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
