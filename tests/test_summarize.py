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

class RetryLLM:
    """Rejects the json_object call (like a model that doesn't support response_format),
    then succeeds on the retry without it."""
    def __init__(self, content):
        self._content = content; self.calls = []
        outer = self
        class _Completions:
            @staticmethod
            async def create(**kw):
                outer.calls.append("response_format" in kw)
                if "response_format" in kw:
                    raise RuntimeError("response_format not supported")
                msg = type("Msg", (), {"content": outer._content})()
                choice = type("Choice", (), {"message": msg})()
                return type("Resp", (), {"choices": [choice]})()
        class _Chat:
            completions = _Completions()
        self.chat = _Chat()


async def test_retries_without_response_format_when_unsupported():
    item = mk_item(SignalType.MR_NEEDS_MY_REVIEW, id="a")
    r = DigestReport(date="2026-06-15", user_name="T", action=[item])
    llm = RetryLLM('{"headline":"H","summary":"S","items":{"a":{"context":"Ctx"}}}')
    await summarize.enrich(r, llm=llm, model="m")
    assert llm.calls == [True, False]          # tried json_object, then retried without it
    assert r.headline == "H"                    # retry result applied (not just fallback)
    assert r.action[0].context == "Ctx"


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
    content = ('{"headline":"Tập trung review","summary":"Hôm nay tập trung 2 việc gấp.",'
               '"items":{"a":{"context":"Reviewer comment",'
               '"needed":"Bọc try/except","next_step":"Sửa rồi re-request"}}}')
    await summarize.enrich(r, llm=StubLLM(content=content), model="m")
    assert r.headline == "Tập trung review"
    assert r.summary == "Hôm nay tập trung 2 việc gấp."
    assert r.action[0].context == "Reviewer comment"
    assert r.action[0].needed == "Bọc try/except"
    assert r.action[0].next_step == "Sửa rồi re-request"

async def test_parses_json_after_reasoning_think_block():
    """Reasoning models (e.g. minimax-m2.5) emit a <think>...</think> block before the JSON,
    so content does not start with '{'. enrich must still extract and apply the JSON."""
    item = mk_item(SignalType.MR_NEEDS_MY_REVIEW, id="a")
    r = DigestReport(date="2026-06-15", user_name="T", action=[item])
    content = ('<think>The user wants a digest.\nLet me produce JSON.</think>\n\n'
               '{"headline":"Tập trung review","summary":"S",'
               '"items":{"a":{"context":"Ctx","needed":"N","next_step":"Step"}}}')
    await summarize.enrich(r, llm=StubLLM(content=content), model="m")
    assert r.headline == "Tập trung review"
    assert r.action[0].context == "Ctx"
    assert r.action[0].next_step == "Step"


async def test_fyi_not_enriched_only_fallback_context():
    """Option A: LLM prose (context/needed/next_step) is requested only for ACTION+WAITING.
    An FYI item gets fallback context from its detail and NO needed/next_step — even if the
    LLM returns prose for its id — so the expensive output tokens are not spent on FYI."""
    action = mk_item(SignalType.JIRA_OVERDUE, id="a", detail="action detail")
    fyi = mk_item(SignalType.JIRA_OTHER, id="f", detail="fyi detail")
    r = DigestReport(date="2026-06-15", user_name="T", action=[action], fyi=[fyi])
    content = ('{"headline":"h","summary":"s","items":{'
               '"a":{"context":"A ctx","needed":"A need","next_step":"A step"},'
               '"f":{"context":"F ctx","needed":"F need","next_step":"F step"}}}')
    await summarize.enrich(r, llm=StubLLM(content=content), model="m")
    # ACTION enriched from the LLM.
    assert r.action[0].context == "A ctx"
    assert r.action[0].needed == "A need"
    assert r.action[0].next_step == "A step"
    # FYI NOT enriched: fallback context from detail, no needed/next_step.
    assert r.fyi[0].context == "fyi detail"
    assert r.fyi[0].needed is None
    assert r.fyi[0].next_step is None


async def test_priorities_built_from_deterministic_top_with_llm_reasons():
    # Ranking is deterministic (top from ACTION then WAITING); LLM only supplies reasons.
    overdue = mk_item(SignalType.JIRA_OVERDUE, id="jira:DEV-1", title="DEV-1", url="u1")
    overdue.priority = "High"
    safe = mk_item(SignalType.MR_MINE_APPROVED, id="g2", title="g!2", url="u2")
    r = DigestReport(date="2026-06-15", user_name="T", action=[overdue], fyi=[safe])
    content = '{"headline":"h","items":{},"priorities":{"jira:DEV-1":"Quá hạn"}}'
    await summarize.enrich(r, llm=StubLLM(content=content), model="m")
    # FYI item excluded; only the deterministic top (the overdue action item) appears.
    assert r.priorities == [{"title": "DEV-1", "url": "u1",
                             "priority": "High", "reason": "Quá hạn"}]

async def test_priorities_fallback_when_llm_fails():
    overdue = mk_item(SignalType.JIRA_OVERDUE, id="jira:DEV-1", title="DEV-1", url="u1")
    overdue.priority = "High"
    r = DigestReport(date="2026-06-15", user_name="T", action=[overdue])
    await summarize.enrich(r, llm=StubLLM(raise_=True), model="m")
    assert r.priorities == [{"title": "DEV-1", "url": "u1",
                             "priority": "High", "reason": ""}]
