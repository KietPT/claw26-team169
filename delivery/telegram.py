"""Render + send a DigestReport via Telegram (HTML parse mode)."""
from __future__ import annotations
import html as _html
from digest.models import DigestItem, DigestReport
from digest.grouping import (by_section, CATEGORY_EMOJI, SECTIONS, status_label,
                             due_label, stale_label, priority_label, PRIORITY_LABEL)

_SECTION_META = {sec: (label, icon) for sec, label, icon in SECTIONS}

def _esc(s: str) -> str:
    return _html.escape(s)

def _block(it: DigestItem, report_date: str) -> str:
    emoji = CATEGORY_EMOJI[it.category]
    title = f'<a href="{_esc(it.url)}">{_esc(it.title)}</a>'
    status = status_label(it)
    new = "🆕 " if it.is_new else ""
    head = f"{emoji} {new}{title}" + (f" — <i>{_esc(status)}</i>" if status else "")
    lines = [head]

    meta = []
    prio = priority_label(it)
    if prio:
        meta.append(_esc(prio))
    stale = stale_label(it)
    if stale:
        meta.append(_esc(stale))
    due = due_label(it, report_date)
    if due:
        meta.append(f"📅 {_esc(due)}")
    if it.people:
        meta.append(f"👤 {_esc(it.people)}")
    if meta:
        lines.append("  " + " · ".join(meta))

    context = it.context or it.summary
    if context:
        lines.append(f"  📌 <b>Bối cảnh:</b> {_esc(context)}")
    if it.needed:
        lines.append(f"  ❓ <b>Cần gì:</b> {_esc(it.needed)}")
    if it.next_step:
        lines.append(f"  ✅ <b>Làm gì:</b> {_esc(it.next_step)}")
    if it.subtasks:
        lines.append("  📋 <b>Cần làm:</b>")
        lines.extend(f"    • {_esc(s)}" for s in it.subtasks)
    return "\n".join(lines)

# Telegram rejects messages > 4096 chars; chunk well under to stay safe.
TELEGRAM_LIMIT = 4000

def _pack(segments: list[str], limit: int) -> list[str]:
    """Pack segments into messages each ≤ limit, breaking on block boundaries."""
    chunks: list[str] = []
    cur = ""
    for seg in segments:
        piece = (cur + "\n" + seg) if cur else seg
        if len(piece) <= limit:
            cur = piece
            continue
        if cur:
            chunks.append(cur)
        # A single oversized segment (rare): hard-split on its internal newlines.
        while len(seg) > limit:
            cut = seg.rfind("\n", 0, limit)
            cut = cut if cut > 0 else limit
            chunks.append(seg[:cut])
            seg = seg[cut:].lstrip("\n")
        cur = seg
    if cur:
        chunks.append(cur)
    return chunks

def render_messages(report: DigestReport, *, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """The overview (date + headline + ranked priorities) is its own message; each section
    (CS Ticket / Task / Merge Request) is a separate message (chunked if too long);
    resolved + errors (if any) become trailing messages."""
    header = [f"<b>Báo cáo {report.date}</b>"]
    if report.headline:
        header.append(f"🎯 {_esc(report.headline)}")
    if report.summary:
        header.append(_esc(report.summary))
    if report.priorities:
        header.append("🔝 <b>Quan trọng nhất hôm nay:</b>")
        for n, p in enumerate(report.priorities, 1):
            line = f'{n}. <a href="{_esc(p.get("url",""))}">{_esc(p.get("title",""))}</a>'
            badge = PRIORITY_LABEL.get(p.get("priority") or "")
            if badge:
                line += f" [{_esc(badge)}]"
            if p.get("reason"):
                line += f': {_esc(p["reason"])}'
            header.append(line)
    messages: list[str] = _pack(header, limit)   # overview (headline + priorities) as its own message
    for sec, items in by_section(report).items():
        label, icon = _SECTION_META[sec]
        segs = [f"{icon} <b>{_esc(label)}</b>"] + [_block(i, report.date) for i in items]
        messages.extend(_pack(segs, limit))
    if report.resolved:
        segs = ["✅ <b>Đã xong từ lần trước</b>"]
        segs += [f'- <a href="{_esc(r.get("url",""))}">{_esc(r.get("title",""))}</a>'
                 for r in report.resolved]
        messages.extend(_pack(segs, limit))
    if report.errors:
        messages.extend(_pack([f"⚠️ {_esc(e)}" for e in report.errors], limit))
    return messages

class TelegramDelivery:
    def __init__(self, bot, chat_id: str) -> None:
        self._bot = bot; self._chat_id = chat_id
    async def deliver(self, report: DigestReport) -> None:
        for msg in render_messages(report):
            await self._bot.send_message(chat_id=self._chat_id, text=msg,
                                         parse_mode="HTML", disable_web_page_preview=True)
