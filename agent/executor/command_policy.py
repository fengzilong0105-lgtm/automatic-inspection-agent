from __future__ import annotations

import re
from typing import Literal

_MAX_COMMAND_LEN = 4000
_MAX_OUTPUT_CHARS = 32_000

CommandRisk = Literal["allow", "confirm"]

# 只读侧常用设备写，不算落盘写操作
_SAFE_REDIRECT_TARGETS = re.compile(
    r">\s*(?:/dev/(?:null|tcp|udp|stdout|stderr)\b)",
    re.I,
)

# 会改状态 / 写盘 / 启停进程的命令 → 需人工确认，不再硬拒绝
_CONFIRM_PATTERNS = tuple(
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
        r"\binit\s+[06]\b",
        r"\bkill(?:all)?\b",
        r"\bpkill\b",
        r"\bchmod\b",
        r"\bchown\b",
        r"\bmv\s+",
        r"\bcp\s+",
        r"\bmkdir\b",
        r"\btouch\b",
        r"\btruncate\b",
        r"\btee\b",
        r"\bnohup\b",
        r"\bsystemctl\s+(?:start|stop|restart|reload|kill|mask|unmask|enable|disable|daemon-reload)\b",
        r"\bservice\s+\S+\s+(?:start|stop|restart|reload)\b",
        r"\bdocker\s+(?:start|stop|restart|kill|rm|run|exec|compose)\b",
        r"\bcrontab\b",
        r"\buseradd\b",
        r"\buserdel\b",
        r"\bpasswd\b",
        r"\bsu\b",
        r"\bsudo\b",
        r"\|\s*(?:ba)?sh\b",
        r"\bcurl\b[^|]*\|\s*(?:ba)?sh",
        r"\bwget\b[^|]*\|\s*(?:ba)?sh",
        r"&\s*$",  # 后台执行
    )
)


def normalize_remote_command(command: str) -> str:
    cmd = (command or "").strip()
    if not cmd:
        raise ValueError("命令不能为空")
    if len(cmd) > _MAX_COMMAND_LEN:
        raise ValueError(f"命令过长（最多 {_MAX_COMMAND_LEN} 字符）")
    if "\n" in cmd or "\r" in cmd:
        raise ValueError("不支持多行命令；可用 && / ; 连接")
    return cmd


def classify_remote_command(command: str) -> CommandRisk:
    """Classify command risk: allow = run now; confirm = needs human approval."""
    cmd = normalize_remote_command(command)
    # 先去掉 /dev/null|/dev/tcp 等安全重定向，避免端口探测被当成写盘
    scrubbed = _SAFE_REDIRECT_TARGETS.sub(" ", cmd)
    if ">" in scrubbed or ">>" in scrubbed:
        return "confirm"
    for pattern in _CONFIRM_PATTERNS:
        if pattern.search(scrubbed):
            return "confirm"
    return "allow"


def validate_remote_command(command: str) -> str:
    """Backward-compatible helper: normalize only (no hard reject)."""
    return normalize_remote_command(command)


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
