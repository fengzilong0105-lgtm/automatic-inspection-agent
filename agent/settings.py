from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from agent.models import AppConfig
from agent.paths import get_app_root, get_data_dir

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")
UNCHANGED_SECRET = "__UNCHANGED__"


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):

        def replacer(match: re.Match[str]) -> str:
            key = match.group(1)
            return os.environ.get(key, "")

        return _ENV_PATTERN.sub(replacer, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def mask_secret(value: str, visible: int = 4) -> str | None:
    if not value:
        return None
    if len(value) <= visible * 2:
        return "*" * len(value)
    return f"{value[:visible]}...{value[-visible:]}"


class Settings:
    def __init__(self, config_path: Path | None = None) -> None:
        self.project_root = get_app_root()
        self.data_dir = get_data_dir()
        self.config_path = config_path or (self.data_dir / "config.yaml")
        self._config = self._load()

    def _load(self) -> AppConfig:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            config = AppConfig()
            if not Path(config.data_dir).is_absolute():
                config = config.model_copy(update={"data_dir": str(self.data_dir)})
            return config

        raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        raw = _expand_env(raw)
        config = AppConfig.model_validate(raw)
        self.data_dir = Path(config.data_dir)
        if not self.data_dir.is_absolute():
            self.data_dir = self.project_root / self.data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return config

    @property
    def config(self) -> AppConfig:
        return self._config

    def reload(self) -> AppConfig:
        self._config = self._load()
        return self._config

    def save(self, config: AppConfig | None = None) -> None:
        payload = (config or self._config).model_dump(mode="json")
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        self._config = AppConfig.model_validate(payload)

    def get_host(self, host_id: str):
        for host in self._config.hosts:
            if host.id == host_id:
                return host
        raise KeyError(f"Host not found: {host_id}")

    def resolve_host_id(self, host_ref: str) -> str:
        """Resolve host id from exact id, IP, or partial match."""
        ref = (host_ref or "").strip()
        if not ref:
            raise KeyError("Host id is required")

        for host in self._config.hosts:
            if host.id == ref:
                return host.id

        for host in self._config.hosts:
            if host.ssh.host == ref:
                return host.id

        lowered = ref.lower()
        for host in self._config.hosts:
            if lowered in host.id.lower() or lowered in host.ssh.host.lower():
                return host.id

        raise KeyError(f"Host not found: {host_ref}")

    def get_service(self, service_id: str):
        for service in self._config.services:
            if service.id == service_id:
                return service
        raise KeyError(f"Service not found: {service_id}")

    def get_enabled_services(self) -> list:
        return [s for s in self._config.services if s.enabled]

    def is_setup_needed(self) -> bool:
        if self._config.setup_completed:
            return False
        if not self._config.hosts:
            return True
        host = self._config.hosts[0]
        return not bool(host.ssh.host and host.ssh.user)

    def to_setup_form(self) -> dict[str, Any]:
        host = self._config.hosts[0] if self._config.hosts else None
        llm = self._config.llm.default
        feishu = self._config.feishu
        return {
            "setup_completed": self._config.setup_completed,
            "active_host_id": self._config.active_host_id,
            "hosts": [
                {
                    "id": item.id,
                    "name": item.name,
                    "ssh": {
                        "host": item.ssh.host,
                        "port": item.ssh.port,
                        "user": item.ssh.user,
                        "key_file": item.ssh.key_file or "",
                        "password_set": bool(item.ssh.password),
                        "use_sudo_su": item.ssh.use_sudo_su,
                        "sudo_password_set": bool(item.ssh.sudo_password),
                    },
                }
                for item in self._config.hosts
            ],
            "host": {
                "id": host.id if host else "prod-01",
                "name": host.name if host else "生产服务器",
                "ssh": {
                    "host": host.ssh.host if host else "",
                    "port": host.ssh.port if host else 22,
                    "user": host.ssh.user if host else "",
                    "key_file": host.ssh.key_file if host else "",
                    "password_set": bool(host and host.ssh.password),
                    "use_sudo_su": host.ssh.use_sudo_su if host else False,
                    "sudo_password_set": bool(host and host.ssh.sudo_password),
                },
            },
            "llm": {
                "provider": llm.provider,
                "base_url": llm.base_url,
                "model": llm.model,
                "temperature": llm.temperature,
                "max_tokens": llm.max_tokens,
                "api_key_set": bool(llm.api_key),
                "api_key_masked": mask_secret(llm.api_key),
                "ollama_base_url": self._config.llm.ollama_base_url,
            },
            "feishu": {
                "enabled": feishu.enabled,
                "app_id": feishu.app_id,
                "alert_chat_id": feishu.alert_chat_id,
                "app_secret_set": bool(feishu.app_secret),
                "app_secret_masked": mask_secret(feishu.app_secret),
            },
            "web": {
                "port": self._config.web.port,
                "auth_token_set": bool(self._config.web.auth_token),
            },
        }


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None
