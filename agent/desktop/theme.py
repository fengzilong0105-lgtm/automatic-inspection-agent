from __future__ import annotations

from pathlib import Path

from agent.paths import get_bundle_root

# Light cool palette — card-first dashboard
BG_APP = "#F0F2F5"
BG_SIDEBAR = "#FFFFFF"
BG_CARD = "#FFFFFF"
BG_HOVER = "#F5F7FA"
BG_INPUT = "#FAFBFC"

PRIMARY = "#1890FF"
PRIMARY_HOVER = "#40A9FF"
PRIMARY_PRESSED = "#096DD9"
PRIMARY_LIGHT = "#E6F4FF"
PRIMARY_BORDER = "#91CAFF"

TEXT_PRIMARY = "#262626"
TEXT_SECONDARY = "#8C8C8C"
TEXT_TERTIARY = "#BFBFBF"

BORDER = "#E8ECF0"
BORDER_LIGHT = "#F0F0F0"

SUCCESS = "#52C41A"
SUCCESS_BG = "#F6FFED"
WARNING = "#FAAD14"
WARNING_BG = "#FFF7E6"
WARNING_BORDER = "#FFD591"
DANGER = "#FF4D4F"
DANGER_BG = "#FFF2F0"

ACCENT_CPU = "#1890FF"
ACCENT_MEM = "#52C41A"
ACCENT_DISK = "#722ED1"
ACCENT_DISK_BG = "#F9F0FF"

CONFIRM_BG = "#FFF7E6"
CONFIRM_BORDER = "#FFD591"
CONFIRM_TITLE = "#D46B08"
COMMAND_BG = "#1F1F1F"
COMMAND_FG = "#FAFAFA"

RADIUS_CARD = 12
RADIUS_BTN = 8
RADIUS_INPUT = 8
RADIUS_PILL = 16

SIDEBAR_WIDTH = 208
TOPBAR_HEIGHT = 56
FONT_FAMILY = "Microsoft YaHei UI"
FONT_SIZE = 13
FONT_SIZE_TITLE = 18
FONT_SIZE_STAT = 28

PAGE_MARGIN = 16
CARD_PADDING = 16
GRID_GAP = 12


def stylesheet_path() -> Path:
    return get_bundle_root() / "agent" / "desktop" / "styles.qss"


def load_stylesheet() -> str:
    return stylesheet_path().read_text(encoding="utf-8")
