"""Group a DigestReport's items by source, preserving ACTION→WAITING→FYI order.

Also derives deterministic metadata (status, due date) so the report never
relies on the LLM for facts that are already known from the source data.
"""
from __future__ import annotations
from datetime import date as _date
from digest.models import Category, DigestItem, DigestReport, SignalType

CATEGORY_LABEL = {
    Category.ACTION: "🔴 Cần xử lý",
    Category.WAITING: "🟡 Đang chờ",
    Category.FYI: "🔵 Để biết",
}
CATEGORY_EMOJI = {Category.ACTION: "🔴", Category.WAITING: "🟡", Category.FYI: "🔵"}

# 3 independent sections, display order: CS Ticket → Task → Merge Request.
SECTIONS = [("cs", "CS Ticket", "🚨"), ("task", "Task", "📋"), ("mr", "Merge Request", "📦")]

def section_of(item: DigestItem) -> str:
    if item.source == "jira":
        return "cs" if item.pinned else "task"   # CS ticket (project ISSUE) vs other Jira
    return "mr"                                  # everything from GitLab (MR + issue)

# Current status derived directly from signal (no LLM guessing).
STATUS_LABEL = {
    SignalType.MR_NEEDS_MY_REVIEW: "Chờ bạn review",
    SignalType.MR_MINE_COMMENTED: "Có comment mới",
    SignalType.MR_MINE_PIPELINE_FAILED: "Pipeline FAIL",
    SignalType.MR_MINE_APPROVED: "Đã approve · sẵn sàng merge",
    SignalType.ISSUE_ASSIGNED_DUE: "Issue tới hạn",
    SignalType.JIRA_OVERDUE: "Quá hạn",
    SignalType.JIRA_DUE_SOON: "Sắp tới hạn, cần xử lý",
    SignalType.JIRA_CS_SLA_DUE_SOON: "🚨 SLA gấp (≤1 ngày)",
    SignalType.JIRA_IN_PROGRESS: "Đang làm",
    SignalType.JIRA_OTHER: "Chưa bắt đầu",
}

def status_label(item: DigestItem) -> str:
    return STATUS_LABEL.get(item.signal, "")

def stale_label(item: DigestItem) -> str | None:
    """'💤 N ngày không cập nhật' khi item bị đánh dấu stale."""
    if item.stale and item.idle_days is not None:
        return f"💤 {item.idle_days} ngày không cập nhật"
    return None

def due_label(item: DigestItem, report_date: str) -> str | None:
    """'Quá hạn N ngày', 'Hạn hôm nay' or 'Còn N ngày' (with dd/mm)."""
    if not item.due:
        return None
    try:
        today = _date.fromisoformat(report_date)
    except (TypeError, ValueError):
        today = _date.today()
    delta = (item.due - today).days
    d = item.due.strftime("%d/%m")
    if delta < 0:
        return f"Quá hạn {abs(delta)} ngày ({d})"
    if delta == 0:
        return f"Hạn hôm nay ({d})"
    return f"Còn {delta} ngày ({d})"

def by_section(report: DigestReport) -> dict[str, list[DigestItem]]:
    """Return {section: [items...]} ordered ACTION→WAITING→FYI, only non-empty sections."""
    ordered = [*report.action, *report.waiting, *report.fyi]
    out: dict[str, list[DigestItem]] = {}
    for sec, _label, _icon in SECTIONS:
        items = [i for i in ordered if section_of(i) == sec]
        if items:
            out[sec] = items
    return out
