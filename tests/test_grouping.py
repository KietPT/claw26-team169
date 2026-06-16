from digest.models import DigestReport
from digest.grouping import by_section, stale_label
from tests.conftest import mk_item
from digest.models import SignalType

def test_by_section_splits_cs_task_mr():
    cs = mk_item(SignalType.JIRA_OVERDUE, id="jira:ISSUE-1", source="jira")
    cs.pinned = True
    task = mk_item(SignalType.JIRA_IN_PROGRESS, id="jira:DEV-1", source="jira")
    mr = mk_item(SignalType.MR_NEEDS_MY_REVIEW, id="g1", source="gitlab")
    r = DigestReport(date="2026-06-15", user_name="T", action=[cs, mr], waiting=[task])
    grouped = by_section(r)
    # đúng 3 section, đúng thứ tự CS → Task → Merge Request
    assert list(grouped.keys()) == ["cs", "task", "mr"]
    assert [i.id for i in grouped["cs"]] == ["jira:ISSUE-1"]
    assert [i.id for i in grouped["task"]] == ["jira:DEV-1"]
    assert [i.id for i in grouped["mr"]] == ["g1"]

def test_by_section_skips_empty():
    mr = mk_item(SignalType.MR_NEEDS_MY_REVIEW, id="g1", source="gitlab")
    r = DigestReport(date="2026-06-15", user_name="T", action=[mr])
    assert list(by_section(r).keys()) == ["mr"]

def test_stale_label():
    it = mk_item(SignalType.MR_MINE_APPROVED, id="g1")
    assert stale_label(it) is None              # not stale
    it.stale = True; it.idle_days = 12
    assert stale_label(it) == "💤 12 ngày không cập nhật"
