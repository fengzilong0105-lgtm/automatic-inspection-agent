from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon, QPixmap

from agent.desktop.icon_raster import get_logo_svg_path, render_svg_pixmap
from agent.paths import get_bundle_root

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def get_assets_dir() -> Path:
    return get_bundle_root() / "agent" / "desktop" / "assets"


def get_logo_path() -> Path:
    svg = get_logo_svg_path()
    if svg.is_file():
        return svg
    return get_assets_dir() / "logo.png"


def get_icon_path() -> Path:
    return get_assets_dir() / "icon.ico"


def load_app_icon() -> QIcon:
    icon = QIcon()
    rendered = False
    for size in ICON_SIZES:
        pixmap = render_svg_pixmap(size)
        if pixmap is not None and not pixmap.isNull():
            icon.addPixmap(pixmap)
            rendered = True
    if rendered:
        return icon

    ico = get_icon_path()
    if ico.is_file():
        return QIcon(str(ico))

    png = get_assets_dir() / "logo.png"
    if png.is_file():
        return QIcon(str(png))
    return QIcon()


def load_logo_pixmap(size: int = 40) -> QPixmap | None:
    pixmap = render_svg_pixmap(size)
    if pixmap is not None and not pixmap.isNull():
        return pixmap

    png = get_assets_dir() / "logo.png"
    if not png.is_file():
        return None
    fallback = QPixmap(str(png))
    if fallback.isNull():
        return None
    from PySide6.QtCore import Qt

    return fallback.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
