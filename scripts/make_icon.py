"""Generate logo.png and multi-size icon.ico from logo.svg."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "agent" / "desktop" / "assets"
SVG = ASSETS / "logo.svg"
PNG = ASSETS / "logo.png"
ICO = ASSETS / "icon.ico"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
PNG_SIZE = 512


def _count_ico_sizes(path: Path) -> list[tuple[int, int]]:
    data = path.read_bytes()
    count = struct.unpack("<H", data[4:6])[0]
    sizes: list[tuple[int, int]] = []
    offset = 6
    for _ in range(count):
        width, height, *_rest = struct.unpack("<BBBBHHII", data[offset : offset + 16])
        width = 256 if width == 0 else width
        height = 256 if height == 0 else height
        sizes.append((width, height))
        offset += 16
    return sizes


def main() -> int:
    if not SVG.is_file():
        print(f"Logo SVG not found: {SVG}", file=sys.stderr)
        return 1

    sys.path.insert(0, str(ROOT))

    try:
        from PIL import Image
    except ImportError:
        print("Install Pillow first: pip install pillow", file=sys.stderr)
        return 1

    from agent.desktop.icon_raster import render_svg_pil

    base = render_svg_pil(PNG_SIZE, svg_path=SVG)
    if base is None:
        print(f"Failed to render SVG: {SVG}", file=sys.stderr)
        return 1

    base.save(PNG, format="PNG", optimize=True)
    print(f"Wrote {PNG} ({PNG_SIZE}x{PNG_SIZE})")

    base.save(ICO, format="ICO", sizes=[(size, size) for size in ICO_SIZES])
    embedded = _count_ico_sizes(ICO)
    print(f"Wrote {ICO} with sizes: {', '.join(f'{w}x{h}' for w, h in embedded)}")
    if len(embedded) < len(ICO_SIZES):
        print("Warning: ICO is missing some target sizes.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
