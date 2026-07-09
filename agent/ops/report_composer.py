from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from agent.langchain.llm_factory import get_llm
from agent.ops.models import EvidenceBundle, ProblemCase, ProblemCaseDraft, ProblemCaseSource, ProblemCaseStatus
from agent.ops.case_store import new_case_id
from agent.settings import get_settings

logger = logging.getLogger(__name__)

_PLACEHOLDER_ANALYSIS = "【待核实】需结合日志与服务状态进一步确认根因。"
_DEFAULT_RECOMMENDATIONS = [
    "查看近期 ERROR 日志并确认服务进程/端口状态",
    "必要时在维护窗口重启并观察",
]


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


def _is_placeholder_analysis(text: str) -> bool:
    cleaned = (text or "").strip()
    return not cleaned or cleaned == _PLACEHOLDER_ANALYSIS or cleaned.startswith("【待核实】需结合日志")


def _is_placeholder_draft(draft: ProblemCaseDraft) -> bool:
    if _is_placeholder_analysis(draft.analysis):
        if not draft.recommendations or draft.recommendations == _DEFAULT_RECOMMENDATIONS:
            return True
    return False


def _extract_assistant_sections(chat_excerpt: str) -> list[str]:
    if not chat_excerpt:
        return []
    sections: list[str] = []
    current: list[str] = []
    in_assistant = False
    for line in chat_excerpt.splitlines():
        if line.startswith("[assistant"):
            if in_assistant and current:
                sections.append("\n".join(current).strip())
            in_assistant = True
            idx = line.find("] ")
            current = [line[idx + 2 :].strip()] if idx >= 0 else [line.strip()]
            continue
        if line.startswith("[") and "]" in line:
            if in_assistant and current:
                sections.append("\n".join(current).strip())
            in_assistant = False
            current = []
            continue
        if in_assistant:
            current.append(line)
    if in_assistant and current:
        sections.append("\n".join(current).strip())
    return [section for section in sections if section]


def _extract_recommendations_from_text(text: str) -> list[str]:
    if not text:
        return []
    section = text
    for marker in (
        "建议（按优先级）",
        "建议(按优先级)",
        "处置建议",
        "建议：",
        "建议:",
        "\n建议\n",
    ):
        if marker in text:
            section = text.split(marker, 1)[1]
            break

    recs: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            recs.append(stripped[2:].strip())
            continue
        numbered = re.match(r"^\d+[.)]\s*(.+)$", stripped)
        if numbered:
            recs.append(numbered.group(1).strip())
            continue
        if stripped.startswith("bash") or stripped.startswith("#"):
            continue
        if stripped.startswith("⭐") or stripped.startswith("*"):
            recs.append(stripped.lstrip("⭐* ").strip())

    deduped: list[str] = []
    seen: set[str] = set()
    for item in recs:
        key = item[:120]
        if key and key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped[:20]


def _split_analysis_and_description(text: str) -> tuple[str, str]:
    if not text:
        return "", ""
    for marker in ("## 结论", "## 根因", "根因判断", "详情## 结论"):
        if marker in text:
            parts = text.split(marker, 1)
            return parts[0].strip()[:2000], text.strip()
    if "【已核实】" in text:
        idx = text.find("【已核实】")
        return text[idx : idx + 500].strip(), text.strip()
    return text[:500].strip(), text.strip()


def _draft_from_chat_evidence(
    evidence: EvidenceBundle,
    incident: dict[str, Any],
) -> ProblemCaseDraft | None:
    excerpt = evidence.chat_excerpt or ""
    if not excerpt and incident.get("diagnosis_context"):
        excerpt = str(incident["diagnosis_context"])

    sections = _extract_assistant_sections(excerpt)
    if not sections:
        return None

    analysis_text = "\n\n".join(sections[-3:])
    if len(analysis_text) > 24000:
        analysis_text = analysis_text[-24000:]

    description, full_analysis = _split_analysis_and_description(analysis_text)
    recommendations = _extract_recommendations_from_text(analysis_text)
    if not recommendations:
        recommendations = _extract_recommendations_from_text(full_analysis)

    title = incident.get("title") or f"{evidence.service_id} 异常"
    for line in analysis_text.splitlines():
        if "ERROR" in line or "异常" in line or "激增" in line:
            title = line.strip("# ").strip()[:120] or title
            break

    return ProblemCaseDraft(
        title=title,
        description=description or incident.get("summary") or analysis_text[:800],
        impact=(
            f"服务 `{evidence.service_id}` 在主机 `{evidence.host_id}` 上出现异常，"
            "详细影响见下方根因分析。"
        ),
        analysis=full_analysis or analysis_text,
        recommendations=recommendations or list(_DEFAULT_RECOMMENDATIONS),
    )


