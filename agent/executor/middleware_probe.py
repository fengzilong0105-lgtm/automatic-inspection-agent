from __future__ import annotations

import re

_MIDDLEWARE_PATTERNS: dict[str, re.Pattern[str]] = {
    "redis": re.compile(r"redis-server", re.I),
    "mysql": re.compile(r"mysqld|mariadbd", re.I),
    "mariadb": re.compile(r"mysqld|mariadbd", re.I),
    "nginx": re.compile(r"nginx:", re.I),
    "postgres": re.compile(r"postgres:", re.I),
    "rabbitmq": re.compile(r"beam\.smtp|rabbitmq", re.I),
    "kafka": re.compile(r"kafka\.Kafka", re.I),
    "zookeeper": re.compile(r"QuorumPeerMain|zookeeper", re.I),
}

_DEFAULT_PORTS: dict[str, int] = {
    "redis": 6379,
    "mysql": 3306,
    "mariadb": 3306,
    "nginx": 80,
    "postgres": 5432,
}


async def probe_middleware_process(executor, service_id: str) -> dict:
    """Fallback probe by process name / listening port when systemd metadata is incomplete."""
    pattern = _MIDDLEWARE_PATTERNS.get(service_id.lower())
    if not pattern:
        for key, regex in _MIDDLEWARE_PATTERNS.items():
            if key in service_id.lower():
                pattern = regex
                break

    ps = await executor.run("ps -eo pid,cmd 2>/dev/null | grep -v grep || true")
    matches: list[dict] = []
    for line in ps.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if not parts or not parts[0].isdigit():
            continue
        pid = int(parts[0])
        cmd = parts[1] if len(parts) > 1 else line
        if pattern and not pattern.search(cmd):
            continue
        matches.append({"pid": pid, "cmdline": cmd})

    port = _DEFAULT_PORTS.get(service_id.lower())
    port_listening = False
    if port:
        ss = await executor.run(f"ss -tln 2>/dev/null | grep ':{port} ' || true")
        port_listening = bool(ss.stdout.strip())

    running = bool(matches) or port_listening
    detail_parts: list[str] = []
    if matches:
        detail_parts.append(f"pid={matches[0]['pid']}, cmd={matches[0]['cmdline'][:160]}")
    if port_listening:
        detail_parts.append(f"port {port} listening")
    if not detail_parts:
        detail_parts.append("未匹配到进程或端口")

    return {
        "running": running,
        "matches": matches,
        "port_listening": port_listening,
        "detail": "; ".join(detail_parts),
    }
