from __future__ import annotations

import re
import shlex

from agent.models import ServiceConfig

_JAR_RE = re.compile(r"-jar\s+(\S+\.jar)")
_PROFILE_RE = re.compile(r"--spring\.profiles\.active=(\S+)")
_JPS_SKIP = frozenset({"jps", "jar"})
_CONFIG_SUFFIXES = (".properties", ".xml", ".yml", ".yaml", ".conf", ".cfg", ".ini")
_MIN_TOKEN_LEN = 5
_MIN_ACCEPT_SCORE = 50

# JVM flags / common classpath fragments that cause false positives when substring-matched.
_JVM_NOISE_TOKENS = frozenset(
    {
        "server",
        "java",
        "apache",
        "file",
        "data",
        "data01",
        "headless",
        "memory",
        "timezone",
        "encoding",
        "oracle",
        "jdbc",
        "opens",
        "unnamed",
        "region",
        "user",
        "spring",
        "boot",
        "application",
        "properties",
        "config",
        "true",
        "false",
        "null",
        "jdk",
        "bin",
        "local",
        "share",
        "lib",
        "class",
        "path",
        "home",
        "version",
        "shanghai",
        "asia",
        "utf",
        "arrow",
        "naming",
        "security",
        "manager",
        "policy",
        "agent",
        "tools",
    }
)


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _tokens_from_text(value: str, min_len: int = 4) -> list[str]:
    if not value:
        return []
    tokens: list[str] = []
    for part in re.findall(r"[a-zA-Z0-9]+", value):
        lowered = part.lower()
        if len(lowered) >= min_len and lowered not in _JVM_NOISE_TOKENS:
            tokens.append(lowered)
    compact = _normalize_text(value)
    if len(compact) >= min_len and compact not in _JVM_NOISE_TOKENS:
        tokens.append(compact)
    return tokens


def jar_candidates(service: ServiceConfig) -> list[str]:
    names: list[str] = []
    if service.jar_path:
        names.append(service.jar_path.split("/")[-1])
        if "/" in service.jar_path:
            names.append(service.jar_path)
    names.append(f"{service.id}.jar")
    names.append(f"{service.id.replace('-', '_')}.jar")
    names.append(f"{service.id.replace('_', '-')}.jar")
    if service.name:
        names.append(service.name)
    main_class = _main_class_simple_name(service.id)
    if main_class:
        names.append(main_class)
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def search_tokens(service: ServiceConfig) -> list[str]:
    tokens: set[str] = set()
    for candidate in jar_candidates(service):
        tokens.update(_tokens_from_text(candidate.replace(".jar", ""), min_len=_MIN_TOKEN_LEN))
    for part in service.id.replace("_", "-").split("-"):
        if len(part) >= _MIN_TOKEN_LEN and part.lower() not in _JVM_NOISE_TOKENS:
            tokens.add(part.lower())
    if service.name:
        tokens.update(_tokens_from_text(service.name, min_len=_MIN_TOKEN_LEN))
    if service.deploy_dir:
        base = service.deploy_dir.rstrip("/").split("/")[-1]
        tokens.update(_tokens_from_text(base.replace(".jar", ""), min_len=_MIN_TOKEN_LEN))
    return sorted(tokens, key=len, reverse=True)


def _main_class_simple_name(value: str) -> str | None:
    if not value:
        return None
    if "." in value:
        return value.split(".")[-1]
    return value


