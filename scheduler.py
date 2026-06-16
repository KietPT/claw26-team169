"""Single-owner digest service + APScheduler cron registration."""
from __future__ import annotations
import logging
from datetime import datetime, timezone as _tz
from typing import Any, Callable
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from config import Owner
from digest.builder import build_digest
from digest.snapshot import StateStore
logger = logging.getLogger(__name__)


class DigestService:
    def __init__(self, *, llm: Any, model: str,
                 gitlab_base: str, jira_base: str, report_dir: str,
                 make_gitlab: Callable, make_jira: Callable, make_delivery: Callable,
                 stale_after: int = 7) -> None:
        self._llm = llm; self._model = model
        self._gl_base = gitlab_base; self._jr_base = jira_base; self._report_dir = report_dir
        self._make_gitlab = make_gitlab; self._make_jira = make_jira; self._make_delivery = make_delivery
        self._stale_after = stale_after
        self._store = StateStore(report_dir)

    async def run_for(self, owner: Owner, *, now: datetime) -> None:
        gitlab = self._make_gitlab(self._gl_base, owner.gitlab_token) if owner.gitlab_token else None
        jira = self._make_jira(self._jr_base, owner.jira_token) if owner.jira_token else None
        report = await build_digest(gitlab=gitlab, jira=jira, user_name=owner.display_name,
                                    llm=self._llm, model=self._model, now=now,
                                    stale_after=self._stale_after, store=self._store)
        await self._make_delivery(owner).deliver(report)


class AgentScheduler:
    def __init__(self, *, owner: Owner, service: DigestService, timezone: str) -> None:
        self._owner = owner; self._service = service
        self._sched = AsyncIOScheduler(timezone=timezone)
        self._active = True

    def start(self) -> None: self._sched.start()

    def next_runs(self) -> list[str]:
        """Human-readable 'job → next fire time' lines (for the web UI status)."""
        return [f"{j.id} → {j.next_run_time}" for j in self._sched.get_jobs()]

    def shutdown(self) -> None:
        if self._sched.running: self._sched.shutdown(wait=False)

    def pause(self) -> None: self._active = False

    def resume(self) -> None: self._active = True

    def schedule_owner(self) -> None:
        # Re-register cleanly: drop any prior digest jobs (handles a shrunk time list).
        for job in list(self._sched.get_jobs()):
            if job.id.startswith("digest:owner"):
                self._sched.remove_job(job.id)
        for job_id, trigger in _digest_jobs(self._owner.digest_times, self._owner.digest_days,
                                            self._owner.timezone):
            self._sched.add_job(self._run, trigger, id=job_id, replace_existing=True)

    async def _run(self) -> None:
        if not self._active:
            return
        try:
            await self._service.run_for(self._owner, now=datetime.now(_tz.utc))
        except Exception as exc:
            logger.exception("digest failed: %s", exc)


def _digest_jobs(times: list[str], days: str, tz: str) -> list[tuple[str, CronTrigger]]:
    """One (job_id, CronTrigger) per run time. `days` is a cron day-of-week expression
    (e.g. 'mon-fri' to skip weekends), evaluated in the owner's timezone."""
    jobs = []
    for i, t in enumerate(times):
        hh, mm = _hhmm(t)
        jobs.append((f"digest:owner:{i}",
                     CronTrigger(hour=hh, minute=mm, day_of_week=days, timezone=tz)))
    return jobs


def _hhmm(value: str) -> tuple[int, int]:
    try:
        hh, mm = value.split(":"); return int(hh), int(mm)
    except (ValueError, AttributeError):
        return 17, 0
