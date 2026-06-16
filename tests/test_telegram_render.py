from digest.models import DigestReport, SignalType, Category
from delivery.telegram import render_messages, TELEGRAM_LIMIT
from tests.conftest import mk_item

def _cs(it):
    it.source = "jira"; it.pinned = True; return it

def test_render_has_header_and_links():
    it = mk_item(SignalType.MR_NEEDS_MY_REVIEW, url="https://g/mr/1", title="MR !1")
    it.category = Category.ACTION
    r = DigestReport(date="2026-06-15", user_name="T", headline="Focus", action=[it])
    msgs = render_messages(r)
    blob = "\n".join(msgs)
    assert "Focus" in blob
    assert "Merge Request" in blob              # section label
    assert '<a href="https://g/mr/1">' in blob

def test_three_sections_become_three_messages():
    cs = _cs(mk_item(SignalType.JIRA_OVERDUE, id="jira:ISSUE-1", title="ISSUE-1"))
    cs.category = Category.ACTION
    task = mk_item(SignalType.JIRA_IN_PROGRESS, id="jira:DEV-1", title="DEV-1", source="jira")
    task.category = Category.WAITING
    mr = mk_item(SignalType.MR_NEEDS_MY_REVIEW, id="g1", title="MR !1")
    mr.category = Category.ACTION
    r = DigestReport(date="2026-06-15", user_name="T", headline="Focus",
                     action=[cs, mr], waiting=[task])
    msgs = render_messages(r)
    assert len(msgs) == 4                         # overview message + 1 per section
    assert "Báo cáo" in msgs[0] and "Focus" in msgs[0]      # overview is its own message
    assert "CS Ticket" in msgs[1] and "Báo cáo" not in msgs[1]
    assert "Task" in msgs[2]
    assert "Merge Request" in msgs[3]

def test_long_section_still_chunked_under_limit():
    items = []
    for n in range(40):
        it = mk_item(SignalType.MR_MINE_COMMENTED, url=f"https://g/mr/{n}", title=f"MR !{n}")
        it.category = Category.ACTION
        it.context = "Bối cảnh " * 30
        items.append(it)
    r = DigestReport(date="2026-06-15", user_name="T", headline="Focus", action=items)
    msgs = render_messages(r)
    assert len(msgs) > 1
    assert all(len(m) <= TELEGRAM_LIMIT for m in msgs)

def test_render_subtasks_listed():
    it = mk_item(SignalType.JIRA_IN_PROGRESS, url="https://j/STORY-1", title="STORY-1", source="jira")
    it.category = Category.WAITING
    it.subtasks = ["STORY-1-1: Write API", "STORY-1-2: Write tests"]
    r = DigestReport(date="2026-06-15", user_name="T", waiting=[it])
    blob = "\n".join(render_messages(r))
    assert "Cần làm" in blob
    assert "STORY-1-1: Write API" in blob
