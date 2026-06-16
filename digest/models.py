"""Domain models for the daily digest (shared interfaces)."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

class SignalType(str, Enum):
    MR_NEEDS_MY_REVIEW = "mr_needs_my_review"
    MR_MINE_COMMENTED = "mr_mine_commented"
    MR_MINE_PIPELINE_FAILED = "mr_mine_pipeline_failed"
    MR_MINE_APPROVED = "mr_mine_approved"
    ISSUE_ASSIGNED_DUE = "issue_assigned_due"
    JIRA_OVERDUE = "jira_overdue"
    JIRA_NEW_DUE_SOON = "jira_new_due_soon"   # status NEW but due soon → alert
    JIRA_CS_SLA_DUE_SOON = "jira_cs_sla_due_soon"  # CS ticket: SLA Date within alert threshold
    JIRA_IN_PROGRESS = "jira_in_progress"
    JIRA_OTHER = "jira_other"

class Category(str, Enum):
    ACTION = "action"     # 🔴 needs action
    WAITING = "waiting"   # 🟡 waiting
    FYI = "fyi"           # 🔵 for your info

@dataclass
class DigestItem:
    id: str                 # stable, e.g. "gitlab:mr:142"
    signal: SignalType
    title: str
    detail: str             # raw context for the LLM (may include comment threads)
    url: str
    source: str             # "gitlab" | "jira"
    category: Category = Category.FYI
    summary: str | None = None   # 1-sentence fallback when LLM cannot split into 3 parts
    context: str | None = None   # what is happening
    needed: str | None = None    # what is being waited on / requested
    next_step: str | None = None # concrete next action to take
    people: str | None = None    # people involved (MR author / commenter)
    due: date | None = None # used for sorting (overdue first)
    updated: date | None = None  # source last-activity date (for staleness detection)
    idle_days: int | None = None # days since last update (computed in rules)
    stale: bool = False     # idle_days >= STALE_AFTER_DAYS (computed in rules)
    is_new: bool = False    # not present in the previous run's snapshot (computed in snapshot)
    pinned: bool = False    # always ACTION + sorted first (e.g. CS tickets highest priority)
    subtasks: list[str] = field(default_factory=list)  # "KEY: summary" — subtasks assigned to me

@dataclass
class DigestReport:
    date: str
    user_name: str
    action: list[DigestItem] = field(default_factory=list)
    waiting: list[DigestItem] = field(default_factory=list)
    fyi: list[DigestItem] = field(default_factory=list)
    headline: str = ""
    summary: str = ""                                   # LLM 2-3 sentence synthesis of the day
    focus: list[dict] = field(default_factory=list)     # {title,url,reason} — tackle next to avoid slipping
    more: dict[str, int] = field(default_factory=dict)  # category.value -> overflow count
    errors: list[str] = field(default_factory=list)     # e.g. "Jira: token expired"
    resolved: list[dict] = field(default_factory=list)  # {title,url} done since the previous run
