from __future__ import annotations

import json
from typing import Annotated

from langchain_core.tools import StructuredTool

from agent.config_mgr.hosts import enrich_service_systemd_unit
from agent.discovery.orchestrator import scan_host, to_service_config
from agent.executor.command_policy import format_command_result, validate_remote_command
from agent.executor.write_policy import is_path_allowed, normalize_remote_path
from agent.executor.ssh import get_executor_registry
from agent.langchain.context_builder import build_diagnosis_context
from agent.langchain.llm_factory import get_llm
from agent.langchain.session_context import chat_session_id
from agent.executor.java_probe import find_java_process
from agent.executor.middleware_probe import probe_middleware_process
from agent.executor.systemd_probe import probe_systemd_for_service, probe_systemd_unit
from agent.models import DiagnosisResult, ServiceType
from agent.monitor.loop import MonitorLoop
from agent.remediation.orchestrator import ActionOrchestrator
from agent.remediation.pending_writes import get_pending_file_op_store
from agent.settings import get_settings
from agent.store.incidents import IncidentStore


def _resolve_host(host_id: str | None = None):
    settings = get_settings()
    if not host_id:
        active = settings.config.active_host_id
        if active:
            return settings.get_host(active)
        if settings.config.hosts:
            return settings.config.hosts[0]
        raise KeyError("未配置任何主机")
    resolved_id = settings.resolve_host_id(host_id)
    return settings.get_host(resolved_id)


def _tool_error(prefix: str, exc: Exception) -> str:
    return f"{prefix}: {exc}"


def _extract_tool_output_text(output: str | object) -> str:
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        parts: list[str] = []
        for block in output:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif "text" in block:
                    parts.append(str(block["text"]))
        return "".join(parts)
    if hasattr(output, "content"):
        return _extract_tool_output_text(output.content)
    return str(output)


def parse_write_tool_pending(output: str | object) -> dict | None:
    return parse_file_op_pending(output)


def parse_file_op_pending(output: str | object) -> dict | None:
    raw = _extract_tool_output_text(output).strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                payload = json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                return None
        else:
            return None
    op_id = payload.get("op_id") or payload.get("write_id")
    if payload.get("status") == "pending_confirm" and op_id:
        payload.setdefault("op_id", op_id)
        payload.setdefault("write_id", op_id)
        return payload
    return None


def _resolve_tool_host(host_id: str | None, service_id: str | None, settings):
    if service_id:
        service = settings.get_service(service_id)
        return settings.get_host(service.host_id)
    return _resolve_host(host_id)


def _pending_file_op_response(pending, host_label: str, hint: str) -> str:
    confirm = get_pending_file_op_store().to_confirm_payload(pending, host_label)
    return json.dumps(
        {"status": "pending_confirm", **confirm, "hint": hint},
        ensure_ascii=False,
        indent=2,
    )


