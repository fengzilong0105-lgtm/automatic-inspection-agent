from __future__ import annotations

import re
from typing import Callable, Awaitable

from agent.models import IncidentSeverity, ServiceConfig, ServiceStatus

AlertCallback = Callable[..., Awaitable[None]]


class RuleEngine:
    ERROR_KEYWORDS = ("OOM", "OutOfMemoryError", "NullPointerException", "Connection refused")

    def evaluate(
        self,
        service: ServiceConfig,
        status: ServiceStatus,
        log_tail: str,
        health_fail_streak: int,
    ) -> list[dict]:
        alerts: list[dict] = []

        if not status.running:
            alerts.append(
                {
                    "title": f"服务 {service.id} 未运行",
                    "severity": IncidentSeverity.P0,
                    "summary": status.detail,
                    "log_snippet": log_tail[:2000],
                }
            )

        if status.health_ok is False:
            alerts.append(
                {
                    "title": f"服务 {service.id} 健康检查失败",
                    "severity": IncidentSeverity.P1 if health_fail_streak < 3 else IncidentSeverity.P0,
                    "summary": status.health_detail,
                    "log_snippet": log_tail[:2000],
                }
            )

        error_lines = [line for line in log_tail.splitlines() if re.search(r"ERROR|Exception", line, re.I)]
        if len(error_lines) >= 5:
            alerts.append(
                {
                    "title": f"服务 {service.id} 日志 ERROR 激增",
                    "severity": IncidentSeverity.P1,
                    "summary": f"最近日志中有 {len(error_lines)} 条 ERROR/Exception",
                    "log_snippet": "\n".join(error_lines[-20:]),
                }
            )

        for keyword in self.ERROR_KEYWORDS:
            if keyword.lower() in log_tail.lower():
                alerts.append(
                    {
                        "title": f"服务 {service.id} 出现关键字 {keyword}",
                        "severity": IncidentSeverity.P1,
                        "summary": f"日志包含关键字: {keyword}",
                        "log_snippet": log_tail[:2000],
                    }
                )
                break

        return alerts
