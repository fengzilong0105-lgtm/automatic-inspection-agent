from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias
from urllib.parse import urlparse

import httpx

from agent.paths import get_app_root, is_frozen
from agent.version import get_app_version, is_remote_newer

logger = logging.getLogger(__name__)

DownloadProgressCallback: TypeAlias = Callable[[int, int | None], None]


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    remote_version: str
    url: str
    sha256: str
    notes: str
    available: bool
    message: str


class UpdateError(RuntimeError):
    pass


def _normalize_feed_url(feed_url: str) -> str:
    url = feed_url.strip()
    if not url:
        raise UpdateError("未配置更新地址（version.json URL）")
    return url


def fetch_version_manifest(feed_url: str, timeout: float = 30.0) -> dict:
    url = _normalize_feed_url(feed_url)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        raise UpdateError(f"无法获取版本信息: {exc}") from exc
    except ValueError as exc:
        raise UpdateError("version.json 不是合法 JSON") from exc

    if not isinstance(data, dict):
        raise UpdateError("version.json 格式无效")
    return data


def check_for_update(feed_url: str) -> UpdateCheckResult:
    current = get_app_version()
    data = fetch_version_manifest(feed_url)
    remote = str(data.get("version") or "").strip()
    url = str(data.get("url") or "").strip()
    sha256 = str(data.get("sha256") or "").strip().lower()
    notes = str(data.get("notes") or "").strip()

    if not remote:
        raise UpdateError("version.json 缺少 version 字段")
    if not url:
        raise UpdateError("version.json 缺少 url 字段")

    available = is_remote_newer(remote, current)
    if available:
        message = f"发现新版本 {remote}（当前 {current}）"
    else:
        message = f"已是最新版本（{current}）"

    return UpdateCheckResult(
        current_version=current,
        remote_version=remote,
        url=url,
        sha256=sha256,
        notes=notes,
        available=available,
        message=message,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def download_installer(
    url: str,
    sha256: str = "",
    timeout: float = 300.0,
    on_progress: DownloadProgressCallback | None = None,
) -> Path:
    parsed = urlparse(url)
    name = Path(parsed.path).name or "SteadyOps-Setup.exe"
    if not name.lower().endswith(".exe"):
        name = f"{name}.exe"

    dest = Path(tempfile.gettempdir()) / "SteadyOpsUpdate" / name
    dest.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Downloading update from %s -> %s", url, dest)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                total_raw = response.headers.get("content-length")
                total = int(total_raw) if total_raw and total_raw.isdigit() else None
                downloaded = 0
                if on_progress:
                    on_progress(0, total)
                with dest.open("wb") as handle:
                    for chunk in response.iter_bytes(64 * 1024):
                        handle.write(chunk)
                        downloaded += len(chunk)
                        if on_progress:
                            on_progress(downloaded, total)
    except httpx.HTTPError as exc:
        raise UpdateError(f"下载安装包失败: {exc}") from exc

    if sha256:
        actual = _sha256_file(dest)
        if actual.lower() != sha256.lower():
            dest.unlink(missing_ok=True)
            raise UpdateError(
                f"安装包校验失败（期望 {sha256[:12]}…，实际 {actual[:12]}…）"
            )
    return dest


def _app_executable() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve()
    return get_app_root() / "dist" / "SteadyOps" / "SteadyOps.exe"


def launch_silent_upgrade(setup_path: Path) -> Path:
    """Spawn a helper script that waits, runs Inno Setup silently, then relaunches the app.

    Returns the helper script path. Caller should quit the current process soon after.
    """
    if sys.platform != "win32":
        raise UpdateError("在线更新仅支持 Windows")

    app_exe = _app_executable()
    if not setup_path.is_file():
        raise UpdateError(f"安装包不存在: {setup_path}")

    install_dir = app_exe.parent
    helper = Path(tempfile.gettempdir()) / "SteadyOpsUpdate" / "apply_update.cmd"
    log_path = helper.parent / "install.log"
    helper.parent.mkdir(parents=True, exist_ok=True)

    # /SILENT: wizard hidden but progress may show; /NORESTART: don't reboot OS
    # /DIR= upgrades the directory of the running exe (not Inno's default path).
    # CLOSEAPPLICATIONS is configured in the .iss; FORCECLOSEAPPLICATIONS helps stubborn locks
    setup = str(setup_path.resolve())
    target = str(app_exe)
    app_dir = str(install_dir)
    log_file = str(log_path)
    script = "\r\n".join(
        [
            "@echo off",
            "ping -n 5 127.0.0.1 >nul",
            (
                f'"{setup}" /SILENT /NORESTART /FORCECLOSEAPPLICATIONS '
                f'/DIR="{app_dir}" /LOG="{log_file}"'
            ),
            "if errorlevel 1 exit /b 1",
            "ping -n 2 127.0.0.1 >nul",
            f'start "" "{target}"',
            'del "%~f0"',
            "",
        ]
    )
    helper.write_text(script, encoding="gbk", errors="replace")

    creationflags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        creationflags |= subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    subprocess.Popen(
        ["cmd.exe", "/c", str(helper)],
        cwd=str(helper.parent),
        creationflags=creationflags,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    logger.info("Silent upgrade helper started: %s", helper)
    return helper


def apply_update(
    feed_url: str,
    on_progress: DownloadProgressCallback | None = None,
) -> UpdateCheckResult:
    """Check, download, verify, launch silent installer. Caller must exit the app."""
    if not is_frozen() and not os.environ.get("STEADYOPS_ALLOW_DEV_UPDATE"):
        raise UpdateError("开发模式默认不执行安装升级。打包安装后再测，或设置 STEADYOPS_ALLOW_DEV_UPDATE=1")

    result = check_for_update(feed_url)
    if not result.available:
        return result

    setup = download_installer(result.url, result.sha256, on_progress=on_progress)
    launch_silent_upgrade(setup)
    return result
