from digest import ruledefs

def _write(tmp_path):
    p = tmp_path / "rules.yaml"
    p.write_text(
        "# Jira digest rules\n"
        "skip_statuses: [New, Backlog]\n"
        "cs_projects: [ISSUE]\n"
        "comment_limit: 2\n"
        "pii:\n"
        "  EMAIL: '[\\w.+-]+@[\\w.-]+\\.\\w+'\n"
        "  CCCD: '\\b\\d{9}\\b|\\b\\d{12}\\b'\n"
        "  PHONE: '(?:\\+84|0)\\d{8,10}'\n"
        "  ACCOUNT: '\\b\\d{10,19}\\b'\n", encoding="utf-8")
    return p

def test_load_parses_yaml(tmp_path):
    rs = ruledefs.load_ruleset(str(_write(tmp_path)))
    assert "new" in rs.skip_statuses and "backlog" in rs.skip_statuses
    assert "ISSUE" in rs.cs_projects
    assert rs.comment_limit == 2

def test_is_cs_by_project_prefix(tmp_path):
    rs = ruledefs.load_ruleset(str(_write(tmp_path)))
    assert ruledefs.is_cs("ISSUE-1234", rs) is True
    assert ruledefs.is_cs("DEV-99", rs) is False

def test_redact_all_pii_types(tmp_path):
    rs = ruledefs.load_ruleset(str(_write(tmp_path)))
    text = "Liên hệ a@b.com hoặc 0901234567, CCCD 012345678901, STK 1234567890123"
    out = ruledefs.redact(text, rs)
    assert "a@b.com" not in out and "[EMAIL]" in out
    assert "0901234567" not in out and "[PHONE]" in out
    assert "012345678901" not in out and "[CCCD]" in out
    assert "1234567890123" not in out and "[ACCOUNT]" in out

def test_missing_file_falls_back_to_defaults(tmp_path):
    rs = ruledefs.load_ruleset(str(tmp_path / "nope.yaml"))
    assert rs.skip_statuses == frozenset()    # default skips nothing
    assert "IS" in rs.cs_projects             # default CS project
    assert rs.duedate_alert_days == 2             # default alert threshold
    assert rs.pii                             # has PII patterns
