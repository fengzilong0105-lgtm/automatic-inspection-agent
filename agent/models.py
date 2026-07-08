from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ServiceType(str, Enum):
    JAVA = "java"
    DOCKER = "docker"
    COMPOSE = "compose"
    MIDDLEWARE = "middleware"


class SSHConfig(BaseModel):
    host: str
    port: int = 22
    user: str
    key_file: str | None = None
    password: str | None = None
    use_sudo_su: bool = False
    sudo_password: str | None = None


class HostConfig(BaseModel):
    id: str
    name: str
    ssh: SSHConfig


class ConfigFileRef(BaseModel):
    name: str
    path: str
    profile: str | None = None


class ServiceConfig(BaseModel):
    id: str
    host_id: str
    name: str | None = None
    type: ServiceType
    enabled: bool = True
    jar_path: str | None = None
    deploy_dir: str | None = None
    systemd_unit: str | None = None
    container_name: str | None = None
    compose_file: str | None = None
    compose_service: str | None = None
    health_url: str | None = None
    health_deep_url: str | None = None
    business_probe_url: str | None = None
    business_probe_expect_code: int = 200
    business_probe_body_contains: str | None = None
    log_path: str | None = None
    config_files: list[ConfigFileRef] = Field(default_factory=list)
    active_profile: str | None = None
    listen_ports: list[int] = Field(default_factory=list)


class LLMTaskConfig(BaseModel):
    model: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None


class LLMDefaultConfig(BaseModel):
    provider: Literal["openai", "ollama"] = "openai"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    temperature: float = 0.2
    max_tokens: int = 4096


class LLMConfig(BaseModel):
    default: LLMDefaultConfig = Field(default_factory=LLMDefaultConfig)
    routing: dict[str, LLMTaskConfig] = Field(default_factory=dict)
    ollama_base_url: str = "http://localhost:11434"


class MonitorConfig(BaseModel):
    interval_seconds: int = 60
    health_fail_threshold: int = 3
    log_error_window_minutes: int = 5
    log_error_threshold: int = 5


class DiscoveryConfig(BaseModel):
    auto_scan_on_setup: bool = True
    rescan_interval_hours: int = 0


class FeishuBotConfig(BaseModel):
    command_enabled: bool = False
    command_chat_id: str = ""
    require_at_mention: bool = True


class FeishuConfig(BaseModel):
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    alert_chat_id: str = ""
    bot: FeishuBotConfig = Field(default_factory=FeishuBotConfig)


class WebConfig(BaseModel):
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8765
    auth_token: str = ""


class AutonomyConfig(BaseModel):
    max_restart_per_15min: int = 3
    write_allow_all_paths: bool = True
    write_path_whitelist: list[str] = Field(default_factory=list)


class ToolCompressionConfig(BaseModel):
    enabled: bool = True
    keep_raw: bool = False
    log_tail_lines: int = 80
    log_error_scan: bool = True


class ChatMemoryConfig(BaseModel):
    auto_extract: bool = True
    max_inject_tokens: int = 2000


class ChatPolicyConfig(BaseModel):
    yellow_threshold: float = 0.6
    orange_threshold: float = 0.8
    red_threshold: float = 0.9
    keep_recent_turns: int = 10
    shrink_keep_turns: int = 5
    summary_trigger_turns: int = 30
    tool_reserve_tokens: int = 8192


class ChatConfig(BaseModel):
    context_limit: int | None = None
    tool_compression: ToolCompressionConfig = Field(default_factory=ToolCompressionConfig)
    memory: ChatMemoryConfig = Field(default_factory=ChatMemoryConfig)
    policy: ChatPolicyConfig = Field(default_factory=ChatPolicyConfig)


class AppConfig(BaseModel):
    mode: Literal["remote", "local-linux", "local-windows"] = "remote"
    setup_completed: bool = False
    active_service_id: str | None = None
    active_host_id: str | None = None
    hosts: list[HostConfig] = Field(default_factory=list)
    services: list[ServiceConfig] = Field(default_factory=list)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    monitor: MonitorConfig = Field(default_factory=MonitorConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    autonomy: AutonomyConfig = Field(default_factory=AutonomyConfig)
    chat: ChatConfig = Field(default_factory=ChatConfig)
    data_dir: str = "./data"


class CommandResult(BaseModel):
    stdout: str
    stderr: str
    exit_code: int


class FileDownloadResult(BaseModel):
    host_id: str
    remote_path: str
    local_path: str
    bytes_downloaded: int
    used_sudo_staging: bool = False


class ArtifactCollectionResult(BaseModel):
    host_id: str
    remote_output_path: str
    bytes_written: int
    line_count: int | None = None
    command: str


class ServiceStatus(BaseModel):
    service_id: str
    running: bool
    detail: str = ""
    health_ok: bool | None = None
    health_detail: str = ""
    checked_at: datetime = Field(default_factory=datetime.utcnow)


class HostMetrics(BaseModel):
    host_id: str
    cpu_percent: float | None = None
    memory_percent: float | None = None
    disk_percent: float | None = None
    load_avg: str | None = None
    detail: str = ""


class ConfigFileCandidate(BaseModel):
    name: str
    path: str
    profile: str | None = None


class DiscoveredService(BaseModel):
    suggested_id: str
    suggested_name: str
    host_id: str
    service_type: ServiceType
    pid: int | None = None
    jar_path: str | None = None
    deploy_dir: str | None = None
    container_name: str | None = None
    compose_file: str | None = None
    compose_service: str | None = None
    systemd_unit: str | None = None
    listen_ports: list[int] = Field(default_factory=list)
    health_url: str | None = None
    log_path: str | None = None
    config_files: list[ConfigFileCandidate] = Field(default_factory=list)
    spring_profile: str | None = None
    confidence: float = 0.5
    evidence: dict[str, str] = Field(default_factory=dict)


class IncidentSeverity(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


class IncidentStatus(str, Enum):
    OPEN = "open"
    DIAGNOSING = "diagnosing"
    NOTIFIED = "notified"
    RESOLVED = "resolved"


class Incident(BaseModel):
    id: str
    service_id: str
    host_id: str
    title: str
    severity: IncidentSeverity
    status: IncidentStatus = IncidentStatus.OPEN
    summary: str = ""
    log_snippet: str = ""
    diagnosis: str | None = None
    suggestions: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DiagnosisResult(BaseModel):
    root_cause: str
    severity: str
    suggestions: list[str] = Field(default_factory=list)
    propose_restart: bool = False
    summary: str = ""
