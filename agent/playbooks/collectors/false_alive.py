from __future__ import annotations

import shlex

from agent.executor.java_probe import find_java_process
from agent.executor.systemd_probe import probe_systemd_unit
from agent.models import ServiceConfig, ServiceStatus, ServiceType
from agent.playbooks.config import DEFAULT_FALSE_ALIVE_THRESHOLDS, FalseAliveThresholds
from agent.playbooks.models import CheckResult, CheckStatus, FalseAliveCollectorState
from agent.playbooks.parsers.port_probe import cmdline_matches_jar, parse_ss_listening_ports


async def _resolve_ports(executor, service: ServiceConfig, state: FalseAliveCollectorState) -> list[int]:
    ports = list(service.listen_ports or [])
    if ports:
        return sorted(set(ports))
    if service.type == ServiceType.JAVA:
        probe = await find_java_process(executor, service)
        primary = probe.get("primary") or {}
        discovered = primary.get("listen_ports") or []
        if discovered:
            return sorted(set(int(p) for p in discovered))
        if state.pid:
            ss = await executor.run(f"ss -lntp 2>/dev/null | grep 'pid={state.pid},' || true")
            return sorted(parse_ss_listening_ports(ss.stdout))
    return []


async def _check_port_listening(executor, port: int) -> bool:
    result = await executor.run(
        f"ss -lntH 2>/dev/null | awk '{{print $4}}' | grep -E ':{port}$' || true"
    )
    if result.stdout.strip():
        return True
    fallback = await executor.run(f"ss -lnt 2>/dev/null | grep -E ':{port} ' || true")
    return bool(fallback.stdout.strip())


async def _check_tcp_connect(executor, port: int, timeout: int) -> bool:
    cmd = (
        f"timeout {timeout} bash -c 'cat < /dev/null > /dev/tcp/127.0.0.1/{port}' "
        f"2>/dev/null && echo OK || echo FAIL"
    )
    result = await executor.run(cmd, timeout=timeout + 5)
    return result.stdout.strip().upper() == "OK"


