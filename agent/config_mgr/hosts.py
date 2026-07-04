from __future__ import annotations

import asyncio

from agent.models import HostConfig, SSHConfig
from agent.settings import UNCHANGED_SECRET, Settings, get_settings, mask_secret


def host_to_safe_dict(host: HostConfig) -> dict:
    data = host.model_dump()
    data["ssh"] = {
        **data["ssh"],
        "password": None,
        "password_set": bool(host.ssh.password),
        "sudo_password": None,
        "sudo_password_set": bool(host.ssh.sudo_password),
    }
    return data


def build_host_config(payload, existing: HostConfig | None = None) -> HostConfig:
    ssh = payload.ssh if hasattr(payload, "ssh") else payload["ssh"]
    password = getattr(ssh, "password", None) or ssh.get("password")
    if password == UNCHANGED_SECRET and existing:
        password = existing.ssh.password

    sudo_password = getattr(ssh, "sudo_password", None) or ssh.get("sudo_password")
    if sudo_password == UNCHANGED_SECRET and existing:
        sudo_password = existing.ssh.sudo_password

    use_sudo_su = getattr(ssh, "use_sudo_su", None)
    if use_sudo_su is None:
        use_sudo_su = ssh.get("use_sudo_su", False)

    return HostConfig(
        id=payload.id if hasattr(payload, "id") else payload["id"],
        name=payload.name if hasattr(payload, "name") else payload["name"],
        ssh=SSHConfig(
            host=ssh.host if hasattr(ssh, "host") else ssh["host"],
            port=ssh.port if hasattr(ssh, "port") else ssh.get("port", 22),
            user=ssh.user if hasattr(ssh, "user") else ssh["user"],
            key_file=(ssh.key_file if hasattr(ssh, "key_file") else ssh.get("key_file")) or None,
            password=password or None,
            use_sudo_su=bool(use_sudo_su),
            sudo_password=sudo_password or None,
        ),
    )


def upsert_host(host: HostConfig, settings: Settings | None = None) -> HostConfig:
    settings = settings or get_settings()
    hosts = list(settings.config.hosts)
    for index, item in enumerate(hosts):
        if item.id == host.id:
            hosts[index] = host
            break
    else:
        if any(h.id == host.id for h in hosts):
            raise ValueError(f"主机 ID 已存在: {host.id}")
        hosts.append(host)

    active_host_id = settings.config.active_host_id or host.id
    settings.save(
        settings.config.model_copy(
            update={"hosts": hosts, "active_host_id": active_host_id, "setup_completed": True}
        )
    )
    _reset_ssh_pool()
    return host


def delete_host(host_id: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    bound = [s.id for s in settings.config.services if s.host_id == host_id]
    if bound:
        raise ValueError(f"主机仍有关联服务，无法删除: {', '.join(bound)}")

    hosts = [h for h in settings.config.hosts if h.id != host_id]
    if len(hosts) == len(settings.config.hosts):
        raise KeyError(f"Host not found: {host_id}")

    active_host_id = settings.config.active_host_id
    if active_host_id == host_id:
        active_host_id = hosts[0].id if hosts else None

    settings.save(settings.config.model_copy(update={"hosts": hosts, "active_host_id": active_host_id}))
    _reset_ssh_pool()


def set_active_host(host_id: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    settings.get_host(host_id)
    settings.save(settings.config.model_copy(update={"active_host_id": host_id}))

    active_service_id = settings.config.active_service_id
    if active_service_id:
        service = settings.get_service(active_service_id)
        if service.host_id != host_id:
            host_services = [s for s in settings.config.services if s.host_id == host_id and s.enabled]
            new_active = host_services[0].id if host_services else None
            settings.save(settings.config.model_copy(update={"active_service_id": new_active}))
    return host_id


def _reset_ssh_pool() -> None:
    from agent.executor.ssh import get_executor_registry

    try:
        from agent.runtime.background import get_runtime

        runtime = get_runtime()
        loop = runtime._loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(get_executor_registry().close_all(), loop)
            return
    except Exception:
        pass

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(get_executor_registry().close_all())
        else:
            loop.run_until_complete(get_executor_registry().close_all())
    except RuntimeError:
        pass


def enrich_service_systemd_unit(service_id: str, unit: str, settings: Settings | None = None) -> bool:
    """Persist a detected systemd unit onto a registered service when missing."""
    from agent.executor.systemd_probe import _normalize_unit_name

    settings = settings or get_settings()
    service = settings.get_service(service_id)
    normalized = _normalize_unit_name(unit)
    if service.systemd_unit == normalized:
        return False
    if service.systemd_unit:
        return False

    services = [
        s.model_copy(update={"systemd_unit": normalized}) if s.id == service_id else s
        for s in settings.config.services
    ]
    settings.save(settings.config.model_copy(update={"services": services}))
    return True
