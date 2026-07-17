from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from agent.models import DiscoveredService, ServiceConfig, ServiceType
from agent.discovery import compose, docker, java, middleware, static_java

if TYPE_CHECKING:
    from agent.executor.ssh import SSHRemoteExecutor

logger = logging.getLogger(__name__)


async def _warm_up(executor: SSHRemoteExecutor, attempts: int = 3) -> None:
    """确保 SSH 连接可用后再扫描。

    首次配置后连接可能尚未就绪（跳板机上尤其明显），以前失败会被静默吞掉、
    表现为「探测出 0 个服务」；现在先预热，连不上直接报错。
    """
    last_err = ""
    for i in range(attempts):
        result = await executor.run("echo steadyops-ready", timeout=12)
        if result.exit_code == 0 and "steadyops-ready" in result.stdout:
            return
        last_err = result.stderr or f"exit_code={result.exit_code}"
        if i < attempts - 1:
            await asyncio.sleep(1.5 * (i + 1))
    raise RuntimeError(f"无法连接主机（SSH 预热失败）：{last_err}")


async def scan_host(executor: SSHRemoteExecutor, host_id: str) -> list[DiscoveredService]:
    await _warm_up(executor)

    discovered: list[DiscoveredService] = []
    errors: list[str] = []

    detectors = (
        ("java", java.detect_java),
        ("docker", docker.detect_docker),
        ("compose", compose.detect_compose),
        ("middleware", middleware.detect_middleware),
    )
    for name, detect in detectors:
        try:
            discovered.extend(await detect(executor, host_id))
        except Exception as exc:
            logger.warning("discovery detector %s failed on %s: %s", name, host_id, exc)
            errors.append(f"{name}: {exc}")

    # 静态扫描：找到磁盘上存在但未运行的 jar 服务（不要求约定路径）
    running_jars = {
        svc.jar_path.rsplit("/", 1)[-1]
        for svc in discovered
        if svc.service_type == ServiceType.JAVA and svc.jar_path
    }
    try:
        discovered.extend(
            await static_java.detect_static_java(executor, host_id, known_jar_names=running_jars)
        )
    except Exception as exc:
        logger.warning("static java discovery failed on %s: %s", host_id, exc)
        errors.append(f"static-java: {exc}")

    if not discovered and errors:
        raise RuntimeError("服务探测失败：" + "；".join(errors))
    return _deduplicate(discovered)


def _deduplicate(services: list[DiscoveredService]) -> list[DiscoveredService]:
    def _rank(svc: DiscoveredService) -> tuple[bool, float]:
        # 运行中的实例优先于静态发现，其次比较置信度
        return (svc.running, svc.confidence)

    by_pid: dict[int, DiscoveredService] = {}
    for svc in services:
        if svc.pid is None:
            continue
        existing = by_pid.get(svc.pid)
        if existing is None or _rank(svc) > _rank(existing):
            by_pid[svc.pid] = svc

    merged = list(by_pid.values()) + [svc for svc in services if svc.pid is None]
    by_key: dict[str, DiscoveredService] = {}
    for svc in merged:
        key = f"{svc.host_id}:{svc.suggested_id}"
        existing = by_key.get(key)
        if existing is None or _rank(svc) > _rank(existing):
            by_key[key] = svc
    return list(by_key.values())


def to_service_config(item: DiscoveredService) -> ServiceConfig:
    return ServiceConfig(
        id=item.suggested_id,
        host_id=item.host_id,
        name=item.suggested_name,
        type=item.service_type,
        # 未运行的服务默认不纳入巡检，避免注册后立刻刷一片「服务未运行」告警；
        # 用户可在服务列表里手动启用
        enabled=item.running,
        jar_path=item.jar_path,
        deploy_dir=item.deploy_dir,
        systemd_unit=item.systemd_unit,
        container_name=item.container_name,
        compose_file=item.compose_file,
        compose_service=item.compose_service,
        health_url=item.health_url,
        log_path=item.log_path,
        config_files=[c.model_copy() for c in item.config_files],
        active_profile=item.spring_profile,
        listen_ports=item.listen_ports,
    )
