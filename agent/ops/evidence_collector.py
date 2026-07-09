from __future__ import annotations

from typing import Any

from agent.executor.ssh import get_executor_registry
from agent.langchain.context_builder import build_diagnosis_context
from agent.models import Incident
from agent.ops.models import EvidenceBundle
from agent.settings import get_settings


def _format_status(status) -> str:
    parts = [f"running={status.running}", status.detail or ""]
    if status.health_ok is not None:
        parts.append(f"health_ok={status.health_ok}")
        if status.health_detail:
            parts.append(status.health_detail)
    return " | ".join(part for part in parts if part)


def _build_chat_excerpt(messages: list, *, max_chars: int = 32000) -> str:
    lines: list[str] = []
    for message in messages[-40:]:
        role = getattr(message, "role", message.get("role") if isinstance(message, dict) else "unknown")
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content", "")
        tool_name = getattr(message, "tool_name", None)
        if tool_name is None and isinstance(message, dict):
            tool_name = message.get("tool_name")
        text = (content or "").strip()
        if not text:
            continue
        prefix = f"[{role}]"
        if tool_name:
            prefix = f"[{role}/{tool_name}]"
        per_message_limit = 12000 if role == "assistant" else 2000
        lines.append(f"{prefix} {text[:per_message_limit]}")
    excerpt = "\n".join(lines)
    return excerpt[:max_chars]


def _build_chat_diagnosis_context(
    *,
    service_id: str,
    host_id: str,
    status_detail: str,
    log_tail: str,
    chat_excerpt: str,
    hint: str | None,
    conversation_id: str,
) -> str:
    parts = [
        f"服务: {service_id}",
        f"主机: {host_id}",
        f"状态: {status_detail}",
        f"对话 ID: {conversation_id}",
    ]
    if hint:
        parts.append(f"运维补充: {hint}")
    if chat_excerpt:
        parts.append(f"对话摘录:\n{chat_excerpt}")
    if log_tail:
        parts.append(f"近期日志:\n{log_tail[:4000]}")
    return "\n\n".join(parts)


async def _collect_service_probe(service_id: str) -> tuple[Any, Any, Any, str, dict[str, Any] | None, str]:
    settings = get_settings()
    service = settings.get_service(service_id)
    host = settings.get_host(service.host_id)
    executor = get_executor_registry().get(service.host_id, host)

    log_tail = ""
    service_status: dict[str, Any] | None = None
    status_detail = ""

    try:
        status = await executor.service_status(service)
        service_status = status.model_dump(mode="json")
        status_detail = _format_status(status)
        if service.log_path:
            log_tail = await executor.tail_log(
                service.log_path, lines=200, pattern="ERROR|Exception|OOM|WARN"
            )
    except Exception as exc:
        service_status = {"error": str(exc)}
        status_detail = str(exc)

    deployment_info = {
        "registered": service.model_dump(mode="json"),
        "host": {"id": host.id, "name": host.name, "ssh_host": host.ssh.host},
    }
    return service, host, executor, log_tail, service_status, status_detail


async def collect_from_incident(incident: Incident) -> EvidenceBundle:
    service, host, _executor, log_tail, service_status, status_detail = await _collect_service_probe(
        incident.service_id
    )
    if not log_tail:
        log_tail = incident.log_snippet or ""

    incident_snapshot = incident.model_dump(mode="json")
    incident_snapshot["diagnosis_context"] = build_diagnosis_context(
        incident,
        service,
        log_tail[:6000],
        status_detail,
    )

    return EvidenceBundle(
        service_id=service.id,
        host_id=host.id,
        incident=incident_snapshot,
        service_status=service_status,
        deployment_info={
            "registered": service.model_dump(mode="json"),
            "host": {"id": host.id, "name": host.name, "ssh_host": host.ssh.host},
        },
        log_tail=log_tail[:6000] if log_tail else None,
    )


async def collect_from_service(
    service_id: str,
    *,
    hint: str | None = None,
) -> EvidenceBundle:
    service, host, _executor, log_tail, service_status, status_detail = await _collect_service_probe(
        service_id
    )
    context = _build_chat_diagnosis_context(
        service_id=service.id,
        host_id=host.id,
        status_detail=status_detail,
        log_tail=log_tail,
        chat_excerpt="",
        hint=hint,
        conversation_id="",
    )
    return EvidenceBundle(
        service_id=service.id,
        host_id=host.id,
        incident={"hint": hint or "", "diagnosis_context": context},
        service_status=service_status,
        deployment_info={
            "registered": service.model_dump(mode="json"),
            "host": {"id": host.id, "name": host.name, "ssh_host": host.ssh.host},
        },
        log_tail=log_tail[:6000] if log_tail else None,
    )


async def collect_from_chat(
    conversation_id: str,
    service_id: str,
    *,
    hint: str | None = None,
) -> EvidenceBundle:
    from agent.store.chat import get_chat_store

    service, host, _executor, log_tail, service_status, status_detail = await _collect_service_probe(
        service_id
    )
    await get_chat_store().init()
    messages = await get_chat_store().list_active_messages(conversation_id)
    chat_excerpt = _build_chat_excerpt(messages)
    context = _build_chat_diagnosis_context(
        service_id=service.id,
        host_id=host.id,
        status_detail=status_detail,
        log_tail=log_tail,
        chat_excerpt=chat_excerpt,
        hint=hint,
        conversation_id=conversation_id,
    )

    return EvidenceBundle(
        service_id=service.id,
        host_id=host.id,
        incident={
            "conversation_id": conversation_id,
            "hint": hint or "",
            "diagnosis_context": context,
        },
        service_status=service_status,
        deployment_info={
            "registered": service.model_dump(mode="json"),
            "host": {"id": host.id, "name": host.name, "ssh_host": host.ssh.host},
        },
        log_tail=log_tail[:6000] if log_tail else None,
        chat_excerpt=chat_excerpt or None,
    )
