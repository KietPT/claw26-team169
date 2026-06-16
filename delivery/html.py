"""Render a DigestReport to a self-contained HTML file (spec §9)."""
from __future__ import annotations
import html as _html, os, webbrowser
from digest.models import Category, DigestItem, DigestReport
from digest.grouping import by_section, SECTIONS, status_label, due_label, stale_label

_SECTION_LABEL = {sec: f"{icon} {label}" for sec, label, icon in SECTIONS}
_CAT_CLASS = {Category.ACTION: "action", Category.WAITING: "waiting", Category.FYI: "fyi"}

_STYLE = (
    "body{font-family:sans-serif;max-width:720px;margin:24px auto;color:#1a1a1a;line-height:1.5}"
    "h1{font-size:20px}h2{font-size:16px;margin-top:24px;border-bottom:1px solid #eee;padding-bottom:4px}"
    ".s{color:#777;font-size:13px}"
    ".hl{background:#eef;padding:10px 12px;border-radius:6px;font-weight:600}"
    ".item{border:1px solid #e3e3e3;border-left:4px solid #ccc;border-radius:8px;"
    "padding:10px 14px;margin:10px 0}"
    ".item.action{border-left-color:#e53935}.item.waiting{border-left-color:#fbc02d}"
    ".item.fyi{border-left-color:#1e88e5}"
    ".hd{font-size:15px;font-weight:600;margin-bottom:2px}"
    ".st{color:#666;font-weight:400;font-size:13px}"
    ".meta{color:#666;font-size:12px;margin:2px 0 6px}"
    ".item p{margin:3px 0;font-size:14px}.item .lbl{color:#444}"
)

def _field(label: str, value: str | None) -> str:
    if not value:
        return ""
    return f'<p><span class="lbl"><b>{label}:</b></span> {_html.escape(value)}</p>'

def _card_html(it: DigestItem, report_date: str) -> str:
    cls = _CAT_CLASS[it.category]
    new = "🆕 " if it.is_new else ""
    link = f'{new}<a href="{_html.escape(it.url)}">{_html.escape(it.title)}</a>'
    status = status_label(it)
    st = f' <span class="st">— {_html.escape(status)}</span>' if status else ""
    meta = []
    stale = stale_label(it)
    if stale:
        meta.append(_html.escape(stale))
    due = due_label(it, report_date)
    if due:
        meta.append(f"📅 {_html.escape(due)}")
    if it.people:
        meta.append(f"👤 {_html.escape(it.people)}")
    meta_html = f'<div class="meta">{" · ".join(meta)}</div>' if meta else ""
    body = (_field("Bối cảnh", it.context or it.summary)
            + _field("Cần gì", it.needed)
            + _field("Làm gì", it.next_step))
    if it.subtasks:
        subs = "".join(f"<li>{_html.escape(s)}</li>" for s in it.subtasks)
        body += f'<p><span class="lbl"><b>Cần làm:</b></span></p><ul>{subs}</ul>'
    return (f'<div class="item {cls}"><div class="hd">{link}{st}</div>'
            f"{meta_html}{body}</div>")

def render_html(report: DigestReport) -> str:
    parts = ['<!doctype html><meta charset="utf-8">',
             f"<style>{_STYLE}</style>",
             f"<h1>Báo cáo của {_html.escape(report.user_name)} — {report.date}</h1>"]
    if report.headline:
        parts.append(f'<p class="hl">🎯 {_html.escape(report.headline)}</p>')
    if report.summary:
        parts.append(f"<p>{_html.escape(report.summary)}</p>")
    if report.focus:
        parts.append('<p class="hl">⏰ <b>Ưu tiên kế tiếp (tránh trễ)</b></p><ul>')
        for f in report.focus:
            reason = f' — {_html.escape(f["reason"])}' if f.get("reason") else ""
            parts.append(f'<li><a href="{_html.escape(f.get("url",""))}">'
                         f'{_html.escape(f.get("title",""))}</a>{reason}</li>')
        parts.append("</ul>")
    for sec, items in by_section(report).items():
        parts.append(f"<h2>{_html.escape(_SECTION_LABEL[sec])}</h2>")
        parts.extend(_card_html(i, report.date) for i in items)
    if report.resolved:
        parts.append("<h2>✅ Đã xong từ lần trước</h2><ul>")
        parts.extend(f'<li><a href="{_html.escape(r.get("url",""))}">'
                     f'{_html.escape(r.get("title",""))}</a></li>' for r in report.resolved)
        parts.append("</ul>")
    for e in report.errors:
        parts.append(f'<p class="s">⚠️ {_html.escape(e)}</p>')
    return "".join(parts)

class HtmlDelivery:
    def __init__(self, report_dir: str, user_key: str, *, open_browser: bool = False) -> None:
        self._dir = report_dir; self._key = user_key; self._open = open_browser
    async def deliver(self, report: DigestReport) -> None:
        os.makedirs(self._dir, exist_ok=True)
        path = os.path.join(self._dir, f"{self._key}-{report.date}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(render_html(report))
        print(f"HTML digest: {path}")
        if self._open:
            webbrowser.open(f"file://{os.path.abspath(path)}")
