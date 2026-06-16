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
    assert 'type="checkbox" name="digest_days" value="fri"' in r.text  # days = checkboxes
    assert "MR/ticket không cập nhật" in r.text                        # stale note shown
    assert "Chỉ lấy MR/issue/ticket" in r.text                         # fetch-window note shown
    assert 'name="llm_api_key"' not in r.text and 'name="timezone"' not in r.text


def test_parse_days():
    assert webui._parse_days("mon-fri") == {"mon", "tue", "wed", "thu", "fri"}
    assert webui._parse_days("mon,wed") == {"mon", "wed"}
    assert webui._parse_days("") == set(webui.DAYS)


def test_merge_overrides_only_nonempty():
    eff = webui._merge({"jira_base_url": "https://my.jira", "jira_token": "",
                        "stale_after_days": "3"})
    assert eff["jira_base_url"] == "https://my.jira"
    assert eff["stale_after_days"] == "3"
    assert eff["jira_token"] == webui._defaults()["jira_token"]   # blank doesn't clobber


def test_save_env_writes_fields_and_preserves_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_apply_live", lambda v: None)
    monkeypatch.setenv("LLM_MODEL", "gpt-x")
    monkeypatch.setenv("TIMEZONE", "Asia/Ho_Chi_Minh")
    monkeypatch.setenv("GITLAB_ENABLED", "false")        # hidden from form, must survive a save
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-keep")
    p = tmp_path / ".env"
    webui._save_env({"jira_base_url": "https://j", "stale_after_days": "5",
                     "digest_days": "mon,wed"}, str(p))
    text = p.read_text()
    assert "JIRA_BASE_URL=https://j" in text and "STALE_AFTER_DAYS=5" in text
    assert "DIGEST_DAYS=mon,wed" in text
    assert "LLM_MODEL=gpt-x" in text and "TIMEZONE=Asia/Ho_Chi_Minh" in text  # preserved
    assert "GITLAB_ENABLED=false" in text and "GITLAB_TOKEN=glpat-keep" in text  # GitLab preserved, not wiped


def test_config_route_joins_days(monkeypatch):
    saved = {}
    monkeypatch.setattr(webui, "_save_env", lambda values, *a: saved.update(values))
    r = client.post("/config", data={"jira_base_url": "https://j",
                                     "digest_days": ["mon", "fri"]})
    assert r.status_code == 200
    assert saved["jira_base_url"] == "https://j"
    assert saved["digest_days"] == "mon,fri"        # checkboxes joined, canonical order
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