def score_java_match(
    service: ServiceConfig, cmdline: str, cwd: str | None = None, listen_ports: list[int] | None = None
) -> int:
    """Score how well a Java process matches a service. Higher is better."""
    lowered = cmdline.lower()
    normalized = _normalize_text(cmdline)
    cwd_norm = _normalize_text(cwd or "")
    score = 0

    if service.jar_path:
        jar_name = service.jar_path.split("/")[-1].lower()
        if jar_name and jar_name in lowered:
            score += 100
        if service.jar_path.lower() in lowered:
            score += 100

    for candidate in jar_candidates(service):
        cl = candidate.lower()
        if cl.endswith(".jar") and cl in lowered:
            score += 100
        elif len(cl) >= 6 and cl in lowered:
            score += 80

    normalized_id = _normalize_text(service.id)
    if len(normalized_id) >= 8 and normalized_id in normalized:
        score += 70

    if service.deploy_dir and cwd_norm:
        deploy_norm = _normalize_text(service.deploy_dir)
        if deploy_norm and deploy_norm in cwd_norm:
            score += 40
        deploy_base = service.deploy_dir.rstrip("/").split("/")[-1]
        base_norm = _normalize_text(deploy_base)
        if len(base_norm) >= 6 and base_norm in cwd_norm:
            score += 25

    for token in search_tokens(service):
        if len(token) >= 8 and token in normalized:
            score += 80
        elif len(token) >= 6 and token in normalized:
            score += 15

    if service.listen_ports and listen_ports:
        overlap = set(service.listen_ports) & set(listen_ports)
        if overlap:
            score += 60

    return score


def _is_config_derived_service(service: ServiceConfig) -> bool:
    if service.jar_path:
        return False
    sid = service.id.lower()
    return sid.endswith("-properties") or sid in {"properties", "zookeeper-properties", "server-properties"}


def _accept_java_match(
    service: ServiceConfig, cmdline: str, cwd: str | None, listen_ports: list[int] | None, score: int
) -> bool:
    if _is_config_derived_service(service):
        return False
    if score < _MIN_ACCEPT_SCORE:
        return False
    if service.jar_path:
        jar_name = service.jar_path.split("/")[-1].lower()
        if jar_name and jar_name in cmdline.lower():
            return True
    if service.listen_ports and listen_ports:
        if set(service.listen_ports) & set(listen_ports):
            return True
    normalized_id = _normalize_text(service.id)
    if len(normalized_id) >= 8 and normalized_id in _normalize_text(cmdline):
        return True
    normalized_cmd = _normalize_text(cmdline)
    if score >= 80 and any(len(token) >= 10 and token in normalized_cmd for token in search_tokens(service)):
        return True
    return False


def match_java_line(line: str, candidates: list[str], service_id: str, service: ServiceConfig | None = None) -> bool:
    """Quick compatibility wrapper; prefer score_java_match for production matching."""
    if service is None:
        from agent.models import ServiceConfig, ServiceType

        service = ServiceConfig(id=service_id, host_id="", type=ServiceType.JAVA)
    _, cmd = parse_ps_java_line(line)
    cmdline = cmd or line
    score = score_java_match(service, cmdline)
    return _accept_java_match(service, cmdline, None, None, score)


def parse_ps_java_line(line: str) -> tuple[int | None, str]:
    line = line.strip()
    if not line:
        return None, ""
    parts = line.split(None, 1)
    if parts and parts[0].isdigit():
        return int(parts[0]), parts[1] if len(parts) > 1 else line
    return None, line


def parse_jar_from_cmd(cmd: str) -> str | None:
    match = _JAR_RE.search(cmd)
    return match.group(1) if match else None


def parse_profile_from_cmd(cmd: str) -> str | None:
    match = _PROFILE_RE.search(cmd)
    return match.group(1) if match else None


