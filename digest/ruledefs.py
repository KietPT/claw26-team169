"""Load digest rules from a YAML config file.

The *values* (which statuses to skip, which projects are CS, PII regex) live in
`rules/jira-digest.yaml`; the *logic* that uses them lives in code. Override the path
via env `RULES_FILE`. Missing/broken file → built-in defaults so the digest never breaks.
"""
from __future__ import annotations
import logging, os, re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
import yaml

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "rules" / "jira-digest.yaml"

# Used when the file cannot be read — must match the committed rules/jira-digest.yaml.
_DEFAULTS = {
    "skip_statuses": [],
    "cs_projects": ["ISSUE"],
    "comment_limit": 3,
    "sandbox_alert_days": 2,
    "sla_alert_days": 1,
    "sandbox_date_field": "",
    "sla_date_field": "",
    "pii": {  # order matters: long/specific numbers (CCCD) first, then PHONE, then ACCOUNT
        "EMAIL": r"[\w.+-]+@[\w.-]+\.\w+",
        "CCCD": r"\b\d{9}\b|\b\d{12}\b",
        "PHONE": r"(?:\+84|0)\d{8,10}",
        "ACCOUNT": r"\b\d{10,19}\b",
    },
}

@dataclass(frozen=True)
class RuleSet:
    skip_statuses: frozenset[str]                 # lowercased status names
    cs_projects: frozenset[str]                   # project keys treated as CS
    comment_limit: int
    sandbox_alert_days: int = 2                       # non-CS NEW + ≤N days until sandbox_date → alert
    sla_alert_days: int = 1                       # CS: SLA Date within N days → JIRA_CS_SLA_DUE_SOON
    sandbox_date_field: str = ""                  # Jira custom field ID for sandbox date (non-CS)
    sla_date_field: str = ""                      # Jira custom field ID for SLA date (CS)
    pii: tuple[tuple[str, re.Pattern], ...] = ()  # ordered (label, compiled regex)

def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

def _build(data: dict) -> RuleSet:
    pii = tuple((label, re.compile(pat)) for label, pat in (data.get("pii") or {}).items())
    return RuleSet(
        skip_statuses=frozenset(s.lower() for s in (data.get("skip_statuses") or [])),
        cs_projects=frozenset(data.get("cs_projects") or []),
        comment_limit=int(data.get("comment_limit", 3)),
        sandbox_alert_days=int(data.get("sandbox_alert_days", 2)),
        sla_alert_days=int(data.get("sla_alert_days", 1)),
        sandbox_date_field=str(data.get("sandbox_date_field") or ""),
        sla_date_field=str(data.get("sla_date_field") or ""),
        pii=pii,
    )

@lru_cache(maxsize=8)
def load_ruleset(path: str | None = None) -> RuleSet:
    target = Path(path or os.getenv("RULES_FILE") or DEFAULT_PATH)
    try:
        return _build(_load_yaml(target))
    except Exception as exc:
        logger.warning("rules file unreadable (%s): %s — using defaults", target, exc)
        return _build(_DEFAULTS)

def is_cs(issue_key: str, rs: RuleSet) -> bool:
    """ISSUE-1234 → project 'ISSUE'."""
    return issue_key.split("-", 1)[0] in rs.cs_projects

def redact(text: str, rs: RuleSet) -> str:
    """Replace all PII with [LABEL], applied sequentially in declaration order."""
    if not text:
        return text
    for label, pat in rs.pii:
        text = pat.sub(f"[{label}]", text)
    return text
