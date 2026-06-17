import httpx
from datetime import date
from connectors.jira import JiraAdapter
from digest.models import SignalType
from digest.ruledefs import RuleSet

def _split(*, cs=None, sprint=None, seen=None):
    """CS query (project ISSUE) vs task query (sprint in openSprints()) trả về set khác nhau."""
    def handler(request):
        if seen is not None:
            seen.append(str(request.url))
        issues = (sprint or []) if "openSprints" in str(request.url) else (cs or [])
        return httpx.Response(200, json={"issues": issues})
    return httpx.MockTransport(handler)

async def test_overdue_and_inprogress():
    seen = []
    tasks = [
        {"key": "PROJ-1", "fields": {"summary": "Overdue", "duedate": "2026-06-10",
            "status": {"statusCategory": {"key": "indeterminate"}}}},
        {"key": "PROJ-2", "fields": {"summary": "Doing", "duedate": None,
            "status": {"statusCategory": {"key": "indeterminate"}}}}]
    adapter = JiraAdapter("https://j", "tok", transport=_split(sprint=tasks, seen=seen))
    items = await adapter.fetch_items(today=date(2026, 6, 15))
    assert any("updated" in u for u in seen)        # CS query giới hạn window
    assert any("openSprints" in u for u in seen)    # task query giới hạn active sprint
    by_key = {i.id: i.signal for i in items}
    assert by_key["jira:PROJ-1"] == SignalType.JIRA_OVERDUE
    assert by_key["jira:PROJ-2"] == SignalType.JIRA_IN_PROGRESS

async def test_cloud_uses_enhanced_jql_endpoint():
    """Jira Cloud removed GET /rest/api/2/search (410 Gone, CHANGE-2046).
    Cloud (*.atlassian.net) must hit the enhanced /rest/api/2/search/jql endpoint."""
    seen = []
    adapter = JiraAdapter("https://acme.atlassian.net", "tok", email="me@x.com",
                          transport=_split(cs=[], sprint=[], seen=seen))
    await adapter.fetch_items(today=date(2026, 6, 15))
    assert seen, "expected at least one Jira request"
    paths = [u.split("?")[0] for u in seen]
    assert all(p.endswith("/rest/api/2/search/jql") for p in paths), paths


async def test_server_dc_keeps_classic_search_endpoint():
    """Server/DC has no enhanced endpoint — the classic /rest/api/2/search still works there."""
    seen = []
    adapter = JiraAdapter("https://jira.internal.example", "tok",
                          transport=_split(cs=[], sprint=[], seen=seen))
    await adapter.fetch_items(today=date(2026, 6, 15))
    assert seen, "expected at least one Jira request"
    paths = [u.split("?")[0] for u in seen]
    assert all(p.endswith("/rest/api/2/search") for p in paths), paths


def test_task_jql_is_sprint_scoped_cs_is_not():
    adapter = JiraAdapter("https://j", "tok", transport=_split())
    cs_jql, task_jql = adapter._jqls()
    # Project keys MUST be quoted: 'IS' is a JQL reserved word (IS EMPTY / IS NULL),
    # so an unquoted `project in (IS)` is a 400 JQL syntax error on Jira.
    assert 'project in ("IS")' in cs_jql and "openSprints" not in cs_jql
    assert "openSprints()" in task_jql and 'project not in ("IS")' in task_jql


_CS = {"key": "IS-1234", "fields": {"summary": "CS", "duedate": None,
    "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
    "description": "KH 0901234567 email kh@x.com CCCD 012345678901 cần hỗ trợ"}}
_TASKS = [
    {"key": "PROJ-9", "fields": {"summary": "New one", "duedate": None,
        "status": {"name": "New", "statusCategory": {"key": "new"}}}},
    {"key": "DEV-3", "fields": {"summary": "Dev task", "duedate": None,
        "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
        "comment": {"comments": [
            {"author": {"displayName": "Alice"}, "body": "Cần thêm test"},
            {"author": {"displayName": "Bob"}, "body": "Đã review"}]}}}]

def _rules_adapter():
    return JiraAdapter("https://j", "tok", transport=_split(cs=[_CS], sprint=_TASKS))

async def test_new_status_now_shown():
    items = await _rules_adapter().fetch_items(today=date(2026, 6, 15))
    proj9 = next(i for i in items if i.id == "jira:PROJ-9")   # NEW (xa due) vẫn hiển thị
    assert proj9.signal == SignalType.JIRA_OTHER             # FYI, không alert

