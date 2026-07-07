from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from agent.models import ServiceType


class CheckStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"
    UNKNOWN = "unknown"


class OomCategory(str, Enum):
    HEAP = "heap"
    METASPACE = "metaspace"
    DIRECT = "direct_buffer"
    CGROUP = "cgroup"
    HOST_KILLER = "host_oom_killer"
    NATIVE = "native"
    UNKNOWN = "unknown"


class CheckResult(BaseModel):
    id: str
    name: str
    status: CheckStatus
    detail: str
    source: Literal["live_probe", "log", "jvm_tool", "gc_log", "config_registry"] = "live_probe"
    weight: int = 0
    metrics: dict[str, Any] = Field(default_factory=dict)


class OomRiskReport(BaseModel):
    playbook: Literal["assess_oom_risk"] = "assess_oom_risk"
    version: str = "1.0"
    service_id: str
    host_id: str
    service_type: ServiceType
    assessed_at: datetime = Field(default_factory=datetime.utcnow)
    running: bool
    pid: int | None = None
    risk_level: Literal["low", "medium", "high", "critical", "unknown"]
    score: int
    confidence: Literal["verified", "partial", "low"]
    primary_category: OomCategory
    categories: list[OomCategory] = Field(default_factory=list)
    summary: str
    checks: list[CheckResult] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class CollectorState(BaseModel):
    """Mutable state passed through collectors before scoring."""

    checks: list[CheckResult] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    categories: set[str] = Field(default_factory=set)
    running: bool = False
    pid: int | None = None
    cmdline: str = ""
    container_name: str | None = None
    deploy_dir: str | None = None
    jvm_flags: dict[str, Any] = Field(default_factory=dict)
    java_heap: dict[str, Any] = Field(default_factory=dict)
    java_metaspace: dict[str, Any] = Field(default_factory=dict)
    jstat: dict[str, Any] = Field(default_factory=dict)
    proc: dict[str, Any] = Field(default_factory=dict)
    host: dict[str, Any] = Field(default_factory=dict)
    docker: dict[str, Any] = Field(default_factory=dict)
    gc_log: dict[str, Any] = Field(default_factory=dict)
    log_oom: dict[str, Any] = Field(default_factory=dict)
    critical: bool = False

    def add_check(self, check: CheckResult) -> None:
        self.checks.append(check)


class CpuCategory(str, Enum):
    PROCESS_HOT = "process_hot"
    HOST_SATURATED = "host_saturated"
    CGROUP_THROTTLED = "cgroup_throttled"
    GC_CPU_STORM = "gc_cpu_storm"
    THREAD_STORM = "thread_storm"
    RESTART_STORM = "restart_storm"
    TRAFFIC_PRESSURE = "traffic_pressure"
    UNKNOWN = "unknown"


class CpuRiskReport(BaseModel):
    playbook: Literal["assess_cpu_risk"] = "assess_cpu_risk"
    version: str = "1.0"
    service_id: str
    host_id: str
    service_type: ServiceType
    assessed_at: datetime = Field(default_factory=datetime.utcnow)
    running: bool
    pid: int | None = None
    risk_level: Literal["low", "medium", "high", "critical", "unknown"]
    score: int
    confidence: Literal["verified", "partial", "low"]
    primary_category: CpuCategory
    categories: list[CpuCategory] = Field(default_factory=list)
    summary: str
    checks: list[CheckResult] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    next_commands: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class CpuCollectorState(BaseModel):
    checks: list[CheckResult] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    categories: set[str] = Field(default_factory=set)
    critical: bool = False
    running: bool = False
    pid: int | None = None
    cmdline: str = ""
    container_name: str | None = None
    deploy_dir: str | None = None
    systemd_unit: str | None = None
    host: dict[str, Any] = Field(default_factory=dict)
    process_cpu: dict[str, Any] = Field(default_factory=dict)
    java: dict[str, Any] = Field(default_factory=dict)
    docker: dict[str, Any] = Field(default_factory=dict)
    restart: dict[str, Any] = Field(default_factory=dict)
    jstack: dict[str, Any] = Field(default_factory=dict)
    uptime_seconds: int | None = None
    next_commands: list[str] = Field(default_factory=list)

    def add_check(self, check: CheckResult) -> None:
        self.checks.append(check)


