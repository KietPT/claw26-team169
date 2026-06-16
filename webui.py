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
import html as _html, os
from datetime import datetime, timezone as _tz
from fastapi import FastAPI, Request
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

app = FastAPI(title="Dev Daily Digest")

# Health endpoint for AgentBase Runtime
@app.get("/health")
async def health():
    return {"status": "ok"}

ENV_PATH = os.environ.get("ENV_FILE", ".env")
_scheduler: AgentScheduler | None = None   # one live scheduler per process

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Editable in the form. kind ∈ text|password|select|days|number.
FORM_FIELDS = [
    {"name": "gitlab_base_url", "env": "GITLAB_BASE_URL", "label": "GitLab base URL", "kind": "text"},
    {"name": "gitlab_token", "env": "GITLAB_TOKEN", "label": "GitLab token (cá nhân)", "kind": "password"},
    {"name": "jira_base_url", "env": "JIRA_BASE_URL", "label": "Jira base URL", "kind": "text"},
    {"name": "jira_token", "env": "JIRA_TOKEN", "label": "Jira token (cá nhân)", "kind": "password"},
    {"name": "telegram_bot_token", "env": "TELEGRAM_BOT_TOKEN", "label": "Telegram bot token", "kind": "password"},
    {"name": "telegram_chat_id", "env": "TELEGRAM_CHAT_ID", "label": "Telegram chat id", "kind": "text"},
    {"name": "delivery_channel", "env": "DELIVERY_CHANNEL", "label": "Delivery", "kind": "select",
     "options": ["telegram", "html", "both"]},
    {"name": "digest_times", "env": "DIGEST_TIMES", "label": "Digest times (CSV HH:MM)", "kind": "text"},
    {"name": "digest_days", "env": "DIGEST_DAYS", "label": "Digest days", "kind": "days"},
    {"name": "stale_after_days", "env": "STALE_AFTER_DAYS", "label": "Stale after (days)", "kind": "number",
     "note": "MR/ticket không cập nhật ≥ N ngày → gắn 💤 và đẩy lên 'Cần xử lý'."},
    {"name": "fetch_window_days", "env": "FETCH_WINDOW_DAYS", "label": "Fetch window (days)", "kind": "number",
     "note": "Chỉ lấy MR/issue/ticket có cập nhật trong N ngày gần nhất."},
]
# Kept from the injected environment (not user-editable) so a save never wipes them.
PRESERVED_ENV = ["LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "TIMEZONE"]


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
        "jira_base_url": c.jira_base_url, "jira_token": c.jira_token,
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


def _widget(f: dict, values: dict) -> str:
    name, kind = f["name"], f["kind"]
    val = str(values.get(name, "") or "")
    if kind == "select":
        opts = "".join(f'<option value="{o}"{" selected" if o == val else ""}>{o}</option>'
                       for o in f["options"])
        return f'<select name="{name}" style="padding:6px">{opts}</select>'
    if kind == "days":
        checked = _parse_days(val)
        return " ".join(
            f'<label style="margin-right:12px"><input type="checkbox" name="digest_days" '
            f'value="{d}"{" checked" if d in checked else ""}> {d}</label>' for d in DAYS)
    typ = "number" if kind == "number" else ("password" if kind == "password" else "text")
    inp = f'<input name="{name}" type="{typ}" value="{_html.escape(val)}" style="width:100%;padding:6px">'
    if f.get("note"):
        inp += f'<br><small style="color:#666">{_html.escape(f["note"])}</small>'
    return inp


def render_form(values: dict, *, message: str = "") -> str:
    rows = "".join(f'<p><label>{_html.escape(f["label"])}<br>{_widget(f, values)}</label></p>'
                   for f in FORM_FIELDS)
    msg = f'<p class="hl">{_html.escape(message)}</p>' if message else ""
    return ('<!doctype html><meta charset="utf-8"><title>Dev Daily Digest</title>'
            "<style>body{font-family:sans-serif;max-width:640px;margin:24px auto;color:#1a1a1a}"
            ".hl{background:#eef;padding:8px 12px;border-radius:6px;white-space:pre-wrap}"
            "button{padding:10px 16px;font-size:15px;margin:12px 8px 0 0;cursor:pointer}</style>"
            "<h1>⚙️ Cấu hình &amp; chạy digest</h1>"
            f"{msg}<form method=\"post\" action=\"/run\">{rows}"
            '<button type="submit" formaction="/config">💾 Lưu cấu hình</button>'
            '<button type="submit" formaction="/run">▶️ Run now</button>'
            '<button type="submit" formaction="/schedule">⏰ Run scheduler</button></form>')


async def _run_once():
    """Build adapters/LLM from the live config and produce one DigestReport."""
    c = AppConfig()
    w = c.fetch_window_days
    gl = (GitLabAdapter(c.gitlab_base_url, c.gitlab_token, window_days=w)
          if c.gitlab_base_url and c.gitlab_token else None)
    jr = (JiraAdapter(c.jira_base_url, c.jira_token, window_days=w)
          if c.jira_base_url and c.jira_token else None)
    llm = AsyncOpenAI(base_url=c.llm_base_url or None, api_key=c.llm_api_key or "not-needed")
    report = await build_digest(gitlab=gl, jira=jr, user_name="me", llm=llm, model=c.llm_model,
                                now=datetime.now(_tz.utc), stale_after=c.stale_after_days,
                                store=StateStore(c.report_dir))
    return c, report


async def _maybe_telegram(report, c: AppConfig) -> str:
    chan = (c.delivery_channel or "telegram").lower()
    if chan not in ("telegram", "both"):
        return ""
    if not (c.telegram_bot_token and c.telegram_chat_id):
        return "⚠️ Bỏ qua Telegram: thiếu bot token / chat id."
    try:
        from telegram import Bot
        async with Bot(c.telegram_bot_token) as bot:
            await TelegramDelivery(bot, c.telegram_chat_id).deliver(report)
        return "✅ Đã gửi Telegram."
    except Exception as exc:  # never fail the page over delivery
        return f"⚠️ Gửi Telegram lỗi: {exc}"


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
async def run(request: Request) -> str:
    _apply_live(_merge(_read_form(await request.form())))
    c, report = await _run_once()
    note = await _maybe_telegram(report, c)
    banner = ('<div style="font-family:sans-serif;max-width:720px;margin:16px auto">'
              '<a href="/">⬅️ Cấu hình lại</a>'
              + (f' &nbsp; <b>{_html.escape(note)}</b>' if note else "") + "</div>")
    return banner + render_html(report)


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
        make_jira=lambda u, t: JiraAdapter(u, t, window_days=c.fetch_window_days),
        make_delivery=lambda o: _SchedDelivery(c, o), stale_after=c.stale_after_days)
    if _scheduler is not None:
        _scheduler.shutdown()
    _scheduler = AgentScheduler(owner=owner, service=service, timezone=c.timezone)
    _scheduler.schedule_owner()
    _scheduler.start()
    runs = "\n".join(_scheduler.next_runs()) or "(không có job)"
    return render_form(values, message=f"⏰ Scheduler đang chạy. Lịch kế tiếp:\n{runs}")
