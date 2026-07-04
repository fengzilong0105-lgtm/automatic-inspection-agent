from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from agent.config_mgr.hosts import (
    build_host_config,
    delete_host,
    host_to_safe_dict,
    set_active_host,
    upsert_host,
)
from agent.config_mgr.setup import (
    FeishuSetupPayload,
    HostSetupPayload,
    InlineSSHTestPayload,
    LLMSetupPayload,
    SetupSavePayload,
    SSHSetupPayload,
    apply_setup_payload,
    apply_llm_feishu_payload,
    test_feishu_config,
    test_llm_config,
    test_ssh_config,
)
from agent.discovery.orchestrator import scan_host, to_service_config
from agent.executor.java_probe import list_java_processes
from agent.executor.ssh import get_executor_registry
from agent.models import AppConfig, ServiceConfig
from agent.runtime.background import BackgroundRuntime, get_runtime
from agent.settings import UNCHANGED_SECRET, get_settings
from agent.services.chat_ops import (
    clear_conversation,
    confirm_memory_suggestion,
    create_conversation_workspace,
    create_knowledge_entry,
    delete_conversation,
    delete_knowledge_entry,
    ensure_default_conversation,
    get_chat_memory_settings,
    get_conversation_messages,
    get_conversation_usage,
    handle_chat_message,
    list_conversations,
    list_knowledge_entries,
    load_chat_workspace,
    save_chat_memory_settings,
    update_knowledge_entry,
)
from agent.store.incidents import IncidentStore


