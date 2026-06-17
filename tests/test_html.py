from digest.models import DigestReport, SignalType, Category
from delivery.html import render_html
from tests.conftest import mk_item

def test_render_contains_links_and_sections():
    it = mk_item(SignalType.MR_NEEDS_MY_REVIEW, url="https://git/mr/1", title="MR !1")
    it.category = Category.ACTION
    it.context = "Reviewer đã comment"
    it.needed = "Bọc try/except"
    it.next_step = "Sửa rồi re-request review"
    r = DigestReport(date="2026-06-15", user_name="Thinh", headline="Focus",
                     summary="Hôm nay có 2 việc gấp cần ưu tiên.", action=[it])
    html = render_html(r)
    assert "Focus" in html
    assert "Hôm nay có 2 việc gấp cần ưu tiên." in html   # synthesized summary paragraph
    assert "Merge Request" in html               # section label
    assert 'href="https://git/mr/1"' in html
    assert "Chờ bạn review" in html          # status từ signal
    assert "Bối cảnh" in html and "Cần gì" in html and "Làm gì" in html
    assert "Sửa rồi re-request review" in html

def test_priority_badge_and_ranked_list_rendered():
    it = mk_item(SignalType.JIRA_OVERDUE, url="https://j/DEV-1", title="DEV-1", source="jira")
    it.category = Category.ACTION
    it.priority = "High"
    r = DigestReport(date="2026-06-15", user_name="T", action=[it],
                     priorities=[{"title": "DEV-1", "url": "https://j/DEV-1",
                                  "priority": "High", "reason": "Quá hạn"}])
    html = render_html(r)
    assert "High" in html                          # priority badge on the card
    assert "Quan trọng nhất hôm nay" in html        # ranked list heading
    assert "Quá hạn" in html                         # reason rendered
    assert "<ol>" in html                            # numbered list

def test_render_subtasks_listed():
    it = mk_item(SignalType.JIRA_IN_PROGRESS, url="https://j/STORY-1", title="STORY-1")
    it.category = Category.WAITING
    it.source = "jira"
    it.subtasks = ["STORY-1-1: Write API"]
    r = DigestReport(date="2026-06-15", user_name="T", waiting=[it])
    html = render_html(r)
    assert "Cần làm" in html
    assert "<li>STORY-1-1: Write API</li>" in html
