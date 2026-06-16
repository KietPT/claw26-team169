import httpx
from connectors.gitlab import GitLabAdapter
from digest.models import SignalType

def _handler(request):
    p = request.url.path
    if p.endswith("/user"):
        return httpx.Response(200, json={"username": "thinh"})
    if "reviewer_username" in str(request.url):
        return httpx.Response(200, json=[{"iid":1,"references":{"full":"g!1"},"title":"Fix",
            "web_url":"https://g/mr/1","project_id":7}])
    if "author_username" in str(request.url):
        return httpx.Response(200, json=[{"iid":2,"references":{"full":"g!2"},"title":"Mine",
            "web_url":"https://g/mr/2","project_id":7,"user_notes_count":3,
            "pipeline":{"status":"failed"}}])
    if p.endswith("/issues"):
        return httpx.Response(200, json=[
            {"id":101,"references":{"full":"g#101"},"title":"Overdue",
             "web_url":"https://g/issue/101","due_date":"2026-01-01"},
            {"id":102,"references":{"full":"g#102"},"title":"Future",
             "web_url":"https://g/issue/102","due_date":"2099-01-01"}])
    if "discussions" in p:
        return httpx.Response(200, json=[])
    return httpx.Response(200, json=[])

async def test_fetch_items_maps_signals():
    seen = []
    def handler(request):
        seen.append(str(request.url))
        return _handler(request)
    transport = httpx.MockTransport(handler)
    adapter = GitLabAdapter("https://g", "tok", transport=transport)
    items = await adapter.fetch_items()
    # only fetch items updated within the last 30 days
    assert any("updated_after" in u for u in seen if "merge_requests" in u)
    assert any("updated_after" in u for u in seen if "/issues" in u)
    signals = {i.signal for i in items}
    assert SignalType.MR_NEEDS_MY_REVIEW in signals
    assert SignalType.MR_MINE_PIPELINE_FAILED in signals
    due = [i for i in items if i.signal == SignalType.ISSUE_ASSIGNED_DUE]
    assert len(due) == 1
    assert due[0].id == "gitlab:issue:101"


def _disc(username, name, body, resolved, *, resolvable=True, system=False):
    return {"notes": [{"system": system, "resolvable": resolvable, "resolved": resolved,
                       "body": body, "author": {"name": name, "username": username}}]}

def _threads_handler(request):
    p = request.url.path
    if p.endswith("/user"):
        return httpx.Response(200, json={"username": "thinh"})
    if "reviewer_username" in str(request.url):
        return httpx.Response(200, json=[{"iid": 1, "references": {"full": "g!1"}, "title": "Review me",
            "description": "desc", "web_url": "https://g/mr/1", "project_id": 7, "author": {"name": "Owner"}}])
    if "author_username" in str(request.url):
        return httpx.Response(200, json=[{"iid": 2, "references": {"full": "g!2"}, "title": "Mine",
            "web_url": "https://g/mr/2", "project_id": 7, "user_notes_count": 5}])
    if p.endswith("/issues"):
        return httpx.Response(200, json=[])
    if "/merge_requests/1/discussions" in p:
        return httpx.Response(200, json=[
            _disc("thinh", "Thinh", "Can doi ten bien", False),   # my unresolved
            _disc("thinh", "Thinh", "Da on", True),               # my resolved
            _disc("alice", "Alice", "Thieu test", False),         # other unresolved
        ])
    if "/merge_requests/2/discussions" in p:
        return httpx.Response(200, json=[
            _disc("alice", "Alice", "Fix loi 1", False),
            _disc("bob", "Bob", "Fix loi 2", False),
            _disc("carol", "Carol", "Fix loi 3", False),
            _disc("dave", "Dave", "Fix loi 4", False),
            _disc("erin", "Erin", "Done roi", True),
        ])
    if "discussions" in p:
        return httpx.Response(200, json=[])
    return httpx.Response(200, json=[])

async def test_reviewing_mr_lists_only_my_unresolved_threads():
    adapter = GitLabAdapter("https://g", "tok", transport=httpx.MockTransport(_threads_handler))
    items = await adapter.fetch_items()
    review = next(i for i in items if i.signal == SignalType.MR_NEEDS_MY_REVIEW)
    assert "Can doi ten bien" in review.detail   # my unresolved thread
    assert "Thieu test" not in review.detail      # someone else's thread excluded
    assert "Da on" not in review.detail           # my resolved thread excluded

async def test_authored_mr_lists_all_unresolved_threads_capped():
    adapter = GitLabAdapter("https://g", "tok", transport=httpx.MockTransport(_threads_handler))
    items = await adapter.fetch_items()
    mine = next(i for i in items if i.signal == SignalType.MR_MINE_COMMENTED)
    assert "Fix loi 1" in mine.detail and "Fix loi 3" in mine.detail
    assert "Fix loi 4" not in mine.detail         # beyond top 3
    assert "và 1 thread khác" in mine.detail       # overflow note
    assert "Done roi" not in mine.detail           # resolved excluded
