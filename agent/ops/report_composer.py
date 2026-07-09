from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from agent.langchain.llm_factory import get_llm
from agent.ops.models import EvidenceBundle, ProblemCase, ProblemCaseDraft, ProblemCaseSource, ProblemCaseStatus
from agent.ops.case_store import new_case_id
from agent.settings import get_settings


_SOURCE_LABELS = {
    ProblemCaseSource.INCIDENT: "告警 Incident",
    ProblemCaseSource.INSPECTION: "自动巡检",
    ProblemCaseSource.CHAT: "AI 对话",
    ProblemCaseSource.MANUAL: "手动创建",
}


def _format_evidence_section(evidence: EvidenceBundle) -> str:
    lines: list[str] = []
    incident = evidence.incident or {}
    if incident.get("summary"):
        lines.append(f"- 告警摘要：{incident['summary']}")
    if incident.get("log_snippet"):
        lines.append(f"- 告警日志片段：已记录（见下方日志节）")
    if evidence.service_status:
        status_json = json.dumps(evidence.service_status, ensure_ascii=False, indent=2)
        lines.append(f"- 服务状态探测：\n```json\n{status_json}\n```")
    if evidence.log_tail:
        lines.append(f"- 近期日志：\n```text\n{evidence.log_tail[:4000]}\n```")
    if not lines:
        lines.append("- （暂无额外证据）")
    return "\n".join(lines)


def _format_recommendations(items: list[str]) -> str:
    if not items:
        return "- （待补充）"
    return "\n".join(f"- {item}" for item in items)


def render_report_markdown(
    *,
    title: str,
    description: str,
    impact: str,
    analysis: str,
    recommendations: list[str],
    evidence: EvidenceBundle,
    initiator: str,
    severity: str,
    service_id: str,
    host_id: str,
    source: ProblemCaseSource,
    created_at: datetime,
    incident_id: str | None = None,
) -> str:
    source_label = _SOURCE_LABELS.get(source, source.value)
    incident_line = f"| Incident ID | `{incident_id}` |\n" if incident_id else ""
    return (
        f"# {title}\n\n"
        "| 项目 | 内容 |\n"
        "|------|------|\n"
        f"| 发起人 | {initiator} |\n"
        f"| 服务 | `{service_id}` |\n"
        f"| 主机 | `{host_id}` |\n"
        f"| 严重级别 | {severity} |\n"
        f"| 发现时间 | {created_at.strftime('%Y-%m-%d %H:%M UTC')} |\n"
        f"| 来源 | {source_label} |\n"
        f"{incident_line}"
        "\n"
        "## 1. 问题描述\n\n"
        f"{description}\n\n"
        "## 2. 影响范围\n\n"
        f"{impact}\n\n"
        "## 3. 证据与现象\n\n"
        f"{_format_evidence_section(evidence)}\n\n"
        "## 4. 根因分析\n\n"
        f"{analysis}\n\n"
        "## 5. 处置建议\n\n"
        f"{_format_recommendations(recommendations)}\n\n"
        "## 6. 后续跟踪\n\n"
        "- [ ] 待指派处理人\n"
        "- [ ] 待验证恢复\n"
        "- [ ] 待发布飞书文档与工单（M2/M3）\n"
    )


def _fallback_draft(evidence: EvidenceBundle, incident: dict[str, Any]) -> ProblemCaseDraft:
    title = incident.get("title") or f"{evidence.service_id} 异常"
    summary = incident.get("summary") or "巡检或告警发现服务异常，需进一步确认。"
    diagnosis = incident.get("diagnosis")
    suggestions = incident.get("suggestions") or []
    analysis = diagnosis or "【待核实】需结合日志与服务状态进一步确认根因。"
    if suggestions:
        recs = [str(item) for item in suggestions]
    else:
        recs = ["查看近期 ERROR 日志并确认服务进程/端口状态", "必要时在维护窗口重启并观察"]
    return ProblemCaseDraft(
        title=title,
        description=summary,
        impact=f"服务 `{evidence.service_id}` 在主机 `{evidence.host_id}` 上出现异常，可能影响业务可用性。",
        analysis=analysis,
        recommendations=recs,
    )


async def compose_problem_case(
    evidence: EvidenceBundle,
    *,
    source: ProblemCaseSource,
    source_ref: str,
    initiator: str | None = None,
    incident_id: str | None = None,
    severity: str | None = None,
) -> ProblemCase:
    settings = get_settings()
    initiator = (initiator or settings.config.ops_report.initiator_default).strip() or "运维值班"
    incident = evidence.incident or {}
    sev = severity or str(incident.get("severity") or "P2")

    draft: ProblemCaseDraft
    try:
        context = (
            incident.get("diagnosis_context")
            or evidence.chat_excerpt
            or json.dumps(evidence.model_dump(mode="json"), ensure_ascii=False, indent=2)
        )
        llm = get_llm("diagnosis")
        structured = llm.with_structured_output(ProblemCaseDraft)
        draft = await structured.ainvoke(
            [
                {
                    "role": "system",
                    "content": (
                        "你是资深 SRE，请根据证据撰写问题案例草稿。"
                        "不得编造证据中没有的事实；不确定的内容在 analysis 中标注【待核实】。"
                        "title 简明；description 描述现象；impact 说明影响；recommendations 给出可执行建议。"
                    ),
                },
                {"role": "user", "content": context},
            ]
        )
    except Exception:
        draft = _fallback_draft(evidence, incident)

    now = datetime.utcnow()
    case = ProblemCase(
        id=new_case_id(),
        title=draft.title.strip() or f"{evidence.service_id} 问题报告",
        description=draft.description.strip(),
        severity=sev,
        service_id=evidence.service_id,
        host_id=evidence.host_id,
        initiator=initiator,
        source=source,
        source_ref=source_ref,
        evidence=evidence.model_dump(mode="json"),
        analysis=draft.analysis.strip(),
        impact=draft.impact.strip(),
        recommendations=draft.recommendations,
        status=ProblemCaseStatus.DRAFT,
        incident_id=incident_id,
        created_at=now,
        updated_at=now,
    )
    case.report_markdown = render_report_markdown(
        title=case.title,
        description=case.description,
        impact=case.impact,
        analysis=case.analysis,
        recommendations=case.recommendations,
        evidence=evidence,
        initiator=case.initiator,
        severity=case.severity,
        service_id=case.service_id,
        host_id=case.host_id,
        source=case.source,
        created_at=case.created_at,
        incident_id=incident_id,
    )
    return case


def apply_case_edits(case: ProblemCase, payload: dict[str, Any]) -> ProblemCase:
    updates = case.model_copy()
    for key in (
        "title",
        "description",
        "analysis",
        "impact",
        "report_markdown",
        "initiator",
        "severity",
        "assignee",
        "ticket_status",
        "close_note",
    ):
        if key in payload and payload[key] is not None:
            setattr(updates, key, payload[key])
    if "recommendations" in payload and payload["recommendations"] is not None:
        updates.recommendations = list(payload["recommendations"])
    if payload.get("status"):
        try:
            updates.status = ProblemCaseStatus(str(payload["status"]))
        except ValueError:
            pass
    updates.updated_at = datetime.utcnow()
    return updates
