"""Per-run snapshot for run-over-run diff (single owner).

Compares the current fetched items against the previous run to flag what is NEW
(🆕) and what got RESOLVED (present last run, gone now → merged/closed/done). No DB —
one flat JSON file in report_dir, written atomically.

Baseline is the *previous run* (not a fixed calendar day): the first run of a day
diffs against yesterday's last run, a later run diffs against the earlier one.
"""
from __future__ import annotations
import json, os, tempfile
from datetime import datetime
from digest.models import DigestItem


def _fingerprint(item: DigestItem) -> str:
    """Cheap change marker: last-update date + signal (day-granular)."""
    return f"{item.updated.isoformat() if item.updated else ''}|{item.signal.value}"


def make_snapshot(items: list[DigestItem], now: datetime) -> dict:
    return {
        "last_run": now.isoformat(),
        "items": {it.id: {"title": it.title, "url": it.url, "fp": _fingerprint(it)}
                  for it in items},
    }


def apply_diff(items: list[DigestItem], prev: dict | None) -> list[dict]:
    """Mark items.is_new vs the previous snapshot and return the RESOLVED list
    (ids present in prev, absent now) as {'title','url'}.

    prev is None (first ever run) → nothing flagged new, nothing resolved, so the
    first digest is not drowned in 🆕 badges.
    """
    if not prev:
        return []
    prev_items = prev.get("items", {})
    current_ids = {it.id for it in items}
    for it in items:
        it.is_new = it.id not in prev_items
    return [{"title": meta.get("title", pid), "url": meta.get("url", "")}
            for pid, meta in prev_items.items() if pid not in current_ids]


class StateStore:
    """Reads/writes report_dir/state.json (atomic via temp-file rename)."""

    def __init__(self, report_dir: str, filename: str = "state.json") -> None:
        self._path = os.path.join(report_dir, filename)

    def load(self) -> dict | None:
        try:
            with open(self._path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, ValueError):
            return None

    def save(self, snapshot: dict) -> None:
        d = os.path.dirname(self._path) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