async def list_java_processes(executor) -> list[dict]:
    """Fetch ps/jps once and load process details for all Java PIDs on the host."""
    import asyncio

    pid_cmds: dict[int, str] = {}

    ps = await executor.run("ps -eo pid,cmd | grep java | grep -v grep || true")
    for line in ps.stdout.splitlines():
        pid, cmd = parse_ps_java_line(line)
        if pid is None:
            continue
        pid_cmds[pid] = cmd

    jps = await executor.run("jps -l 2>/dev/null || jps 2>/dev/null || true")
    for line in jps.stdout.splitlines():
        pid, cmd = parse_ps_java_line(line)
        if pid is None or pid in pid_cmds:
            continue
        if cmd.strip().lower() in _JPS_SKIP:
            continue
        pid_cmds[pid] = cmd

    sem = asyncio.Semaphore(6)

    async def load_process(pid: int, cmd: str) -> dict:
        async with sem:
            details = await get_process_details(executor, pid, include_logs=False)
            cmdline = cmd or details.get("cmdline", "")
            jar_path = parse_jar_from_cmd(cmdline)
            if not jar_path and cmdline and " " not in cmdline.strip():
                jar_path = cmdline.strip()
            return {
                "pid": pid,
                "cmdline": cmdline,
                "deploy_dir": details.get("deploy_dir"),
                "jar_path": jar_path,
                "spring_profile": parse_profile_from_cmd(cmdline),
                "listen_ports": details.get("listen_ports", []),
                "log_candidates": details.get("log_candidates", []),
            }

    if not pid_cmds:
        return []
    return list(await asyncio.gather(*(load_process(pid, cmd) for pid, cmd in pid_cmds.items())))


def _match_java_processes(service: ServiceConfig, processes: list[dict]) -> list[dict]:
    scored: list[dict] = []
    for proc in processes:
        cmdline = proc.get("cmdline", "")
        listen_ports = proc.get("listen_ports", [])
        score = score_java_match(service, cmdline, proc.get("deploy_dir"), listen_ports)
        if not _accept_java_match(service, cmdline, proc.get("deploy_dir"), listen_ports, score):
            continue
        scored.append({**proc, "score": score})
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored


async def find_java_process(
    executor, service: ServiceConfig, process_index: list[dict] | None = None
) -> dict:
    """Locate Java process by jar name / service id via SSH."""
    processes = process_index if process_index is not None else await list_java_processes(executor)
    scored = _match_java_processes(service, processes)

    if not scored:
        token_hint = ", ".join(search_tokens(service)[:6]) or ", ".join(jar_candidates(service))
        return {
            "running": False,
            "detail": f"未找到 Java 进程（匹配: {token_hint}）",
            "matches": [],
        }

    primary = scored[0]
    return {
        "running": True,
        "detail": (
            f"pid={primary['pid']}, cwd={primary.get('deploy_dir')}, "
            f"cmd={primary.get('cmdline', '')[:200]}"
        ),
        "matches": scored,
        "primary": primary,
    }


async def get_process_details(executor, pid: int, *, include_logs: bool = True) -> dict:
    deploy_dir = None
    for cmd in (
        f"readlink -f /proc/{pid}/cwd 2>/dev/null",
        f"readlink /proc/{pid}/cwd 2>/dev/null",
        f"pwdx {pid} 2>/dev/null | awk '{{print $2}}'",
    ):
        cwd_result = await executor.run(f"{cmd} || true")
        value = cwd_result.stdout.strip()
        if value and "permission denied" not in value.lower():
            deploy_dir = value
            break

    cmd_result = await executor.run(
        f"tr '\\0' ' ' < /proc/{pid}/cmdline 2>/dev/null || true"
    )
    cmdline = cmd_result.stdout.strip()

    listen_ports: list[int] = []
    ss_result = await executor.run(
        f"ss -tlnp 2>/dev/null | grep 'pid={pid},' || true"
    )
    for line in ss_result.stdout.splitlines():
        for part in line.split():
            if ":" in part and part.rsplit(":", 1)[-1].isdigit():
                listen_ports.append(int(part.rsplit(":", 1)[-1]))

    log_candidates: list[str] = []
    if include_logs and deploy_dir:
        for name in ("app.log", "nohup.out", "logs/app.log", "logs/spring.log"):
            path = f"{deploy_dir}/{name}"
            check = await executor.run(f"test -e {shlex.quote(path)} && echo yes || true")
            if check.stdout.strip() == "yes":
                log_candidates.append(path)

    return {
        "deploy_dir": deploy_dir,
        "cmdline": cmdline,
        "listen_ports": sorted(set(listen_ports)),
        "log_candidates": log_candidates,
    }
