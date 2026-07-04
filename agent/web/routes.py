from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent.config_mgr.setup import (
    FeishuSetupPayload,
    HostSetupPayload,
    InlineSSHTestPayload,
    LLMSetupPayload,
    SetupSavePayload,
    apply_setup_payload,
    test_feishu_config,
    test_llm_config,
    test_ssh_config,
)
from agent.discovery.orchestrator import scan_host, to_service_config
from agent.executor.ssh import get_executor_registry
from agent.executor.java_probe import find_java_process
from agent.feishu.notifier import FeishuNotifier
from agent.langchain.chat_graph import ChatAgent
from agent.models import AppConfig, ServiceConfig
from agent.monitor.loop import MonitorLoop
from agent.remediation.orchestrator import ActionOrchestrator
from agent.remediation.write_orchestrator import WriteOrchestrator
from agent.config_mgr.hosts import (
    build_host_config,
    delete_host,
    host_to_safe_dict,
    set_active_host,
    upsert_host,
)
from agent.paths import get_static_dir
from agent.services.chat_ops import (
    clear_conversation,
    confirm_memory_suggestion,
    create_conversation_workspace,
    create_knowledge_entry,
    delete_conversation,
    delete_knowledge_entry,
    get_chat_memory_settings,
    get_conversation_usage,
    handle_chat_message,
    list_conversations,
    list_knowledge_entries,
    load_chat_workspace,
    prepare_stream_chat,
    save_chat_memory_settings,
    update_knowledge_entry,
)
from agent.settings import Settings, UNCHANGED_SECRET, get_settings
from agent.store.incidents import IncidentStore

STATIC_DIR = get_static_dir()
NO_CACHE = "no-cache, no-store, must-revalidate"


def _html_response(path: Path) -> FileResponse:
    response = FileResponse(path)
    response.headers["Cache-Control"] = NO_CACHE
    return response


class ScanRequest(BaseModel):
    host_id: str


class RegisterServicesRequest(BaseModel):
    services: list[ServiceConfig]


class ChatSessionRequest(BaseModel):
    session_id: str = "default"


class CreateConversationRequest(BaseModel):
    title: str | None = None


class ChatMessageRequest(BaseModel):
    message: str
    session_id: str = "default"
    confirmed: bool = False


class ConfirmRestartRequest(BaseModel):
    service_id: str
    session_id: str = "default"


class ConfirmWriteRequest(BaseModel):
    write_id: str
    op_id: str | None = None
    session_id: str = "default"


class KnowledgeCreateRequest(BaseModel):
    category: str
    key: str
    value: str
    source_conv_id: str | None = None


class KnowledgeUpdateRequest(BaseModel):
    category: str | None = None
    key: str | None = None
    value: str | None = None


class KnowledgeConfirmRequest(BaseModel):
    category: str
    key: str
    value: str
    session_id: str | None = None


class ChatMemorySettingsRequest(BaseModel):
    auto_extract: bool


