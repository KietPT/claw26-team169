"""Environment configuration (single-user personal agent)."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv
load_dotenv()
def _g(n, d=""): return os.environ.get(n, d)
def _gb(n, d=True):
    """Bool env: 'false'/'0'/'no'/'off'/'' (any case) → False, anything else → True.
    Unset → default `d`."""
    v = os.environ.get(n)
    if v is None:
        return d
    return v.strip().lower() not in ("false", "0", "no", "off", "")


def _parse_times(csv: str, fallback: str) -> list[str]:
    """CSV of HH:MM run times → list, e.g. '08:30, 17:30'. Empty/blank → [fallback]."""
    times = [t.strip() for t in csv.split(",") if t.strip()]
    return times or [fallback]


@dataclass
class Owner:
    """The single owner of this personal agent, built from .env (tokens in plaintext)."""
    chat_id: str
    display_name: str = "me"
    gitlab_token: str = ""
    jira_token: str = ""
    jira_email: str = ""  # set for Jira Cloud (Basic auth); leave empty for Server/DC (Bearer PAT)
    digest_times: list[str] = field(default_factory=lambda: ["17:00"])  # one cron job per time
    digest_days: str = "mon-fri"        # cron day-of-week expr; default skips weekends
    timezone: str = "Asia/Ho_Chi_Minh"
    delivery_channel: str = "telegram"  # telegram | html | both


@dataclass
class AppConfig:
    # GitLab is off by default (reachable only on the private network). Set GITLAB_ENABLED=true to re-enable.
    gitlab_enabled: bool = field(default_factory=lambda: _gb("GITLAB_ENABLED", False))
    gitlab_base_url: str = field(default_factory=lambda: _g("GITLAB_BASE_URL"))
    jira_base_url: str = field(default_factory=lambda: _g("JIRA_BASE_URL"))
    delivery_channel: str = field(default_factory=lambda: _g("DELIVERY_CHANNEL", "telegram"))
    default_digest_time: str = field(default_factory=lambda: _g("DEFAULT_DIGEST_TIME", "17:00"))
    digest_times: str = field(default_factory=lambda: _g("DIGEST_TIMES", ""))  # CSV; empty → default_digest_time
    digest_days: str = field(default_factory=lambda: _g("DIGEST_DAYS", "mon-fri"))  # skip weekends by default
    timezone: str = field(default_factory=lambda: _g("TIMEZONE", "Asia/Ho_Chi_Minh"))
    fetch_window_days: int = field(default_factory=lambda: int(_g("FETCH_WINDOW_DAYS", "30")))
    stale_after_days: int = field(default_factory=lambda: int(_g("STALE_AFTER_DAYS", "7")))
    telegram_bot_token: str = field(default_factory=lambda: _g("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _g("TELEGRAM_CHAT_ID"))
    report_dir: str = field(default_factory=lambda: _g("REPORT_DIR", "./reports"))
    llm_base_url: str = field(default_factory=lambda: _g("LLM_BASE_URL"))
    llm_api_key: str = field(default_factory=lambda: _g("LLM_API_KEY"))
    llm_model: str = field(default_factory=lambda: _g("LLM_MODEL", "gpt-4o-mini"))
    gitlab_token: str = field(default_factory=lambda: _g("GITLAB_TOKEN"))
    jira_token: str = field(default_factory=lambda: _g("JIRA_TOKEN"))
    jira_email: str = field(default_factory=lambda: _g("JIRA_EMAIL"))  # Cloud → Basic auth; empty → Bearer

    def owner(self) -> Owner:
        return Owner(chat_id=self.telegram_chat_id, display_name="me",
                     gitlab_token=self.gitlab_token, jira_token=self.jira_token,
                     jira_email=self.jira_email,
                     digest_times=_parse_times(self.digest_times, self.default_digest_time),
                     digest_days=self.digest_days, timezone=self.timezone,
                     delivery_channel=self.delivery_channel)
