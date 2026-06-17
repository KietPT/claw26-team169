"""Deterministic categorization, sorting and capping of digest items (spec §7)."""
from __future__ import annotations
from datetime import date as _date
from digest.models import Category, DigestItem, DigestReport, SignalType

CATEGORY_BY_SIGNAL: dict[SignalType, Category] = {
    SignalType.MR_NEEDS_MY_REVIEW: Category.ACTION,
    SignalType.MR_MINE_PIPELINE_FAILED: Category.ACTION,
    SignalType.JIRA_OVERDUE: Category.ACTION,
    SignalType.JIRA_DUE_SOON: Category.ACTION,
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
    SignalType.JIRA_DUE_SOON,
    SignalType.JIRA_CS_SLA_DUE_SOON,
}

def is_at_risk(item: DigestItem) -> bool:
    """Deterministic 'could be late' flag: a deadline signal, or gone stale."""
    return item.signal in RISK_SIGNALS or item.stale

# Importance ranking inputs (spec: deadline group → priority → due → id).
PRIORITY_RANK = {"Highest": 0, "High": 1, "Medium": 2, "Low": 3, "Lowest": 4}
DEADLINE_SIGNALS = {SignalType.JIRA_OVERDUE, SignalType.JIRA_CS_SLA_DUE_SOON,
                    SignalType.JIRA_DUE_SOON, SignalType.ISSUE_ASSIGNED_DUE}

def priority_rank(item: DigestItem) -> int:
    return PRIORITY_RANK.get(item.priority or "", 2)   # unknown/None -> Medium

def deadline_group(item: DigestItem) -> int:
    return 0 if item.signal in DEADLINE_SIGNALS else 1

def importance_key(item: DigestItem):
    from datetime import date as _d
    return (deadline_group(item), priority_rank(item),
            item.due or _d.max, item.id)

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
        ordered = sorted(buckets[cat], key=importance_key)
        if len(ordered) > cap:
            report.more[cat.value] = len(ordered) - cap
        setattr(report, target, ordered[:cap])
    return report
