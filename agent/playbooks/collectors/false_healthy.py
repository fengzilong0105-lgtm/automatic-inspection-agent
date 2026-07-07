from __future__ import annotations

import re
import shlex

from agent.models import ServiceConfig, ServiceStatus
from agent.playbooks.config import (
    DEFAULT_FALSE_HEALTHY_THRESHOLDS,
    FALSE_HEALTHY_LOG_PATTERN,
    FalseHealthyThresholds,
)
from agent.playbooks.models import CheckResult, CheckStatus, FalseHealthyCollectorState
from agent.playbooks.parsers.actuator_health import parse_actuator_health, parse_curl_meta

_ERROR_LINE_RE = re.compile(r"ERROR|Exception", re.I)
_BUSINESS_LINE_RE = re.compile(FALSE_HEALTHY_LOG_PATTERN, re.I)


def resolve_deep_health_url(service: ServiceConfig) -> str | None:
    return service.health_deep_url or service.health_url


async def _http_probe(executor, url: str, timeout: int) -> dict:
    quoted = shlex.quote(url)
    cmd = (
        f"curl -sS -m {timeout} -w '\\n__META__code=%{{http_code}}&time=%{{time_total}}' "
        f"{quoted} 2>/dev/null || echo '__META__code=000&time=0'"
    )
    result = await executor.run(cmd, timeout=timeout + 5)
    body, http_code, latency = parse_curl_meta(result.stdout)
    return {
        "body": body,
        "http_code": http_code,
        "latency_seconds": latency,
        "raw": result.stdout,
    }


