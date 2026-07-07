from __future__ import annotations

import re

_BLOCK_PLACEHOLDER = "\x00BLOCK{}\x00"


def escape_html(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_inline_markdown(text: str) -> str:
    result = text
    result = re.sub(r"`([^`\n]+)`", r'<code style="background:#F5F5F5;padding:1px 5px;border-radius:3px;font-family:Consolas,monospace;font-size:12px;color:#D4380D;">\1</code>', result)
    result = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", result)
    result = re.sub(r"\*([^*\n]+)\*", r"<em>\1</em>", result)
    return result


def _parse_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _build_table(headers: list[str], rows: list[list[str]]) -> str:
    th = "".join(
        f'<th style="border:1px solid #E8ECF0;padding:6px 8px;background:#FAFAFA;font-weight:600;text-align:left;">'
        f"{render_inline_markdown(header)}</th>"
        for header in headers
    )
    trs = []
    for row in rows:
        cells = "".join(
            f'<td style="border:1px solid #E8ECF0;padding:6px 8px;">{render_inline_markdown(cell)}</td>'
            for cell in row
        )
        trs.append(f"<tr>{cells}</tr>")
    return (
        '<table cellspacing="0" cellpadding="0" '
        'style="border-collapse:collapse;margin:8px 0;width:100%;font-size:13px;">'
        f"<thead><tr>{th}</tr></thead><tbody>{''.join(trs)}</tbody></table>"
    )


def render_markdown(text: str) -> str:
    src = str(text or "")
    placeholders: list[str] = []
    processed = escape_html(src)

    def _code_block(match: re.Match[str]) -> str:
        lang = match.group(1) or ""
        code = match.group(2).strip()
        lang_label = (
            f'<div style="background:#F0F0F0;color:#8C8C8C;font-size:11px;padding:4px 8px;">{lang}</div>'
            if lang
            else ""
        )
        block_id = len(placeholders)
        placeholders.append(
            f'<div style="margin:8px 0;border:1px solid #E8ECF0;border-radius:6px;overflow:hidden;background:#FAFAFA;">'
            f"{lang_label}"
            f'<pre style="margin:0;padding:8px 10px;font-family:Consolas,monospace;font-size:12px;'
            f'white-space:pre-wrap;color:#262626;"><code>{code}</code></pre></div>'
        )
        return _BLOCK_PLACEHOLDER.format(block_id)

    processed = re.sub(r"```(\w*)\r?\n?([\s\S]*?)```", _code_block, processed)

    lines = processed.splitlines()
    blocks: list[str] = []
    para_buf: list[str] = []
    list_buf: str | None = None
    list_items: list[str] = []

    def flush_paragraph() -> None:
        nonlocal para_buf
        if not para_buf:
            return
        body = render_inline_markdown("<br>".join(para_buf))
        blocks.append(f'<p style="margin:0 0 8px;line-height:1.6;">{body}</p>')
        para_buf = []

    def flush_list() -> None:
        nonlocal list_buf, list_items
        if not list_items:
            return
        tag = list_buf or "ul"
        items = "".join(
            f'<li style="margin:2px 0;">{render_inline_markdown(item)}</li>' for item in list_items
        )
        blocks.append(
            f'<{tag} style="margin:6px 0 10px;padding-left:20px;">{items}</{tag}>'
        )
        list_items = []
        list_buf = None

    def is_table_row(line: str) -> bool:
        return bool(re.match(r"^\s*\|.+\|\s*$", line))

    def is_table_sep(line: str) -> bool:
        return bool(re.match(r"^\s*\|?[\s|:-]+\|[\s|:-]+\|?\s*$", line))

    i = 0
    while i < len(lines):
        line = lines[i]
        trimmed = line.strip()

        if not trimmed:
            flush_list()
            flush_paragraph()
            i += 1
            continue

        if is_table_row(line) and i + 1 < len(lines) and is_table_sep(lines[i + 1]):
            flush_list()
            flush_paragraph()
            headers = _parse_table_row(line)
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and is_table_row(lines[i]):
                rows.append(_parse_table_row(lines[i]))
                i += 1
            blocks.append(_build_table(headers, rows))
            continue

        if match := re.match(r"^###\s+(.+)$", trimmed):
            flush_list()
            flush_paragraph()
            blocks.append(
                f'<h3 style="font-size:14px;font-weight:600;margin:12px 0 6px;color:#262626;">'
                f"{render_inline_markdown(match.group(1))}</h3>"
            )
            i += 1
            continue

        if match := re.match(r"^##\s+(.+)$", trimmed):
            flush_list()
            flush_paragraph()
            blocks.append(
                f'<h2 style="font-size:15px;font-weight:700;margin:14px 0 8px;padding-bottom:6px;'
                f'border-bottom:1px solid #E8ECF0;color:#262626;">'
                f"{render_inline_markdown(match.group(1))}</h2>"
            )
            i += 1
            continue

        if match := re.match(r"^#\s+(.+)$", trimmed):
            flush_list()
            flush_paragraph()
            blocks.append(
                f'<h2 style="font-size:16px;font-weight:700;margin:14px 0 8px;color:#262626;">'
                f"{render_inline_markdown(match.group(1))}</h2>"
            )
            i += 1
            continue

        if match := re.match(r"^>\s?(.*)$", trimmed):
            flush_list()
            flush_paragraph()
            blocks.append(
                f'<blockquote style="margin:8px 0;padding:8px 12px;border-left:3px solid #1890FF;'
                f'background:#F0F7FF;color:#595959;">{render_inline_markdown(match.group(1))}</blockquote>'
            )
            i += 1
            continue

        if re.match(r"^---+$", trimmed) or re.match(r"^\*\*\*+$", trimmed):
            flush_list()
            flush_paragraph()
            blocks.append('<hr style="border:none;border-top:1px solid #E8ECF0;margin:12px 0;">')
            i += 1
            continue

        if match := re.match(r"^[-*]\s+(.+)$", trimmed):
            flush_paragraph()
            if list_buf and list_buf != "ul":
                flush_list()
            list_buf = "ul"
            list_items.append(match.group(1))
            i += 1
            continue

        if match := re.match(r"^\d+\.\s+(.+)$", trimmed):
            flush_paragraph()
            if list_buf and list_buf != "ol":
                flush_list()
            list_buf = "ol"
            list_items.append(match.group(1))
            i += 1
            continue

        flush_list()
        para_buf.append(line)
        i += 1

    flush_list()
    flush_paragraph()

    body = "".join(blocks)
    for index, placeholder in enumerate(placeholders):
        body = body.replace(_BLOCK_PLACEHOLDER.format(index), placeholder)
    return body or '<p style="color:#8C8C8C;">（空回复）</p>'


def format_user_message(text: str) -> str:
    escaped = escape_html(text).replace("\n", "<br>")
    return (
        '<table width="100%" cellspacing="0" cellpadding="0" style="margin:10px 0;">'
        "<tr><td align=\"right\">"
        '<table cellspacing="0" cellpadding="0"><tr>'
        '<td style="background-color:#E6F4FF;border:1px solid #91CAFF;border-radius:10px;'
        'border-top-right-radius:2px;padding:8px 14px;color:#262626;font-size:13px;'
        'line-height:1.6;max-width:520px;">'
        f"{escaped}</td></tr></table></td></tr></table>"
    )


def format_assistant_status(status: str) -> str:
    escaped = escape_html(status)
    return (
        '<table width="100%" cellspacing="0" cellpadding="0" style="margin:10px 0;">'
        "<tr>"
        '<td width="34" valign="top" style="padding-top:6px;">'
        '<span style="display:inline-block;background:#F6FFED;color:#389E0D;font-size:11px;'
        'font-weight:700;padding:2px 7px;border-radius:10px;">AI</span>'
        "</td>"
        '<td valign="top">'
        '<table cellspacing="0" cellpadding="0" width="100%"><tr>'
        '<td style="background-color:#FFFFFF;border:1px solid #E8ECF0;border-radius:10px;'
        'border-top-left-radius:2px;padding:10px 14px;color:#8C8C8C;font-size:13px;'
        'line-height:1.6;">'
        f'<span style="font-style:italic;">{escaped}</span>'
        "</td></tr></table></td></tr></table>"
    )


def format_assistant_streaming(text: str, status: str | None = None) -> str:
    body = render_markdown(text) if text else ""
    status_html = ""
    if status:
        escaped = escape_html(status)
        status_html = (
            '<div style="margin-top:8px;padding-top:8px;border-top:1px solid #F0F0F0;'
            'color:#8C8C8C;font-size:12px;font-style:italic;">'
            f"{escaped}</div>"
        )
    if not body and not status_html:
        body = '<span style="color:#8C8C8C;font-style:italic;">正在思考…</span>'
    return (
        '<table width="100%" cellspacing="0" cellpadding="0" style="margin:10px 0;">'
        "<tr>"
        '<td width="34" valign="top" style="padding-top:6px;">'
        '<span style="display:inline-block;background:#F6FFED;color:#389E0D;font-size:11px;'
        'font-weight:700;padding:2px 7px;border-radius:10px;">AI</span>'
        "</td>"
        '<td valign="top">'
        '<table cellspacing="0" cellpadding="0" width="100%"><tr>'
        '<td style="background-color:#FFFFFF;border:1px solid #E8ECF0;border-radius:10px;'
        'border-top-left-radius:2px;padding:10px 14px;color:#262626;font-size:13px;'
        'line-height:1.6;">'
        f"{body}{status_html}"
        "</td></tr></table></td></tr></table>"
    )


def format_assistant_message(text: str) -> str:
    body = render_markdown(text)
    return (
        '<table width="100%" cellspacing="0" cellpadding="0" style="margin:10px 0;">'
        "<tr>"
        '<td width="34" valign="top" style="padding-top:6px;">'
        '<span style="display:inline-block;background:#F6FFED;color:#389E0D;font-size:11px;'
        'font-weight:700;padding:2px 7px;border-radius:10px;">AI</span>'
        "</td>"
        '<td valign="top">'
        '<table cellspacing="0" cellpadding="0" width="100%"><tr>'
        '<td style="background-color:#FFFFFF;border:1px solid #E8ECF0;border-radius:10px;'
        'border-top-left-radius:2px;padding:10px 14px;color:#262626;font-size:13px;'
        'line-height:1.6;">'
        f"{body}</td></tr></table></td></tr></table>"
    )


def format_system_message(text: str) -> str:
    escaped = escape_html(text)
    return (
        '<p align="center" style="color:#8C8C8C;font-size:12px;margin:8px 0;font-style:italic;">'
        f"{escaped}</p>"
    )
