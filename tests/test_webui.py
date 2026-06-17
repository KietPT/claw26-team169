from fastapi.testclient import TestClient
import webui
from config import AppConfig
from digest.models import DigestReport

client = TestClient(webui.app)


def test_index_widgets_and_no_llm_fields():
    r = client.get("/")
    assert r.status_code == 200
    assert 'name="jira_token"' in r.text
    assert 'name="gitlab_token"' not in r.text                         # GitLab hidden (private-network only)
    assert '<select name="delivery_channel"' in r.text                 # delivery = dropdown
    assert 'name="digest_days"' in r.text and '<div class="days"' in r.text   # day-of-week checkboxes
    # hidden for simplicity: Jira base URL, Fetch window, Stale after
    assert 'name="jira_base_url"' not in r.text
    assert 'name="fetch_window_days"' not in r.text
    assert 'name="stale_after_days"' not in r.text
    assert 'name="llm_api_key"' not in r.text and 'name="timezone"' not in r.text


def test_field_help_tooltips_shown():
    r = client.get("/")
    # Each guided field has a click-to-expand help block (pure HTML <details>, no JS).
    assert r.text.count("❔ Hướng dẫn lấy thông tin") == 3        # jira_token, bot token, chat id
    assert "@BotFather" in r.text                                 # how to create the Telegram bot
    assert "/getUpdates" in r.text                                # how to find the chat id
    assert "Atlassian API tokens" in r.text                      # how to get the Jira token


def test_secret_fields_not_autofilled(monkeypatch):
    # Sensitive credentials must never be echoed back into the rendered form, even when
    # they exist in the environment — the boxes render empty (with a hint placeholder).
    monkeypatch.setenv("JIRA_EMAIL", "me@example.com")
    monkeypatch.setenv("JIRA_TOKEN", "secret-jira-tok")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:BOTSECRET")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999888777")
    r = client.get("/")
    for leaked in ("me@example.com", "secret-jira-tok", "123456:BOTSECRET", "999888777"):
        assert leaked not in r.text
    assert 'value=""' in r.text                                   # boxes rendered empty


def test_required_fields_marked_and_guard_present():
    r = client.get("/")
    # All four credential fields are required (marker + data-req + HTML5 backstop), and the
    # inline guard that disables the buttons while any is empty is included.
    assert r.text.count("*bắt buộc") == 4
    assert r.text.count("data-req required") == 4
    assert r.text.count('placeholder="Bắt buộc nhập"') == 4
    assert "b.disabled=!ok" in r.text                             # button-guard script present


def test_digest_days_default_checks_mon_to_fri(monkeypatch):
    # With no DIGEST_DAYS in env, the day checkboxes default to mon–fri (weekends unchecked).
    monkeypatch.delenv("DIGEST_DAYS", raising=False)
    r = client.get("/")
    import re
    checked = re.findall(r'value="(\w+)" checked', r.text)
    assert checked == ["mon", "tue", "wed", "thu", "fri"]
    assert "sat" not in checked and "sun" not in checked


def test_parse_days():
    assert webui._parse_days("mon-fri") == {"mon", "tue", "wed", "thu", "fri"}
    assert webui._parse_days("mon,wed") == {"mon", "wed"}
    assert webui._parse_days("") == set(webui.DAYS)


def test_merge_overrides_only_nonempty():
    eff = webui._merge({"jira_email": "me@my.jira", "jira_token": "",
                        "digest_times": "09:00"})
    assert eff["jira_email"] == "me@my.jira"
    assert eff["digest_times"] == "09:00"
    assert eff["jira_token"] == webui._defaults()["jira_token"]   # blank doesn't clobber


def test_save_env_writes_fields_and_preserves_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_apply_live", lambda v: None)
    monkeypatch.setenv("LLM_MODEL", "gpt-x")
    monkeypatch.setenv("TIMEZONE", "Asia/Ho_Chi_Minh")
    monkeypatch.setenv("GITLAB_ENABLED", "false")        # hidden from form, must survive a save
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-keep")
    monkeypatch.setenv("JIRA_BASE_URL", "https://j")     # hidden from form, preserved from env
    monkeypatch.setenv("STALE_AFTER_DAYS", "5")
    p = tmp_path / ".env"
    webui._save_env({"digest_times": "08:30", "digest_days": "mon,wed"}, str(p))
    text = p.read_text()
    assert "DIGEST_TIMES=08:30" in text
    assert "DIGEST_DAYS=mon,wed" in text                                       # editable, saved from form
    assert "STALE_AFTER_DAYS=5" in text                                        # hidden, preserved
    assert "JIRA_BASE_URL=https://j" in text                                   # hidden, preserved
    assert "LLM_MODEL=gpt-x" in text and "TIMEZONE=Asia/Ho_Chi_Minh" in text  # preserved
    assert "GITLAB_ENABLED=false" in text and "GITLAB_TOKEN=glpat-keep" in text  # GitLab preserved, not wiped


