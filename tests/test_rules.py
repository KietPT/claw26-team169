# tests/test_rules.py
from datetime import date
from digest.models import DigestItem, Category, SignalType
from digest import rules

def mk(signal, id="x", due=None, priority=None):
    return DigestItem(id=id, signal=signal, title="t", detail="d", url="u",
                      source="gitlab", due=due, priority=priority)

def test_category_mapping():
    assert rules.categorize(mk(SignalType.MR_NEEDS_MY_REVIEW)) is Category.ACTION
    assert rules.categorize(mk(SignalType.MR_MINE_PIPELINE_FAILED)) is Category.ACTION
    assert rules.categorize(mk(SignalType.JIRA_OVERDUE)) is Category.ACTION
    assert rules.categorize(mk(SignalType.MR_MINE_COMMENTED)) is Category.WAITING
    assert rules.categorize(mk(SignalType.JIRA_IN_PROGRESS)) is Category.WAITING
    assert rules.categorize(mk(SignalType.MR_MINE_APPROVED)) is Category.FYI
    assert rules.categorize(mk(SignalType.JIRA_OTHER)) is Category.FYI

def test_group_and_cap_overflow():
    items = [mk(SignalType.MR_NEEDS_MY_REVIEW, id=str(i)) for i in range(12)]
    report = rules.build_report(items, date="2026-06-15", user_name="Thinh", cap=10)
    assert len(report.action) == 10
    assert report.more["action"] == 2

def test_pinned_forces_action_but_does_not_sort_first():
    # Pinned still forces ACTION category, but importance_key sorts the overdue
    # (deadline group 0) above the non-deadline pinned item (deadline group 1).
    pinned = mk(SignalType.JIRA_OTHER, id="cs", due=None)   # signal would be FYI
    pinned.pinned = True
    overdue = mk(SignalType.JIRA_OVERDUE, id="od", due=date(2026, 6, 1))
    report = rules.build_report([overdue, pinned], date="2026-06-15", user_name="T")
    assert pinned.category is Category.ACTION                # forced into ACTION
    assert [i.id for i in report.action] == ["od", "cs"]     # deadline-first, not pinned-first

def test_overdue_medium_sorts_above_non_deadline_highest():
    # Deadline group wins over priority: an overdue Medium beats a non-deadline Highest.
    overdue_med = mk(SignalType.JIRA_OVERDUE, id="od", due=date(2026, 6, 1), priority="Medium")
    # Non-deadline Highest, forced into ACTION via pinned so both share the action bucket.
    highest = mk(SignalType.JIRA_IN_PROGRESS, id="hi", priority="Highest")
    highest.pinned = True
    report = rules.build_report([highest, overdue_med], date="2026-06-15", user_name="T")
    assert [i.id for i in report.action] == ["od", "hi"]

def test_within_overdue_group_high_before_medium():
    high = mk(SignalType.JIRA_OVERDUE, id="hi", due=date(2026, 6, 10), priority="High")
    med = mk(SignalType.JIRA_OVERDUE, id="md", due=date(2026, 6, 10), priority="Medium")
    report = rules.build_report([med, high], date="2026-06-15", user_name="T")
    assert [i.id for i in report.action] == ["hi", "md"]

def test_same_group_and_priority_earlier_due_first():
    early = mk(SignalType.JIRA_OVERDUE, id="early", due=date(2026, 6, 1), priority="High")
    late = mk(SignalType.JIRA_OVERDUE, id="late", due=date(2026, 6, 10), priority="High")
    report = rules.build_report([late, early], date="2026-06-15", user_name="T")
    assert [i.id for i in report.action] == ["early", "late"]

def test_overdue_sorted_first():
    older = mk(SignalType.JIRA_OVERDUE, id="old", due=date(2026, 6, 10))
    newer = mk(SignalType.JIRA_OVERDUE, id="new", due=date(2026, 6, 14))
    report = rules.build_report([newer, older], date="2026-06-15", user_name="T")
    assert [i.id for i in report.action] == ["old", "new"]


def _with_updated(signal, id, updated):
    it = mk(signal, id=id)
    it.updated = updated
    return it

def test_stale_passive_item_promoted_to_action():
    # MR_MINE_APPROVED is normally FYI; idle 14 days (>= 7) → bumped to ACTION + flagged.
    it = _with_updated(SignalType.MR_MINE_APPROVED, "stale", date(2026, 6, 1))
    report = rules.build_report([it], date="2026-06-15", user_name="T", stale_after=7)
    assert it.category is Category.ACTION
    assert it.stale is True and it.idle_days == 14
    assert it.id in [i.id for i in report.action]

def test_fresh_item_not_stale_keeps_category():
    it = _with_updated(SignalType.MR_MINE_APPROVED, "fresh", date(2026, 6, 14))  # idle 1
    rules.build_report([it], date="2026-06-15", user_name="T", stale_after=7)
    assert it.stale is False and it.idle_days == 1
    assert it.category is Category.FYI

def test_action_item_stays_action_when_stale():
    it = _with_updated(SignalType.MR_NEEDS_MY_REVIEW, "rev", date(2026, 6, 1))
    rules.build_report([it], date="2026-06-15", user_name="T", stale_after=7)
    assert it.stale is True
    assert it.category is Category.ACTION   # already action, just flagged

def test_no_updated_means_not_stale():
    it = mk(SignalType.MR_MINE_APPROVED, id="nounix")   # updated is None
    rules.build_report([it], date="2026-06-15", user_name="T", stale_after=7)
    assert it.stale is False and it.idle_days is None
    assert it.category is Category.FYI

def test_is_at_risk():
    assert rules.is_at_risk(mk(SignalType.JIRA_OVERDUE))
    assert rules.is_at_risk(mk(SignalType.JIRA_CS_SLA_DUE_SOON))
    safe = mk(SignalType.MR_MINE_APPROVED)
    assert not rules.is_at_risk(safe)
    safe.stale = True
    assert rules.is_at_risk(safe)                # stale items also count as at-risk
