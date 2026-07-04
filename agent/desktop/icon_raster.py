from __future__ import annotations

import io
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from agent.paths import get_bundle_root

_qt_app: QApplication | None = None


def get_logo_svg_path() -> Path:
    return get_bundle_root() / "agent" / "desktop" / "assets" / "logo.svg"


def _ensure_qt_app() -> QApplication:
    global _qt_app
    app = QApplication.instance()
    if app is None:
        _qt_app = QApplication(sys.argv[:1] if sys.argv else ["steadyops-icon"])
        return _qt_app
    return app


def render_svg_qimage(
    size: int,
    *,
    svg_path: Path | None = None,
    background: Qt.GlobalColor | None = None,
) -> QImage | None:
    path = svg_path or get_logo_svg_path()
    if not path.is_file():
        return None

    _ensure_qt_app()
    renderer = QSvgRenderer(str(path))
    if not renderer.isValid():
        return None

    image = QImage(size, size, QImage.Format.Format_ARGB32)
    if background is None:
        image.fill(Qt.GlobalColor.transparent)
    else:
        image.fill(background)

    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    return image


def render_svg_pixmap(size: int, *, svg_path: Path | None = None) -> QPixmap | None:
    image = render_svg_qimage(size, svg_path=svg_path)
    if image is None:
        return None
    return QPixmap.fromImage(image)


def render_svg_pil(size: int, *, svg_path: Path | None = None):
    """Return a PIL RGBA image rendered from the logo SVG."""
    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice

    image = render_svg_qimage(size, svg_path=svg_path)
    if image is None:
        return None

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGBA")