class FalseAliveCategory(str, Enum):
    PORT_DEAD = "port_dead"
    HEALTH_DEAD = "health_dead"
    WRONG_PROCESS = "wrong_process"
    SYSTEMD_MISMATCH = "systemd_mismatch"
    DOCKER_UNHEALTHY = "docker_unhealthy"
    UNKNOWN = "unknown"


class FalseAliveReport(BaseModel):
    playbook: Literal["assess_false_alive"] = "assess_false_alive"
    version: str = "1.0"
    service_id: str
    host_id: str
    service_type: ServiceType
    assessed_at: datetime = Field(default_factory=datetime.utcnow)
    running: bool
    pid: int | None = None
    health_ok: bool | None = None
    risk_level: Literal["low", "medium", "high", "critical", "unknown"]
    score: int
    confidence: Literal["verified", "partial", "low"]
    primary_category: FalseAliveCategory
    categories: list[FalseAliveCategory] = Field(default_factory=list)
    summary: str
    checks: list[CheckResult] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    next_commands: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class FalseAliveCollectorState(BaseModel):
    checks: list[CheckResult] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    categories: set[str] = Field(default_factory=set)
    critical: bool = False
    running: bool = False
    health_ok: bool | None = None
    health_detail: str = ""
    status_detail: str = ""
    pid: int | None = None
    cmdline: str = ""
    container_name: str | None = None
    systemd_unit: str | None = None
    systemd_main_pid: int | None = None
    systemd_sub_state: str = ""
    ports: list[int] = Field(default_factory=list)
    port_results: list[dict[str, Any]] = Field(default_factory=list)
    next_commands: list[str] = Field(default_factory=list)

    def add_check(self, check: CheckResult) -> None:
        self.checks.append(check)


class FalseHealthyCategory(str, Enum):
    COMPONENT_DOWN = "component_down"
    READINESS_DOWN = "readiness_down"
    BUSINESS_PROBE_FAIL = "business_probe_fail"
    LOG_ERROR_SURGE = "log_error_surge"
    LOG_BUSINESS_ERROR = "log_business_error"
    HEALTH_SLOW = "health_slow"
    UNKNOWN = "unknown"


class FalseHealthyReport(BaseModel):
    playbook: Literal["assess_false_healthy"] = "assess_false_healthy"
    version: str = "1.0"
    service_id: str
    host_id: str
    service_type: ServiceType
    assessed_at: datetime = Field(default_factory=datetime.utcnow)
    running: bool
    health_ok: bool | None = None
    risk_level: Literal["low", "medium", "high", "critical", "unknown"]
    score: int
    confidence: Literal["verified", "partial", "low"]
    primary_category: FalseHealthyCategory
    categories: list[FalseHealthyCategory] = Field(default_factory=list)
    summary: str
    checks: list[CheckResult] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    next_commands: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class FalseHealthyCollectorState(BaseModel):
    checks: list[CheckResult] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    categories: set[str] = Field(default_factory=set)
    critical: bool = False
    running: bool = False
    health_ok: bool | None = None
    health_detail: str = ""
    shallow_http_code: int | None = None
    deep_health_url: str = ""
    health_body: str = ""
    health_latency_seconds: float | None = None
    down_components: list[dict[str, Any]] = Field(default_factory=list)
    business_probe_ok: bool | None = None
    log_error_count: int = 0
    log_business_error_count: int = 0
    next_commands: list[str] = Field(default_factory=list)

    def add_check(self, check: CheckResult) -> None:
        self.checks.append(check)