def _format_status(status) -> str:
    payload = {
        "service_id": status.service_id,
        "running": status.running,
        "running_label": "运行中" if status.running else "未运行",
        "detail": status.detail,
        "health_ok": status.health_ok,
        "health_detail": status.health_detail,
        "note": "running 由进程/systemd/docker 探针判定；health_ok 为 HTTP 健康检查（未配置则为 null）",
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _build_startup_summary(service, systemd_probe: dict, payload: dict) -> dict:
    verification = systemd_probe.get("verification", "unverified")
    managed = systemd_probe.get("managed_by_systemd")
    active_unit = systemd_probe.get("active_unit")
    registered_unit = service.systemd_unit

    if managed and active_unit:
        method = "systemd"
        conclusion = f"由 systemd 单元 {active_unit} 托管"
        confidence = "verified_systemd"
    elif service.container_name:
        method = "docker"
        conclusion = f"由 Docker 容器 {service.container_name} 运行"
        confidence = "registered"
    elif service.compose_file and service.compose_service:
        method = "docker_compose"
        conclusion = f"由 Compose 服务 {service.compose_service} 运行"
        confidence = "registered"
    elif payload.get("runtime", {}).get("running") or payload.get("pid"):
        method = "process"
        conclusion = "以进程方式运行（未确认 systemd/docker 托管）"
        confidence = verification
    else:
        method = "unknown"
        conclusion = "未检测到运行中的进程"
        confidence = "unverified"

    return {
        "method": method,
        "conclusion": conclusion,
        "confidence": confidence,
        "registered_systemd_unit": registered_unit,
        "do_not_infer_from_null": (
            "registered.systemd_unit 为 null 仅表示注册信息未记录，"
            "不能据此断定主机上没有 systemd 单元；以 systemd_probe 为准。"
        ),
    }


def build_readonly_tools() -> list[StructuredTool]:
    settings = get_settings()
    registry = get_executor_registry()
    incident_store = IncidentStore(settings.data_dir / "agent.db")
    monitor = MonitorLoop(settings=settings, incident_store=incident_store)

    async def list_services(host_id: str | None = None) -> str:
        """列出已注册服务，可按 host_id 过滤。"""
        try:
            services = settings.get_enabled_services()
            if host_id:
                resolved_id = settings.resolve_host_id(host_id)
                services = [s for s in services if s.host_id == resolved_id]
            payload = [s.model_dump() for s in services]
            return json.dumps(payload, ensure_ascii=False, indent=2)
        except Exception as exc:
            return _tool_error("list_services 失败", exc)

    async def get_service_status(service_id: str) -> str:
        """查询指定服务的运行状态和健康检查结果。"""
        try:
            service = settings.get_service(service_id)
            host = settings.get_host(service.host_id)
            executor = registry.get(service.host_id, host)
            status = await executor.service_status(service)
            return _format_status(status)
        except Exception as exc:
            return _tool_error("get_service_status 失败", exc)

    async def get_host_metrics(host_id: str) -> str:
        """查询 Linux 主机 CPU/内存/磁盘等指标。"""
        try:
            host = _resolve_host(host_id)
            executor = registry.get(host.id, host)
            metrics = await executor.get_metrics()
            return metrics.model_dump_json(indent=2)
        except Exception as exc:
            return _tool_error("get_host_metrics 失败", exc)

    async def read_log(service_id: str, pattern: str = "ERROR", lines: int = 200) -> str:
        """读取服务最近日志，可按 pattern 过滤。"""
        try:
            service = settings.get_service(service_id)
            if not service.log_path:
                return f"服务 {service_id} 未配置 log_path"
            host = settings.get_host(service.host_id)
            executor = registry.get(service.host_id, host)
            return await executor.tail_log(service.log_path, lines=lines, pattern=pattern or None)
        except Exception as exc:
            return _tool_error("read_log 失败", exc)

    async def read_remote_file(
        path: str,
        host_id: str | None = None,
        service_id: str | None = None,
        max_bytes: int = 65536,
    ) -> str:
        """通过 SSH 读取 Linux 主机上的文本文件（如配置文件、脚本）。path 须为绝对路径。"""
        try:
            path = path.strip()
            if not path.startswith("/"):
                return "请提供绝对路径，例如 /etc/nginx/nginx.conf"
            max_bytes = max(1024, min(int(max_bytes), 262144))

            if service_id:
                service = settings.get_service(service_id)
                host = settings.get_host(service.host_id)
            else:
                host = _resolve_host(host_id)
            executor = registry.get(host.id, host)
            content = await executor.read_file(path, max_bytes=max_bytes)
            if content.startswith(f"FILE_NOT_FOUND:{path}"):
                return f"文件不存在或无法读取: {path}"
            truncated = len(content.encode("utf-8", errors="ignore")) >= max_bytes
            header = f"# {path} @ {host.id} ({host.ssh.host})\n"
            if truncated:
                header += f"# 仅显示前 {max_bytes} 字节\n"
            return header + content
        except Exception as exc:
            return _tool_error("read_remote_file 失败", exc)

    async def run_remote_command(
        command: str,
        host_id: str | None = None,
        service_id: str | None = None,
        timeout_seconds: int = 60,
    ) -> str:
        """通过 SSH 在 Linux 主机上执行只读/诊断类 shell 命令（如 ls、ps、grep、systemctl status）。禁止 rm/reboot 等写操作。"""
        try:
            cmd = validate_remote_command(command)
            timeout_seconds = max(5, min(int(timeout_seconds), 120))

            if service_id:
                service = settings.get_service(service_id)
                host = settings.get_host(service.host_id)
            else:
                host = _resolve_host(host_id)
            executor = registry.get(host.id, host)
            result = await executor.run(cmd, timeout=timeout_seconds)
            host_label = f"{host.id} ({host.ssh.host})"
            return format_command_result(host_label, cmd, result)
        except Exception as exc:
            return _tool_error("run_remote_command 失败", exc)

    async def write_remote_file(
        path: str,
        content: str,
        host_id: str | None = None,
        service_id: str | None = None,
    ) -> str:
        """在 Linux 主机新建或覆盖文本文件。不会立即落盘，需用户在对话中逐次确认后执行。"""
        try:
            normalized = normalize_remote_path(path)
            if not is_path_allowed(normalized, settings.config.autonomy):
                return json.dumps(
                    {
                        "status": "rejected",
                        "reason": "路径未被允许（可在配置中开启 write_allow_all_paths）",
                        "path": normalized,
                    },
                    ensure_ascii=False,
                    indent=2,
                )

            host = _resolve_tool_host(host_id, service_id, settings)
            pending = get_pending_file_op_store().create_write(
                session_id=chat_session_id.get(),
                host_id=host.id,
                path=normalized,
                content=content,
            )
            host_label = f"{host.id} ({host.ssh.host})"
            return _pending_file_op_response(
                pending,
                host_label,
                "已创建写入请求，请等待用户在界面确认后才会落盘。一次只处理一个文件操作。",
            )
        except Exception as exc:
            return _tool_error("write_remote_file 失败", exc)

    async def delete_remote_file(
        path: str,
        host_id: str | None = None,
        service_id: str | None = None,
    ) -> str:
        """删除 Linux 主机上的文件。不会立即删除，需用户在对话中逐次确认后执行。"""
        try:
            normalized = normalize_remote_path(path)
            if not is_path_allowed(normalized, settings.config.autonomy):
                return json.dumps(
                    {
                        "status": "rejected",
                        "reason": "路径未被允许（可在配置中开启 write_allow_all_paths）",
                        "path": normalized,
                    },
                    ensure_ascii=False,
                    indent=2,
                )

            host = _resolve_tool_host(host_id, service_id, settings)
            pending = get_pending_file_op_store().create_delete(
                session_id=chat_session_id.get(),
                host_id=host.id,
                path=normalized,
            )
            host_label = f"{host.id} ({host.ssh.host})"
            return _pending_file_op_response(
                pending,
                host_label,
                "已创建删除请求，请等待用户在界面确认后才会执行。",
            )
        except Exception as exc:
            return _tool_error("delete_remote_file 失败", exc)

    async def discovery_scan(host_id: str) -> str:
        """扫描指定 Linux 主机上的 Java/Docker/Compose/中间件服务。"""
        try:
            host = _resolve_host(host_id)
            executor = registry.get(host.id, host)
            discovered = await scan_host(executor, host.id)
            return json.dumps([d.model_dump() for d in discovered], ensure_ascii=False, indent=2, default=str)
        except Exception as exc:
            return _tool_error("discovery_scan 失败", exc)

    async def run_inspection(host_id: str | None = None) -> str:
        """立即执行一次巡检，返回新产生的告警数量。"""
        try:
            incidents = await monitor.run_once()
            if host_id:
                resolved_id = settings.resolve_host_id(host_id)
                incidents = [i for i in incidents if i.host_id == resolved_id]
            return json.dumps(
                [{"id": i.id, "title": i.title, "service_id": i.service_id} for i in incidents],
                ensure_ascii=False,
                indent=2,
            )
        except Exception as exc:
            return _tool_error("run_inspection 失败", exc)

    async def list_incidents(limit: int = 10) -> str:
        """列出最近 Incident 告警记录。"""
        await incident_store.init()
        incidents = await incident_store.list_incidents(limit=limit)
        return json.dumps([i.model_dump() for i in incidents], ensure_ascii=False, indent=2, default=str)

    async def analyze_incident(incident_id: str) -> str:
        """对指定 Incident 进行 LLM 根因分析与修复建议。"""
        await incident_store.init()
        incident = await incident_store.get_incident(incident_id)
        if not incident:
            return f"未找到 incident: {incident_id}"
        service = settings.get_service(incident.service_id)
        host = settings.get_host(service.host_id)
        executor = registry.get(service.host_id, host)
        log_tail = incident.log_snippet
        if service.log_path:
            log_tail = await executor.tail_log(service.log_path, lines=200, pattern="ERROR|Exception|OOM")
        status = await executor.service_status(service)
        context = build_diagnosis_context(incident, service, log_tail, _format_status(status))
        llm = get_llm("diagnosis")
        structured = llm.with_structured_output(DiagnosisResult)
        result: DiagnosisResult = await structured.ainvoke(
            [
                {
                    "role": "system",
                    "content": "你是资深 SRE，请根据证据给出根因、严重级别、修复建议，仅在必要时建议重启。",
                },
                {"role": "user", "content": context},
            ]
        )
        await incident_store.update_diagnosis(incident_id, result.root_cause, result.suggestions)
        return result.model_dump_json(indent=2)

    async def get_deployment_info(service_id: str) -> str:
        """查询服务部署位置：部署目录(cwd)、PID、启动命令、jar 路径、端口、日志候选、systemd 托管探测。"""
        try:
            service = settings.get_service(service_id)
            host = settings.get_host(service.host_id)
            executor = registry.get(service.host_id, host)
            runtime_pid: int | None = None
            payload: dict = {
                "service_id": service_id,
                "host": {"id": host.id, "name": host.name, "ssh_host": host.ssh.host},
                "registered": service.model_dump(),
                "source_labels": {
                    "registered": "config_registry",
                    "runtime": "live_probe",
                    "systemd_probe": "live_probe",
                },
            }
            if service.type == ServiceType.JAVA:
                probe = await find_java_process(executor, service)
                payload["runtime"] = probe
                if probe.get("primary"):
                    primary = probe["primary"]
                    runtime_pid = primary.get("pid")
                    payload["deploy_dir"] = primary.get("deploy_dir")
                    payload["pid"] = runtime_pid
                    payload["cmdline"] = primary.get("cmdline")
                    payload["jar_path"] = primary.get("jar_path")
                    payload["listen_ports"] = primary.get("listen_ports", [])
                    payload["log_candidates"] = primary.get("log_candidates", [])
            elif service.type == ServiceType.MIDDLEWARE:
                if service.systemd_unit:
                    payload["systemd"] = await probe_systemd_unit(executor, service.systemd_unit)
                payload["process"] = await probe_middleware_process(executor, service.id)
                proc = payload.get("process") or {}
                if isinstance(proc, dict) and proc.get("pid"):
                    runtime_pid = proc.get("pid")

            systemd_probe = await probe_systemd_for_service(
                executor,
                service_id,
                pid=runtime_pid,
                registered_unit=service.systemd_unit,
            )
            payload["systemd_probe"] = systemd_probe
            payload["startup_summary"] = _build_startup_summary(service, systemd_probe, payload)

            detected_unit = systemd_probe.get("active_unit") or systemd_probe.get("detected_from_pid")
            if detected_unit and enrich_service_systemd_unit(service_id, detected_unit):
                payload["registry_updated"] = {
                    "systemd_unit": detected_unit,
                    "message": "已自动补全注册信息中的 systemd_unit",
                }
                service = settings.get_service(service_id)
                payload["registered"] = service.model_dump()

            status = await executor.service_status(service)
            payload["status"] = status.model_dump(mode="json")
            return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        except Exception as exc:
            return _tool_error("get_deployment_info 失败", exc)

    async def list_config_files(service_id: str) -> str:
        """列出服务关联的配置文件路径（读取内容请用 read_remote_file）。"""
        service = settings.get_service(service_id)
        files = [c.model_dump() for c in service.config_files]
        if service.log_path:
            files.append({"name": "log", "path": service.log_path})
        if service.jar_path:
            files.append({"name": "jar", "path": service.jar_path})
        if service.deploy_dir:
            files.append({"name": "deploy_dir", "path": service.deploy_dir})
        return json.dumps(files, ensure_ascii=False, indent=2)

    return [
        StructuredTool.from_function(coroutine=list_services, name="list_services"),
        StructuredTool.from_function(coroutine=get_service_status, name="get_service_status"),
        StructuredTool.from_function(coroutine=get_deployment_info, name="get_deployment_info"),
        StructuredTool.from_function(coroutine=get_host_metrics, name="get_host_metrics"),
        StructuredTool.from_function(coroutine=read_log, name="read_log"),
        StructuredTool.from_function(coroutine=read_remote_file, name="read_remote_file"),
        StructuredTool.from_function(coroutine=write_remote_file, name="write_remote_file"),
        StructuredTool.from_function(coroutine=delete_remote_file, name="delete_remote_file"),
        StructuredTool.from_function(coroutine=run_remote_command, name="run_remote_command"),
        StructuredTool.from_function(coroutine=discovery_scan, name="discovery_scan"),
        StructuredTool.from_function(coroutine=run_inspection, name="run_inspection"),
        StructuredTool.from_function(coroutine=list_incidents, name="list_incidents"),
        StructuredTool.from_function(coroutine=analyze_incident, name="analyze_incident"),
        StructuredTool.from_function(coroutine=list_config_files, name="list_config_files"),
    ]
