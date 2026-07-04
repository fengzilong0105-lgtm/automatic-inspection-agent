"""Generate multi-size icon.ico from logo.png for Windows exe / taskbar."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "agent" / "desktop" / "assets" / "logo.png"
ICO = ROOT / "agent" / "desktop" / "assets" / "icon.ico"


def main() -> int:
    if not LOGO.is_file():
        print(f"Logo not found: {LOGO}", file=sys.stderr)
        return 1

    try:
        from PIL import Image
    except ImportError:
        print("Install Pillow first: pip install pillow", file=sys.stderr)
        return 1

    src = Image.open(LOGO).convert("RGBA")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [src.resize((s, s), Image.Resampling.LANCZOS) for s in sizes]
    frames[0].save(
        ICO,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=frames[1:],
    )
    print(f"Wrote {ICO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
