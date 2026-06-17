"""Render a DigestReport to a self-contained HTML file (spec §9)."""
from __future__ import annotations
import html as _html, os, webbrowser
from digest.models import Category, DigestItem, DigestReport
from digest.grouping import by_section, SECTIONS, status_label, due_label, stale_label, priority_label, PRIORITY_LABEL

_SECTION_LABEL = {sec: f"{icon} {label}" for sec, label, icon in SECTIONS}
_CAT_CLASS = {Category.ACTION: "action", Category.WAITING: "waiting", Category.FYI: "fyi"}

_STYLE = (
    "*{box-sizing:border-box}"
    "body{font-family:-apple-system,\"Segoe UI\",Roboto,\"Helvetica Neue\",Arial,sans-serif;"
    "background:#f4f5f7;color:#1f2430;line-height:1.55;margin:0;padding:24px 16px}"
    ".wrap{max-width:760px;margin:0 auto}"
    ".pagehd{background:linear-gradient(135deg,#1f2430,#3b3f4a);color:#fff;border-radius:14px;"
    "padding:22px 24px;box-shadow:0 6px 20px rgba(31,36,48,.18);margin-bottom:20px}"
    ".pagehd h1{margin:0;font-size:21px;font-weight:700;letter-spacing:.2px;border:0}"
    ".pagehd .date{opacity:.8;font-size:13px;margin-top:4px}"
    "h1{font-size:21px}"
    "h2{font-size:15px;font-weight:700;margin:26px 0 12px;color:#3b3f4a;"
    "text-transform:uppercase;letter-spacing:.4px}"
    ".s{color:#8a8f99;font-size:13px;margin:8px 0}"
    ".hl{background:#fff;padding:14px 16px;border-radius:12px;font-weight:600;font-size:15px;"
    "box-shadow:0 1px 3px rgba(31,36,48,.08);margin:14px 0;border-left:4px solid #3b82f6}"
    ".summary{color:#3b3f4a;font-size:14.5px;margin:12px 2px 4px;line-height:1.6}"
    ".top{background:#fff;border-radius:14px;padding:8px 18px 14px;margin:14px 0 4px;"
    "box-shadow:0 4px 14px rgba(31,36,48,.10);border-top:4px solid #e5484d}"
    ".top .top-h{font-weight:700;font-size:15px;margin:12px 0 6px;color:#1f2430}"
    ".top ol{list-style:none;counter-reset:rk;margin:0;padding:0}"
    ".top ol li{counter-increment:rk;display:flex;align-items:flex-start;gap:10px;"
    "padding:10px 0;border-top:1px solid #f0f1f4}"
    ".top ol li:first-child{border-top:0}"
    ".top ol li::before{content:counter(rk);flex:0 0 24px;width:24px;height:24px;"
    "display:inline-flex;align-items:center;justify-content:center;background:#1f2430;color:#fff;"
    "border-radius:50%;font-size:12px;font-weight:700;margin-top:1px}"
    ".top .rk-body{flex:1}.top .rk-reason{color:#6b7280;font-size:13px;margin-top:2px}"
    ".item{background:#fff;border:1px solid #ebedf0;border-left:4px solid #cbd0d8;border-radius:12px;"
    "padding:14px 16px;margin:12px 0;box-shadow:0 1px 3px rgba(31,36,48,.06)}"
    ".item.action{border-left-color:#e5484d}.item.waiting{border-left-color:#f5a623}"
    ".item.fyi{border-left-color:#3b82f6}"
    ".hd{font-size:15px;font-weight:600;margin-bottom:3px}"
    ".hd a{color:#1f2430;text-decoration:none}.hd a:hover{text-decoration:underline}"
    ".st{color:#8a8f99;font-weight:400;font-size:13px}"
    ".meta{color:#6b7280;font-size:12px;margin:4px 0 8px}"
    ".item p{margin:4px 0;font-size:14px}.item .lbl{color:#3b3f4a}"
    ".item ul{margin:4px 0 4px;padding-left:20px}.item li{font-size:14px;margin:2px 0}"
    ".pill{display:inline-block;font-size:11px;font-weight:700;padding:2px 9px;border-radius:999px;"
    "background:#eef0f3;color:#4b5563;vertical-align:middle}"
    ".pill.hi{background:#fdecec;color:#c0353a}.pill.lo{background:#eef0f3;color:#6b7280}"
    "a{color:#3b82f6}"
    "@media(max-width:600px){body{padding:14px 10px}.pagehd{padding:18px}"
    ".item,.top,.hl{border-radius:10px}}"
)

def _prio_pill(text: str) -> str:
    """Styled badge for a priority_label string (already-escaped-safe label text)."""
    cls = "hi" if ("Highest" in text or "High" in text) else "lo"
    return f'<span class="pill {cls}">{_html.escape(text)}</span>'

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
    prio = priority_label(it)
    if prio:
        meta.append(_prio_pill(prio))
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
             '<div class="wrap">',
             '<div class="pagehd">'
             f"<h1>Báo cáo của {_html.escape(report.user_name)}</h1>"
             f'<div class="date">📅 {report.date}</div></div>']
    if report.headline:
        parts.append(f'<p class="hl">🎯 {_html.escape(report.headline)}</p>')
    if report.summary:
        parts.append(f'<p class="summary">{_html.escape(report.summary)}</p>')
    if report.priorities:
        parts.append('<div class="top"><div class="top-h">🔝 Quan trọng nhất hôm nay</div><ol>')
        for p in report.priorities:
            badge = PRIORITY_LABEL.get(p.get("priority") or "")
            badge_html = f" {_prio_pill(badge)}" if badge else ""
            reason = (f'<div class="rk-reason">{_html.escape(p["reason"])}</div>'
                      if p.get("reason") else "")
            parts.append(f'<li><div class="rk-body">'
                         f'<a href="{_html.escape(p.get("url",""))}">'
                         f'{_html.escape(p.get("title",""))}</a>{badge_html}{reason}</div></li>')
        parts.append("</ol></div>")
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
    parts.append("</div>")
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
