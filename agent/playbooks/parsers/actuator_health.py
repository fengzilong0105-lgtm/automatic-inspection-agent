from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

_UNHEALTHY = {"DOWN", "OUT_OF_SERVICE", "OUT-OF-SERVICE", "REFUSING_TRAFFIC"}
_READINESS_KEYS = {"readiness", "readinessstate", "readinessState"}


@dataclass
class ActuatorHealthSummary:
    parse_ok: bool = False
    top_status: str | None = None
    down_components: list[dict[str, str]] = field(default_factory=list)
    readiness_down: bool = False
    raw_error: str = ""


def _is_unhealthy(status: str | None) -> bool:
    if not status:
        return False
    normalized = status.strip().upper().replace("-", "_")
    return normalized in {item.replace("-", "_") for item in _UNHEALTHY}


def _walk_health_node(node: Any, path: str, summary: ActuatorHealthSummary) -> None:
    if isinstance(node, dict):
        status = node.get("status")
        if isinstance(status, str) and path and _is_unhealthy(status):
            detail = ""
            details = node.get("details")
            if isinstance(details, dict):
                detail = str(details.get("error") or details.get("message") or "")[:200]
            summary.down_components.append(
                {"path": path, "status": status, "detail": detail}
            )
            key_lower = path.rsplit(".", 1)[-1].lower()
            if key_lower in _READINESS_KEYS or "readiness" in path.lower():
                summary.readiness_down = True

        for key, value in node.items():
            if key in {"status", "details"}:
                continue
            child_path = f"{path}.{key}" if path else str(key)
            _walk_health_node(value, child_path, summary)

    elif isinstance(node, list):
        for idx, item in enumerate(node):
            _walk_health_node(item, f"{path}[{idx}]", summary)


def parse_actuator_health(body: str) -> ActuatorHealthSummary:
    summary = ActuatorHealthSummary()
    text = (body or "").strip()
    if not text:
        summary.raw_error = "empty body"
        return summary

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        summary.raw_error = str(exc)
        return summary

    summary.parse_ok = True
    if isinstance(payload, dict):
        top = payload.get("status")
        if isinstance(top, str):
            summary.top_status = top
        _walk_health_node(payload, "", summary)
    elif isinstance(payload, list):
        _walk_health_node(payload, "root", summary)
    else:
        summary.parse_ok = False
        summary.raw_error = "unexpected json type"

    return summary


def parse_curl_meta(stdout: str) -> tuple[str, int | None, float | None]:
    """Split curl body from trailing __META__code=...&time=... marker."""
    text = stdout or ""
    marker = "__META__"
    if marker not in text:
        return text.strip(), None, None

    body, _, meta = text.rpartition(marker)
    http_code: int | None = None
    latency: float | None = None
    for part in meta.strip().split("&"):
        if part.startswith("code="):
            raw = part.split("=", 1)[-1]
            http_code = int(raw) if raw.isdigit() else None
        elif part.startswith("time="):
            raw = part.split("=", 1)[-1]
            try:
                latency = float(raw)
            except ValueError:
                latency = None
    return body.strip(), http_code, latency
