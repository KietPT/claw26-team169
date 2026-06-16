"""Jira Server/DC connector (REST v2, PAT bearer). Maps issues to DigestItems (spec §8).

Rule values (skip statuses, CS projects, PII redaction) come from rules/jira-digest.yaml
via digest.ruledefs — edit that file to change behavior without touching code.
"""
from __future__ import annotations
import base64
import logging
from datetime import date
import httpx
from digest.models import DigestItem, SignalType
from digest import ruledefs

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_DAYS = 30  # fallback if FETCH_WINDOW_DAYS is not configured
_BASE_JQL = "assignee = currentUser() AND statusCategory != Done"

class JiraAdapter:
    def __init__(self, base_url: str, token: str, *, email: str = "",
                 window_days: int = DEFAULT_WINDOW_DAYS, transport=None) -> None:
        self._base = base_url.rstrip("/")
        self._window_days = window_days
        self._rules = ruledefs.load_ruleset()
        # Jira Cloud (*.atlassian.net) REMOVED the classic GET /rest/api/2/search on
        # 2025-05-01 (CHANGE-2046); it now returns 410 Gone. The replacement is the
        # enhanced JQL endpoint /rest/api/2/search/jql. Server/DC never got that endpoint
        # and still serves the classic /search, so pick per flavour. We stay on REST v2
        # (not v3) so description/comment bodies remain plain text — v3 returns ADF objects
        # that would break PII redaction and comment collection below.
        self._is_cloud = ".atlassian.net" in self._base
        # Auth scheme depends on the Jira flavour:
        #   • Cloud (*.atlassian.net): Basic auth with `email:api_token` → set JIRA_EMAIL.
        #   • Server/DC: Bearer PAT → leave email empty.
        tok = token or ""
        if email.strip():
            cred = base64.b64encode(f"{email}:{token}".encode()).decode()
            auth_header = f"Basic {cred}"
            auth_mode = "basic(cloud)"
        else:
            auth_header = f"Bearer {token}"
            auth_mode = "bearer(server/dc)"
        # Diagnostic: confirm a non-empty token reached the adapter, which base URL + auth mode
        # are in use (both are common causes of the 302→login redirect). The token VALUE and the
        # encoded Basic credential are never logged — only presence/length/mode.
        logger.info("JiraAdapter init: base=%s auth_mode=%s email_present=%s token_present=%s token_len=%d",
                    self._base, auth_mode, bool(email.strip()), bool(tok.strip()), len(tok))
        if not tok.strip():
            logger.warning("JiraAdapter created with an EMPTY token — Jira calls will redirect "
                           "to login (302). Check JIRA_TOKEN is injected/loaded.")
        self._client = httpx.AsyncClient(headers={"Authorization": auth_header},
                                         timeout=20, transport=transport)

    def _fields(self) -> str:
        base = "summary,duedate,status,description,comment,issuetype,parent,updated"
        extra = [f for f in [self._rules.sla_date_field] if f]
        return ",".join([base] + extra) if extra else base

    def _jqls(self) -> list[str]:
        """CS (project ISSUE): fetch all. Task (non-CS): active sprint only."""
        cs = sorted(self._rules.cs_projects)
        jqls = []
        if cs:
            # Quote each project key: some keys collide with JQL reserved words
            # (e.g. "IS" is used in `IS EMPTY` / `IS NULL`), so an unquoted
            # `project in (IS)` is a 400 JQL syntax error. Quoting is always valid.
            cs_in = ", ".join(f'"{p}"' for p in cs)
            jqls.append(f"{_BASE_JQL} AND project in ({cs_in}) "
                        f"AND updated >= -{self._window_days}d ORDER BY duedate ASC")
            jqls.append(f"{_BASE_JQL} AND project not in ({cs_in}) "
                        f"AND sprint in openSprints() ORDER BY duedate ASC")
        else:
            jqls.append(f"{_BASE_JQL} AND sprint in openSprints() ORDER BY duedate ASC")
        return jqls

    async def _search(self, jql: str) -> list[dict]:
        # Cloud → enhanced /search/jql (classic /search is 410 Gone); Server/DC → classic /search.
        path = "/rest/api/2/search/jql" if self._is_cloud else "/rest/api/2/search"
        url = self._base + path
        logger.info("Jira GET %s jql=%r", url, jql)
        r = await self._client.get(url,
            params={"jql": jql, "maxResults": 50, "fields": self._fields()})
        logger.info("Jira responded %s for %s", r.status_code, url)
        # httpx does not follow redirects by default, so a 3xx (typically 302 → login page)
        # is NOT a raise_for_status error — it just slips through and r.json() then explodes.
        # A 302 to the login page almost always means the PAT is invalid/expired or the
        # base URL is wrong. Surface it explicitly with the redirect target (token is in the
        # Authorization header and is never logged).
        if r.is_redirect or 300 <= r.status_code < 400:
            location = r.headers.get("location", "<none>")
            logger.warning("Jira returned %s redirect to %s — likely invalid/expired PAT "
                           "or wrong JIRA_BASE_URL (Jira redirects unauthenticated calls to "
                           "its login page)", r.status_code, location)
            raise httpx.HTTPStatusError(
                f"Jira returned {r.status_code} redirect to {location} "
                f"(suspected invalid/expired token)", request=r.request, response=r)
        r.raise_for_status()
        return r.json().get("issues", [])

    async def fetch_items(self, *, today: date | None = None) -> list[DigestItem]:
        today = today or date.today()
        rs = self._rules
        try:
            issues: list[dict] = []
            for jql in self._jqls():
                issues += await self._search(jql)

            # Partition: non-CS subtasks grouped by parent; everything else stands alone.
            subtasks_by_parent: dict[str, list[dict]] = {}
            standalone: list[dict] = []
            for iss in issues:
                f = iss.get("fields", {})
                if (f.get("status") or {}).get("name", "").lower() in rs.skip_statuses:
                    continue  # e.g. status "New" → skip (including subtasks)
                is_sub = (f.get("issuetype") or {}).get("subtask")
                parent = f.get("parent") or {}
                if is_sub and not ruledefs.is_cs(iss["key"], rs) and parent.get("key"):
                    subtasks_by_parent.setdefault(parent["key"], []).append(iss)
                else:
                    standalone.append(iss)

            items: dict[str, DigestItem] = {}
            order: list[str] = []
            for iss in standalone:
                items[iss["key"]] = self._build_item(iss, today, rs)
                order.append(iss["key"])

            # Subtasks assigned to me → folded into parent Story's to-do list; only fold into
            # Stories already in items (active sprint); orphan subtasks (Story outside sprint) dropped.
            for pkey, subs in subtasks_by_parent.items():
                if pkey in items:
                    items[pkey].subtasks.extend(
                        f"{s['key']}: {(s.get('fields') or {}).get('summary', '')}" for s in subs)
        finally:
            await self._client.aclose()
        return [items[k] for k in order]

    def _build_item(self, iss: dict, today: date, rs) -> DigestItem:
        f = iss.get("fields", {})
        key = iss["key"]
        cs = ruledefs.is_cs(key, rs)
        status = f.get("status") or {}
        status_cat = (status.get("statusCategory") or {}).get("key")

        if cs:
            # CS: SLA Date drives both sorting and the alert. A CS ticket whose SLA Date
            # is within sla_alert_days (≤1 day, including already breached) is flagged.
            due = _field_date(f, rs.sla_date_field)
            if due and (due - today).days <= rs.sla_alert_days:
                signal = SignalType.JIRA_CS_SLA_DUE_SOON
            elif status_cat == "indeterminate":
                signal = SignalType.JIRA_IN_PROGRESS
            else:
                signal = SignalType.JIRA_OTHER
        else:
            # Non-CS: the plain `duedate` field drives both sorting and alerts. Any ticket
            # (not just NEW) within duedate_alert_days of its due date is flagged due-soon.
            due = _date(f.get("duedate"))
            if due and due <= today:
                signal = SignalType.JIRA_OVERDUE
            elif due and (due - today).days <= rs.duedate_alert_days:
                signal = SignalType.JIRA_DUE_SOON
            elif status_cat == "indeterminate":
                signal = SignalType.JIRA_IN_PROGRESS
            else:
                signal = SignalType.JIRA_OTHER

        # CS: use description summary; non-CS: collect recent comments. PII redacted before leaving the machine.
        raw = (f.get("description") or "") if cs else _comments(f, rs.comment_limit)
        detail = ruledefs.redact(raw, rs) or f.get("summary", "")
        return DigestItem(id=f"jira:{key}", signal=signal, title=key, detail=detail,
                          url=f"{self._base}/browse/{key}", source="jira", due=due,
                          updated=_date10(f.get("updated")), pinned=cs)

def _comments(fields: dict, limit: int) -> str:
    """Collect the `limit` most recent comments with author name: 'Name: body'."""
    comments = ((fields.get("comment") or {}).get("comments")) or []
    lines = []
    for c in comments[-limit:]:
        who = (c.get("author") or {}).get("displayName") or ""
        body = (c.get("body") or "").strip()
        if body:
            lines.append(f"{who}: {body}" if who else body)
    return "\n".join(lines)

def _field_date(fields: dict, custom_field: str) -> date | None:
    """Date from the configured SLA custom field (CS tickets), falling back to `duedate`
    when the field is unconfigured or has no value — so the digest still works before
    the custom-field ID is wired up."""
    if custom_field:
        d = _date(fields.get(custom_field))
        if d is not None:
            return d
    return _date(fields.get("duedate"))

def _date(s):
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None

def _date10(s):
    """Parse the YYYY-MM-DD prefix of an ISO timestamp (ignores time/offset)."""
    try:
        return date.fromisoformat(s[:10])
    except (TypeError, ValueError):
        return None
