from __future__ import annotations

import re
from typing import TYPE_CHECKING

from agent.executor.java_probe import (
    _CONFIG_SUFFIXES,
    list_java_processes,
    parse_jar_from_cmd,
)
from agent.models import DiscoveredService, ServiceType

if TYPE_CHECKING:
    from agent.executor.ssh import SSHRemoteExecutor


async def detect_java(
    executor: SSHRemoteExecutor, host_id: str, *, process_index: list[dict] | None = None
) -> list[DiscoveredService]:
    """Detect running Java services in a single batched SSH round trip."""
    processes = (
        process_index if process_index is not None else await list_java_processes(executor, strict=True)
    )
    services: list[DiscoveredService] = []
    for proc in processes:
        discovered = _build_discovered(host_id, proc)
        if discovered is not None:
            services.append(discovered)
    return services


def _build_discovered(host_id: str, proc: dict) -> DiscoveredService | None:
    cmd = proc.get("cmdline") or ""
    pid = proc.get("pid")
    identity = _identity_from_cmd(cmd)
    if identity is None or pid is None:
        return None
    suggested_id, suggested_name = identity

    deploy_dir = proc.get("deploy_dir")
    jar_path = proc.get("jar_path") or parse_jar_from_cmd(cmd)
    if jar_path and not jar_path.endswith(".jar"):
        # jps 只给主类名（如 QuorumPeerMain）时不要冒充 jar 路径
        jar_path = None
    if jar_path and not jar_path.startswith("/") and deploy_dir:
        jar_path = f"{deploy_dir}/{jar_path.split('/')[-1]}"

    log_candidates = proc.get("log_candidates") or []
    log_path = log_candidates[0] if log_candidates else None
    systemd_unit = proc.get("systemd_unit")

    evidence = {
        "source": "ps+jps",
        "pid": str(pid),
        "cmdline": cmd[:300],
        "cwd": deploy_dir or "",
    }
    if systemd_unit:
        evidence["systemd_unit"] = systemd_unit

    return DiscoveredService(
        suggested_id=suggested_id,
        suggested_name=suggested_name,
        host_id=host_id,
        service_type=ServiceType.JAVA,
        pid=pid,
        jar_path=jar_path,
        deploy_dir=deploy_dir,
        systemd_unit=systemd_unit,
        listen_ports=proc.get("listen_ports", []),
        log_path=log_path,
        spring_profile=proc.get("spring_profile"),
        confidence=0.92 if systemd_unit else (0.9 if deploy_dir else 0.75),
        running=True,
        evidence=evidence,
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
