from __future__ import annotations

import re

_MAX_COMMAND_LEN = 2000
_MAX_OUTPUT_CHARS = 32_000

_BLOCKED_PATTERNS = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"\brm\s+-[a-z]*r",
        r"\brm\s+",
        r"\brmdir\b",
        r">\s*/",
        r">>",
        r"\bdd\s+",
        r"\bmkfs\b",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bpoweroff\b",
        r"\bhalt\b",
        r"\binit\s+0\b",
        r"\bkill\s+-9\b",
        r"\bpkill\b",
        r"\bkillall\b",
        r"\bchmod\b",
        r"\bchown\b",
        r"\bmv\s+",
        r"\bcp\s+.+\s+/",
        r"\|\s*sh\b",
        r"\|\s*bash\b",
        r"\bcurl\b[^|]*\|\s*(ba)?sh",
        r"\bwget\b[^|]*\|\s*(ba)?sh",
    )
)


def validate_remote_command(command: str) -> str:
    cmd = command.strip()
    if not cmd:
        raise ValueError("命令不能为空")
    if len(cmd) > _MAX_COMMAND_LEN:
        raise ValueError(f"命令过长（最多 {_MAX_COMMAND_LEN} 字符）")
    if "\n" in cmd or "\r" in cmd:
        raise ValueError("不支持多行命令")
    for pattern in _BLOCKED_PATTERNS:
        if pattern.search(cmd):
            raise ValueError("命令被安全策略拒绝（含删除/写盘/关机/管道执行等高风险操作）")
    return cmd


def format_command_result(host_label: str, command: str, result) -> str:
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    truncated = False
    if len(stdout) > _MAX_OUTPUT_CHARS:
        stdout = stdout[:_MAX_OUTPUT_CHARS]
        truncated = True

    lines = [
        f"# host: {host_label}",
        f"# command: {command}",
        f"# exit_code: {result.exit_code}",
    ]
    if truncated:
        lines.append(f"# stdout 已截断至前 {_MAX_OUTPUT_CHARS} 字符")
    lines.append("")
    if stdout:
        lines.append(stdout)
    if stderr:
        lines.append("")
        lines.append(f"[stderr]\n{stderr}")
    if not stdout and not stderr:
        lines.append("(无输出)")
    return "\n".join(lines)
