# tests/test_summarize.py
from digest.models import DigestReport, SignalType
from digest import summarize
from tests.conftest import mk_item

class StubLLM:
    def __init__(self, raise_=False, content="{}"):
        self._raise = raise_; self._content = content
        outer = self
        class _Completions:
            @staticmethod
            async def create(**kw):
                if outer._raise:
                    raise RuntimeError("down")
                msg = type("Msg", (), {"content": outer._content})()
                choice = type("Choice", (), {"message": msg})()
                return type("Resp", (), {"choices": [choice]})()
        class _Chat:
            completions = _Completions()
        self.chat = _Chat()

async def test_fallback_keeps_items_on_error():
    r = DigestReport(date="2026-06-15", user_name="T",
                     action=[mk_item(SignalType.MR_NEEDS_MY_REVIEW, detail="x" * 200)])
    await summarize.enrich(r, llm=StubLLM(raise_=True), model="m")
    assert r.action[0].summary == ("x" * 200)[:140]
    assert r.action[0].context == ("x" * 200)[:140]
    assert r.headline == ""

async def test_applies_llm_output():
    item = mk_item(SignalType.MR_NEEDS_MY_REVIEW, id="a")
    r = DigestReport(date="2026-06-15", user_name="T", action=[item])
    content = ('{"headline":"Tập trung review","items":{"a":{"context":"Reviewer comment",'
               '"needed":"Bọc try/except","next_step":"Sửa rồi re-request"}},"action_order":["a"]}')
    await summarize.enrich(r, llm=StubLLM(content=content), model="m")
    assert r.headline == "Tập trung review"
    assert r.action[0].context == "Reviewer comment"
    assert r.action[0].needed == "Bọc try/except"
    assert r.action[0].next_step == "Sửa rồi re-request"

async def test_summary_and_focus_parsed_and_resolved():
    overdue = mk_item(SignalType.JIRA_OVERDUE, id="jira:DEV-1", title="DEV-1", url="u1")
    safe = mk_item(SignalType.MR_MINE_APPROVED, id="g2", title="g!2", url="u2")
    r = DigestReport(date="2026-06-15", user_name="T", action=[overdue], fyi=[safe])
    content = ('{"headline":"h","summary":"Hôm nay có 1 việc gấp.","items":{},'
               '"action_order":[],'
               '"focus":[{"id":"jira:DEV-1","reason":"Quá hạn"},{"id":"ghost","reason":"x"}]}')
    await summarize.enrich(r, llm=StubLLM(content=content), model="m")
    assert r.summary == "Hôm nay có 1 việc gấp."
    assert len(r.focus) == 1                       # hallucinated "ghost" id dropped
    assert r.focus[0] == {"title": "DEV-1", "url": "u1", "reason": "Quá hạn"}