def _fallback_draft(evidence: EvidenceBundle, incident: dict[str, Any]) -> ProblemCaseDraft:
    chat_draft = _draft_from_chat_evidence(evidence, incident)
    if chat_draft and not _is_placeholder_draft(chat_draft):
        return chat_draft

    title = incident.get("title") or f"{evidence.service_id} 异常"
    summary = incident.get("summary") or "巡检或告警发现服务异常，需进一步确认。"
    diagnosis = incident.get("diagnosis")
    suggestions = incident.get("suggestions") or []
    analysis = diagnosis or _PLACEHOLDER_ANALYSIS
    if suggestions:
        recs = [str(item) for item in suggestions]
    else:
        recs = list(_DEFAULT_RECOMMENDATIONS)
    return ProblemCaseDraft(
        title=title,
        description=summary,
        impact=f"服务 `{evidence.service_id}` 在主机 `{evidence.host_id}` 上出现异常，可能影响业务可用性。",
        analysis=analysis,
        recommendations=recs,
    )


async def _compose_draft_with_llm(context: str) -> ProblemCaseDraft | None:
    llm = get_llm("diagnosis")
    structured = llm.with_structured_output(ProblemCaseDraft)
    return await structured.ainvoke(
        [
            {
                "role": "system",
                "content": (
                    "你是资深 SRE，请根据证据撰写问题案例草稿。"
                    "不得编造证据中没有的事实；不确定的内容在 analysis 中标注【待核实】。"
                    "若对话中已有详细根因判断与处置建议，必须完整保留到 analysis 与 recommendations，"
                    "不要改写为泛泛而谈的占位语句。"
                    "title 简明；description 描述现象；impact 说明影响；recommendations 给出可执行建议。"
                ),
            },
            {"role": "user", "content": context},
        ]
    )


def _apply_draft_override(
    draft: ProblemCaseDraft,
    override: dict[str, Any] | None,
) -> ProblemCaseDraft:
    if not override:
        return draft
    data = draft.model_dump()
    for key in ("title", "description", "impact", "analysis"):
        value = override.get(key)
        if value is not None and str(value).strip():
            data[key] = str(value).strip()
    recs = override.get("recommendations")
    if recs is not None:
        if isinstance(recs, str):
            data["recommendations"] = _extract_recommendations_from_text(recs) or [recs.strip()]
        else:
            data["recommendations"] = [str(item).strip() for item in recs if str(item).strip()]
    return ProblemCaseDraft.model_validate(data)


def _enrich_draft_from_chat(
    draft: ProblemCaseDraft,
    evidence: EvidenceBundle,
    incident: dict[str, Any],
) -> ProblemCaseDraft:
    chat_draft = _draft_from_chat_evidence(evidence, incident)
    if not chat_draft:
        return draft
    if _is_placeholder_analysis(draft.analysis) and chat_draft.analysis:
        draft = draft.model_copy(update={"analysis": chat_draft.analysis})
    if (
        not draft.recommendations
        or draft.recommendations == _DEFAULT_RECOMMENDATIONS
    ) and chat_draft.recommendations:
        draft = draft.model_copy(update={"recommendations": chat_draft.recommendations})
    if len(draft.description) < 80 and len(chat_draft.description) > len(draft.description):
        draft = draft.model_copy(update={"description": chat_draft.description})
    return draft


def case_needs_content_refresh(case: ProblemCase) -> bool:
    return _is_placeholder_analysis(case.analysis)


def rebuild_case_report_markdown(case: ProblemCase) -> ProblemCase:
    evidence = EvidenceBundle.model_validate(case.evidence or {})
    markdown = render_report_markdown(
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
        incident_id=case.incident_id,
    )
    return case.model_copy(update={"report_markdown": markdown, "updated_at": datetime.utcnow()})


async def compose_problem_case(
    evidence: EvidenceBundle,
    *,
    source: ProblemCaseSource,
    source_ref: str,
    initiator: str | None = None,
    incident_id: str | None = None,
    severity: str | None = None,
    draft_override: dict[str, Any] | None = None,
) -> ProblemCase:
    settings = get_settings()
    initiator = (initiator or settings.config.ops_report.initiator_default).strip() or "运维值班"
    incident = evidence.incident or {}
    sev = severity or str(incident.get("severity") or "P2")

    context = (
        incident.get("diagnosis_context")
        or evidence.chat_excerpt
        or json.dumps(evidence.model_dump(mode="json"), ensure_ascii=False, indent=2)
    )

    draft: ProblemCaseDraft
    try:
        llm_draft = await _compose_draft_with_llm(context)
        draft = llm_draft if llm_draft is not None else _fallback_draft(evidence, incident)
    except Exception as exc:
        logger.warning("LLM 报告撰写失败，回退到对话摘录: %s", exc)
        draft = _fallback_draft(evidence, incident)

    if _is_placeholder_draft(draft):
        draft = _enrich_draft_from_chat(draft, evidence, incident)

    draft = _apply_draft_override(draft, draft_override)

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
