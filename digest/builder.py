"""Orchestrate: fetch sources (parallel, fault-tolerant) → rules → summarize → report."""
from __future__ import annotations
import asyncio, logging
from datetime import datetime
from connectors.base import GitLabPort, JiraPort
from digest import rules, summarize, snapshot
from digest.models import DigestItem, DigestReport
logger = logging.getLogger(__name__)

async def _safe(source, label: str, errors: list[str]) -> list[DigestItem]:
    if source is None:
        return []
    try:
        return list(await source.fetch_items())
    except Exception as exc:
        logger.warning("%s fetch failed: %s", label, exc)
        errors.append(f"{label}: {exc}")
        return []

async def build_digest(*, gitlab: GitLabPort | None, jira: JiraPort | None,
                       user_name: str, llm, model: str, now: datetime, cap: int = 10,
                       stale_after: int = 7, store=None) -> DigestReport:
    errors: list[str] = []
    gl_items, jr_items = await asyncio.gather(
        _safe(gitlab, "GitLab", errors), _safe(jira, "Jira", errors))
    all_items = [*gl_items, *jr_items]

    # Run-over-run diff. Skipped on a partial fetch (errors) so a transient outage
    # never makes still-open items look "resolved" or spuriously new.
    do_diff = store is not None and not errors
    resolved = snapshot.apply_diff(all_items, store.load()) if do_diff else []

    report = rules.build_report(all_items, date=now.date().isoformat(),
                                user_name=user_name, cap=cap, stale_after=stale_after)
    report.errors = errors
    report.resolved = resolved
    await summarize.enrich(report, llm=llm, model=model)
    if do_diff:
        store.save(snapshot.make_snapshot(all_items, now))
    return report