def test_config_route_saves_form_fields(monkeypatch):
    saved = {}
    monkeypatch.setattr(webui, "_save_env", lambda values, *a: saved.update(values))
    r = client.post("/config", data={"jira_email": "me@j", "digest_times": "08:30"})
    assert r.status_code == 200
    assert saved["jira_email"] == "me@j"
    assert saved["digest_times"] == "08:30"
    assert "Đã lưu" in r.text


def test_run_now_renders_report(monkeypatch):
    monkeypatch.setattr(webui, "_apply_live", lambda v: None)
    async def fake_run_once():
        return (AppConfig(delivery_channel="html"),
                DigestReport(date="2026-06-16", user_name="me", headline="Demo focus"))
    monkeypatch.setattr(webui, "_run_once", fake_run_once)
    r = client.post("/run", data={"delivery_channel": "html"})
    assert r.status_code == 200
    assert "Báo cáo" in r.text and "Demo focus" in r.text
    assert "Cấu hình lại" in r.text


def test_run_now_sends_telegram_in_background(monkeypatch):
    """Telegram must NOT block the HTTP response: the page renders immediately and the
    send is scheduled as a background task that runs after the response is returned."""
    monkeypatch.setattr(webui, "_apply_live", lambda v: None)
    async def fake_run_once():
        return (AppConfig(delivery_channel="both", telegram_bot_token="t",
                          telegram_chat_id="c"),
                DigestReport(date="2026-06-16", user_name="me", headline="Demo focus"))
    monkeypatch.setattr(webui, "_run_once", fake_run_once)
    sent = []
    async def fake_send(report, c):
        sent.append(report.headline)
    monkeypatch.setattr(webui, "_send_telegram", fake_send)
    r = client.post("/run", data={"delivery_channel": "both"})
    assert r.status_code == 200
    assert "Demo focus" in r.text                  # report rendered
    assert "nền" in r.text                          # backgrounded note, not an inline confirmation
    assert sent == ["Demo focus"]                   # background task actually delivered


def test_run_now_skips_telegram_when_unconfigured(monkeypatch):
    """No bot token/chat id → no background send scheduled, skip note shown, page still renders."""
    monkeypatch.setattr(webui, "_apply_live", lambda v: None)
    async def fake_run_once():
        return (AppConfig(delivery_channel="both", telegram_bot_token="", telegram_chat_id=""),
                DigestReport(date="2026-06-16", user_name="me", headline="Demo focus"))
    monkeypatch.setattr(webui, "_run_once", fake_run_once)
    sent = []
    async def fake_send(report, c):
        sent.append(report.headline)
    monkeypatch.setattr(webui, "_send_telegram", fake_send)
    r = client.post("/run", data={"delivery_channel": "both"})
    assert r.status_code == 200
    assert "Demo focus" in r.text
    assert sent == []                               # nothing scheduled when unconfigured
    assert "Bỏ qua Telegram" in r.text


def test_schedule_route_starts_scheduler(monkeypatch):
    monkeypatch.setattr(webui, "_apply_live", lambda v: None)
    rec = {}
    class FakeSched:
        def __init__(self, *, owner, service, timezone): rec["owner"] = owner
        def schedule_owner(self): rec["scheduled"] = True
        def start(self): rec["started"] = True
        def shutdown(self): rec["shutdown"] = True
        def next_runs(self): return ["digest:owner:0 → 2026-06-17 08:30:00+07:00"]
    monkeypatch.setattr(webui, "AgentScheduler", FakeSched)
    webui._scheduler = None
    r = client.post("/schedule", data={"digest_times": "08:30", "delivery_channel": "html"})
    assert r.status_code == 200
    assert rec.get("scheduled") and rec.get("started")
    assert "08:30" in r.text and "Scheduler" in r.text
