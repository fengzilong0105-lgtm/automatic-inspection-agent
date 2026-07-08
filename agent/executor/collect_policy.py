from __future__ import annotations

import re
from pathlib import PurePosixPath

_MAX_COMMAND_LEN = 4000
_OUTPUT_PLACEHOLDER = "{{output_path}}"
_DANGEROUS_PATTERNS = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"\brm\s+",
        r"\brmdir\b",
        r"\bdd\s+",
        r"\bmkfs\b",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bpoweroff\b",
        r"\bhalt\b",
        r"\binit\s+0\b",
        r"\bkill(?:all)?\b",
        r"\bpkill\b",
        r"\bchmod\b",
        r"\bchown\b",
        r"\bmv\s+",
        r"\bmkdir\b",
        r"\btouch\b",
        r"\btruncate\b",
        r"\bcurl\b[^|]*\|\s*(ba)?sh",
        r"\bwget\b[^|]*\|\s*(ba)?sh",
        r";",
        r"&&",
        r"\|\|",
        r">\s*(?!{{output_path}})",
        r">>",
    )
)


def normalize_collect_output_path(path: str) -> str:
    raw = (path or "").strip()
    if not raw.startswith("/"):
        raise ValueError("remote_output_path 必须是 Linux 绝对路径")
    parts = PurePosixPath(raw).parts
    if ".." in parts:
        raise ValueError("remote_output_path 不能包含 ..")
    normalized = str(PurePosixPath(*parts))
    if normalized in {"/", "/tmp", "/var/tmp", "/dev/shm"}:
        raise ValueError("请提供具体文件路径，而不是目录")
    return normalized


def is_collect_output_path_allowed(path: str) -> bool:
    normalized = normalize_collect_output_path(path)
    return normalized.startswith(("/tmp/", "/var/tmp/", "/dev/shm/"))


def validate_collect_command(command: str) -> str:
    cmd = command.strip()
    if not cmd:
        raise ValueError("采集命令不能为空")
    if len(cmd) > _MAX_COMMAND_LEN:
        raise ValueError(f"采集命令过长（最多 {_MAX_COMMAND_LEN} 字符）")
    if "\n" in cmd or "\r" in cmd:
        raise ValueError("不支持多行采集命令")
    for pattern in _DANGEROUS_PATTERNS:
        if pattern.search(cmd):
            raise ValueError("采集命令被安全策略拒绝（含删除/改权/链式执行/自定义重定向等高风险操作）")
    return cmd


def build_collect_command(command: str, output_path: str) -> str:
    validated = validate_collect_command(command)
    normalized_output = normalize_collect_output_path(output_path)
    if _OUTPUT_PLACEHOLDER in validated:
        return validated.replace(_OUTPUT_PLACEHOLDER, normalized_output)
    return f"{validated} > {normalized_output}"
