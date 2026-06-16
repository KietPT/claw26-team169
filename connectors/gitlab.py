"""GitLab connector (REST v4, self-hosted). Maps MRs/issues to DigestItems (spec §8)."""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
import httpx
from digest.models import DigestItem, SignalType

DEFAULT_WINDOW_DAYS = 30  # fallback if FETCH_WINDOW_DAYS is not configured

class GitLabAdapter:
    def __init__(self, base_url: str, token: str, *, window_days: int = DEFAULT_WINDOW_DAYS,
                 transport=None) -> None:
        self._base = base_url.rstrip("/") + "/api/v4"
        self._window_days = window_days  # only fetch MRs/issues updated within the last N days
        self._client = httpx.AsyncClient(headers={"PRIVATE-TOKEN": token},
                                         timeout=20, transport=transport)

    async def _get(self, path: str, **params):
        r = await self._client.get(self._base + path, params=params)
        r.raise_for_status()
        return r.json()

    async def fetch_items(self) -> list[DigestItem]:
        try:
            me = (await self._get("/user")).get("username")
            updated_after = (datetime.now(timezone.utc) - timedelta(days=self._window_days)).isoformat()
            items: list[DigestItem] = []
            for mr in await self._get("/merge_requests", reviewer_username=me, state="opened", scope="all", updated_after=updated_after):
                detail = " — ".join(p for p in (mr.get("title", ""), mr.get("description") or "") if p).strip()
                item = self._mr(mr, SignalType.MR_NEEDS_MY_REVIEW, detail[:500] or "Cần bạn review")
                item.people = (mr.get("author") or {}).get("name")
                item.updated = _date10(mr.get("updated_at"))
                # Threads I (as reviewer) opened that the author has not yet resolved.
                threads_text, _ = _format_threads(await self._safe_threads(mr["project_id"], mr["iid"]), mine=me)
                if threads_text:
                    item.detail = f"{item.detail}\n\n{threads_text}"
                items.append(item)
            for mr in await self._get("/merge_requests", author_username=me, state="opened", scope="all", updated_after=updated_after):
                it = await self._authored(mr)
                it.updated = _date10(mr.get("updated_at"))
                items.append(it)
            today = datetime.now(timezone.utc).date()
            for iss in await self._get("/issues", assignee_username=me, state="opened", scope="all", updated_after=updated_after):
                due = _date(iss.get("due_date"))
                if due is not None and due <= today:
                    items.append(DigestItem(id=f"gitlab:issue:{iss['id']}",
                        signal=SignalType.ISSUE_ASSIGNED_DUE,
                        title=iss.get("references", {}).get("full") or iss["title"],
                        detail=iss.get("title", ""), url=iss["web_url"], source="gitlab",
                        due=_date(iss.get("due_date")), updated=_date10(iss.get("updated_at"))))
            return items
        finally:
            await self._client.aclose()

    def _mr(self, mr, signal, detail) -> DigestItem:
        return DigestItem(id=f"gitlab:mr:{mr['project_id']}:{mr['iid']}", signal=signal,
                          title=mr.get("references", {}).get("full") or f"!{mr['iid']}",
                          detail=detail, url=mr["web_url"], source="gitlab")

    async def _authored(self, mr) -> DigestItem:
        status = (mr.get("pipeline") or {}).get("status")
        if status == "failed":
            return self._mr(mr, SignalType.MR_MINE_PIPELINE_FAILED, "Pipeline FAIL")
        if mr.get("user_notes_count", 0) > 0:
            item = self._mr(mr, SignalType.MR_MINE_COMMENTED, "")
            # All threads opened by reviewers that I (as author) have not yet resolved.
            text, people = _format_threads(await self._safe_threads(mr["project_id"], mr["iid"]))
            item.detail = text or "Có comment mới"
            item.people = ", ".join(people) or None
            return item
        return self._mr(mr, SignalType.MR_MINE_APPROVED, "Sẵn sàng merge")

    async def _safe_threads(self, project_id, iid) -> list[dict]:
        """Parse MR discussions into threads (opener note + resolve state). Errors → []."""
        try:
            data = await self._get(f"/projects/{project_id}/merge_requests/{iid}/discussions")
        except Exception:
            return []
        threads: list[dict] = []
        for d in data:
            notes = [n for n in d.get("notes", []) if not n.get("system")]
            if not notes:
                continue
            opener = notes[0]
            if not opener.get("resolvable"):
                continue  # skip plain comments, keep only resolvable review threads
            author = opener.get("author") or {}
            threads.append({
                "name": author.get("name") or author.get("username") or "",
                "username": author.get("username") or "",
                "body": opener.get("body", ""),
                "resolved": bool(opener.get("resolved")),
            })
        return threads

def _format_threads(threads: list[dict], *, mine: str | None = None, limit: int = 3) -> tuple[str, list[str]]:
    """List unresolved threads concisely. mine=username → only threads opened by me."""
    unresolved = [t for t in threads if not t["resolved"]]
    if mine is not None:
        unresolved = [t for t in unresolved if t["username"] == mine]
    if not unresolved:
        return "", []
    shown = unresolved[:limit]
    lines = ["🧵 Thread chưa resolve:"]
    for t in shown:
        body = " ".join((t["body"] or "").split())[:120]
        lines.append(f"- {t['name'] or '?'}: {body}")
    extra = len(unresolved) - len(shown)
    if extra > 0:
        lines.append(f"+ và {extra} thread khác")
    people: list[str] = []
    for t in shown:
        if t["name"] and t["name"] not in people:
            people.append(t["name"])
    return "\n".join(lines), people

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