def _auth_dependency(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if settings.is_setup_needed():
        return
    token = settings.config.web.auth_token
    if not token:
        return
    if not authorization or authorization.removeprefix("Bearer ").strip() != token:
        raise HTTPException(status_code=401, detail="Unauthorized")


def create_app() -> FastAPI:
    app = FastAPI(title="Automatic Inspection Agent", version="0.1.0")
    chat_agent = ChatAgent()
    orchestrator = ActionOrchestrator()
    write_orchestrator = WriteOrchestrator()
    feishu = FeishuNotifier()

    @app.on_event("startup")
    async def startup() -> None:
        settings = get_settings()
        store = IncidentStore(settings.data_dir / "agent.db")
        await store.init()

        async def on_alert(incident):
            await feishu.send_incident_card(incident)

        monitor = MonitorLoop(on_alert=on_alert, incident_store=store)
        app.state.monitor = monitor
        await monitor.start()

        from agent.langchain.checkpointer import get_checkpointer
        from agent.store.chat import get_chat_store
        from agent.store.knowledge import get_knowledge_store

        await get_chat_store().init()
        await get_knowledge_store().init()
        await get_checkpointer()
        await chat_agent._ensure_checkpointer()

        from agent.feishu.runner import start_feishu_bot_if_enabled

        start_feishu_bot_if_enabled()

    @app.on_event("shutdown")
    async def shutdown() -> None:
        monitor = getattr(app.state, "monitor", None)
        if monitor:
            await monitor.stop()
        await get_executor_registry().close_all()
        from agent.langchain.checkpointer import close_checkpointer

        await close_checkpointer()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/")
    async def index() -> FileResponse:
        settings = get_settings()
        if settings.is_setup_needed():
            return _html_response(STATIC_DIR / "setup.html")
        return _html_response(STATIC_DIR / "index.html")

    @app.get("/setup")
    async def setup_page() -> FileResponse:
        return _html_response(STATIC_DIR / "setup.html")

    @app.get("/api/setup/status")
    async def setup_status() -> dict[str, Any]:
        settings = get_settings()
        return {
            "setup_needed": settings.is_setup_needed(),
            "setup_completed": settings.config.setup_completed,
            "config_path": str(settings.config_path),
        }

    @app.get("/api/setup/form")
    async def setup_form() -> dict[str, Any]:
        return get_settings().to_setup_form()

    @app.put("/api/setup/save")
    async def setup_save(body: SetupSavePayload) -> dict[str, Any]:
        apply_setup_payload(body)
        return {"saved": True, "setup_completed": get_settings().config.setup_completed}

    @app.post("/api/setup/test-ssh")
    async def setup_test_ssh(body: InlineSSHTestPayload) -> dict[str, Any]:
        try:
            return await test_ssh_config(body.host)
        except Exception as exc:
            return {"success": False, "stderr": str(exc), "exit_code": 1}

    @app.post("/api/setup/test-llm")
    async def setup_test_llm(body: LLMSetupPayload) -> dict[str, Any]:
        try:
            return await test_llm_config(body)
        except Exception as exc:
            return {"success": False, "response": str(exc)}

    @app.post("/api/setup/test-feishu")
    async def setup_test_feishu(body: FeishuSetupPayload) -> dict[str, Any]:
        return await test_feishu_config(body)

    @app.post("/api/setup/complete")
    async def setup_complete() -> dict[str, Any]:
        settings = get_settings()
        if not settings.config.hosts:
            raise HTTPException(status_code=400, detail="请先保存 SSH 主机配置")
        config = settings.config.model_copy(update={"setup_completed": True})
        settings.save(config)
        return {"setup_completed": True}

    @app.get("/api/config")
    async def get_config(_: None = Depends(_auth_dependency)) -> dict[str, Any]:
        return get_settings().config.model_dump()

    @app.put("/api/config")
    async def put_config(
        payload: dict[str, Any], _: None = Depends(_auth_dependency)
    ) -> dict[str, Any]:
        settings = get_settings()
        config = AppConfig.model_validate(payload)
        settings.save(config)
        return config.model_dump()

    @app.get("/api/hosts")
    async def list_hosts(_: None = Depends(_auth_dependency)) -> dict[str, Any]:
        settings = get_settings()
        return {
            "active_host_id": settings.config.active_host_id,
            "hosts": [host_to_safe_dict(h) for h in settings.config.hosts],
        }

    @app.post("/api/hosts")
    async def create_host(
        body: HostSetupPayload, _: None = Depends(_auth_dependency)
    ) -> dict[str, Any]:
        settings = get_settings()
        if any(h.id == body.id for h in settings.config.hosts):
            raise HTTPException(status_code=409, detail=f"主机 ID 已存在: {body.id}")
        host = upsert_host(build_host_config(body))
        return host_to_safe_dict(host)

    @app.put("/api/hosts/active")
    async def set_active_host_route(
        host_id: str, _: None = Depends(_auth_dependency)
    ) -> dict[str, str]:
        try:
            set_active_host(host_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        settings = get_settings()
        return {
            "active_host_id": host_id,
            "active_service_id": settings.config.active_service_id or "",
        }

    @app.put("/api/hosts/{host_id}")
    async def update_host(
        host_id: str, body: HostSetupPayload, _: None = Depends(_auth_dependency)
    ) -> dict[str, Any]:
        settings = get_settings()
        existing = settings.get_host(host_id)
        if body.id != host_id and any(h.id == body.id for h in settings.config.hosts):
            raise HTTPException(status_code=409, detail=f"主机 ID 已存在: {body.id}")
        host = build_host_config(body, existing)
        if body.id != host_id:
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

    @app.delete("/api/hosts/{host_id}")
    async def remove_host(host_id: str, _: None = Depends(_auth_dependency)) -> dict[str, Any]:
        try:
            delete_host(host_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"deleted": host_id}

    @app.post("/api/hosts/{host_id}/test-ssh")
    async def test_host_ssh(host_id: str, _: None = Depends(_auth_dependency)) -> dict[str, Any]:
        settings = get_settings()
        host = settings.get_host(host_id)
        from agent.config_mgr.setup import SSHSetupPayload

        return await test_ssh_config(
            SSHSetupPayload(
                host=host.ssh.host,
                port=host.ssh.port,
                user=host.ssh.user,
                key_file=host.ssh.key_file,
                password=host.ssh.password or UNCHANGED_SECRET,
                use_sudo_su=host.ssh.use_sudo_su,
                sudo_password=host.ssh.sudo_password or UNCHANGED_SECRET,
            ),
            existing=host,
        )

    @app.get("/api/services")
    async def list_services(_: None = Depends(_auth_dependency)) -> dict[str, Any]:
        settings = get_settings()
        return {
            "active_service_id": settings.config.active_service_id,
            "services": [s.model_dump() for s in settings.config.services],
        }

    @app.put("/api/services/active")
    async def set_active_service(
        service_id: str, _: None = Depends(_auth_dependency)
    ) -> dict[str, str]:
        settings = get_settings()
        settings.get_service(service_id)
        config = settings.config.model_copy(update={"active_service_id": service_id})
        settings.save(config)
        return {"active_service_id": service_id}

    @app.post("/api/discovery/scan")
    async def discovery_scan(
        body: ScanRequest, _: None = Depends(_auth_dependency)
    ) -> list[dict[str, Any]]:
        settings = get_settings()
        host = settings.get_host(body.host_id)
        executor = get_executor_registry().get(body.host_id, host)
        discovered = await scan_host(executor, body.host_id)
        return [d.model_dump() for d in discovered]

    @app.post("/api/discovery/register")
    async def register_services(
        body: RegisterServicesRequest, _: None = Depends(_auth_dependency)
    ) -> dict[str, Any]:
        settings = get_settings()
        existing = {s.id: s for s in settings.config.services}
        for svc in body.services:
            existing[svc.id] = svc
        config = settings.config.model_copy(update={"services": list(existing.values()), "setup_completed": True})
        if not config.active_service_id and config.services:
            config = config.model_copy(update={"active_service_id": config.services[0].id})
        settings.save(config)
        return {"registered": len(body.services), "services": [s.model_dump() for s in config.services]}

    @app.post("/api/services/{service_id}/sync-runtime")
    async def sync_service_runtime(
        service_id: str, _: None = Depends(_auth_dependency)
    ) -> dict[str, Any]:
        """从 Linux 进程实时读取 cwd/jar/log，补全服务注册信息。"""
        settings = get_settings()
        service = settings.get_service(service_id)
        host = settings.get_host(service.host_id)
        executor = get_executor_registry().get(service.host_id, host)

        updates: dict[str, Any] = {}
        if service.type.value == "java":
            probe = await find_java_process(executor, service)
            if not probe.get("running"):
                raise HTTPException(status_code=404, detail=probe.get("detail", "进程未找到"))
            primary = probe["primary"]
            if primary.get("deploy_dir"):
                updates["deploy_dir"] = primary["deploy_dir"]
            if primary.get("jar_path"):
                jar = primary["jar_path"]
                if jar and not str(jar).startswith("/") and primary.get("deploy_dir"):
                    jar = f"{primary['deploy_dir']}/{str(jar).split('/')[-1]}"
                updates["jar_path"] = jar
            if primary.get("log_candidates"):
                updates["log_path"] = primary["log_candidates"][0]
            if primary.get("listen_ports"):
                updates["listen_ports"] = primary["listen_ports"]
            if primary.get("spring_profile"):
                updates["active_profile"] = primary["spring_profile"]

        if not updates:
            return {"updated": False, "message": "无可同步字段"}

        merged = service.model_copy(update=updates)
        services = [merged if s.id == service_id else s for s in settings.config.services]
        settings.save(settings.config.model_copy(update={"services": services}))
        return {"updated": True, "service": merged.model_dump(), "synced": updates}

    @app.get("/api/status/summary")
    async def status_summary(
        host_id: str | None = None, _: None = Depends(_auth_dependency)
    ) -> list[dict[str, Any]]:
        import asyncio
        from collections import defaultdict

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
                from agent.executor.java_probe import list_java_processes

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

    @app.get("/api/incidents")
    async def list_incidents(_: None = Depends(_auth_dependency)) -> list[dict[str, Any]]:
        store = IncidentStore(get_settings().data_dir / "agent.db")
        await store.init()
        incidents = await store.list_incidents()
        return [i.model_dump() for i in incidents]

    @app.post("/api/inspection/run")
    async def run_inspection(_: None = Depends(_auth_dependency)) -> dict[str, Any]:
        monitor: MonitorLoop = app.state.monitor
        incidents = await monitor.run_once()
        return {"created": len(incidents), "incidents": [i.model_dump() for i in incidents]}

    @app.get("/api/chat/workspace")
    async def chat_workspace(
        conversation_id: str | None = None, _: None = Depends(_auth_dependency)
    ) -> dict[str, Any]:
        return await load_chat_workspace(conversation_id)

    @app.get("/api/chat/conversations")
    async def chat_conversations(_: None = Depends(_auth_dependency)) -> list[dict[str, Any]]:
        return await list_conversations()

    @app.post("/api/chat/conversations")
    async def chat_create_conversation(
        body: CreateConversationRequest, _: None = Depends(_auth_dependency)
    ) -> dict[str, Any]:
        return await create_conversation_workspace(body.title)

    @app.get("/api/chat/conversations/{conversation_id}/messages")
    async def chat_conversation_messages(
        conversation_id: str, _: None = Depends(_auth_dependency)
    ) -> dict[str, Any]:
        return await load_chat_workspace(conversation_id)

    @app.get("/api/chat/conversations/{conversation_id}/usage")
    async def chat_conversation_usage(
        conversation_id: str, _: None = Depends(_auth_dependency)
    ) -> dict[str, Any]:
        return await get_conversation_usage(conversation_id)

    @app.delete("/api/chat/conversations/{conversation_id}")
    async def chat_delete_conversation(
        conversation_id: str, _: None = Depends(_auth_dependency)
    ) -> dict[str, Any]:
        return await delete_conversation(conversation_id)

    @app.post("/api/chat/clear")
    async def chat_clear(
        body: ChatSessionRequest, _: None = Depends(_auth_dependency)
    ) -> dict[str, Any]:
        return await clear_conversation(body.session_id)

    @app.get("/api/chat/pending-file-op")
    async def pending_file_op(
        session_id: str = "default", _: None = Depends(_auth_dependency)
    ) -> dict[str, Any]:
        from agent.remediation.pending_writes import get_pending_file_op_store

        store = get_pending_file_op_store()
        item = store.latest_for_session(session_id)
        if not item:
            return {"pending": False}
        settings = get_settings()
        host = settings.get_host(item.host_id)
        host_label = f"{host.id} ({host.ssh.host})"
        return {"pending": True, **store.to_confirm_payload(item, host_label)}

    @app.post("/api/chat/message")
    async def chat_message(
        body: ChatMessageRequest, _: None = Depends(_auth_dependency)
    ) -> dict[str, Any]:
        import logging

        logger = logging.getLogger(__name__)
        try:
            result = await handle_chat_message(
                chat_agent,
                conversation_id=body.session_id,
                message=body.message,
                confirmed=body.confirmed,
            )
        except Exception as exc:
            logger.exception("chat_message failed")
            raise HTTPException(status_code=503, detail=f"对话服务异常: {exc}") from exc
        return result

    @app.post("/api/chat/stream")
    async def chat_stream(body: ChatMessageRequest, _: None = Depends(_auth_dependency)):
        from agent.langchain.memory_extractor import process_turn_memories
        from agent.store.chat import get_chat_store

        async def event_generator():
            prep = await prepare_stream_chat(
                chat_agent,
                body.session_id,
                body.message,
                confirmed=body.confirmed,
            )
            applied = prep.get("applied", [])
            for notice in prep.get("notices") or []:
                yield f"data: {json.dumps({'event': 'compaction', 'data': notice}, ensure_ascii=False)}\n\n"
            if prep.get("type") == "error":
                yield f"data: {json.dumps({'event': 'error', 'data': prep.get('message')}, ensure_ascii=False)}\n\n"
                usage = prep.get("usage") or await get_chat_store().get_usage(body.session_id, actions_applied=applied)
                yield f"data: {json.dumps({'event': 'usage', 'data': usage}, ensure_ascii=False)}\n\n"
                return

            store = get_chat_store()
            assistant_parts: list[str] = []
            async for chunk in chat_agent.stream_message(
                body.session_id, body.message, confirmed=body.confirmed
            ):
                if chunk.get("event") == "delta":
                    assistant_parts.append(str(chunk.get("data", "")))
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

            if assistant_parts:
                assistant_text = "".join(assistant_parts)
                await store.append_message(
                    body.session_id,
                    role="assistant",
                    content=assistant_text,
                )
                memory_info = await process_turn_memories(
                    user_text=body.message,
                    assistant_text=assistant_text,
                    conversation_id=body.session_id,
                )
                if memory_info.get("auto_saved"):
                    chat_agent.invalidate_graph()
                yield f"data: {json.dumps({'event': 'memory', 'data': memory_info}, ensure_ascii=False)}\n\n"
            usage = await store.get_usage(body.session_id, actions_applied=applied)
            yield f"data: {json.dumps({'event': 'usage', 'data': usage}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/chat/confirm-restart")
    async def confirm_restart(
        body: ConfirmRestartRequest, _: None = Depends(_auth_dependency)
    ) -> dict[str, Any]:
        result = await orchestrator.restart_service(body.service_id)
        return {
            "success": result.exit_code == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
        }

    @app.post("/api/chat/confirm-write")
    async def confirm_write(
        body: ConfirmWriteRequest, _: None = Depends(_auth_dependency)
    ) -> dict[str, Any]:
        op_id = body.op_id or body.write_id
        result = await write_orchestrator.execute_pending_op(op_id, body.session_id)
        return {
            "success": result.exit_code == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
        }

    @app.get("/api/chat/knowledge")
    async def chat_knowledge_list(_: None = Depends(_auth_dependency)) -> list[dict[str, Any]]:
        return await list_knowledge_entries()

    @app.post("/api/chat/knowledge")
    async def chat_knowledge_create(
        body: KnowledgeCreateRequest, _: None = Depends(_auth_dependency)
    ) -> dict[str, Any]:
        return await create_knowledge_entry(
            category=body.category,
            key=body.key,
            value=body.value,
            source_conv_id=body.source_conv_id,
            chat_agent=chat_agent,
        )

    @app.put("/api/chat/knowledge/{entry_id}")
    async def chat_knowledge_update(
        entry_id: str, body: KnowledgeUpdateRequest, _: None = Depends(_auth_dependency)
    ) -> dict[str, Any]:
        return await update_knowledge_entry(
            entry_id,
            category=body.category,
            key=body.key,
            value=body.value,
            chat_agent=chat_agent,
        )

    @app.delete("/api/chat/knowledge/{entry_id}")
    async def chat_knowledge_delete(
        entry_id: str, _: None = Depends(_auth_dependency)
    ) -> dict[str, Any]:
        return await delete_knowledge_entry(entry_id, chat_agent=chat_agent)

    @app.post("/api/chat/knowledge/confirm")
    async def chat_knowledge_confirm(
        body: KnowledgeConfirmRequest, _: None = Depends(_auth_dependency)
    ) -> dict[str, Any]:
        return await confirm_memory_suggestion(
            category=body.category,
            key=body.key,
            value=body.value,
            conversation_id=body.session_id,
            chat_agent=chat_agent,
        )

    @app.get("/api/chat/memory-settings")
    async def chat_memory_settings(_: None = Depends(_auth_dependency)) -> dict[str, Any]:
        return await get_chat_memory_settings()

    @app.put("/api/chat/memory-settings")
    async def chat_memory_settings_save(
        body: ChatMemorySettingsRequest, _: None = Depends(_auth_dependency)
    ) -> dict[str, Any]:
        return await save_chat_memory_settings(auto_extract=body.auto_extract)

    @app.post("/api/ssh/test")
    async def test_ssh(host_id: str, _: None = Depends(_auth_dependency)) -> dict[str, Any]:
        settings = get_settings()
        host = settings.get_host(host_id)
        executor = get_executor_registry().get(host_id, host)
        result = await executor.test_connection()
        return result.model_dump()

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app
