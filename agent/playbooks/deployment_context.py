from __future__ import annotations

from agent.executor.java_probe import find_java_process
from agent.executor.middleware_probe import probe_middleware_process
from agent.executor.systemd_probe import probe_systemd_for_service
from agent.models import ServiceConfig, ServiceType
from agent.playbooks.models import CollectorState, CpuCollectorState


async def resolve_deployment(executor, service: ServiceConfig) -> CollectorState:
    state = CollectorState()
    runtime_pid: int | None = None
    cmdline = ""

    if service.type == ServiceType.JAVA:
        probe = await find_java_process(executor, service)
        state.running = probe.get("running", False)
        primary = probe.get("primary") or {}
        if primary:
            runtime_pid = primary.get("pid")
            cmdline = primary.get("cmdline") or ""
            state.deploy_dir = primary.get("deploy_dir")
    elif service.type in (ServiceType.DOCKER, ServiceType.MIDDLEWARE) and service.container_name:
        import shlex

        result = await executor.run(
            f"docker inspect -f '{{{{.State.Running}}}}' {shlex.quote(service.container_name)}"
        )
        state.running = result.stdout.strip().lower() == "true"
        state.container_name = service.container_name
    elif service.type == ServiceType.MIDDLEWARE:
        fallback = await probe_middleware_process(executor, service.id)
        state.running = fallback.get("running", False)
        proc = fallback.get("process") or {}
        if isinstance(proc, dict) and proc.get("pid"):
            runtime_pid = proc.get("pid")
    elif service.type == ServiceType.COMPOSE and service.compose_file and service.compose_service:
        import shlex

        result = await executor.run(
            f"docker compose -f {shlex.quote(service.compose_file)} ps -q "
            f"{shlex.quote(service.compose_service)} 2>/dev/null | head -1"
        )
        container_id = result.stdout.strip()
        if container_id:
            name_result = await executor.run(
                f"docker inspect -f '{{{{.Name}}}}' {shlex.quote(container_id)}"
            )
            name = name_result.stdout.strip().lstrip("/")
            state.container_name = name or container_id
            running_result = await executor.run(
                f"docker inspect -f '{{{{.State.Running}}}}' {shlex.quote(container_id)}"
            )
            state.running = running_result.stdout.strip().lower() == "true"

    if service.type == ServiceType.JAVA and runtime_pid is None and state.running:
        probe = await find_java_process(executor, service)
        primary = probe.get("primary") or {}
        runtime_pid = primary.get("pid")
        cmdline = primary.get("cmdline") or cmdline

    if service.type == ServiceType.JAVA and runtime_pid:
        systemd_probe = await probe_systemd_for_service(
            executor,
            service.id,
            pid=runtime_pid,
            registered_unit=service.systemd_unit,
        )
        if not cmdline and systemd_probe.get("detail"):
            pass

    if runtime_pid and not cmdline:
        proc_cmd = await executor.run(
            f"tr '\\0' ' ' < /proc/{runtime_pid}/cmdline 2>/dev/null || true"
        )
        cmdline = proc_cmd.stdout.strip()

    state.pid = runtime_pid
    state.cmdline = cmdline
    if service.container_name and not state.container_name:
        state.container_name = service.container_name
    return state


async def resolve_cpu_deployment(executor, service: ServiceConfig) -> CpuCollectorState:
    base = await resolve_deployment(executor, service)
    cpu_state = CpuCollectorState(
        running=base.running,
        pid=base.pid,
        cmdline=base.cmdline,
        container_name=base.container_name,
        deploy_dir=base.deploy_dir,
        systemd_unit=service.systemd_unit,
    )
    if base.pid:
        etime = await executor.run(f"ps -p {base.pid} -o etimes= 2>/dev/null")
        try:
            cpu_state.uptime_seconds = int((etime.stdout or "").strip())
        except ValueError:
            pass
    return cpu_state
