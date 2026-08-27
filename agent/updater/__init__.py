"""Online update helpers (scheme B: version.json → Setup → silent install)."""

from agent.updater.service import (
    UpdateCheckResult,
    UpdateError,
    apply_update,
    check_for_update,
    download_installer,
    launch_silent_upgrade,
)

__all__ = [
    "UpdateCheckResult",
    "UpdateError",
    "apply_update",
    "check_for_update",
    "download_installer",
    "launch_silent_upgrade",
]