async def collect_false_healthy(
    executor,
    service: ServiceConfig,
    state: FalseHealthyCollectorState,
    status: ServiceStatus,
    thresholds: FalseHealthyThresholds = DEFAULT_FALSE_HEALTHY_THRESHOLDS,
) -> None:
    state.running = status.running
    state.health_ok = status.health_ok
    state.health_detail = status.health_detail or ""

    state.add_check(
        CheckResult(
            id="service_running",
            name="服务运行状态",
            status=CheckStatus.PASS if status.running else CheckStatus.FAIL,
            detail="运行中" if status.running else f"未运行：{status.detail}",
            source="live_probe",
        )
    )

    if not status.running:
        state.add_check(
            CheckResult(
                id="false_healthy_context",
                name="假健康判定",
                status=CheckStatus.SKIP,
                detail="服务未运行，不属于假健康",
                source="live_probe",
            )
        )
        return

    if not service.health_url and not service.health_deep_url:
        state.limitations.append("未配置 health_url / health_deep_url，无法评估假健康")
        state.add_check(
            CheckResult(
                id="shallow_health",
                name="浅健康检查",
                status=CheckStatus.SKIP,
                detail="未配置 health_url",
                source="live_probe",
            )
        )
        return

    shallow_ok = status.health_ok is True
    shallow_status = CheckStatus.PASS if shallow_ok else CheckStatus.FAIL
    if status.health_ok is None:
        shallow_status = CheckStatus.UNKNOWN

    state.add_check(
        CheckResult(
            id="shallow_health",
            name="浅健康检查",
            status=shallow_status,
            detail=status.health_detail or ("通过" if shallow_ok else "未通过/未知"),
            source="live_probe",
            metrics={"health_ok": status.health_ok, "health_url": service.health_url},
        )
    )

    if status.health_ok is False:
        state.add_check(
            CheckResult(
                id="false_healthy_context",
                name="假健康判定",
                status=CheckStatus.SKIP,
                detail="健康检查未通过，属于真不健康而非假健康",
                source="live_probe",
            )
        )
        return

    if status.health_ok is None:
        state.limitations.append("浅健康结果未知，假健康结论置信度降低")

    deep_url = resolve_deep_health_url(service)
    if not deep_url:
        state.limitations.append("无 deep health URL")
        return

    state.deep_health_url = deep_url
    probe = await _http_probe(executor, deep_url, thresholds.health_curl_timeout_seconds)
    state.health_body = probe.get("body") or ""
    state.shallow_http_code = probe.get("http_code")
    state.health_latency_seconds = probe.get("latency_seconds")

    latency = state.health_latency_seconds
    if latency is not None:
        if latency >= thresholds.health_latency_fail_seconds:
            state.categories.add("health_slow")
            state.add_check(
                CheckResult(
                    id="health_latency",
                    name="健康检查响应时间",
                    status=CheckStatus.FAIL,
                    detail=f"响应 {latency:.2f}s（阈值 {thresholds.health_latency_fail_seconds}s）",
                    source="live_probe",
                    metrics={"latency_seconds": latency},
                )
            )
            state.evidence.append(f"health 响应过慢 {latency:.2f}s")
        elif latency >= thresholds.health_latency_warn_seconds:
            state.categories.add("health_slow")
            state.add_check(
                CheckResult(
                    id="health_latency",
                    name="健康检查响应时间",
                    status=CheckStatus.WARN,
                    detail=f"响应 {latency:.2f}s（warn {thresholds.health_latency_warn_seconds}s）",
                    source="live_probe",
                    metrics={"latency_seconds": latency},
                )
            )
        else:
            state.add_check(
                CheckResult(
                    id="health_latency",
                    name="健康检查响应时间",
                    status=CheckStatus.PASS,
                    detail=f"响应 {latency:.2f}s",
                    source="live_probe",
                )
            )

    summary = parse_actuator_health(state.health_body)
    if summary.parse_ok:
        down = summary.down_components
        state.down_components = down
        if down:
            state.categories.add("component_down")
            if summary.readiness_down:
                state.categories.add("readiness_down")
            names = ", ".join(item["path"] for item in down[:5])
            detail = f"顶层 {summary.top_status or '?'}，但组件异常: {names}"
            state.add_check(
                CheckResult(
                    id="deep_health_parse",
                    name="深健康 JSON 解析",
                    status=CheckStatus.FAIL,
                    detail=detail,
                    source="live_probe",
                    metrics={"down_components": down, "top_status": summary.top_status},
                )
            )
            state.evidence.append(detail)
            state.next_commands.append(
                f"curl -sS -m {thresholds.health_curl_timeout_seconds} {shlex.quote(deep_url)} | head -c 4096"
            )
        else:
            state.add_check(
                CheckResult(
                    id="deep_health_parse",
                    name="深健康 JSON 解析",
                    status=CheckStatus.PASS,
                    detail=f"解析成功，top={summary.top_status or 'UP'}，组件均正常",
                    source="live_probe",
                    metrics={"top_status": summary.top_status},
                )
            )
    else:
        looks_json = state.health_body.lstrip().startswith("{")
        if looks_json:
            state.add_check(
                CheckResult(
                    id="deep_health_parse",
                    name="深健康 JSON 解析",
                    status=CheckStatus.UNKNOWN,
                    detail=f"JSON 解析失败: {summary.raw_error}",
                    source="live_probe",
                )
            )
            state.limitations.append("health 响应非标准 Actuator JSON，仅依赖浅健康与日志")
        else:
            state.add_check(
                CheckResult(
                    id="deep_health_parse",
                    name="深健康 JSON 解析",
                    status=CheckStatus.SKIP,
                    detail="health 响应非 JSON（可能是简单 UP 探针）",
                    source="live_probe",
                )
            )
            state.limitations.append("health 非 JSON，无法做 component 级解析")

    if service.business_probe_url:
        biz = await _http_probe(
            executor,
            service.business_probe_url,
            thresholds.health_curl_timeout_seconds,
        )
        code = biz.get("http_code")
        body = biz.get("body") or ""
        expect = service.business_probe_expect_code
        code_ok = code == expect
        body_ok = True
        if service.business_probe_body_contains:
            body_ok = service.business_probe_body_contains in body
        ok = code_ok and body_ok
        state.business_probe_ok = ok
        if not ok:
            state.categories.add("business_probe_fail")
            state.critical = True
            detail = f"business_probe HTTP {code}（期望 {expect}）"
            if not body_ok:
                detail += f"，响应未包含 `{service.business_probe_body_contains}`"
            state.add_check(
                CheckResult(
                    id="business_probe",
                    name="业务冒烟探针",
                    status=CheckStatus.FAIL,
                    detail=detail,
                    source="live_probe",
                    metrics={"http_code": code, "url": service.business_probe_url},
                )
            )
            state.evidence.append(f"health 通过但业务探针失败: {detail}")
        else:
            state.add_check(
                CheckResult(
                    id="business_probe",
                    name="业务冒烟探针",
                    status=CheckStatus.PASS,
                    detail=f"HTTP {code}，业务探针通过",
                    source="live_probe",
                )
            )
    else:
        state.add_check(
            CheckResult(
                id="business_probe",
                name="业务冒烟探针",
                status=CheckStatus.SKIP,
                detail="未配置 business_probe_url",
                source="config_registry",
            )
        )

    if service.log_path:
        raw = await executor.tail_log(
            service.log_path,
            lines=thresholds.log_tail_lines,
            pattern="ERROR|Exception|FATAL|timeout|503|CircuitBreaker",
        )
        lines = raw.splitlines()
        error_lines = [line for line in lines if _ERROR_LINE_RE.search(line)]
        business_lines = [line for line in lines if _BUSINESS_LINE_RE.search(line)]
        state.log_error_count = len(error_lines)
        state.log_business_error_count = len(business_lines)

        if state.log_error_count >= thresholds.log_error_fail_count:
            state.categories.add("log_error_surge")
            detail = f"health 通过但日志含 {state.log_error_count} 条 ERROR/Exception"
            state.add_check(
                CheckResult(
                    id="log_error_vs_health",
                    name="日志 ERROR 与健康矛盾",
                    status=CheckStatus.FAIL,
                    detail=detail,
                    source="log",
                    metrics={"error_count": state.log_error_count},
                )
            )
            state.evidence.append(detail)
            state.next_commands.append(
                f"tail -n {thresholds.log_tail_lines} {shlex.quote(service.log_path)} | grep -Ei 'ERROR|Exception'"
            )
        elif state.log_error_count >= thresholds.log_error_warn_count:
            state.categories.add("log_error_surge")
            state.add_check(
                CheckResult(
                    id="log_error_vs_health",
                    name="日志 ERROR 与健康矛盾",
                    status=CheckStatus.WARN,
                    detail=f"最近日志有 {state.log_error_count} 条 ERROR/Exception",
                    source="log",
                )
            )
        else:
            state.add_check(
                CheckResult(
                    id="log_error_vs_health",
                    name="日志 ERROR 与健康矛盾",
                    status=CheckStatus.PASS,
                    detail=f"ERROR/Exception 计数 {state.log_error_count}",
                    source="log",
                )
            )

        biz_only = max(0, state.log_business_error_count - state.log_error_count)
        if biz_only >= thresholds.log_business_error_warn_count:
            state.categories.add("log_business_error")
            state.add_check(
                CheckResult(
                    id="log_business_errors",
                    name="业务异常日志",
                    status=CheckStatus.WARN if biz_only < thresholds.log_error_warn_count else CheckStatus.FAIL,
                    detail=f"命中 timeout/503/CircuitBreaker 等 {state.log_business_error_count} 条",
                    source="log",
                )
            )
            if biz_only >= thresholds.log_business_error_warn_count:
                state.evidence.append(
                    f"日志含业务异常关键字 {state.log_business_error_count} 条"
                )
        else:
            state.add_check(
                CheckResult(
                    id="log_business_errors",
                    name="业务异常日志",
                    status=CheckStatus.PASS,
                    detail="未发现明显业务异常日志",
                    source="log",
                )
            )
    else:
        state.limitations.append("未配置 log_path，无法做日志与健康交叉验证")
        state.add_check(
            CheckResult(
                id="log_error_vs_health",
                name="日志 ERROR 与健康矛盾",
                status=CheckStatus.SKIP,
                detail="未配置 log_path",
                source="log",
            )
        )
        state.add_check(
            CheckResult(
                id="log_business_errors",
                name="业务异常日志",
                status=CheckStatus.SKIP,
                detail="未配置 log_path",
                source="log",
            )
        )
