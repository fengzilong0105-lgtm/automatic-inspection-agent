from __future__ import annotations

import re
from typing import TYPE_CHECKING

from agent.executor.java_probe import (
    _CONFIG_SUFFIXES,
    get_process_details,
    parse_jar_from_cmd,
    parse_profile_from_cmd,
    parse_ps_java_line,
)
from agent.models import DiscoveredService, ServiceType

if TYPE_CHECKING:
    from agent.executor.ssh import SSHRemoteExecutor

_JPS_SKIP = frozenset({"jps", "jar"})


async def detect_java(executor: SSHRemoteExecutor, host_id: str) -> list[DiscoveredService]:
    services: list[DiscoveredService] = []
    seen_pids: set[int] = set()

    ps = await executor.run("ps -eo pid,cmd | grep java | grep -v grep || true")
    for line in ps.stdout.splitlines():
        pid, cmd = parse_ps_java_line(line)
        if pid is None or pid in seen_pids or "grep" in cmd:
            continue
        if "java" not in cmd.lower():
            continue
        seen_pids.add(pid)
        discovered = await _build_discovered(executor, host_id, pid, cmd)
        if discovered is not None:
            services.append(discovered)

    jps = await executor.run("jps -l 2>/dev/null || jps 2>/dev/null || true")
    for line in jps.stdout.splitlines():
        pid, cmd = parse_ps_java_line(line)
        if pid is None or pid in seen_pids:
            continue
        simple = cmd.strip()
        if simple.lower() in _JPS_SKIP:
            continue
        seen_pids.add(pid)
        discovered = await _build_discovered(executor, host_id, pid, simple)
        if discovered is not None:
            services.append(discovered)

    return services


async def _build_discovered(
    executor: SSHRemoteExecutor, host_id: str, pid: int, cmd: str
) -> DiscoveredService | None:
    identity = _identity_from_cmd(cmd)
    if identity is None:
        return None
    suggested_id, suggested_name = identity

    details = await get_process_details(executor, pid)
    jar_path = parse_jar_from_cmd(cmd)
    if jar_path and not jar_path.startswith("/") and details.get("deploy_dir"):
        jar_path = f"{details['deploy_dir']}/{jar_path.split('/')[-1]}"

    deploy_dir = details.get("deploy_dir")
    log_path = details["log_candidates"][0] if details.get("log_candidates") else None
    profile = parse_profile_from_cmd(cmd)

    return DiscoveredService(
        suggested_id=suggested_id,
        suggested_name=suggested_name,
        host_id=host_id,
        service_type=ServiceType.JAVA,
        pid=pid,
        jar_path=jar_path,
        deploy_dir=deploy_dir,
        listen_ports=details.get("listen_ports", []),
        log_path=log_path,
        spring_profile=profile,
        confidence=0.9 if deploy_dir else 0.75,
        evidence={
            "source": "ps+jps",
            "pid": str(pid),
            "cmdline": cmd[:300],
            "cwd": deploy_dir or "",
        },
    )


def _identity_from_cmd(cmd: str) -> tuple[str, str] | None:
    jar = parse_jar_from_cmd(cmd)
    if jar:
        raw = re.sub(r"\.jar$", "", jar.split("/")[-1])
        return _slug_from_path(raw), _humanize_name(raw)

    lowered = cmd.lower()
    if "kafka.kafka" in lowered:
        return "kafka", "Kafka"

    for token in reversed(cmd.split()):
        if any(token.endswith(suffix) for suffix in _CONFIG_SUFFIXES):
            continue
        if token.startswith("-") or "=" in token:
            continue
        if token.endswith(".jar"):
            raw = re.sub(r"\.jar$", "", token.split("/")[-1])
            return _slug_from_path(raw), _humanize_name(raw)
        if "." in token and not token.endswith(".jar"):
            simple = token.split(".")[-1]
            if simple.lower() not in {"java", "jar"}:
                return _slug_from_path(simple), _humanize_name(simple)
        if token[:1].isupper() and token.isalnum() and len(token) >= 4:
            return _slug_from_path(token), _humanize_name(token)

    raw = cmd.split()[-1] if cmd else ""
    if not raw or any(raw.endswith(suffix) for suffix in _CONFIG_SUFFIXES):
        return None
    if raw.endswith(".jar"):
        raw = re.sub(r"\.jar$", "", raw.split("/")[-1])
    return _slug_from_path(raw), _humanize_name(raw)


def _humanize_name(raw: str) -> str:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", raw).strip()
    return spaced or raw


def _slug_from_path(path: str) -> str:
    name = path.split("/")[-1]
    name = re.sub(r"\.jar$", "", name)
    if re.search(r"[A-Z]", name):
        name = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", name)
    name = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-").lower()
    return name or "java-service"
