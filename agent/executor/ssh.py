from __future__ import annotations

import asyncio
import shlex
import uuid
from pathlib import Path, PurePosixPath

import asyncssh

from agent.executor.collect_policy import (
    build_collect_command,
    is_collect_output_path_allowed,
    normalize_collect_output_path,
)
from agent.executor.base import Executor
from agent.executor.java_probe import find_java_process
from agent.executor.middleware_probe import probe_middleware_process
from agent.executor.systemd_probe import detect_systemd_unit_from_pid, probe_systemd_unit
from agent.models import (
    CommandResult,
    ArtifactCollectionResult,
    FileDownloadResult,
    HostConfig,
    HostMetrics,
    ServiceConfig,
    ServiceStatus,
    ServiceType,
)


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

    async def run(self, cmd: str, timeout: int = 60, *, _retry: bool = True) -> CommandResult:
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
            except (
                asyncssh.misc.ChannelOpenError,
                asyncssh.misc.ConnectionLost,
                asyncssh.DisconnectError,
                OSError,
            ):
                self._conn = None
                if _retry:
                    return await self.run(cmd, timeout=timeout, _retry=False)
                return CommandResult(stdout="", stderr="SSH connection closed", exit_code=255)

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

    async def download_file(
        self,
        remote_path: str,
        local_path: str | Path,
        *,
        timeout: int = 300,
    ) -> FileDownloadResult:
        remote_path = remote_path.strip()
        if not remote_path.startswith("/"):
            raise ValueError("remote_path 必须为 Linux 绝对路径")

        target = Path(local_path).expanduser().resolve(strict=False)
        target.parent.mkdir(parents=True, exist_ok=True)

        source_path = remote_path
        staging_path: str | None = None
        used_sudo_staging = False

        try:
            if self.host.ssh.use_sudo_su:
                check = await self.run(f"test -r {shlex.quote(remote_path)} && echo yes || true", timeout=20)
                if check.stdout.strip() != "yes":
                    filename = PurePosixPath(remote_path).name or "download.bin"
                    staging_path = f"/tmp/steadyops-download-{uuid.uuid4().hex}-{filename}"
                    copy_result = await self.run(
                        " && ".join(
                            [
                                f"cp {shlex.quote(remote_path)} {shlex.quote(staging_path)}",
                                f"chown {shlex.quote(self.host.ssh.user)} {shlex.quote(staging_path)}",
                                f"chmod 600 {shlex.quote(staging_path)}",
                            ]
                        ),
                        timeout=60,
                    )
                    if copy_result.exit_code != 0:
                        raise RuntimeError(copy_result.stderr or copy_result.stdout or "准备下载文件失败")
                    source_path = staging_path
                    used_sudo_staging = True

            conn = await self._get_conn()

            async def _download_via_sftp() -> int:
                async with conn.start_sftp_client() as sftp:
                    attrs = await sftp.stat(source_path)
                    await sftp.get(source_path, str(target))
                    size = getattr(attrs, "size", None)
                    if isinstance(size, int) and size >= 0:
                        return size
                return target.stat().st_size

            bytes_downloaded = await asyncio.wait_for(_download_via_sftp(), timeout=timeout)
            return FileDownloadResult(
                host_id=self.host_id,
                remote_path=remote_path,
                local_path=str(target),
                bytes_downloaded=bytes_downloaded,
                used_sudo_staging=used_sudo_staging,
            )
        except (
            asyncssh.Error,
            asyncssh.misc.ChannelOpenError,
            asyncssh.misc.ConnectionLost,
            asyncssh.DisconnectError,
            OSError,
        ) as exc:
            self._conn = None
            raise RuntimeError(f"下载失败: {exc}") from exc
        finally:
            if staging_path:
                await self.run(f"rm -f {shlex.quote(staging_path)}", timeout=30, _retry=False)

    async def collect_artifact(
        self,
        command: str,
        remote_output_path: str,
        *,
        timeout: int = 300,
    ) -> ArtifactCollectionResult:
        output_path = normalize_collect_output_path(remote_output_path)
        if not is_collect_output_path_allowed(output_path):
            raise ValueError("remote_output_path 仅允许写入 /tmp、/var/tmp 或 /dev/shm")

        parent = PurePosixPath(output_path).parent.as_posix()
        temp_path = f"{parent}/.steadyops-{uuid.uuid4().hex}.tmp"
        collect_cmd = build_collect_command(command, temp_path)
        inner = (
            "set -e; "
            f"rm -f {shlex.quote(temp_path)} {shlex.quote(output_path)}; "
            f"{collect_cmd}; "
            f"test -e {shlex.quote(temp_path)}; "
            f"mv {shlex.quote(temp_path)} {shlex.quote(output_path)}"
        )
        result = await self.run(inner, timeout=timeout)
        if result.exit_code != 0:
            raise RuntimeError(result.stderr or result.stdout or "采集命令执行失败")

        stat_result = await self.run(
            f"wc -c < {shlex.quote(output_path)} && wc -l < {shlex.quote(output_path)}",
            timeout=30,
        )
        if stat_result.exit_code != 0:
            raise RuntimeError(stat_result.stderr or stat_result.stdout or "采集结果校验失败")

        lines = [line.strip() for line in stat_result.stdout.splitlines() if line.strip()]
        byte_count = int(lines[0]) if lines and lines[0].isdigit() else 0
        line_count = int(lines[1]) if len(lines) > 1 and lines[1].isdigit() else None
        return ArtifactCollectionResult(
            host_id=self.host_id,
            remote_output_path=output_path,
            bytes_written=byte_count,
            line_count=line_count,
            command=command,
        )

    async def get_metrics(self) -> HostMetrics:
        metrics = await self._get_metrics_psutil()
        if metrics.cpu_percent is None and metrics.memory_percent is None:
            fallback = await self._get_metrics_proc_fallback()
            if fallback.cpu_percent is not None or fallback.memory_percent is not None:
                return fallback
        return metrics

    async def _get_metrics_psutil(self) -> HostMetrics:
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

    async def _get_metrics_proc_fallback(self) -> HostMetrics:
        """Collect host metrics via /proc + free + df when psutil is unavailable."""
        cmd = (
            "python3 - <<'PY'\n"
            "import json, time, subprocess\n"
            "def cpu_pct():\n"
            "    def snap():\n"
            "        parts = open('/proc/stat').readline().split()[1:]\n"
            "        vals = list(map(int, parts))\n"
            "        return vals[3], sum(vals)\n"
            "    i1, t1 = snap(); time.sleep(1); i2, t2 = snap()\n"
            "    dt, di = t2 - t1, i2 - i1\n"
            "    return round((dt - di) / dt * 100, 1) if dt else None\n"
            "load = open('/proc/loadavg').read().split()[:3]\n"
            "mem = subprocess.check_output(['free', '-b'], text=True).splitlines()[1].split()\n"
            "mem_total, mem_used = int(mem[1]), int(mem[2])\n"
            "disk = subprocess.check_output(['df', '-P', '/'], text=True).splitlines()[1].split()\n"
            "print(json.dumps({\n"
            "    'cpu_percent': cpu_pct(),\n"
            "    'memory_percent': round(mem_used / mem_total * 100, 1) if mem_total else None,\n"
            "    'disk_percent': float(disk[4].rstrip('%')) if len(disk) > 4 else None,\n"
            "    'load_avg': ' '.join(load),\n"
            "    'detail': 'fallback:/proc+free+df (psutil unavailable)',\n"
            "}))\n"
            "PY"
        )
        result = await self.run(cmd, timeout=35)
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
