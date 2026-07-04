from __future__ import annotations

import json
import re
from typing import Any

from agent.settings import get_settings

_ERROR_PATTERNS = re.compile(
    r"ERROR|WARN(?:ING)?|Exception|FATAL|CRITICAL|Traceback|OOM|OutOfMemory",
    re.IGNORECASE,
)

_DEPLOYMENT_KEEP_KEYS = {
    "service_id",
    "host",
    "registered",
    "runtime",
    "systemd_probe",
    "startup_summary",
    "status",
    "deploy_dir",
    "pid",
    "cmdline",
    "jar_path",
    "listen_ports",
    "log_candidates",
    "registry_updated",
    "source_labels",
}


def _tool_compression_config():
    return get_settings().config.chat.tool_compression


def _compress_read_log(text: str, *, tail_lines: int, error_scan: bool) -> str:
    lines = text.splitlines()
    total = len(lines)
    if total <= tail_lines and not error_scan:
        return text

    selected: list[str] = []
    seen: set[int] = set()

    if error_scan:
        for idx, line in enumerate(lines):
            if _ERROR_PATTERNS.search(line):
                selected.append(line)
                seen.add(idx)

    tail_start = max(0, total - tail_lines)
    for idx in range(tail_start, total):
        if idx not in seen:
            selected.append(lines[idx])
            seen.add(idx)

    if not selected:
        selected = lines[-tail_lines:]

    header = f"[read_log 压缩] 共 {total} 行"
    if error_scan:
        error_count = sum(1 for line in lines if _ERROR_PATTERNS.search(line))
        header += f"，含 ERROR/WARN/Exception 等 {error_count} 行"
    header += f"；展示 {len(selected)} 行（末尾 {tail_lines} 行 + 异常行）\n"
    return header + "\n".join(selected)


def _compress_read_remote_file(text: str, *, max_bytes: int = 8192) -> str:
    if len(text.encode("utf-8", errors="ignore")) <= max_bytes:
        return text

    lines = text.splitlines()
    if len(lines) <= 80:
        return text[:max_bytes] + "\n[已截断：超出 8KB 限制]"

    head = lines[:40]
    tail = lines[-40:]
    return (
        "\n".join(head)
        + f"\n\n... [已截断，省略 {len(lines) - 80} 行，原文约 {len(text)} 字符] ...\n\n"
        + "\n".join(tail)
    )


def _compress_run_remote_command(text: str, *, limit: int = 4000) -> str:
    if len(text) <= limit * 2:
        return text

    def _truncate_block(label: str, block: str) -> str:
        if len(block) <= limit:
            return block
        return block[:limit] + f"\n... [{label} 已截断，原长 {len(block)} 字符]"

    stdout_marker = "stdout:"
    stderr_marker = "stderr:"
    lower = text.lower()
    if stdout_marker in lower and stderr_marker in lower:
        parts = re.split(r"(?i)(stdout:|stderr:)", text)
        rebuilt: list[str] = []
        current_label = ""
        for part in parts:
            if part.lower() in {"stdout:", "stderr:"}:
                current_label = part.strip(":").upper()
                rebuilt.append(part)
                continue
            if current_label:
                rebuilt.append(_truncate_block(current_label, part))
            else:
                rebuilt.append(part)
        return "".join(rebuilt)

    half = limit
    return text[:half] + f"\n... [已截断，原长 {len(text)} 字符] ...\n" + text[-half:]


def _compress_get_deployment_info(text: str, *, max_bytes: int = 4096) -> str:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text[:max_bytes] + ("\n[已截断]" if len(text) > max_bytes else "")

    if len(text.encode("utf-8", errors="ignore")) <= max_bytes:
        return text

    slim: dict[str, Any] = {}
    for key, value in payload.items():
        if key in _DEPLOYMENT_KEEP_KEYS:
            slim[key] = value

    slim["_compressed"] = True
    slim["_note"] = "已去掉冗余字段以节省上下文；需要完整 JSON 请缩小查询范围"
    result = json.dumps(slim, ensure_ascii=False, indent=2, default=str)
    if len(result.encode("utf-8", errors="ignore")) > max_bytes:
        return result[:max_bytes] + "\n[已截断]"
    return result


def _compress_analyze_incident(text: str, *, log_limit: int = 2000) -> str:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        if len(text) <= log_limit:
            return text
        return text[:log_limit] + f"\n[已截断，原长 {len(text)} 字符]"

    kept = {
        "root_cause": payload.get("root_cause"),
        "severity": payload.get("severity"),
        "suggestions": payload.get("suggestions"),
        "propose_restart": payload.get("propose_restart"),
        "summary": payload.get("summary"),
    }
    for noisy_key in ("log_snippet", "logs", "recent_logs", "evidence"):
        if noisy_key in payload and payload[noisy_key]:
            snippet = str(payload[noisy_key])
            kept[noisy_key] = snippet[:log_limit] + ("…" if len(snippet) > log_limit else "")

    return json.dumps(kept, ensure_ascii=False, indent=2)


def compress_tool_output(tool_name: str, raw: str) -> str:
    """Compress tool output before it enters chat history / model context."""
    cfg = _tool_compression_config()
    if not cfg.enabled or not raw:
        return raw

    if tool_name == "read_log":
        return _compress_read_log(raw, tail_lines=cfg.log_tail_lines, error_scan=cfg.log_error_scan)
    if tool_name == "read_remote_file":
        return _compress_read_remote_file(raw)
    if tool_name == "run_remote_command":
        return _compress_run_remote_command(raw)
    if tool_name == "get_deployment_info":
        return _compress_get_deployment_info(raw)
    if tool_name == "analyze_incident":
        return _compress_analyze_incident(raw)
    return raw


def aggressive_compress_tool_output(tool_name: str, raw: str) -> str:
    """Aggressive single-turn compression: keep only critical lines."""
    if not raw:
        return raw
    if tool_name == "read_log":
        lines = [line for line in raw.splitlines() if _ERROR_PATTERNS.search(line)]
        if not lines:
            lines = raw.splitlines()[-20:]
        return "[激进压缩 read_log]\n" + "\n".join(lines[:50])
    if tool_name == "read_remote_file":
        return raw[:1500] + "\n[激进压缩：仅保留前 1500 字符]"
    if tool_name == "run_remote_command":
        return raw[:2000] + "\n[激进压缩：仅保留前 2000 字符]"
    if tool_name in {"get_deployment_info", "analyze_incident"}:
        return _compress_get_deployment_info(raw, max_bytes=2048)
    return raw[:2000] + ("\n[激进压缩]" if len(raw) > 2000 else "")
