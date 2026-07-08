from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from agent.paths import get_app_root


def allowed_local_download_roots(data_dir: Path | None = None) -> list[Path]:
    roots = [
        Path.home() / "Downloads",
        Path.home() / "Desktop",
        get_app_root() / "downloads",
    ]
    if data_dir is not None:
        roots.append(data_dir / "downloads")

    deduped: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        resolved = root.resolve(strict=False)
        if resolved not in seen:
            seen.add(resolved)
            deduped.append(resolved)
    return deduped


def allowed_local_download_roots_text(data_dir: Path | None = None) -> str:
    return "、".join(str(path) for path in allowed_local_download_roots(data_dir))


def resolve_local_download_path(
    local_path: str | None,
    remote_path: str,
    *,
    data_dir: Path | None = None,
) -> Path:
    filename = PurePosixPath(remote_path.strip()).name or "download.bin"
    raw = (local_path or "").strip().strip('"')
    roots = allowed_local_download_roots(data_dir)
    candidate = roots[0] / filename

    if raw:
        expanded = Path(os.path.expandvars(os.path.expanduser(raw)))
        if raw.endswith(("\\", "/")) or (expanded.exists() and expanded.is_dir()):
            candidate = expanded / filename
        else:
            candidate = expanded

    resolved = candidate.resolve(strict=False)
    if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
        raise ValueError(
            "本地保存路径不在允许范围内。允许目录："
            f"{allowed_local_download_roots_text(data_dir)}"
        )
    return resolved
