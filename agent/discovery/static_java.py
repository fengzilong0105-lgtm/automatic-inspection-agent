from __future__ import annotations

import re
from typing import TYPE_CHECKING

from agent.discovery.java import _humanize_name, _slug_from_path
from agent.models import DiscoveredService, ServiceType

if TYPE_CHECKING:
    from agent.executor.ssh import SSHRemoteExecutor

# 常见部署根目录；不存在的目录会被跳过，因此宁多勿少（不要求用户约定路径）
_SCAN_ROOTS = (
    "/opt",
    "/srv",
    "/app",
    "/apps",
    "/data",
    "/data01",
    "/data02",
    "/DATA01",
    "/home",
    "/usr/local",
    "/var/www",
    "/work",
    "/export",
)

# 依赖库/运行时目录，里面的 jar 不是可部署服务
_PRUNE_PATTERNS = (
    "*/lib/*",
    "*/libs/*",
    "*/.m2/*",
    "*/repository/*",
    "*/node_modules/*",
    "*/jdk*",
    "*/jre*",
    "*/maven*",
    "*/gradle*",
    "*/BOOT-INF/*",
    "*/tmp/*",
    "*/temp/*",
    "*/.git/*",
)

_SKIP_BASENAME_RE = re.compile(r"(-sources|-javadoc|-tests?)\.jar$", re.I)

# 常见依赖库 jar（zstd-jni、scala-library 这类），不是业务服务，直接跳过
_LIBRARY_JAR_RE = re.compile(
    r"^(zstd-jni|rocksdbjni|scala-library|scala-reflect|kafka-clients|snappy-java|lz4-java"
    r"|slf4j|log4j|logback|jackson|netty|guava|protobuf-java|commons-|javassist|lombok"
    r"|fastjson|gson|okhttp|zookeeper-jute|jline|jna)",
    re.I,
)

_FIND_SCRIPT = (
    "dirs=$(for d in " + " ".join(_SCAN_ROOTS) + "; do [ -d \"$d\" ] && printf '%s ' \"$d\"; done); "
    "[ -z \"$dirs\" ] && exit 0; "
    "find $dirs -maxdepth 5 "
    + " ".join(f"\\( -path '{p}' \\) -prune -o" for p in _PRUNE_PATTERNS)
    + " -type f -name '*.jar' -size +5M -print 2>/dev/null | head -100"
)


async def detect_static_java(
    executor: SSHRemoteExecutor,
    host_id: str,
    *,
    known_jar_names: set[str] | None = None,
) -> list[DiscoveredService]:
    """Find deployable JARs on disk (services that exist but are not running).

    known_jar_names: 已由运行中进程覆盖的 jar 文件名（小写 basename），跳过以减少重复。
    """
    known = {name.lower() for name in (known_jar_names or set())}
    result = await executor.run(_FIND_SCRIPT, timeout=60)
    services: list[DiscoveredService] = []
    seen_ids: set[str] = set()
    for line in result.stdout.splitlines():
        path = line.strip()
        if not path.startswith("/") or not path.endswith(".jar"):
            continue
        basename = path.rsplit("/", 1)[-1]
        if _SKIP_BASENAME_RE.search(basename) or basename.lower() in known:
            continue
        if _LIBRARY_JAR_RE.match(basename):
            continue
        raw = re.sub(r"\.jar$", "", basename)
        suggested_id = _slug_from_path(raw)
        if suggested_id in seen_ids:
            continue
        seen_ids.add(suggested_id)
        services.append(
            DiscoveredService(
                suggested_id=suggested_id,
                suggested_name=_humanize_name(raw),
                host_id=host_id,
                service_type=ServiceType.JAVA,
                jar_path=path,
                deploy_dir=path.rsplit("/", 1)[0],
                confidence=0.55,
                running=False,
                evidence={"source": "static-scan", "jar": path, "note": "磁盘发现，当前未运行"},
            )
        )
    return services
