from datetime import datetime
from types import SimpleNamespace
from digest import snapshot
from digest.snapshot import StateStore, apply_diff, make_snapshot
from digest.builder import build_digest
from digest.models import SignalType
from tests.conftest import mk_item, FakeSource


def test_first_run_flags_nothing():
    items = [mk_item(SignalType.MR_NEEDS_MY_REVIEW, id="a"),
             mk_item(SignalType.MR_NEEDS_MY_REVIEW, id="b")]
    resolved = apply_diff(items, None)            # no prior snapshot
    assert resolved == []
    assert all(not it.is_new for it in items)     # first digest not drowned in 🆕


def test_new_items_flagged_against_prev():
    prev = {"items": {"a": {"title": "A", "url": "ua", "fp": "x"}}}
    items = [mk_item(SignalType.MR_NEEDS_MY_REVIEW, id="a"),
             mk_item(SignalType.MR_NEEDS_MY_REVIEW, id="b")]
    resolved = apply_diff(items, prev)
    by = {it.id: it for it in items}
    assert by["b"].is_new is True and by["a"].is_new is False
    assert resolved == []


def test_resolved_when_item_gone():
    prev = {"items": {"a": {"title": "A", "url": "ua", "fp": "x"},
                      "gone": {"title": "Gone MR", "url": "ug", "fp": "y"}}}
    items = [mk_item(SignalType.MR_NEEDS_MY_REVIEW, id="a")]
    resolved = apply_diff(items, prev)
    assert resolved == [{"title": "Gone MR", "url": "ug"}]


def test_make_snapshot_shape():
    items = [mk_item(SignalType.MR_NEEDS_MY_REVIEW, id="a", title="A", url="ua")]
    snap = make_snapshot(items, datetime(2026, 6, 16, 8, 30))
    assert snap["last_run"].startswith("2026-06-16")
    assert snap["items"]["a"]["title"] == "A" and snap["items"]["a"]["url"] == "ua"
    assert "|" in snap["items"]["a"]["fp"]   # "<updated>|<signal>"


def test_store_roundtrip_and_missing(tmp_path):
    store = StateStore(str(tmp_path))
    assert store.load() is None                  # nothing yet
    store.save({"last_run": "x", "items": {"a": {"title": "A", "url": "u", "fp": "f"}}})
    assert store.load()["items"]["a"]["title"] == "A"


class _LLM:
    """Stub LLM whose call fails — enrich() catches and falls back, so build still works."""
    def __init__(self):
        async def _raise(**_):
            raise RuntimeError("no llm in test")
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=_raise))


class _FakeStore:
    def __init__(self, prev=None):
        self._prev = prev; self.saved = None
    def load(self):
        return self._prev
    def save(self, snap):
        self.saved = snap


async def test_build_digest_diff_new_resolved_and_save():
    prev = {"items": {"gitlab:mr:1": {"title": "Keep", "url": "u1", "fp": "x"},
                      "gone:1": {"title": "Gone", "url": "ug", "fp": "y"}}}
    store = _FakeStore(prev)
    items = [mk_item(SignalType.MR_NEEDS_MY_REVIEW, id="gitlab:mr:1"),
             mk_item(SignalType.MR_NEEDS_MY_REVIEW, id="gitlab:mr:2")]
    report = await build_digest(gitlab=FakeSource(items), jira=None, user_name="T",
                                llm=_LLM(), model="m", now=datetime(2026, 6, 16), store=store)
    flat = {i.id: i for i in (report.action + report.waiting + report.fyi)}
    assert flat["gitlab:mr:2"].is_new is True and flat["gitlab:mr:1"].is_new is False
    assert [r["title"] for r in report.resolved] == ["Gone"]
    assert store.saved is not None and set(store.saved["items"]) == {"gitlab:mr:1", "gitlab:mr:2"}


async def test_build_digest_skips_diff_on_fetch_error():
    store = _FakeStore({"items": {"x": {"title": "X", "url": "u", "fp": "f"}}})
    failing = FakeSource(error=RuntimeError("boom"))
    report = await build_digest(gitlab=failing, jira=None, user_name="T",
                                llm=_LLM(), model="m", now=datetime(2026, 6, 16), store=store)
    assert report.resolved == []        # no false "resolved" from an outage
    assert store.saved is None          # snapshot not clobbered
    assert report.errors                # error surfaced