async def test_due_soon_alerts():
    near = {"key": "DEV-5", "fields": {"summary": "Chưa làm", "duedate": "2026-06-17",
        "status": {"name": "New", "statusCategory": {"key": "new"}}}}   # còn 2 ngày
    far = {"key": "DEV-6", "fields": {"summary": "Còn xa", "duedate": "2026-06-30",
        "status": {"name": "New", "statusCategory": {"key": "new"}}}}
    adapter = JiraAdapter("https://j", "tok", transport=_split(sprint=[near, far]))
    items = await adapter.fetch_items(today=date(2026, 6, 15))
    by_key = {i.id: i.signal for i in items}
    assert by_key["jira:DEV-5"] == SignalType.JIRA_DUE_SOON       # ≤2 ngày → alert
    assert by_key["jira:DEV-6"] == SignalType.JIRA_OTHER          # >2 ngày → FYI


async def test_due_soon_applies_to_non_new_tickets():
    """Pure duedate: ANY non-CS ticket within duedate_alert_days alerts, not just NEW ones.
    An In Progress ticket due in 2 days → JIRA_DUE_SOON (was JIRA_IN_PROGRESS before)."""
    doing = {"key": "DEV-8", "fields": {"summary": "Đang làm, sắp hạn", "duedate": "2026-06-17",
        "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}}}}
    adapter = JiraAdapter("https://j", "tok", transport=_split(sprint=[doing]))
    items = await adapter.fetch_items(today=date(2026, 6, 15))
    assert next(i for i in items if i.id == "jira:DEV-8").signal == SignalType.JIRA_DUE_SOON

async def test_cs_ticket_pinned_and_pii_redacted():
    items = await _rules_adapter().fetch_items(today=date(2026, 6, 15))
    cs = next(i for i in items if i.id == "jira:IS-1234")
    assert cs.pinned is True
    assert "0901234567" not in cs.detail and "[PHONE]" in cs.detail
    assert "kh@x.com" not in cs.detail and "[EMAIL]" in cs.detail
    assert "012345678901" not in cs.detail and "[CCCD]" in cs.detail

async def test_non_cs_collects_comments():
    items = await _rules_adapter().fetch_items(today=date(2026, 6, 15))
    dev = next(i for i in items if i.id == "jira:DEV-3")
    assert dev.pinned is False
    assert "Alice: Cần thêm test" in dev.detail
    assert "Bob: Đã review" in dev.detail


def _story(key, summary):
    return {"key": key, "fields": {"summary": summary, "duedate": None,
        "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
        "issuetype": {"name": "Story", "subtask": False}}}

def _subtask(key, summary, parent_key):
    return {"key": key, "fields": {"summary": summary, "duedate": None,
        "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
        "issuetype": {"name": "Sub-task", "subtask": True},
        "parent": {"key": parent_key}}}

async def test_subtask_folds_into_in_sprint_parent():
    sprint = [_story("STORY-1", "Build feature"),
              _subtask("STORY-1-1", "Write API", "STORY-1"),
              _subtask("STORY-1-2", "Write tests", "STORY-1")]
    adapter = JiraAdapter("https://j", "tok", transport=_split(sprint=sprint))
    items = await adapter.fetch_items(today=date(2026, 6, 15))
    assert {i.id for i in items} == {"jira:STORY-1"}     # subtask không đứng riêng
    assert items[0].subtasks == ["STORY-1-1: Write API", "STORY-1-2: Write tests"]

async def test_orphan_subtask_dropped_when_parent_not_in_sprint():
    # Story cha ngoài active sprint → không nằm trong kết quả → subtask bị bỏ.
    adapter = JiraAdapter("https://j", "tok",
                          transport=_split(sprint=[_subtask("STORY-9-1", "Orphan", "STORY-9")]))
    items = await adapter.fetch_items(today=date(2026, 6, 15))
    assert items == []

async def test_task_type_still_shown():
    task = {"key": "DEV-7", "fields": {"summary": "A task", "duedate": None,
        "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
        "issuetype": {"name": "Task", "subtask": False}}}
    adapter = JiraAdapter("https://j", "tok", transport=_split(sprint=[task]))
    items = await adapter.fetch_items(today=date(2026, 6, 15))
    assert any(i.id == "jira:DEV-7" for i in items)   # Task type (trong sprint) vẫn hiển thị


async def test_combines_cs_and_task_cs_first():
    """Both JQLs are fetched concurrently and merged with CS results before task results.
    Guards the parallel (asyncio.gather) fetch against reordering the combined items."""
    cs = [{"key": "IS-1", "fields": {"summary": "cs", "duedate": None,
        "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
        "description": "d"}}]
    task = [{"key": "DEV-1", "fields": {"summary": "task", "duedate": None,
        "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}}}}]
    seen = []
    adapter = JiraAdapter("https://j", "tok", transport=_split(cs=cs, sprint=task, seen=seen))
    items = await adapter.fetch_items(today=date(2026, 6, 15))
    assert [i.id for i in items] == ["jira:IS-1", "jira:DEV-1"]   # CS first, then task
    assert len(seen) == 2                                          # both JQLs dispatched


def test_priority_in_fields():
    adapter = JiraAdapter("https://j", "tok", transport=_split())
    assert "priority" in adapter._fields()


async def test_build_item_maps_priority():
    withp = {"key": "DEV-20", "fields": {"summary": "Has priority", "duedate": None,
        "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
        "priority": {"name": "High"}}}
    without = {"key": "DEV-21", "fields": {"summary": "No priority", "duedate": None,
        "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}}}}
    adapter = JiraAdapter("https://j", "tok", transport=_split(sprint=[withp, without]))
    items = await adapter.fetch_items(today=date(2026, 6, 15))
    by_key = {i.id: i for i in items}
    assert by_key["jira:DEV-20"].priority == "High"
    assert by_key["jira:DEV-21"].priority is None


def _rs(**kwargs) -> RuleSet:
    """Build a minimal RuleSet with overrides for testing duedate-based rules."""
    base = dict(skip_statuses=frozenset(), cs_projects=frozenset(["ISSUE"]),
                comment_limit=3, duedate_alert_days=2, sla_alert_days=1,
                sla_date_field="")
    base.update(kwargs)
    return RuleSet(**base)


async def test_non_cs_overdue_uses_duedate():
    """Non-CS overdue signal is driven by the plain duedate field."""
    od = {"key": "DEV-10", "fields": {
        "summary": "Quá hạn", "duedate": "2026-06-14",   # yesterday → overdue
        "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}}}}
    adapter = JiraAdapter("https://j", "tok", transport=_split(sprint=[od]))
    items = await adapter.fetch_items(today=date(2026, 6, 15))
    it = next(i for i in items if i.id == "jira:DEV-10")
    assert it.signal == SignalType.JIRA_OVERDUE
    assert it.due == date(2026, 6, 14)


async def test_non_cs_due_soon_threshold():
    """duedate_alert_days bounds the due-soon alert: within → JIRA_DUE_SOON, beyond → not."""
    near = {"key": "DEV-11", "fields": {"summary": "Sắp hạn", "duedate": "2026-06-17",  # 2 days
        "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}}}}
    far = {"key": "DEV-12", "fields": {"summary": "Còn xa", "duedate": "2026-06-30",    # 15 days
        "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}}}}
    adapter = JiraAdapter("https://j", "tok", transport=_split(sprint=[near, far]))
    adapter._rules = _rs(duedate_alert_days=2)
    items = await adapter.fetch_items(today=date(2026, 6, 15))
    by_key = {i.id: i.signal for i in items}
    assert by_key["jira:DEV-11"] == SignalType.JIRA_DUE_SOON       # ≤2 ngày → alert
    assert by_key["jira:DEV-12"] == SignalType.JIRA_IN_PROGRESS    # >2 ngày → đang làm


async def test_cs_sla_alert_within_1_day():
    """CS ticket with SLA Date within 1 day triggers JIRA_CS_SLA_DUE_SOON."""
    cs_near = {"key": "ISSUE-5", "fields": {
        "summary": "CS SLA soon", "duedate": "2026-12-31",
        "customfield_10002": "2026-06-15",   # today → 0 days left → alert
        "description": "Need help",
        "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}}}}
    cs_far = {"key": "ISSUE-6", "fields": {
        "summary": "CS SLA far", "duedate": None,
        "customfield_10002": "2026-06-30",   # 15 days away → no alert
        "description": "Low urgency",
        "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}}}}
    adapter = JiraAdapter("https://j", "tok", transport=_split(cs=[cs_near, cs_far]))
    adapter._rules = _rs(sla_date_field="customfield_10002", sla_alert_days=1)
    items = await adapter.fetch_items(today=date(2026, 6, 15))
    by_key = {i.id: i for i in items}
    assert by_key["jira:ISSUE-5"].signal == SignalType.JIRA_CS_SLA_DUE_SOON
    assert by_key["jira:ISSUE-6"].signal == SignalType.JIRA_IN_PROGRESS


async def test_cs_sorts_by_sla_date():
    """CS tickets with sla_date_field are sorted by SLA date (earliest first)."""
    cs1 = {"key": "ISSUE-10", "fields": {
        "summary": "Later SLA", "duedate": None,
        "customfield_10002": "2026-06-20",
        "description": "d", "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}}}}
    cs2 = {"key": "ISSUE-11", "fields": {
        "summary": "Earlier SLA", "duedate": None,
        "customfield_10002": "2026-06-16",
        "description": "d", "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}}}}
    from digest import rules
    adapter = JiraAdapter("https://j", "tok", transport=_split(cs=[cs1, cs2]))
    adapter._rules = _rs(sla_date_field="customfield_10002")
    items = await adapter.fetch_items(today=date(2026, 6, 15))
    report = rules.build_report(items, date="2026-06-15", user_name="T")
    assert [i.id for i in report.action] == ["jira:ISSUE-11", "jira:ISSUE-10"]
