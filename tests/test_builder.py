# tests/test_builder.py
from datetime import datetime, timezone
from digest import builder
from digest.models import SignalType
from tests.conftest import FakeSource, mk_item

class NoLLM:  # enrich will fallback
    class chat:
        class completions:
            @staticmethod
            async def create(**kw): raise RuntimeError("no llm")

async def test_merges_sources_and_categorizes():
    gl = FakeSource([mk_item(SignalType.MR_NEEDS_MY_REVIEW, id="gl1")])
    jr = FakeSource([mk_item(SignalType.JIRA_IN_PROGRESS, id="jr1", source="jira")])
    now = datetime(2026, 6, 15, 17, 0, tzinfo=timezone.utc)
    r = await builder.build_digest(gitlab=gl, jira=jr, user_name="T", llm=NoLLM(), model="m", now=now)
    assert [i.id for i in r.action] == ["gl1"]
    assert [i.id for i in r.waiting] == ["jr1"]

async def test_source_error_recorded_not_raised():
    gl = FakeSource([mk_item(SignalType.MR_NEEDS_MY_REVIEW, id="gl1")])
    jr = FakeSource(error=RuntimeError("Jira 500"))
    now = datetime(2026, 6, 15, 17, 0, tzinfo=timezone.utc)
    r = await builder.build_digest(gitlab=gl, jira=jr, user_name="T", llm=NoLLM(), model="m", now=now)
    assert [i.id for i in r.action] == ["gl1"]
    assert any("Jira" in e for e in r.errors)
