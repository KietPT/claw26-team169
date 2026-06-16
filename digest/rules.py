"""Deterministic categorization, sorting and capping of digest items (spec §7)."""
from __future__ import annotations
from datetime import date as _date
from digest.models import Category, DigestItem, DigestReport, SignalType

CATEGORY_BY_SIGNAL: dict[SignalType, Category] = {
    SignalType.MR_NEEDS_MY_REVIEW: Category.ACTION,
    SignalType.MR_MINE_PIPELINE_FAILED: Category.ACTION,
    SignalType.JIRA_OVERDUE: Category.ACTION,
    SignalType.JIRA_NEW_DUE_SOON: Category.ACTION,
    SignalType.JIRA_CS_SLA_DUE_SOON: Category.ACTION,
    SignalType.ISSUE_ASSIGNED_DUE: Category.ACTION,
    SignalType.MR_MINE_COMMENTED: Category.WAITING,
    SignalType.JIRA_IN_PROGRESS: Category.WAITING,
    SignalType.MR_MINE_APPROVED: Category.FYI,
    SignalType.JIRA_OTHER: Category.FYI,
}

# Signals that mean "at risk of slipping a deadline" → candidates for the focus list.
RISK_SIGNALS = {
    SignalType.JIRA_OVERDUE,
    SignalType.ISSUE_ASSIGNED_DUE,
    SignalType.JIRA_NEW_DUE_SOON,
    SignalType.JIRA_CS_SLA_DUE_SOON,
}

def is_at_risk(item: DigestItem) -> bool:
    """Deterministic 'could be late' flag: a deadline signal, or gone stale."""
    return item.signal in RISK_SIGNALS or item.stale

def annotate_stale(item: DigestItem, today: _date, stale_after: int) -> None:
    """Compute idle_days/stale from the source's last-update date (no-op if unknown)."""
    if item.updated is None:
        item.idle_days = None
        item.stale = False
        return
    item.idle_days = (today - item.updated).days
    item.stale = item.idle_days >= stale_after

def categorize(item: DigestItem) -> Category:
    if item.pinned:           # CS ticket → always in ACTION group
        return Category.ACTION
    base = CATEGORY_BY_SIGNAL.get(item.signal, Category.FYI)
    # Stale items sitting in WAITING/FYI get nudged up to ACTION ("poke this").
    if item.stale and base in (Category.WAITING, Category.FYI):
        return Category.ACTION
    return base

def _sort_key(item: DigestItem) -> tuple:
    # pinned sorts first absolutely; then earlier due dates; no due date goes last
    pin = 0 if item.pinned else 1
    return (pin, 0, item.due) if item.due else (pin, 1, _date.max)

def build_report(items: list[DigestItem], *, date: str, user_name: str, cap: int = 10,
                 stale_after: int = 7) -> DigestReport:
    try:
        today = _date.fromisoformat(date)
    except (TypeError, ValueError):
        today = _date.today()
    buckets: dict[Category, list[DigestItem]] = {c: [] for c in Category}
    for it in items:
        annotate_stale(it, today, stale_after)
        it.category = categorize(it)
        buckets[it.category].append(it)
    report = DigestReport(date=date, user_name=user_name)
    for cat, target in ((Category.ACTION, "action"), (Category.WAITING, "waiting"), (Category.FYI, "fyi")):
        ordered = sorted(buckets[cat], key=_sort_key)
        if len(ordered) > cap:
            report.more[cat.value] = len(ordered) - cap
        setattr(report, target, ordered[:cap])
    return report
