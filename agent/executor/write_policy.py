from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath

from agent.models import AutonomyConfig

_MAX_WRITE_BYTES = 65_536
_PREVIEW_CHARS = 800

DEFAULT_WRITE_WHITELIST = (
    "/etc/systemd/system/*.service",
    "/etc/systemd/system/*.timer",
)


def normalize_remote_path(path: str) -> str:
    raw = (path or "").strip()
    if not raw.startswith("/"):
        raise ValueError("路径必须是绝对路径，例如 /etc/systemd/system/app.service")
    parts = PurePosixPath(raw).parts
    if ".." in parts:
        raise ValueError("路径不能包含 ..")
    normalized = str(PurePosixPath(*parts))
    if normalized == "/":
        raise ValueError("不允许对根目录 / 执行文件操作")
    return normalized


def is_path_allowed(path: str, autonomy: AutonomyConfig) -> bool:
    normalized = normalize_remote_path(path)
    if autonomy.write_allow_all_paths:
        return True
    patterns = autonomy.write_path_whitelist or list(DEFAULT_WRITE_WHITELIST)
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in patterns)


def validate_write_content(content: str) -> str:
    if content is None:
        raise ValueError("文件内容不能为空")
    text = str(content)
    if not text.strip():
        raise ValueError("文件内容不能为空")
    size = len(text.encode("utf-8"))
    if size > _MAX_WRITE_BYTES:
        raise ValueError(f"文件过大（{size} 字节），上限 {_MAX_WRITE_BYTES} 字节")
    return text


def content_preview(content: str, limit: int = _PREVIEW_CHARS) -> str:
    if len(content) <= limit:
        return content
    return content[:limit] + "\n…（预览已截断）"
