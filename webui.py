"""Local web UI for demos.

One config form with three actions:
  • 💾 Lưu cấu hình (POST /config) — write the values to the .env file (and apply live).
  • ▶️ Run now      (POST /run)     — build one digest now, show the report, push to Telegram.
  • ⏰ Run scheduler (POST /schedule) — start the cron at the configured times/days.

LLM settings (LLM_BASE_URL/KEY/MODEL) and TIMEZONE are NOT editable here — they come
from the environment injected at Docker build/run and are preserved verbatim when the
.env is rewritten. Docker ships only the .env *format* (.env.example); the real .env is
created when you click Lưu cấu hình.

Run:  uvicorn webui:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations
import html as _html, logging, os, time
from datetime import datetime, timezone as _tz
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse
from openai import AsyncOpenAI

from config import AppConfig
from connectors.gitlab import GitLabAdapter
from connectors.jira import JiraAdapter
from delivery.html import HtmlDelivery, render_html
from delivery.telegram import TelegramDelivery
from digest.builder import build_digest
from digest.snapshot import StateStore
from scheduler import AgentScheduler, DigestService

logger = logging.getLogger(__name__)
app = FastAPI(title="Personal Daily Digest")

# Health endpoint for AgentBase Runtime
@app.get("/health")
async def health():
    return {"status": "ok"}

ENV_PATH = os.environ.get("ENV_FILE", ".env")
_scheduler: AgentScheduler | None = None   # one live scheduler per process

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Editable in the form. kind ∈ text|password|select|days|number.
FORM_FIELDS = [
    # GitLab fields are hidden: GitLab is disabled by default (private-network only).
    # To re-enable, set GITLAB_ENABLED=true and GITLAB_BASE_URL/GITLAB_TOKEN in the .env directly.
    # Jira base URL and Fetch window are hidden too (kept simple) — they come from
    # the environment/defaults and are preserved on save via PRESERVED_ENV below.
    # Digest days IS editable (the day-of-week checkboxes), defaulting to mon-fri.
    {"name": "jira_email", "env": "JIRA_EMAIL", "label": "Jira email", "kind": "text", "no_autofill": True, "required": True},
    {"name": "jira_token", "env": "JIRA_TOKEN", "label": "Jira token", "kind": "password", "no_autofill": True, "required": True,
     "help": [
         "<b>Jira Cloud:</b> mở <a href='https://id.atlassian.com/manage-profile/security/api-tokens' target='_blank' rel='noopener'>Atlassian API tokens</a> → bấm <i>Create API token</i> → đặt tên → <i>Copy</i>.",
         "Dán token vào ô này và nhập <b>email</b> Atlassian của bạn ở ô “Jira email” phía trên.",
         "<b>Jira Server/Data Center:</b> mở Jira → bấm ảnh đại diện → <i>Profile</i> → <i>Personal Access Tokens</i> → <i>Create token</i> → copy. Để <b>trống</b> ô email.",
     ]},
    {"name": "telegram_bot_token", "env": "TELEGRAM_BOT_TOKEN", "label": "Telegram bot token", "kind": "password", "no_autofill": True, "required": True,
     "help": [
         "Mở Telegram, tìm tài khoản <b>@BotFather</b> (có tích xanh chính chủ).",
         "Gửi lệnh <code>/newbot</code> → đặt <i>tên hiển thị</i> → đặt <i>username</i> phải kết thúc bằng <code>bot</code> (ví dụ <code>dev_digest_bot</code>).",
         "BotFather trả về token dạng <code>123456789:ABCdefGhi...</code> → copy và dán vào ô này.",
     ]},
    {"name": "telegram_chat_id", "env": "TELEGRAM_CHAT_ID", "label": "Telegram chat id", "kind": "text", "no_autofill": True, "required": True,
     "help": [
         "Nhắn cho bot vừa tạo ít nhất 1 tin (chat riêng), <b>hoặc</b> add bot vào nhóm rồi gửi 1 tin trong nhóm.",
         "Mở trình duyệt vào: <code>https://api.telegram.org/bot&lt;TOKEN&gt;/getUpdates</code> — thay <code>&lt;TOKEN&gt;</code> bằng bot token ở trên.",
         "Tìm đoạn <code>\"chat\":{\"id\":...}</code> — con số đó là chat id (nhóm thường là số âm, ví dụ <code>-1001234567890</code>).",
         "Cách nhanh cho chat cá nhân: nhắn cho <b>@userinfobot</b>, nó trả về id của bạn ngay.",
     ]},
    {"name": "delivery_channel", "env": "DELIVERY_CHANNEL", "label": "Delivery", "kind": "select",
     "options": ["telegram", "html", "both"]},
    {"name": "digest_times", "env": "DIGEST_TIMES", "label": "Digest times (CSV HH:MM)", "kind": "text"},
    {"name": "digest_days", "env": "DIGEST_DAYS", "label": "Digest days (ngày chạy)", "kind": "days"},
]
# Kept from the injected environment (not user-editable) so a save never wipes them.
# GitLab keys are preserved too — hidden from the form, but a form save must not drop them.
# JIRA_BASE_URL / FETCH_WINDOW_DAYS / STALE_AFTER_DAYS are hidden but preserved here.
PRESERVED_ENV = ["LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "TIMEZONE",
                 "GITLAB_ENABLED", "GITLAB_BASE_URL", "GITLAB_TOKEN",
                 "JIRA_BASE_URL", "FETCH_WINDOW_DAYS", "STALE_AFTER_DAYS"]


def _parse_days(expr: str | None) -> set[str]:
    """'mon-fri' / 'mon,wed,fri' → set of day tokens. Empty → all days."""
    expr = (expr or "").strip().lower()
    if not expr:
        return set(DAYS)
    out: set[str] = set()
    for part in expr.split(","):
        part = part.strip()
        if "-" in part:
            a, b = (p.strip() for p in part.split("-", 1))
            if a in DAYS and b in DAYS:
                out.update(DAYS[DAYS.index(a):DAYS.index(b) + 1])
        elif part in DAYS:
            out.add(part)
    return out or set(DAYS)


def _defaults() -> dict:
    """Form defaults from the current environment (read fresh each render)."""
    c = AppConfig()
    return {
        "gitlab_base_url": c.gitlab_base_url, "gitlab_token": c.gitlab_token,
        "jira_base_url": c.jira_base_url, "jira_email": c.jira_email, "jira_token": c.jira_token,
        "telegram_bot_token": c.telegram_bot_token, "telegram_chat_id": c.telegram_chat_id,
        "delivery_channel": c.delivery_channel,
        "digest_times": c.digest_times or c.default_digest_time, "digest_days": c.digest_days,
        "stale_after_days": str(c.stale_after_days), "fetch_window_days": str(c.fetch_window_days),
    }


def _read_form(form) -> dict:
    """Normalize submitted FormData → plain dict; join multi-checked days into a CSV expr."""
    d = dict(form)
    days = form.getlist("digest_days")
    if days:
        d["digest_days"] = ",".join(x for x in DAYS if x in days)   # canonical order
    return d


def _merge(form: dict) -> dict:
    """Effective values = env defaults overridden by non-empty submitted fields."""
    eff = _defaults()
    for f in FORM_FIELDS:
        v = form.get(f["name"])
        if v not in (None, ""):
            eff[f["name"]] = v
    return eff


def _apply_live(values: dict) -> None:
    """Push form values into os.environ so the next AppConfig() reads them this process."""
    for f in FORM_FIELDS:
        os.environ[f["env"]] = str(values.get(f["name"], "") or "")


def _save_env(values: dict, path: str = ENV_PATH) -> None:
    """Write the .env file: form fields from `values`, LLM/TIMEZONE preserved from env."""
    lines = [f"{f['env']}={values.get(f['name'], '') or ''}" for f in FORM_FIELDS]
    lines += [f"{env}={os.environ.get(env, '')}" for env in PRESERVED_ENV]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    _apply_live(values)


_FORM_STYLE = (
    "*{box-sizing:border-box}"
    "body{font-family:-apple-system,\"Segoe UI\",Roboto,\"Helvetica Neue\",Arial,sans-serif;"
    "background:#f4f5f7;color:#1f2430;line-height:1.55;margin:0;padding:24px 16px}"
    ".wrap{max-width:760px;margin:0 auto}"
    ".pagehd{background:linear-gradient(135deg,#1f2430,#3b3f4a);color:#fff;border-radius:14px;"
    "padding:22px 24px;box-shadow:0 6px 20px rgba(31,36,48,.18);margin-bottom:20px}"
    ".pagehd h1{margin:0;font-size:21px;font-weight:700;letter-spacing:.2px}"
    ".card{background:#fff;border:1px solid #ebedf0;border-radius:14px;padding:20px 22px;"
    "box-shadow:0 4px 14px rgba(31,36,48,.08)}"
    ".hl{background:#eff6ff;border-left:4px solid #3b82f6;color:#1e3a8a;padding:12px 14px;"
    "border-radius:10px;white-space:pre-wrap;font-weight:600;margin-bottom:18px}"
    ".fld{margin:0 0 16px}"
    ".fld>.lbl{display:block;font-size:13px;font-weight:600;color:#3b3f4a;margin-bottom:6px}"
    "input[type=text],input[type=password],input[type=number],select{width:100%;padding:9px 11px;"
    "font-size:14px;border:1px solid #d6d9df;border-radius:9px;background:#fff;color:#1f2430;"
    "transition:border-color .15s,box-shadow .15s}"
    "input:focus,select:focus{outline:none;border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.2)}"
    "small.note{display:block;color:#8a8f99;font-size:12px;margin-top:5px}"
    ".lbl .req{color:#e5484d;font-size:11.5px;font-weight:600}"
    "button:disabled{opacity:.45;cursor:not-allowed;filter:none}"
    "details.help{margin-top:6px}"
    "details.help>summary{display:inline-flex;align-items:center;gap:5px;cursor:pointer;"
    "font-size:12px;font-weight:600;color:#2563eb;list-style:none;user-select:none}"
    "details.help>summary::-webkit-details-marker{display:none}"
    "details.help[open]>summary{margin-bottom:6px}"
    "details.help ol{margin:0;padding:10px 12px 10px 26px;background:#f8fafc;"
    "border:1px solid #e6e9ef;border-radius:9px;font-size:12.5px;color:#3b3f4a}"
    "details.help li{margin:4px 0}"
    "details.help code{background:#eef1f5;padding:1px 5px;border-radius:5px;"
    "font-size:11.5px;word-break:break-all}"
    "details.help a{color:#2563eb}"
    ".days{display:flex;flex-wrap:wrap;gap:8px}"
    ".days label{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border:1px solid #d6d9df;"
    "border-radius:999px;font-size:13px;cursor:pointer;background:#fff;text-transform:uppercase;"
    "letter-spacing:.3px;color:#4b5563}"
    ".days input{accent-color:#3b82f6}"
    ".actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:22px;padding-top:18px;"
    "border-top:1px solid #f0f1f4}"
    "button{padding:11px 18px;font-size:14px;font-weight:600;border-radius:10px;cursor:pointer;"
    "border:1px solid #d6d9df;background:#fff;color:#3b3f4a;transition:filter .15s,background .15s}"
    "button:hover{filter:brightness(.97)}"
    "button.primary{background:#e5484d;border-color:#e5484d;color:#fff}"
    "button.primary:hover{background:#d23b40}"
    "@media(max-width:600px){body{padding:14px 10px}.pagehd{padding:18px}.card{padding:16px}"
    ".actions button{flex:1 1 100%}}"
)

# Disables the action buttons whenever any required field is empty, so the user cannot save /
# run / schedule without filling Jira + Telegram credentials. Pure vanilla JS, no dependencies.
_GUARD_SCRIPT = (
    "<script>(function(){"
    "var f=document.querySelector('form');if(!f)return;"
    "var req=[].slice.call(f.querySelectorAll('[data-req]'));"
    "var btns=[].slice.call(f.querySelectorAll('button'));"
    "function sync(){var ok=req.every(function(i){return i.value.trim()!=='';});"
    "btns.forEach(function(b){b.disabled=!ok;});}"
    "req.forEach(function(i){i.addEventListener('input',sync);});"
    "sync();})();</script>"
)


def _widget(f: dict, values: dict) -> str:
    name, kind = f["name"], f["kind"]
    val = str(values.get(name, "") or "")
    if f.get("no_autofill"):
        # Sensitive field: never echo the stored value back into the HTML. The box renders
        # empty; leaving it blank on save keeps the existing .env value (see _merge).
        val = ""
    if kind == "select":
        opts = "".join(f'<option value="{o}"{" selected" if o == val else ""}>{o}</option>'
                       for o in f["options"])
        return f'<select name="{name}">{opts}</select>'
    if kind == "days":
        checked = _parse_days(val)
        boxes = "".join(
            f'<label><input type="checkbox" name="digest_days" '
            f'value="{d}"{" checked" if d in checked else ""}> {d}</label>' for d in DAYS)
        return f'<div class="days">{boxes}</div>'
    typ = "number" if kind == "number" else ("password" if kind == "password" else "text")
    # Required fields carry data-req so the inline guard can disable the action buttons while
    # any of them is empty; `required` is an HTML5 backstop if scripting is unavailable.
    attrs = ' placeholder="Bắt buộc nhập" data-req required' if f.get("required") else ""
    inp = f'<input name="{name}" type="{typ}" value="{_html.escape(val)}"{attrs}>'
    if f.get("note"):
        inp += f'<small class="note">{_html.escape(f["note"])}</small>'
    return inp


def _help_block(f: dict) -> str:
    """Optional click-to-expand step-by-step guidance for a field (pure HTML <details>, no JS).
    Steps are trusted constants authored above, so they may carry links/markup verbatim."""
    steps = f.get("help")
    if not steps:
        return ""
    items = "".join(f"<li>{s}</li>" for s in steps)
    return ('<details class="help"><summary>❔ Hướng dẫn lấy thông tin</summary>'
            f'<ol>{items}</ol></details>')


def _label(f: dict) -> str:
    lbl = _html.escape(f["label"])
    if f.get("required"):
        lbl += '<span class="req"> *bắt buộc</span>'
    return lbl


def render_form(values: dict, *, message: str = "") -> str:
    rows = "".join(
        f'<div class="fld"><label><span class="lbl">{_label(f)}</span>'
        f'{_widget(f, values)}</label>{_help_block(f)}</div>' for f in FORM_FIELDS)
    msg = f'<p class="hl">{_html.escape(message)}</p>' if message else ""
    return ('<!doctype html><meta charset="utf-8"><title>Personal Daily Digest</title>'
            f"<style>{_FORM_STYLE}</style>"
            '<div class="wrap">'
            '<div class="pagehd"><h1>⚙️ Cấu hình &amp; chạy digest</h1></div>'
            f'{msg}<div class="card"><form method="post" action="/run">{rows}'
            '<div class="actions">'
            '<button type="submit" formaction="/config">💾 Lưu cấu hình</button>'
            '<button type="submit" class="primary" formaction="/run">▶️ Run now</button>'
            '<button type="submit" formaction="/schedule">⏰ Run scheduler</button>'
            "</div></form></div>"
            f"{_GUARD_SCRIPT}</div>")


async def _run_once():
    """Build adapters/LLM from the live config and produce one DigestReport."""
    c = AppConfig()
    w = c.fetch_window_days
    # Diagnostic: presence/length of the Jira config at run time (values never logged).
    # If jira_token_present is False here, the adapter below is None and Jira is silently skipped.
    logger.info("UI _run_once: jira_base_present=%s jira_token_present=%s jira_token_len=%d "
                "gitlab_enabled=%s",
                bool(c.jira_base_url), bool((c.jira_token or '').strip()),
                len(c.jira_token or ''), c.gitlab_enabled)
    gl = (GitLabAdapter(c.gitlab_base_url, c.gitlab_token, window_days=w)
          if c.gitlab_enabled and c.gitlab_base_url and c.gitlab_token else None)
    jr = (JiraAdapter(c.jira_base_url, c.jira_token, email=c.jira_email, window_days=w)
          if c.jira_base_url and c.jira_token else None)
    if jr is None:
        logger.warning("Jira adapter NOT created (base or token empty) — digest will have no "
                       "Jira items. base_present=%s token_present=%s",
                       bool(c.jira_base_url), bool((c.jira_token or '').strip()))
    llm = AsyncOpenAI(base_url=c.llm_base_url or None, api_key=c.llm_api_key or "not-needed")
    report = await build_digest(gitlab=gl, jira=jr, user_name="me", llm=llm, model=c.llm_model,
                                now=datetime.now(_tz.utc), stale_after=c.stale_after_days,
                                store=StateStore(c.report_dir))
    return c, report


def _telegram_plan(c: AppConfig) -> tuple[bool, str]:
    """Synchronous precheck (no network): (should_send, banner_note). Decides whether a
    Telegram push is wanted/configured WITHOUT doing the slow send — so the /run response
    can return immediately and the actual send runs in the background."""
    chan = (c.delivery_channel or "telegram").lower()
    if chan not in ("telegram", "both"):
        return False, ""
    if not (c.telegram_bot_token and c.telegram_chat_id):
        return False, "⚠️ Bỏ qua Telegram: thiếu bot token / chat id."
    return True, "📨 Đang gửi Telegram ở chế độ nền…"


async def _send_telegram(report, c: AppConfig) -> None:
    """Actual Telegram push. Runs as a background task (never on the HTTP response path),
    so a slow/sequential send no longer delays the rendered report."""
    try:
        from telegram import Bot
        async with Bot(c.telegram_bot_token) as bot:
            await TelegramDelivery(bot, c.telegram_chat_id).deliver(report)
    except Exception as exc:  # never let a delivery error escape the background task
        logger.warning("Telegram delivery failed: %s", exc)


class _SchedDelivery:
    """Delivery used by scheduled runs: write HTML and/or push Telegram (bot per run)."""
    def __init__(self, cfg: AppConfig, owner) -> None:
        self._cfg = cfg; self._owner = owner

    async def deliver(self, report) -> None:
        chan = (self._owner.delivery_channel or "telegram").lower()
        if chan in ("html", "both"):
            await HtmlDelivery(self._cfg.report_dir, self._owner.chat_id or "web",
                               open_browser=False).deliver(report)
        if chan in ("telegram", "both") and self._cfg.telegram_bot_token and self._owner.chat_id:
            from telegram import Bot
            async with Bot(self._cfg.telegram_bot_token) as bot:
                await TelegramDelivery(bot, self._owner.chat_id).deliver(report)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return render_form(_defaults())


@app.post("/config", response_class=HTMLResponse)
async def config(request: Request) -> str:
    values = _merge(_read_form(await request.form()))
    _save_env(values)
    return render_form(values, message=f"💾 Đã lưu cấu hình vào {ENV_PATH}.")


@app.post("/run", response_class=HTMLResponse)
async def run(request: Request, background_tasks: BackgroundTasks) -> str:
    _apply_live(_merge(_read_form(await request.form())))
    _t = time.perf_counter()
    c, report = await _run_once()
    build_ms = (time.perf_counter() - _t) * 1000
    should_send, note = _telegram_plan(c)
    if should_send:
        background_tasks.add_task(_send_telegram, report, c)  # off the response path
    banner = ('<div style="font-family:-apple-system,\'Segoe UI\',Roboto,Arial,sans-serif;'
              'max-width:760px;margin:16px auto 0;padding:0 16px">'
              '<a href="/" style="display:inline-block;padding:8px 14px;background:#fff;'
              'border:1px solid #d6d9df;border-radius:10px;text-decoration:none;color:#3b3f4a;'
              'font-weight:600;font-size:14px">⬅️ Cấu hình lại</a>'
              + (f' &nbsp; <b>{_html.escape(note)}</b>' if note else "") + "</div>")
    _t = time.perf_counter()
    html = banner + render_html(report)
    logger.info("/run timing: build=%.0fms render=%.1fms total=%.0fms",
                build_ms, (time.perf_counter() - _t) * 1000, build_ms + (time.perf_counter() - _t) * 1000)
    return html


@app.post("/schedule", response_class=HTMLResponse)
async def schedule(request: Request) -> str:
    global _scheduler
    values = _merge(_read_form(await request.form()))
    _apply_live(values)
    c = AppConfig()
    owner = c.owner()
    llm = AsyncOpenAI(base_url=c.llm_base_url or None, api_key=c.llm_api_key or "not-needed")
    service = DigestService(
        llm=llm, model=c.llm_model, gitlab_base=c.gitlab_base_url, jira_base=c.jira_base_url,
        report_dir=c.report_dir,
        make_gitlab=lambda u, t: GitLabAdapter(u, t, window_days=c.fetch_window_days),
        make_jira=lambda u, t, email: JiraAdapter(u, t, email=email, window_days=c.fetch_window_days),
        make_delivery=lambda o: _SchedDelivery(c, o), stale_after=c.stale_after_days,
        gitlab_enabled=c.gitlab_enabled)
    if _scheduler is not None:
        _scheduler.shutdown()
    _scheduler = AgentScheduler(owner=owner, service=service, timezone=c.timezone)
    _scheduler.schedule_owner()
    _scheduler.start()
    runs = "\n".join(_scheduler.next_runs()) or "(không có job)"
    return render_form(values, message=f"⏰ Scheduler đang chạy. Lịch kế tiếp:\n{runs}")