async def collect_false_alive(
    executor,
    service: ServiceConfig,
    state: FalseAliveCollectorState,
    status: ServiceStatus,
    thresholds: FalseAliveThresholds = DEFAULT_FALSE_ALIVE_THRESHOLDS,
) -> None:
    state.running = status.running
    state.health_ok = status.health_ok
    state.health_detail = status.health_detail or ""
    state.status_detail = status.detail or ""

    state.add_check(
        CheckResult(
            id="probe_running",
            name="存活探针",
            status=CheckStatus.PASS if status.running else CheckStatus.FAIL,
            detail="运行中" if status.running else f"未运行：{status.detail}",
            source="live_probe",
            metrics={"running": status.running},
        )
    )

    if not status.running:
        state.add_check(
            CheckResult(
                id="false_alive_context",
                name="假活判定",
                status=CheckStatus.SKIP,
                detail="服务未运行，属于真宕机而非假活",
                source="live_probe",
            )
        )
        return

    if service.health_url:
        if status.health_ok is False:
            state.categories.add("health_dead")
            state.critical = True
            detail = f"进程在运行但健康检查失败（{status.health_detail}）"
            state.add_check(
                CheckResult(
                    id="health_vs_running",
                    name="健康检查与存活矛盾",
                    status=CheckStatus.FAIL,
                    detail=detail,
                    source="live_probe",
                    metrics={"health_ok": False, "health_url": service.health_url},
                )
            )
            state.evidence.append(detail)
            state.next_commands.append(
                f"curl -v -m {thresholds.health_curl_timeout_seconds} {shlex.quote(service.health_url)}"
            )
        elif status.health_ok is True:
            state.add_check(
                CheckResult(
                    id="health_vs_running",
                    name="健康检查与存活矛盾",
                    status=CheckStatus.PASS,
                    detail=f"健康检查通过（{status.health_detail}）",
                    source="live_probe",
                )
            )
        else:
            state.add_check(
                CheckResult(
                    id="health_vs_running",
                    name="健康检查与存活矛盾",
                    status=CheckStatus.UNKNOWN,
                    detail="健康检查结果未知",
                    source="live_probe",
                )
            )
    else:
        state.limitations.append("未配置 health_url，无法做健康检查交叉验证")
        state.add_check(
            CheckResult(
                id="health_vs_running",
                name="健康检查与存活矛盾",
                status=CheckStatus.SKIP,
                detail="未配置 health_url",
                source="live_probe",
            )
        )

    state.ports = await _resolve_ports(executor, service, state)
    if not state.ports:
        state.limitations.append("未配置 listen_ports 且未能从进程探测端口")
        state.add_check(
            CheckResult(
                id="port_listening",
                name="监听端口",
                status=CheckStatus.SKIP,
                detail="无可用端口列表",
                source="live_probe",
            )
        )
    else:
        dead_ports: list[int] = []
        for port in state.ports:
            listening = await _check_port_listening(executor, port)
            tcp_ok = await _check_tcp_connect(executor, port, thresholds.tcp_connect_timeout_seconds)
            state.port_results.append(
                {"port": port, "listening": listening, "tcp_connect": tcp_ok}
            )
            if not listening:
                dead_ports.append(port)

        if dead_ports:
            state.categories.add("port_dead")
            state.critical = True
            detail = f"进程在运行但端口未监听: {', '.join(str(p) for p in dead_ports)}"
            state.add_check(
                CheckResult(
                    id="port_listening",
                    name="监听端口",
                    status=CheckStatus.FAIL,
                    detail=detail,
                    source="live_probe",
                    metrics={"dead_ports": dead_ports, "checked_ports": state.ports},
                )
            )
            state.evidence.append(detail)
            state.next_commands.append(f"ss -lntp | grep -E ':({'|'.join(str(p) for p in dead_ports)}) '")
        else:
            state.add_check(
                CheckResult(
                    id="port_listening",
                    name="监听端口",
                    status=CheckStatus.PASS,
                    detail=f"端口 {', '.join(str(p) for p in state.ports)} 均在监听",
                    source="live_probe",
                )
            )

        failed_tcp = [item["port"] for item in state.port_results if not item.get("tcp_connect")]
        if failed_tcp and not dead_ports:
            detail = f"端口在监听但 TCP 连接失败: {', '.join(str(p) for p in failed_tcp)}"
            state.categories.add("port_dead")
            state.add_check(
                CheckResult(
                    id="tcp_connect",
                    name="TCP 连通性",
                    status=CheckStatus.WARN,
                    detail=detail,
                    source="live_probe",
                )
            )
            state.evidence.append(detail)
        elif state.port_results:
            state.add_check(
                CheckResult(
                    id="tcp_connect",
                    name="TCP 连通性",
                    status=CheckStatus.PASS,
                    detail="本地 TCP 探测可达",
                    source="live_probe",
                )
            )

    unit = service.systemd_unit
    if unit:
        probe = await probe_systemd_unit(executor, unit)
        state.systemd_main_pid = probe.get("main_pid")
        state.systemd_sub_state = probe.get("state") or ""
        main_pid = probe.get("main_pid")
        if state.pid and main_pid and state.pid != main_pid:
            state.categories.add("systemd_mismatch")
            detail = f"探测 PID={state.pid} 与 systemd MainPID={main_pid} 不一致"
            state.add_check(
                CheckResult(
                    id="systemd_main_pid",
                    name="systemd MainPID 一致性",
                    status=CheckStatus.FAIL,
                    detail=detail,
                    source="live_probe",
                    metrics={"pid": state.pid, "main_pid": main_pid},
                )
            )
            state.evidence.append(detail)
            state.next_commands.append(f"systemctl status {shlex.quote(unit)}")
        elif main_pid:
            state.add_check(
                CheckResult(
                    id="systemd_main_pid",
                    name="systemd MainPID 一致性",
                    status=CheckStatus.PASS,
                    detail=f"MainPID={main_pid} 与探测 PID 一致",
                    source="live_probe",
                )
            )
        sub = probe.get("state") or ""
        if sub and sub not in {"active"}:
            state.add_check(
                CheckResult(
                    id="systemd_active_state",
                    name="systemd 状态",
                    status=CheckStatus.WARN,
                    detail=f"ActiveState={sub}",
                    source="live_probe",
                )
            )
    else:
        state.add_check(
            CheckResult(
                id="systemd_main_pid",
                name="systemd MainPID 一致性",
                status=CheckStatus.SKIP,
                detail="未配置 systemd_unit",
                source="live_probe",
            )
        )

    if service.type == ServiceType.JAVA and service.jar_path and state.cmdline:
        matched = cmdline_matches_jar(state.cmdline, service.jar_path)
        if matched is False:
            state.categories.add("wrong_process")
            state.critical = True
            jar_name = service.jar_path.split("/")[-1]
            detail = f"进程 cmdline 未包含注册 jar: {jar_name}"
            state.add_check(
                CheckResult(
                    id="pid_cmdline_match",
                    name="进程身份匹配",
                    status=CheckStatus.FAIL,
                    detail=detail,
                    source="config_registry",
                )
            )
            state.evidence.append(detail)
        elif matched is True:
            state.add_check(
                CheckResult(
                    id="pid_cmdline_match",
                    name="进程身份匹配",
                    status=CheckStatus.PASS,
                    detail="cmdline 与 jar_path 匹配",
                    source="config_registry",
                )
            )
    else:
        state.add_check(
            CheckResult(
                id="pid_cmdline_match",
                name="进程身份匹配",
                status=CheckStatus.SKIP,
                detail="无 jar_path 或非 Java 服务",
                source="config_registry",
            )
        )

    container = state.container_name or service.container_name
    if container:
        quoted = shlex.quote(container)
        health = await executor.run(
            f"docker inspect -f '{{{{.State.Health.Status}}}}' {quoted} 2>/dev/null || echo none"
        )
        health_status = (health.stdout or "none").strip().lower()
        if health_status == "unhealthy":
            state.categories.add("docker_unhealthy")
            state.critical = True
            detail = "容器 Running 但 Docker Health=unhealthy"
            state.add_check(
                CheckResult(
                    id="docker_health",
                    name="容器健康状态",
                    status=CheckStatus.FAIL,
                    detail=detail,
                    source="live_probe",
                )
            )
            state.evidence.append(detail)
        elif health_status in {"healthy", "none", ""}:
            state.add_check(
                CheckResult(
                    id="docker_health",
                    name="容器健康状态",
                    status=CheckStatus.PASS,
                    detail=f"Docker Health={health_status or '未配置'}",
                    source="live_probe",
                )
            )
        else:
            state.add_check(
                CheckResult(
                    id="docker_health",
                    name="容器健康状态",
                    status=CheckStatus.WARN,
                    detail=f"Docker Health={health_status}",
                    source="live_probe",
                )
            )
