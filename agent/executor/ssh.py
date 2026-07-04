from __future__ import annotations

import asyncio
import shlex
from pathlib import Path, PurePosixPath

import asyncssh

from agent.executor.base import Executor
from agent.executor.java_probe import find_java_process
from agent.executor.middleware_probe import probe_middleware_process
from agent.executor.systemd_probe import detect_systemd_unit_from_pid, probe_systemd_unit
from agent.models import CommandResult, HostConfig, HostMetrics, ServiceConfig, ServiceStatus, ServiceType


def _posix_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def wrap_command_for_host(cmd: str, ssh) -> str:
    """Wrap remote command with sudo su elevation when configured."""
    if not getattr(ssh, "use_sudo_su", False):
        return cmd
    quoted_cmd = _posix_quote(cmd)
    sudo_pwd = getattr(ssh, "sudo_password", None) or getattr(ssh, "password", None)
    if sudo_pwd:
        return f"printf '%s\\n' {_posix_quote(sudo_pwd)} | sudo -S su - root -c {quoted_cmd}"
    return f"sudo su - root -c {quoted_cmd}"


class SSHRemoteExecutor:
    def __init__(self, host: HostConfig) -> None:
        self.host = host
        self.host_id = host.id
        self._conn: asyncssh.SSHClientConnection | None = None
        self._run_lock: asyncio.Lock | None = None

    async def _get_conn(self) -> asyncssh.SSHClientConnection:
        if self._conn is not None:
            return self._conn

        connect_kwargs: dict = {
            "host": self.host.ssh.host,
            "port": self.host.ssh.port,
            "username": self.host.ssh.user,
            "known_hosts": None,
        }
        if self.host.ssh.key_file:
            connect_kwargs["client_keys"] = [self.host.ssh.key_file]
        if self.host.ssh.password:
            connect_kwargs["password"] = self.host.ssh.password

        self._conn = await asyncssh.connect(**connect_kwargs)
        return self._conn

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            await self._conn.wait_closed()
            self._conn = None

    async def run(self, cmd: str, timeout: int = 60) -> CommandResult:
        if self._run_lock is None:
            self._run_lock = asyncio.Lock()
        async with self._run_lock:
            conn = await self._get_conn()
            remote_cmd = wrap_command_for_host(cmd, self.host.ssh)
            try:
                result = await asyncio.wait_for(conn.run(remote_cmd, check=False), timeout=timeout)
                return CommandResult(
                    stdout=(result.stdout or "").strip(),
                    stderr=(result.stderr or "").strip(),
                    exit_code=int(result.exit_status or 0),
                )
            except TimeoutError:
                return CommandResult(stdout="", stderr=f"Command timed out after {timeout}s", exit_code=124)

    async def test_connection(self) -> CommandResult:
        if self.host.ssh.use_sudo_su:
            result = await self.run("whoami && id -u", timeout=20)
            if result.exit_code == 0 and result.stdout:
                lines = result.stdout.splitlines()
                uid = lines[-1].strip() if lines else ""
                if uid != "0":
                    result = CommandResult(
                        stdout=result.stdout,
                        stderr=(result.stderr + "\n" if result.stderr else "")
                        + "已启用 sudo su 提权，但当前命令未获得 root（uid 应为 0）。请确认该用户可 sudo su，且密码正确。",
                        exit_code=1,
                    )
            return result
        return await self.run("whoami && uname -a", timeout=15)

    async def tail_log(self, path: str, lines: int = 200, pattern: str | None = None) -> str:
        quoted = shlex.quote(path)
        if pattern:
            cmd = f"tail -n {lines} {quoted} | grep -E {shlex.quote(pattern)} || true"
        else:
            cmd = f"tail -n {lines} {quoted} 2>/dev/null || echo 'LOG_NOT_FOUND:{path}'"
        result = await self.run(cmd)
        return result.stdout or result.stderr

    async def read_file(self, path: str, max_bytes: int = 65536) -> str:
        quoted = shlex.quote(path)
        cmd = f"head -c {max_bytes} {quoted} 2>/dev/null || echo 'FILE_NOT_FOUND:{path}'"
        result = await self.run(cmd)
        return result.stdout

    async def write_file(self, path: str, content: str) -> CommandResult:
        import base64

        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        quoted_path = shlex.quote(path)
        parent = shlex.quote(str(PurePosixPath(path).parent))
        inner = (
            f"mkdir -p {parent} && "
            f"printf '%s' {shlex.quote(encoded)} | base64 -d > {quoted_path} && "
            f"test -f {quoted_path}"
        )
        return await self.run(inner, timeout=90)

    async def delete_file(self, path: str) -> CommandResult:
        quoted_path = shlex.quote(path)
        inner = f"rm -f {quoted_path} && test ! -e {quoted_path}"
        return await self.run(inner, timeout=60)

    async def get_metrics(self) -> HostMetrics:
        cmd = (
            "python3 - <<'PY'\n"
            "import json\n"
            "try:\n"
            " import psutil\n"
            " vm=psutil.virtual_memory()\n"
            " du=psutil.disk_usage('/')\n"
            " load=' '.join(map(str, psutil.getloadavg())) if hasattr(psutil,'getloadavg') else ''\n"
            " print(json.dumps({'cpu_percent': psutil.cpu_percent(interval=0.5),"
            " 'memory_percent': vm.percent, 'disk_percent': du.percent, 'load_avg': load}))\n"
            "except Exception as e:\n"
            " print(json.dumps({'detail': str(e)}))\n"
            "PY"
        )
        result = await self.run(cmd, timeout=30)
        metrics = HostMetrics(host_id=self.host_id, detail=result.stdout or result.stderr)
        if result.stdout:
            import json

            try:
                data = json.loads(result.stdout)
                metrics.cpu_percent = data.get("cpu_percent")
                metrics.memory_percent = data.get("memory_percent")
                metrics.disk_percent = data.get("disk_percent")
                metrics.load_avg = data.get("load_avg")
                metrics.detail = data.get("detail", "")
            except json.JSONDecodeError:
                metrics.detail = result.stdout
        return metrics

    async def service_status(
        self, service: ServiceConfig, java_process_index: list[dict] | None = None
    ) -> ServiceStatus:
        running = False
        detail = ""
        probe_method = ""

        if service.systemd_unit:
            probe_method = "systemd"
            probe = await probe_systemd_unit(self, service.systemd_unit)
            running = probe["running"]
            detail = probe["detail"]
            if not running and service.type == ServiceType.MIDDLEWARE:
                fallback = await probe_middleware_process(self, service.id)
                if fallback["running"]:
                    running = True
                    probe_method = "middleware_process"
                    detail = fallback["detail"]
        elif service.type == ServiceType.JAVA:
            probe_method = "java_process"
            probe = await find_java_process(self, service, java_process_index)
            running = probe["running"]
            detail = probe["detail"]
            primary = probe.get("primary") or {}
            pid = primary.get("pid")
            if running and pid and not service.systemd_unit:
                detected_unit = await detect_systemd_unit_from_pid(self, pid)
                if detected_unit:
                    sys_probe = await probe_systemd_unit(self, detected_unit)
                    if sys_probe["running"] and sys_probe.get("main_pid") == pid:
                        probe_method = "systemd"
                        running = True
                        detail = f"[systemd:{detected_unit}] {sys_probe['detail']}"
        elif service.type in (ServiceType.DOCKER, ServiceType.MIDDLEWARE) and service.container_name:
            probe_method = "docker"
            result = await self.run(
                f"docker inspect -f '{{{{.State.Running}}}}' {shlex.quote(service.container_name)}"
            )
            running = result.stdout.strip().lower() == "true"
            detail = result.stdout or result.stderr
        elif service.type == ServiceType.MIDDLEWARE:
            probe_method = "middleware_process"
            fallback = await probe_middleware_process(self, service.id)
            running = fallback["running"]
            detail = fallback["detail"]
        elif service.type == ServiceType.COMPOSE and service.compose_file and service.compose_service:
            probe_method = "compose"
            result = await self.run(
                f"docker compose -f {shlex.quote(service.compose_file)} ps --status running "
                f"{shlex.quote(service.compose_service)}"
            )
            running = service.compose_service in result.stdout
            detail = result.stdout or result.stderr
        else:
            detail = "未配置状态探针（systemd/docker/compose）"

        health_ok: bool | None = None
        health_detail = ""
        if service.health_url:
            health_result = await self.run(
                f"curl -fsS -m 5 -o /dev/null -w '%{{http_code}}' {shlex.quote(service.health_url)} || echo FAIL"
            )
            code = health_result.stdout.strip()
            health_ok = code.isdigit() and code.startswith("2")
            health_detail = f"HTTP {code}"

        if probe_method:
            detail = f"[{probe_method}] {detail}"

        return ServiceStatus(
            service_id=service.id,
            running=running,
            detail=detail,
            health_ok=health_ok,
            health_detail=health_detail,
        )

    async def restart_service(self, service: ServiceConfig) -> CommandResult:
        if service.systemd_unit:
            return await self.run(f"systemctl restart {shlex.quote(service.systemd_unit)}", timeout=120)
        if service.container_name:
            return await self.run(f"docker restart {shlex.quote(service.container_name)}", timeout=120)
        if service.compose_file and service.compose_service:
            return await self.run(
                f"docker compose -f {shlex.quote(service.compose_file)} restart "
                f"{shlex.quote(service.compose_service)}",
                timeout=120,
            )
        return CommandResult(stdout="", stderr="No restart method configured", exit_code=1)


class ExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[str, SSHRemoteExecutor] = {}

    def get(self, host_id: str, host: HostConfig) -> SSHRemoteExecutor:
        if host_id not in self._executors:
            self._executors[host_id] = SSHRemoteExecutor(host)
        return self._executors[host_id]

    async def close_all(self) -> None:
        for executor in self._executors.values():
            await executor.close()
        self._executors.clear()


_executor_registry = ExecutorRegistry()


def get_executor_registry() -> ExecutorRegistry:
    return _executor_registry