class AgentService:
    """Business facade for the desktop UI (same capabilities as the former Web API)."""

    def __init__(self, runtime: BackgroundRuntime | None = None) -> None:
        self.runtime = runtime or get_runtime()

    def _run(self, coro):
        return self.runtime.run(coro)

    # --- setup ---

    def setup_status(self) -> dict[str, Any]:
        settings = get_settings()
        return {
            "setup_needed": settings.is_setup_needed(),
            "setup_completed": settings.config.setup_completed,
            "config_path": str(settings.config_path),
        }

    def setup_form(self) -> dict[str, Any]:
        return get_settings().to_setup_form()

    def save_setup(self, payload: SetupSavePayload) -> dict[str, Any]:
        apply_setup_payload(payload)
        settings = get_settings()
        return {"saved": True, "setup_completed": settings.config.setup_completed}

    def complete_setup(self) -> dict[str, Any]:
        settings = get_settings()
        if not settings.config.hosts:
            raise ValueError("请先保存 SSH 主机配置")
        config = settings.config.model_copy(update={"setup_completed": True})
        settings.save(config)
        return {"setup_completed": True}

    def test_ssh(self, ssh: InlineSSHTestPayload) -> Any:
        return self._run(test_ssh_config(ssh.host))

    def test_llm(self, llm: LLMSetupPayload) -> Any:
        return self._run(test_llm_config(llm))

    def test_feishu(self, feishu: FeishuSetupPayload) -> Any:
        return self._run(test_feishu_config(feishu))

    # --- hosts / config ---

    def list_hosts(self) -> dict[str, Any]:
        settings = get_settings()
        return {
            "active_host_id": settings.config.active_host_id,
            "hosts": [host_to_safe_dict(h) for h in settings.config.hosts],
        }

    def set_active_host(self, host_id: str) -> dict[str, str]:
        set_active_host(host_id)
        settings = get_settings()
        return {
            "active_host_id": host_id,
            "active_service_id": settings.config.active_service_id or "",
        }

    def upsert_host_config(self, body: HostSetupPayload, host_id: str | None = None) -> dict[str, Any]:
        settings = get_settings()
        existing = settings.get_host(host_id) if host_id else None
        if host_id and body.id != host_id and any(h.id == body.id for h in settings.config.hosts):
            raise ValueError(f"主机 ID 已存在: {body.id}")
        host = build_host_config(body, existing)
        if host_id and body.id != host_id:
            services = [
                s.model_copy(update={"host_id": body.id}) if s.host_id == host_id else s
                for s in settings.config.services
            ]
            hosts = [host if h.id == host_id else h for h in settings.config.hosts]
            active_host_id = (
                body.id if settings.config.active_host_id == host_id else settings.config.active_host_id
            )
            settings.save(
                settings.config.model_copy(
                    update={"hosts": hosts, "services": services, "active_host_id": active_host_id}
                )
            )
            from agent.config_mgr.hosts import _reset_ssh_pool

            _reset_ssh_pool()
        else:
            upsert_host(host, settings)
        return host_to_safe_dict(host)

    def delete_host(self, host_id: str) -> dict[str, Any]:
        delete_host(host_id)
        return {"deleted": host_id}

    def save_llm_feishu(self, llm: LLMSetupPayload, feishu: FeishuSetupPayload | None) -> None:
        apply_llm_feishu_payload(llm, feishu)

    def save_llm_feishu_async(self, llm: LLMSetupPayload, feishu: FeishuSetupPayload | None):
        return self._run(self._save_llm_feishu_async(llm, feishu))

    async def _save_llm_feishu_async(
        self, llm: LLMSetupPayload, feishu: FeishuSetupPayload | None
    ) -> str:
        await asyncio.to_thread(apply_llm_feishu_payload, llm, feishu)
        return "设置已保存"

    # --- discovery / services ---

    def scan_host(self, host_id: str) -> Any:
        return self._run(self._scan_host(host_id))

    async def _scan_host(self, host_id: str) -> list[dict[str, Any]]:
        settings = get_settings()
        host = settings.get_host(host_id)
        executor = get_executor_registry().get(host_id, host)
        discovered = await scan_host(executor, host_id)
        return [d.model_dump() for d in discovered]

    def register_services(self, services: list[ServiceConfig]) -> dict[str, Any]:
        settings = get_settings()
        existing = {s.id: s for s in settings.config.services}
        for svc in services:
            old = existing.get(svc.id)
            if old:
                svc = svc.model_copy(
                    update={
                        "systemd_unit": svc.systemd_unit or old.systemd_unit,
                        "deploy_dir": svc.deploy_dir or old.deploy_dir,
                        "jar_path": svc.jar_path or old.jar_path,
                        "log_path": svc.log_path or old.log_path,
                        "listen_ports": svc.listen_ports or old.listen_ports,
                    }
                )
            existing[svc.id] = svc
        config = settings.config.model_copy(
            update={"services": list(existing.values()), "setup_completed": True}
        )
        if not config.active_service_id and config.services:
            config = config.model_copy(update={"active_service_id": config.services[0].id})
        settings.save(config)
        return {"registered": len(services), "services": [s.model_dump() for s in config.services]}

    def list_services(self) -> dict[str, Any]:
        settings = get_settings()
        return {
            "active_service_id": settings.config.active_service_id,
            "services": [s.model_dump() for s in settings.config.services],
        }

    def set_active_service(self, service_id: str) -> dict[str, str]:
        settings = get_settings()
        settings.get_service(service_id)
        config = settings.config.model_copy(update={"active_service_id": service_id})
        settings.save(config)
        return {"active_service_id": service_id}

    # --- status / incidents / inspection ---

    def status_summary(self, host_id: str | None = None) -> Any:
        return self._run(self._status_summary(host_id))

    async def _status_summary(self, host_id: str | None = None) -> list[dict[str, Any]]:
        settings = get_settings()
        registry = get_executor_registry()
        services = settings.get_enabled_services()
        if host_id:
            services = [s for s in services if s.host_id == host_id]

        by_host: dict[str, list] = defaultdict(list)
        for service in services:
            by_host[service.host_id].append(service)

        async def check_host_services(hid: str, host_services: list) -> list[dict[str, Any]]:
            host = settings.get_host(hid)
            executor = registry.get(hid, host)
            java_index = None
            if any(s.type.value == "java" and not s.systemd_unit for s in host_services):
                try:
                    java_index = await list_java_processes(executor)
                except Exception:
                    java_index = None

            sem = asyncio.Semaphore(6)

            async def check_service(service) -> dict[str, Any]:
                async with sem:
                    try:
                        status = await executor.service_status(service, java_process_index=java_index)
                        return {
                            "service": service.model_dump(mode="json"),
                            "status": status.model_dump(mode="json"),
                        }
                    except Exception as exc:
                        return {
                            "service": service.model_dump(mode="json"),
                            "status": {
                                "service_id": service.id,
                                "running": False,
                                "detail": f"检测失败: {exc}",
                                "health_ok": None,
                                "health_detail": "",
                            },
                        }

            return list(await asyncio.gather(*(check_service(service) for service in host_services)))

        chunks = await asyncio.gather(
            *[check_host_services(hid, svcs) for hid, svcs in by_host.items()]
        )
        results: list[dict[str, Any]] = []
        for chunk in chunks:
            results.extend(chunk)
        return results

    def list_incidents(self) -> Any:
        return self._run(self._list_incidents())

    async def _list_incidents(self) -> list[dict[str, Any]]:
        store = IncidentStore(get_settings().data_dir / "agent.db")
        await store.init()
        incidents = await store.list_incidents()
        return [i.model_dump() for i in incidents]

    def run_inspection(self) -> Any:
        return self._run(self._run_inspection())

    async def _run_inspection(self) -> dict[str, Any]:
        monitor = self.runtime.monitor
        if not monitor:
            raise RuntimeError("Monitor is not running")
        incidents = await monitor.run_once()
        return {"created": len(incidents), "incidents": [i.model_dump() for i in incidents]}

    # --- chat ---

    def load_chat_workspace(self, conversation_id: str | None = None) -> Any:
        return self._run(load_chat_workspace(conversation_id))

    def create_conversation_workspace(self, title: str | None = None) -> Any:
        return self._run(create_conversation_workspace(title))

    def list_conversations(self) -> Any:
        return self._run(list_conversations())

    def create_conversation(self, title: str | None = None) -> Any:
        return self._run(create_conversation_workspace(title))

    def ensure_default_conversation(self) -> Any:
        return self._run(ensure_default_conversation())

    def get_conversation_messages(self, conversation_id: str) -> Any:
        return self._run(get_conversation_messages(conversation_id))

    def get_conversation_usage(self, conversation_id: str) -> Any:
        return self._run(get_conversation_usage(conversation_id))

    def delete_conversation(self, conversation_id: str) -> Any:
        return self._run(delete_conversation(conversation_id))

    def chat_message(
        self, message: str, session_id: str = "desktop-default", confirmed: bool = False
    ) -> Any:
        return self._run(
            handle_chat_message(
                self.runtime.chat_agent,
                conversation_id=session_id,
                message=message,
                confirmed=confirmed,
            )
        )

    def chat_clear(self, session_id: str = "desktop-default") -> Any:
        return self._run(clear_conversation(session_id))

    def confirm_restart(self, service_id: str) -> Any:
        return self._run(self._confirm_restart(service_id))

    async def _confirm_restart(self, service_id: str) -> dict[str, Any]:
        result = await self.runtime.action_orchestrator.restart_service(service_id)
        return {
            "success": result.exit_code == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
        }

    def confirm_write(self, write_id: str, session_id: str = "desktop-default") -> Any:
        return self._run(self._confirm_write(write_id, session_id))

    async def _confirm_write(self, write_id: str, session_id: str) -> dict[str, Any]:
        result = await self.runtime.write_orchestrator.execute_pending_op(write_id, session_id)
        return {
            "success": result.exit_code == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
        }

    def pending_file_op(self, session_id: str = "desktop-default") -> Any:
        return self._run(self._pending_file_op(session_id))

    async def _pending_file_op(self, session_id: str) -> dict[str, Any]:
        from agent.remediation.pending_writes import get_pending_file_op_store

        store = get_pending_file_op_store()
        item = store.latest_for_session(session_id)
        if not item:
            return {"pending": False}
        settings = get_settings()
        host = settings.get_host(item.host_id)
        host_label = f"{host.id} ({host.ssh.host})"
        return {"pending": True, **store.to_confirm_payload(item, host_label)}

    # --- knowledge / memory ---

    def list_knowledge(self) -> Any:
        return self._run(list_knowledge_entries())

    def create_knowledge(
        self, category: str, key: str, value: str, source_conv_id: str | None = None
    ) -> Any:
        return self._run(
            create_knowledge_entry(
                category=category,
                key=key,
                value=value,
                source_conv_id=source_conv_id,
                chat_agent=self.runtime.chat_agent,
            )
        )

    def update_knowledge(
        self,
        entry_id: str,
        *,
        category: str | None = None,
        key: str | None = None,
        value: str | None = None,
    ) -> Any:
        return self._run(
            update_knowledge_entry(
                entry_id,
                category=category,
                key=key,
                value=value,
                chat_agent=self.runtime.chat_agent,
            )
        )

    def delete_knowledge(self, entry_id: str) -> Any:
        return self._run(delete_knowledge_entry(entry_id, chat_agent=self.runtime.chat_agent))

    def confirm_memory(
        self,
        category: str,
        key: str,
        value: str,
        conversation_id: str | None = None,
    ) -> Any:
        return self._run(
            confirm_memory_suggestion(
                category=category,
                key=key,
                value=value,
                conversation_id=conversation_id,
                chat_agent=self.runtime.chat_agent,
            )
        )

    def get_memory_settings(self) -> Any:
        return self._run(get_chat_memory_settings())

    def save_memory_settings(self, auto_extract: bool) -> Any:
        return self._run(save_chat_memory_settings(auto_extract=auto_extract))

    @staticmethod
    def discovered_to_services(host_id: str, discovered: list[dict[str, Any]]) -> list[ServiceConfig]:
        from agent.models import DiscoveredService

        return [to_service_config(DiscoveredService.model_validate(item)) for item in discovered]
