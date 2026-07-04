from __future__ import annotations

from datetime import datetime

STATUS_LABELS: dict[str, str] = {
    "open": "未处理",
    "diagnosing": "分析中",
    "notified": "已通知",
    "resolved": "已解决",
}

STATUS_COLORS: dict[str, tuple[str, str]] = {
    "open": ("#FFF2F0", "#CF1322"),
    "diagnosing": ("#E6F4FF", "#096DD9"),
    "notified": ("#FFF7E6", "#D46B08"),
    "resolved": ("#F6FFED", "#389E0D"),
}

SEVERITY_COLORS: dict[str, tuple[str, str]] = {
    "P0": ("#FFF2F0", "#CF1322"),
    "P1": ("#FFF7E6", "#D46B08"),
    "P2": ("#E6F4FF", "#096DD9"),
}


def _enum_token(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text.lower()


def format_datetime(value: object) -> str:
    if not value:
        return "-"
    text = str(value).strip()
    try:
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text.replace("T", " ")[:19]


def format_incident_status(value: object) -> str:
    token = _enum_token(value)
    if token in STATUS_LABELS:
        return STATUS_LABELS[token]
    return str(value) if value else "-"


def format_incident_severity(value: object) -> str:
    token = _enum_token(value).upper()
    if token in SEVERITY_COLORS:
        return token
    return str(value) if value else "-"


def incident_status_colors(value: object) -> tuple[str, str]:
    token = _enum_token(value)
    return STATUS_COLORS.get(token, ("#F5F5F5", "#595959"))


def incident_severity_colors(value: object) -> tuple[str, str]:
    token = _enum_token(value).upper()
    return SEVERITY_COLORS.get(token, ("#F5F5F5", "#595959"))
