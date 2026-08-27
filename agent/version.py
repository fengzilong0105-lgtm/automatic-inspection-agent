"""Application version (keep in sync with pyproject.toml; build.ps1 rewrites APP_VERSION)."""

from __future__ import annotations

from pathlib import Path

from agent.paths import is_frozen

# Single source embedded for frozen builds; build.ps1 syncs from pyproject.toml.
APP_VERSION = "0.3.2"


def get_app_version() -> str:
    # Frozen builds must use the constant baked in at compile time. Bundled
    # importlib metadata can lag behind APP_VERSION and report a stale version.
    if is_frozen():
        return APP_VERSION

    try:
        from importlib.metadata import version

        return version("automatic-inspection-agent")
    except Exception:
        pass

    try:
        root = Path(__file__).resolve().parent.parent
        text = (root / "pyproject.toml").read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("version"):
                _, _, rest = line.partition("=")
                return rest.strip().strip('"').strip("'")
    except Exception:
        pass

    return APP_VERSION


def parse_version(value: str) -> tuple[int, ...]:
    cleaned = value.strip().lstrip("vV")
    parts: list[int] = []
    for chunk in cleaned.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits or 0))
    while parts and parts[-1] == 0 and len(parts) > 1:
        parts.pop()
    return tuple(parts) if parts else (0,)


def is_remote_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)
