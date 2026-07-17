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


# 单次往返拿到全部 Java 进程的 pid/cmdline/cwd/cgroup/日志候选 + 全量 ss 端口表，
# 避免逐 PID 多次 SSH 往返（跳板机/高延迟链路下扫描慢且易超时）。
_BATCH_JAVA_SCRIPT = (
    "pids=$({ ps -eo pid,cmd | grep java | grep -v grep | awk '{print $1}'; "
    "jps -q 2>/dev/null; } | sort -un); "
    "echo '##PS_BEGIN'; ps -eo pid,cmd | grep java | grep -v grep || true; echo '##PS_END'; "
    "echo '##JPS_BEGIN'; jps -l 2>/dev/null || true; echo '##JPS_END'; "
    "echo '##SS_BEGIN'; ss -tlnp 2>/dev/null || true; echo '##SS_END'; "
    "for p in $pids; do "
    "echo \"##PID $p\"; "
    "cw=$(readlink -f /proc/$p/cwd 2>/dev/null || readlink /proc/$p/cwd 2>/dev/null); "
    "[ -z \"$cw\" ] && cw=$(pwdx $p 2>/dev/null | awk '{print $2}'); "
    "echo \"##CWD $cw\"; "
    "printf '%s ' '##CMD'; tr '\\0' ' ' < /proc/$p/cmdline 2>/dev/null; echo; "
    "echo \"##UNIT $(cat /proc/$p/cgroup 2>/dev/null | grep -oE '[a-zA-Z0-9@._-]+\\.service' | head -1)\"; "
    "if [ -n \"$cw\" ]; then for f in app.log nohup.out logs/app.log logs/spring.log; do "
    "[ -e \"$cw/$f\" ] && echo \"##LOG $cw/$f\"; done; fi; "
    "done"
)


def _parse_ss_pid_ports(ss_lines: list[str]) -> dict[int, list[int]]:
    ports: dict[int, set[int]] = {}
    pid_re = re.compile(r"pid=(\d+)")
    for line in ss_lines:
        pids = [int(m) for m in pid_re.findall(line)]
        if not pids:
            continue
        for part in line.split():
            if ":" in part and part.rsplit(":", 1)[-1].isdigit():
                port = int(part.rsplit(":", 1)[-1])
                for pid in pids:
                    ports.setdefault(pid, set()).add(port)
                break
    return {pid: sorted(vals) for pid, vals in ports.items()}


def parse_batch_java_output(stdout: str) -> list[dict]:
    """Parse the output of _BATCH_JAVA_SCRIPT into process dicts."""
    lines = stdout.splitlines()
    sections: dict[str, list[str]] = {"PS": [], "JPS": [], "SS": []}
    detail_lines: list[str] = []
    current: str | None = None
    for line in lines:
        stripped = line.strip()
        if stripped in ("##PS_BEGIN", "##JPS_BEGIN", "##SS_BEGIN"):
            current = stripped[2:-6]
            continue
        if stripped in ("##PS_END", "##JPS_END", "##SS_END"):
            current = None
            continue
        if current is not None:
            sections[current].append(line)
        else:
            detail_lines.append(line)

    ps_cmds: dict[int, str] = {}
    for line in sections["PS"]:
        pid, cmd = parse_ps_java_line(line)
        if pid is not None:
            ps_cmds[pid] = cmd
    jps_cmds: dict[int, str] = {}
    for line in sections["JPS"]:
        pid, cmd = parse_ps_java_line(line)
        if pid is None or cmd.strip().lower() in _JPS_SKIP:
            continue
        jps_cmds[pid] = cmd
    pid_ports = _parse_ss_pid_ports(sections["SS"])

    processes: list[dict] = []
    proc: dict | None = None

    def _finish(item: dict | None) -> None:
        if item is None:
            return
        pid = item["pid"]
        cmdline = item.get("cmdline") or ps_cmds.get(pid) or jps_cmds.get(pid) or ""
        if not cmdline:
            return
        jar_path = parse_jar_from_cmd(cmdline)
        if not jar_path and cmdline and " " not in cmdline.strip():
            jar_path = cmdline.strip()
        processes.append(
            {
                "pid": pid,
                "cmdline": cmdline,
                "deploy_dir": item.get("deploy_dir") or None,
                "jar_path": jar_path,
                "spring_profile": parse_profile_from_cmd(cmdline),
                "listen_ports": pid_ports.get(pid, []),
                "log_candidates": item.get("log_candidates", []),
                "systemd_unit": item.get("systemd_unit") or None,
            }
        )

    for line in detail_lines:
        stripped = line.strip()
        if stripped.startswith("##PID "):
            _finish(proc)
            pid_text = stripped[6:].strip()
            proc = {"pid": int(pid_text), "log_candidates": []} if pid_text.isdigit() else None
        elif proc is None:
            continue
        elif stripped.startswith("##CWD"):
            proc["deploy_dir"] = stripped[5:].strip()
        elif stripped.startswith("##CMD"):
            proc["cmdline"] = stripped[5:].strip()
        elif stripped.startswith("##UNIT"):
            proc["systemd_unit"] = stripped[6:].strip()
        elif stripped.startswith("##LOG "):
            proc["log_candidates"].append(stripped[6:].strip())
    _finish(proc)
    return processes


async def list_java_processes(executor, *, strict: bool = False) -> list[dict]:
    """Fetch all Java process details in a single SSH round trip.

    strict=True 时，SSH 传输层失败（超时/断连）会抛异常而不是静默返回空列表，
    避免「探测出 0 个服务」这种假结果。
    """
    result = await executor.run(_BATCH_JAVA_SCRIPT, timeout=45)
    if result.exit_code in (124, 255) and not result.stdout:
        if strict:
            raise RuntimeError(f"Java 进程探测失败（SSH 命令未成功）: {result.stderr or 'no output'}")
        return []
    return parse_batch_java_output(result.stdout)


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
