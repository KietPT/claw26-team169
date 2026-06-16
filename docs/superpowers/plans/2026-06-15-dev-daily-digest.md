# Dev Daily Digest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an agent that, at each user's configured time, pulls their GitLab + Jira work items, categorizes them by rule, enriches them with one LLM call, and delivers a clickable daily digest via Telegram and/or a local HTML file.

**Architecture:** Ports-and-adapters. Connectors (`GitLabPort`/`JiraPort`) fetch `DigestItem`s over REST (httpx) using per-user PATs (encrypted at rest with Fernet). A pure `rules` module categorizes/sorts/caps; one `summarize` LLM call adds per-item summaries, comment→actionable rewrites, a headline, and ACTION ordering (fallback-safe). A `DeliveryPort` renders to Telegram and/or HTML. Data is queried at digest time — no polling, no message store. Only users + encrypted tokens + prefs are persisted (aiosqlite).

**Tech Stack:** Python 3.13 · httpx · python-telegram-bot v21 · APScheduler (AsyncIOScheduler) · aiosqlite · openai (AsyncOpenAI) · cryptography (Fernet) · python-dotenv · pytest + pytest-asyncio.

**Working dir:** `/Users/thinhtran/Working/Training/claw-a-thon/dev-daily-digest/`

**Spec:** `docs/superpowers/specs/2026-06-15-dev-daily-digest-design.md`

---

## Shared interfaces (locked — referenced by all tasks)

```python
# digest/models.py
class SignalType(str, Enum):
    MR_NEEDS_MY_REVIEW = "mr_needs_my_review"
    MR_MINE_COMMENTED = "mr_mine_commented"
    MR_MINE_PIPELINE_FAILED = "mr_mine_pipeline_failed"
    MR_MINE_APPROVED = "mr_mine_approved"
    ISSUE_ASSIGNED_DUE = "issue_assigned_due"
    JIRA_OVERDUE = "jira_overdue"
    JIRA_IN_PROGRESS = "jira_in_progress"
    JIRA_OTHER = "jira_other"

class Category(str, Enum):
    ACTION = "action"     # 🔴 Cần xử lý
    WAITING = "waiting"   # 🟡 Đang chờ
    FYI = "fyi"           # 🔵 Để biết

@dataclass
class DigestItem:
    id: str                 # stable, e.g. "gitlab:mr:142"
    signal: SignalType
    title: str
    detail: str             # raw context for the LLM (may include comment threads)
    url: str
    source: str             # "gitlab" | "jira"
    category: Category = Category.FYI
    summary: str | None = None
    due: date | None = None # used for sorting (overdue first)

@dataclass
class DigestReport:
    date: str
    user_name: str
    action: list[DigestItem] = field(default_factory=list)
    waiting: list[DigestItem] = field(default_factory=list)
    fyi: list[DigestItem] = field(default_factory=list)
    headline: str = ""
    more: dict[str, int] = field(default_factory=dict)  # category.value -> overflow count
    errors: list[str] = field(default_factory=list)     # e.g. "Jira: token hết hạn"
```

Ports (`connectors/base.py`):
```python
class GitLabPort(Protocol):
    async def fetch_items(self) -> list[DigestItem]: ...
class JiraPort(Protocol):
    async def fetch_items(self) -> list[DigestItem]: ...
class DeliveryPort(Protocol):
    async def deliver(self, report: DigestReport) -> None: ...
```

---

## Task 0: Scaffold + tooling

**Files:**
- Create: `pyproject.toml`, `.env.example`, all package `__init__.py` (`connectors/`, `digest/`, `delivery/`, `store/`, `telegram/`, `tests/`)

- [ ] **Step 1: git init + dirs**

```bash
cd /Users/thinhtran/Working/Training/claw-a-thon/dev-daily-digest
git init
mkdir -p connectors digest delivery store telegram tests reports
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "dev-daily-digest"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "httpx>=0.27",
    "python-telegram-bot>=21,<23",
    "apscheduler>=3.10,<4",
    "aiosqlite>=0.20",
    "openai>=1.40",
    "cryptography>=43",
    "python-dotenv>=1.0",
]
[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "pytest-cov"]

[tool.setuptools]
packages = ["connectors", "digest", "delivery", "store", "telegram"]
py-modules = ["config", "main", "scheduler"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 3: Write `.env.example`**

```
GITLAB_BASE_URL=https://git.company.vn
JIRA_BASE_URL=https://jira.company.vn
DELIVERY_CHANNEL=telegram
DEFAULT_DIGEST_TIME=17:00
TIMEZONE=Asia/Ho_Chi_Minh
TELEGRAM_BOT_TOKEN=
ENCRYPTION_KEY=
REPORT_DIR=./reports
DATABASE_PATH=./data/digest.db
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=
LLM_MODEL=gpt-4o-mini
# html single-user mode only:
GITLAB_TOKEN=
JIRA_TOKEN=
```

- [ ] **Step 4: Create empty `__init__.py`** in `connectors/ digest/ delivery/ store/ telegram/ tests/` (each: one-line docstring).

- [ ] **Step 5: Install + commit**

```bash
python3.13 -m venv .venv && .venv/bin/pip install -e ".[dev]"
git add -A && git commit -m "chore: scaffold dev-daily-digest"
```
Expected: install succeeds (verify `cryptography`/`python-telegram-bot` wheels resolve on 3.13).

---

## Task 1: Token encryption (`store/crypto.py`)

**Files:** Create `store/crypto.py`, `tests/test_crypto.py`

- [ ] **Step 1: Failing test**
```python
# tests/test_crypto.py
from store.crypto import TokenCipher, generate_key

def test_encrypt_decrypt_round_trip():
    cipher = TokenCipher(generate_key())
    token = cipher.encrypt("glpat-secret-123")
    assert token != b"glpat-secret-123"
    assert cipher.decrypt(token) == "glpat-secret-123"

def test_none_passthrough():
    cipher = TokenCipher(generate_key())
    assert cipher.decrypt(None) is None
    assert cipher.encrypt(None) is None
```

- [ ] **Step 2: Run, expect fail** — `pytest tests/test_crypto.py -v` → ImportError.

- [ ] **Step 3: Implement `store/crypto.py`**
```python
"""Symmetric encryption of per-user PATs at rest (Fernet)."""
from __future__ import annotations
from cryptography.fernet import Fernet

def generate_key() -> str:
    return Fernet.generate_key().decode()

class TokenCipher:
    def __init__(self, key: str) -> None:
        self._f = Fernet(key.encode() if isinstance(key, str) else key)
    def encrypt(self, plaintext: str | None) -> bytes | None:
        return self._f.encrypt(plaintext.encode()) if plaintext else None
    def decrypt(self, token: bytes | None) -> str | None:
        return self._f.decrypt(token).decode() if token else None
```

- [ ] **Step 4: Run, expect pass.**
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: Fernet token cipher"`

---

## Task 2: Domain models (`digest/models.py`)

**Files:** Create `digest/models.py`, `tests/test_models.py`

- [ ] **Step 1: Failing test**
```python
# tests/test_models.py
from digest.models import DigestItem, DigestReport, Category, SignalType

def test_item_defaults():
    it = DigestItem(id="gitlab:mr:1", signal=SignalType.MR_NEEDS_MY_REVIEW,
                    title="MR !1", detail="d", url="u", source="gitlab")
    assert it.category is Category.FYI and it.summary is None

def test_report_defaults():
    r = DigestReport(date="2026-06-15", user_name="Thinh")
    assert r.action == [] and r.headline == "" and r.more == {}
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement** `digest/models.py` exactly as the "Shared interfaces" block above (with `from __future__ import annotations`, `dataclass`, `field`, `Enum`, `date`).
- [ ] **Step 4: Run, expect pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: digest domain models"`

---

## Task 3: Categorization rules (`digest/rules.py`) — core pure logic

**Files:** Create `digest/rules.py`, `tests/test_rules.py`

- [ ] **Step 1: Failing test**
```python
# tests/test_rules.py
from datetime import date
from digest.models import DigestItem, Category, SignalType
from digest import rules

def mk(signal, id="x", due=None):
    return DigestItem(id=id, signal=signal, title="t", detail="d", url="u",
                      source="gitlab", due=due)

def test_category_mapping():
    assert rules.categorize(mk(SignalType.MR_NEEDS_MY_REVIEW)) is Category.ACTION
    assert rules.categorize(mk(SignalType.MR_MINE_PIPELINE_FAILED)) is Category.ACTION
    assert rules.categorize(mk(SignalType.JIRA_OVERDUE)) is Category.ACTION
    assert rules.categorize(mk(SignalType.MR_MINE_COMMENTED)) is Category.WAITING
    assert rules.categorize(mk(SignalType.JIRA_IN_PROGRESS)) is Category.WAITING
    assert rules.categorize(mk(SignalType.MR_MINE_APPROVED)) is Category.FYI
    assert rules.categorize(mk(SignalType.JIRA_OTHER)) is Category.FYI

def test_group_and_cap_overflow():
    items = [mk(SignalType.MR_NEEDS_MY_REVIEW, id=str(i)) for i in range(12)]
    report = rules.build_report(items, date="2026-06-15", user_name="Thinh", cap=10)
    assert len(report.action) == 10
    assert report.more["action"] == 2

def test_overdue_sorted_first():
    older = mk(SignalType.JIRA_OVERDUE, id="old", due=date(2026, 6, 10))
    newer = mk(SignalType.JIRA_OVERDUE, id="new", due=date(2026, 6, 14))
    report = rules.build_report([newer, older], date="2026-06-15", user_name="T")
    assert [i.id for i in report.action] == ["old", "new"]
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement `digest/rules.py`**
```python
"""Deterministic categorization, sorting and capping of digest items (spec §7)."""
from __future__ import annotations
from datetime import date as _date
from digest.models import Category, DigestItem, DigestReport, SignalType

CATEGORY_BY_SIGNAL: dict[SignalType, Category] = {
    SignalType.MR_NEEDS_MY_REVIEW: Category.ACTION,
    SignalType.MR_MINE_PIPELINE_FAILED: Category.ACTION,
    SignalType.JIRA_OVERDUE: Category.ACTION,
    SignalType.ISSUE_ASSIGNED_DUE: Category.ACTION,
    SignalType.MR_MINE_COMMENTED: Category.WAITING,
    SignalType.JIRA_IN_PROGRESS: Category.WAITING,
    SignalType.MR_MINE_APPROVED: Category.FYI,
    SignalType.JIRA_OTHER: Category.FYI,
}

def categorize(item: DigestItem) -> Category:
    return CATEGORY_BY_SIGNAL.get(item.signal, Category.FYI)

def _sort_key(item: DigestItem) -> tuple:
    # earlier due dates first; items without due date last
    return (0, item.due) if item.due else (1, _date.max)

def build_report(items: list[DigestItem], *, date: str, user_name: str, cap: int = 10) -> DigestReport:
    buckets: dict[Category, list[DigestItem]] = {c: [] for c in Category}
    for it in items:
        it.category = categorize(it)
        buckets[it.category].append(it)
    report = DigestReport(date=date, user_name=user_name)
    for cat, target in ((Category.ACTION, "action"), (Category.WAITING, "waiting"), (Category.FYI, "fyi")):
        ordered = sorted(buckets[cat], key=_sort_key)
        if len(ordered) > cap:
            report.more[cat.value] = len(ordered) - cap
        setattr(report, target, ordered[:cap])
    return report
```

- [ ] **Step 4: Run, expect pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: digest categorization rules"`

---

## Task 4: User store (`store/models.py`, `store/db.py`)

**Files:** Create `store/models.py`, `store/db.py`, `tests/test_store.py`

- [ ] **Step 1: Failing test**
```python
# tests/test_store.py
import os, tempfile
from store.db import Store
from store.models import User

async def _store():
    s = Store(os.path.join(tempfile.mkdtemp(), "t.db")); await s.init_db(); return s

async def test_upsert_and_get():
    s = await _store()
    await s.upsert_user(User(telegram_chat_id="42", display_name="Thinh",
                             gitlab_token_enc=b"x", jira_token_enc=None))
    u = await s.get_user("42")
    assert u.display_name == "Thinh" and u.gitlab_token_enc == b"x"

async def test_list_active_excludes_paused():
    s = await _store()
    await s.upsert_user(User(telegram_chat_id="1", display_name="A", active=True))
    await s.upsert_user(User(telegram_chat_id="2", display_name="B", active=False))
    ids = {u.telegram_chat_id for u in await s.list_active_users()}
    assert ids == {"1"}
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement `store/models.py`**
```python
"""Persisted entities."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class User:
    telegram_chat_id: str
    display_name: str = ""
    gitlab_token_enc: bytes | None = None
    jira_token_enc: bytes | None = None
    digest_time: str = "17:00"
    timezone: str = "Asia/Ho_Chi_Minh"
    delivery_channel: str = "telegram"  # telegram | html | both
    active: bool = True
```

- [ ] **Step 4: Implement `store/db.py`**
```python
"""aiosqlite store for users (spec §6)."""
from __future__ import annotations
import os
import aiosqlite
from store.models import User

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_chat_id TEXT PRIMARY KEY,
    display_name TEXT DEFAULT '',
    gitlab_token_enc BLOB,
    jira_token_enc BLOB,
    digest_time TEXT DEFAULT '17:00',
    timezone TEXT DEFAULT 'Asia/Ho_Chi_Minh',
    delivery_channel TEXT DEFAULT 'telegram',
    active INTEGER DEFAULT 1
);
"""

class Store:
    def __init__(self, path: str) -> None:
        self._path = path
    async def init_db(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self._path)) or ".", exist_ok=True)
        async with aiosqlite.connect(self._path) as db:
            await db.executescript(SCHEMA); await db.commit()
    async def upsert_user(self, u: User) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                """INSERT INTO users (telegram_chat_id, display_name, gitlab_token_enc,
                     jira_token_enc, digest_time, timezone, delivery_channel, active)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(telegram_chat_id) DO UPDATE SET
                     display_name=excluded.display_name,
                     gitlab_token_enc=excluded.gitlab_token_enc,
                     jira_token_enc=excluded.jira_token_enc,
                     digest_time=excluded.digest_time, timezone=excluded.timezone,
                     delivery_channel=excluded.delivery_channel, active=excluded.active""",
                (u.telegram_chat_id, u.display_name, u.gitlab_token_enc, u.jira_token_enc,
                 u.digest_time, u.timezone, u.delivery_channel, 1 if u.active else 0))
            await db.commit()
    async def get_user(self, chat_id: str) -> User | None:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM users WHERE telegram_chat_id=?", (chat_id,))
            row = await cur.fetchone()
        return _row(row) if row else None
    async def list_active_users(self) -> list[User]:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM users WHERE active=1")
            rows = await cur.fetchall()
        return [_row(r) for r in rows]

def _row(r: aiosqlite.Row) -> User:
    return User(telegram_chat_id=r["telegram_chat_id"], display_name=r["display_name"],
                gitlab_token_enc=r["gitlab_token_enc"], jira_token_enc=r["jira_token_enc"],
                digest_time=r["digest_time"], timezone=r["timezone"],
                delivery_channel=r["delivery_channel"], active=bool(r["active"]))
```

- [ ] **Step 5: Run tests, expect pass. Commit** — `git commit -am "feat: user store"`

---

## Task 5: Connector ports + fakes (`connectors/base.py`, `tests/conftest.py`)

**Files:** Create `connectors/base.py`, `tests/conftest.py`

- [ ] **Step 1: Implement `connectors/base.py`**
```python
"""Ports for data sources and delivery (spec §3)."""
from __future__ import annotations
from typing import Protocol
from digest.models import DigestItem, DigestReport

class GitLabPort(Protocol):
    async def fetch_items(self) -> list[DigestItem]: ...
class JiraPort(Protocol):
    async def fetch_items(self) -> list[DigestItem]: ...
class DeliveryPort(Protocol):
    async def deliver(self, report: DigestReport) -> None: ...
```

- [ ] **Step 2: Implement `tests/conftest.py`**
```python
from digest.models import DigestItem, SignalType

class FakeSource:
    def __init__(self, items=None, error: Exception | None = None):
        self._items = items or []; self._error = error
    async def fetch_items(self):
        if self._error: raise self._error
        return self._items

def mk_item(signal, id="gitlab:mr:1", title="MR !1", detail="d", url="u", source="gitlab", due=None):
    return DigestItem(id=id, signal=signal, title=title, detail=detail, url=url, source=source, due=due)
```

- [ ] **Step 3: Commit** — `git commit -am "feat: connector ports + test fakes"`

---

## Task 6: Summarize (LLM 4-in-1, fallback-safe) (`digest/summarize.py`)

**Files:** Create `digest/summarize.py`, `tests/test_summarize.py`

LLM contract: input = report items (id, title, detail, category); output JSON
`{"headline": str, "summaries": {id: str}, "action_order": [id,...]}`. Applies summaries,
sets `report.headline`, reorders `report.action` by `action_order`. On any error → fallback:
`summary = detail[:140]`, `headline = ""`, action order unchanged.

- [ ] **Step 1: Failing test**
```python
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
    assert r.headline == ""

async def test_applies_llm_output():
    item = mk_item(SignalType.MR_NEEDS_MY_REVIEW, id="a")
    r = DigestReport(date="2026-06-15", user_name="T", action=[item])
    content = '{"headline":"Tập trung review","summaries":{"a":"Review MR !1"},"action_order":["a"]}'
    await summarize.enrich(r, llm=StubLLM(content=content), model="m")
    assert r.headline == "Tập trung review"
    assert r.action[0].summary == "Review MR !1"
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement `digest/summarize.py`**
```python
"""Single LLM call enriching a DigestReport: per-item summary, comment→actionable,
headline, ACTION ordering. Fallback-safe (spec §3, §13)."""
from __future__ import annotations
import json, logging
from digest.models import DigestReport
logger = logging.getLogger(__name__)

PROMPT = """Bạn tóm tắt digest công việc cho dev (tiếng Việt, ngắn gọn).
Headline: 1 câu "hôm nay tập trung gì".
Với mỗi mục: 1 câu tóm tắt; nếu detail có comment review thì viết rõ "reviewer yêu cầu gì".
action_order: sắp xếp lại id nhóm ACTION theo độ ưu tiên xử lý.
Mục (JSON): {items}
Trả về DUY NHẤT JSON: {{"headline":"...","summaries":{{"<id>":"..."}},"action_order":["<id>"]}}"""

def _all(report: DigestReport):
    return [*report.action, *report.waiting, *report.fyi]

def _fallback(report: DigestReport) -> None:
    for it in _all(report):
        if not it.summary:
            it.summary = (it.detail or it.title)[:140]

async def enrich(report: DigestReport, *, llm, model: str) -> None:
    items = [{"id": i.id, "title": i.title, "detail": i.detail[:500],
              "category": i.category.value} for i in _all(report)]
    try:
        resp = await llm.chat.completions.create(
            model=model, temperature=0, response_format={"type": "json_object"},
            messages=[{"role": "user", "content": PROMPT.format(items=json.dumps(items, ensure_ascii=False))}])
        data = json.loads(resp.choices[0].message.content or "{}")
        summaries = data.get("summaries", {}) or {}
        for it in _all(report):
            s = summaries.get(it.id)
            if isinstance(s, str) and s.strip():
                it.summary = s.strip()
        report.headline = str(data.get("headline") or "").strip()
        order = data.get("action_order") or []
        if order:
            by_id = {i.id: i for i in report.action}
            report.action = [by_id[i] for i in order if i in by_id] + \
                            [i for i in report.action if i.id not in set(order)]
    except Exception as exc:  # never break the digest
        logger.warning("summarize failed: %s", exc)
    _fallback(report)
```

- [ ] **Step 4: Run, expect pass. Step 5: Commit** — `git commit -am "feat: LLM summarize enrichment"`

---

## Task 7: Builder (`digest/builder.py`)

**Files:** Create `digest/builder.py`, `tests/test_builder.py`

- [ ] **Step 1: Failing test**
```python
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
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement `digest/builder.py`**
```python
"""Orchestrate: fetch sources (parallel, fault-tolerant) → rules → summarize → report."""
from __future__ import annotations
import asyncio, logging
from datetime import datetime
from connectors.base import GitLabPort, JiraPort
from digest import rules, summarize
from digest.models import DigestItem, DigestReport
logger = logging.getLogger(__name__)

async def _safe(source, label: str, errors: list[str]) -> list[DigestItem]:
    if source is None:
        return []
    try:
        return list(await source.fetch_items())
    except Exception as exc:
        logger.warning("%s fetch failed: %s", label, exc)
        errors.append(f"{label}: {exc}")
        return []

async def build_digest(*, gitlab: GitLabPort | None, jira: JiraPort | None,
                       user_name: str, llm, model: str, now: datetime, cap: int = 10) -> DigestReport:
    errors: list[str] = []
    gl_items, jr_items = await asyncio.gather(
        _safe(gitlab, "GitLab", errors), _safe(jira, "Jira", errors))
    report = rules.build_report([*gl_items, *jr_items],
                                date=now.date().isoformat(), user_name=user_name, cap=cap)
    report.errors = errors
    await summarize.enrich(report, llm=llm, model=model)
    return report
```

- [ ] **Step 4: Run, expect pass. Step 5: Commit** — `git commit -am "feat: digest builder"`

---

## Task 8: HTML delivery (`delivery/base.py`, `delivery/html.py`)

**Files:** Create `delivery/base.py` (re-export DeliveryPort), `delivery/html.py`, `tests/test_html.py`

- [ ] **Step 1: Failing test**
```python
# tests/test_html.py
from digest.models import DigestReport, SignalType
from delivery.html import render_html
from tests.conftest import mk_item

def test_render_contains_links_and_sections():
    r = DigestReport(date="2026-06-15", user_name="Thinh", headline="Focus",
                     action=[mk_item(SignalType.MR_NEEDS_MY_REVIEW, url="https://git/mr/1", title="MR !1")])
    html = render_html(r)
    assert "Focus" in html
    assert 'href="https://git/mr/1"' in html
    assert "Cần xử lý" in html
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement `delivery/base.py`**
```python
"""Delivery port (re-export)."""
from connectors.base import DeliveryPort  # noqa: F401
```

- [ ] **Step 4: Implement `delivery/html.py`**
```python
"""Render a DigestReport to a self-contained HTML file (spec §9)."""
from __future__ import annotations
import html as _html, os, webbrowser
from digest.models import Category, DigestItem, DigestReport

_SECTIONS = [("action", "🔴 Cần xử lý"), ("waiting", "🟡 Đang chờ"), ("fyi", "🔵 Để biết")]

def _item_html(it: DigestItem) -> str:
    summ = _html.escape(it.summary or it.title)
    return (f'<li><a href="{_html.escape(it.url)}">{_html.escape(it.title)}</a>'
            f'<div class="s">{summ}</div></li>')

def render_html(report: DigestReport) -> str:
    parts = ['<!doctype html><meta charset="utf-8">',
             '<style>body{font-family:sans-serif;max-width:680px;margin:24px auto}'
             'h1{font-size:20px}h2{font-size:16px;margin-top:20px}.s{color:#555;font-size:13px}'
             'li{margin:8px 0}.hl{background:#eef;padding:8px;border-radius:6px}</style>',
             f"<h1>Báo cáo của {_html.escape(report.user_name)} — {report.date}</h1>"]
    if report.headline:
        parts.append(f'<p class="hl">{_html.escape(report.headline)}</p>')
    for attr, title in _SECTIONS:
        items = getattr(report, attr)
        if not items:
            continue
        parts.append(f"<h2>{title} ({len(items)})</h2><ul>")
        parts.extend(_item_html(i) for i in items)
        parts.append("</ul>")
        if report.more.get(attr):
            parts.append(f'<p class="s">… còn {report.more[attr]} mục khác</p>')
    for e in report.errors:
        parts.append(f'<p class="s">⚠️ {_html.escape(e)}</p>')
    return "".join(parts)

class HtmlDelivery:
    def __init__(self, report_dir: str, user_key: str, *, open_browser: bool = False) -> None:
        self._dir = report_dir; self._key = user_key; self._open = open_browser
    async def deliver(self, report: DigestReport) -> None:
        os.makedirs(self._dir, exist_ok=True)
        path = os.path.join(self._dir, f"{self._key}-{report.date}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(render_html(report))
        print(f"HTML digest: {path}")
        if self._open:
            webbrowser.open(f"file://{os.path.abspath(path)}")
```

- [ ] **Step 5: Run test, expect pass. Commit** — `git commit -am "feat: HTML delivery"`

---

## Task 9: Telegram delivery (`delivery/telegram.py`)

**Files:** Create `delivery/telegram.py`, `tests/test_telegram_render.py`

- [ ] **Step 1: Failing test (pure render only)**
```python
# tests/test_telegram_render.py
from digest.models import DigestReport, SignalType
from delivery.telegram import render_message
from tests.conftest import mk_item

def test_render_message_has_links():
    r = DigestReport(date="2026-06-15", user_name="T", headline="Focus",
                     action=[mk_item(SignalType.MR_NEEDS_MY_REVIEW, url="https://g/mr/1", title="MR !1")])
    text = render_message(r)
    assert "Focus" in text
    assert '<a href="https://g/mr/1">' in text
    assert "Cần xử lý" in text
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement `delivery/telegram.py`**
```python
"""Render + send a DigestReport via Telegram (HTML parse mode)."""
from __future__ import annotations
import html as _html
from digest.models import DigestItem, DigestReport

_SECTIONS = [("action", "🔴 Cần xử lý"), ("waiting", "🟡 Đang chờ"), ("fyi", "🔵 Để biết")]

def _line(it: DigestItem) -> str:
    summ = _html.escape(it.summary or "")
    title = f'<a href="{_html.escape(it.url)}">{_html.escape(it.title)}</a>'
    return f"• {title}\n  {summ}" if summ else f"• {title}"

def render_message(report: DigestReport) -> str:
    out = [f"<b>Báo cáo {report.date}</b>"]
    if report.headline:
        out.append(f"🎯 {_html.escape(report.headline)}")
    for attr, title in _SECTIONS:
        items = getattr(report, attr)
        if not items:
            continue
        out.append(f"\n<b>{title} ({len(items)})</b>")
        out.extend(_line(i) for i in items)
        if report.more.get(attr):
            out.append(f"… còn {report.more[attr]} mục khác")
    for e in report.errors:
        out.append(f"⚠️ {_html.escape(e)}")
    return "\n".join(out)

class TelegramDelivery:
    def __init__(self, bot, chat_id: str) -> None:
        self._bot = bot; self._chat_id = chat_id
    async def deliver(self, report: DigestReport) -> None:
        await self._bot.send_message(chat_id=self._chat_id, text=render_message(report),
                                     parse_mode="HTML", disable_web_page_preview=True)
```

- [ ] **Step 4: Run test, expect pass. Commit** — `git commit -am "feat: Telegram delivery"`

---

## Task 10: GitLab connector (`connectors/gitlab.py`)

**Files:** Create `connectors/gitlab.py`, `tests/test_gitlab.py`

Test uses `httpx.MockTransport` (no network). Map: review MRs → MR_NEEDS_MY_REVIEW;
authored MRs → check `pipeline.status=='failed'`→MR_MINE_PIPELINE_FAILED, else
`user_notes_count>0`→MR_MINE_COMMENTED (and fetch discussions into `detail`), else
→MR_MINE_APPROVED.

- [ ] **Step 1: Failing test**
```python
# tests/test_gitlab.py
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
        return httpx.Response(200, json=[])
    if "discussions" in p:
        return httpx.Response(200, json=[])
    return httpx.Response(200, json=[])

async def test_fetch_items_maps_signals():
    transport = httpx.MockTransport(_handler)
    adapter = GitLabAdapter("https://g", "tok", transport=transport)
    items = await adapter.fetch_items()
    signals = {i.signal for i in items}
    assert SignalType.MR_NEEDS_MY_REVIEW in signals
    assert SignalType.MR_MINE_PIPELINE_FAILED in signals
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement `connectors/gitlab.py`**
```python
"""GitLab connector (REST v4, self-hosted). Maps MRs/issues to DigestItems (spec §8)."""
from __future__ import annotations
from datetime import date
import httpx
from digest.models import DigestItem, SignalType

class GitLabAdapter:
    def __init__(self, base_url: str, token: str, *, transport=None) -> None:
        self._base = base_url.rstrip("/") + "/api/v4"
        self._client = httpx.AsyncClient(headers={"PRIVATE-TOKEN": token},
                                         timeout=20, transport=transport)

    async def _get(self, path: str, **params):
        r = await self._client.get(self._base + path, params=params)
        r.raise_for_status()
        return r.json()

    async def fetch_items(self) -> list[DigestItem]:
        try:
            me = (await self._get("/user")).get("username")
            items: list[DigestItem] = []
            for mr in await self._get("/merge_requests", reviewer_username=me, state="opened", scope="all"):
                items.append(self._mr(mr, SignalType.MR_NEEDS_MY_REVIEW, "Cần bạn review"))
            for mr in await self._get("/merge_requests", author_username=me, state="opened", scope="all"):
                items.append(await self._authored(mr))
            for iss in await self._get("/issues", assignee_username=me, state="opened", scope="all"):
                if iss.get("due_date"):
                    items.append(DigestItem(id=f"gitlab:issue:{iss['id']}",
                        signal=SignalType.ISSUE_ASSIGNED_DUE,
                        title=iss.get("references", {}).get("full") or iss["title"],
                        detail=iss.get("title", ""), url=iss["web_url"], source="gitlab",
                        due=_date(iss.get("due_date"))))
            return items
        finally:
            await self._client.aclose()

    def _mr(self, mr, signal, detail) -> DigestItem:
        return DigestItem(id=f"gitlab:mr:{mr['project_id']}:{mr['iid']}", signal=signal,
                          title=mr.get("references", {}).get("full") or f"!{mr['iid']}",
                          detail=detail, url=mr["web_url"], source="gitlab")

    async def _authored(self, mr) -> DigestItem:
        status = (mr.get("pipeline") or {}).get("status")
        if status == "failed":
            return self._mr(mr, SignalType.MR_MINE_PIPELINE_FAILED, "Pipeline FAIL")
        if mr.get("user_notes_count", 0) > 0:
            item = self._mr(mr, SignalType.MR_MINE_COMMENTED, "")
            item.detail = await self._discussions(mr["project_id"], mr["iid"])
            return item
        return self._mr(mr, SignalType.MR_MINE_APPROVED, "Sẵn sàng merge")

    async def _discussions(self, project_id, iid) -> str:
        try:
            data = await self._get(f"/projects/{project_id}/merge_requests/{iid}/discussions")
        except Exception:
            return "Có comment mới"
        notes = [n.get("body", "") for d in data for n in d.get("notes", []) if not n.get("system")]
        return " | ".join(notes[-5:]) or "Có comment mới"

def _date(s):
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 4: Run test, expect pass. Commit** — `git commit -am "feat: GitLab connector"`

---

## Task 11: Jira connector (`connectors/jira.py`)

**Files:** Create `connectors/jira.py`, `tests/test_jira.py`

- [ ] **Step 1: Failing test**
```python
# tests/test_jira.py
import httpx
from datetime import date
from connectors.jira import JiraAdapter
from digest.models import SignalType

def _handler(request):
    return httpx.Response(200, json={"issues": [
        {"key":"PROJ-1","fields":{"summary":"Overdue","duedate":"2026-06-10",
            "status":{"statusCategory":{"key":"indeterminate"}}}},
        {"key":"PROJ-2","fields":{"summary":"Doing","duedate":None,
            "status":{"statusCategory":{"key":"indeterminate"}}}}]})

async def test_overdue_and_inprogress():
    transport = httpx.MockTransport(_handler)
    adapter = JiraAdapter("https://j", "tok", transport=transport)
    items = await adapter.fetch_items(today=date(2026, 6, 15))
    by_key = {i.id: i.signal for i in items}
    assert by_key["jira:PROJ-1"] == SignalType.JIRA_OVERDUE
    assert by_key["jira:PROJ-2"] == SignalType.JIRA_IN_PROGRESS
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement `connectors/jira.py`**
```python
"""Jira Server/DC connector (REST v2, PAT bearer). Maps issues to DigestItems (spec §8)."""
from __future__ import annotations
from datetime import date
import httpx
from digest.models import DigestItem, SignalType

JQL = "assignee = currentUser() AND statusCategory != Done ORDER BY duedate ASC"

class JiraAdapter:
    def __init__(self, base_url: str, token: str, *, transport=None) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"},
                                         timeout=20, transport=transport)

    async def fetch_items(self, *, today: date | None = None) -> list[DigestItem]:
        today = today or date.today()
        try:
            r = await self._client.get(self._base + "/rest/api/2/search",
                params={"jql": JQL, "maxResults": 50, "fields": "summary,duedate,status"})
            r.raise_for_status()
            issues = r.json().get("issues", [])
        finally:
            await self._client.aclose()
        items = []
        for iss in issues:
            f = iss.get("fields", {})
            due = _date(f.get("duedate"))
            cat = (f.get("status", {}).get("statusCategory", {}) or {}).get("key")
            if due and due <= today:
                signal = SignalType.JIRA_OVERDUE
            elif cat == "indeterminate":
                signal = SignalType.JIRA_IN_PROGRESS
            else:
                signal = SignalType.JIRA_OTHER
            items.append(DigestItem(id=f"jira:{iss['key']}", signal=signal,
                title=iss["key"], detail=f.get("summary", ""),
                url=f"{self._base}/browse/{iss['key']}", source="jira", due=due))
        return items

def _date(s):
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 4: Run test, expect pass. Commit** — `git commit -am "feat: Jira connector"`

---

## Task 12: Config (`config.py`)

**Files:** Create `config.py`, `tests/test_config.py`

- [ ] **Step 1: Failing test**
```python
# tests/test_config.py
import importlib
def test_defaults(monkeypatch):
    for k in ["DELIVERY_CHANNEL","DEFAULT_DIGEST_TIME","TIMEZONE"]:
        monkeypatch.delenv(k, raising=False)
    import config; importlib.reload(config)
    c = config.AppConfig()
    assert c.delivery_channel == "telegram"
    assert c.default_digest_time == "17:00"
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement `config.py`**
```python
"""Environment configuration (spec §10)."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv
load_dotenv()
def _g(n, d=""): return os.environ.get(n, d)

@dataclass
class AppConfig:
    gitlab_base_url: str = field(default_factory=lambda: _g("GITLAB_BASE_URL"))
    jira_base_url: str = field(default_factory=lambda: _g("JIRA_BASE_URL"))
    delivery_channel: str = field(default_factory=lambda: _g("DELIVERY_CHANNEL", "telegram"))
    default_digest_time: str = field(default_factory=lambda: _g("DEFAULT_DIGEST_TIME", "17:00"))
    timezone: str = field(default_factory=lambda: _g("TIMEZONE", "Asia/Ho_Chi_Minh"))
    telegram_bot_token: str = field(default_factory=lambda: _g("TELEGRAM_BOT_TOKEN"))
    encryption_key: str = field(default_factory=lambda: _g("ENCRYPTION_KEY"))
    report_dir: str = field(default_factory=lambda: _g("REPORT_DIR", "./reports"))
    database_path: str = field(default_factory=lambda: _g("DATABASE_PATH", "./data/digest.db"))
    llm_base_url: str = field(default_factory=lambda: _g("LLM_BASE_URL"))
    llm_api_key: str = field(default_factory=lambda: _g("LLM_API_KEY"))
    llm_model: str = field(default_factory=lambda: _g("LLM_MODEL", "gpt-4o-mini"))
    gitlab_token: str = field(default_factory=lambda: _g("GITLAB_TOKEN"))   # html single-user
    jira_token: str = field(default_factory=lambda: _g("JIRA_TOKEN"))
```

- [ ] **Step 4: Run test, expect pass. Commit** — `git commit -am "feat: config"`

---

## Task 13: Per-user digest service + scheduler (`scheduler.py`)

A `DigestService` builds connectors from a user's decrypted tokens, runs `build_digest`,
and delivers per `delivery_channel`. `AgentScheduler` registers per-user cron jobs.

**Files:** Create `scheduler.py`, `tests/test_service.py`

- [ ] **Step 1: Failing test**
```python
# tests/test_service.py
from datetime import datetime, timezone
from scheduler import DigestService
from store.models import User
from store.crypto import TokenCipher, generate_key
from digest.models import DigestReport

class CaptureDelivery:
    def __init__(self): self.sent = []
    async def deliver(self, report): self.sent.append(report)

class NoLLM:
    class chat:
        class completions:
            @staticmethod
            async def create(**kw): raise RuntimeError("x")

class _FakeSrc:
    async def fetch_items(self): return []

async def test_service_builds_and_delivers():
    cipher = TokenCipher(generate_key())
    user = User(telegram_chat_id="1", display_name="T",
                gitlab_token_enc=cipher.encrypt("glt"), jira_token_enc=None,
                delivery_channel="html")
    cap = CaptureDelivery()
    svc = DigestService(cipher=cipher, llm=NoLLM(), model="m",
                        gitlab_base="https://g", jira_base="https://j", report_dir="./reports",
                        make_gitlab=lambda url, tok: _FakeSrc(),
                        make_jira=lambda url, tok: _FakeSrc(),
                        make_delivery=lambda u: cap)
    await svc.run_for(user, now=datetime(2026, 6, 15, 17, 0, tzinfo=timezone.utc))
    assert len(cap.sent) == 1 and isinstance(cap.sent[0], DigestReport)
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement `scheduler.py`**
```python
"""Per-user digest service + APScheduler cron registration (spec §9)."""
from __future__ import annotations
import logging
from datetime import datetime, timezone as _tz
from typing import Any, Callable
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from digest.builder import build_digest
from store.crypto import TokenCipher
from store.db import Store
from store.models import User
logger = logging.getLogger(__name__)

class DigestService:
    def __init__(self, *, cipher: TokenCipher, llm: Any, model: str,
                 gitlab_base: str, jira_base: str, report_dir: str,
                 make_gitlab: Callable, make_jira: Callable, make_delivery: Callable) -> None:
        self._cipher = cipher; self._llm = llm; self._model = model
        self._gl_base = gitlab_base; self._jr_base = jira_base; self._report_dir = report_dir
        self._make_gitlab = make_gitlab; self._make_jira = make_jira; self._make_delivery = make_delivery

    async def run_for(self, user: User, *, now: datetime) -> None:
        gl_tok = self._cipher.decrypt(user.gitlab_token_enc)
        jr_tok = self._cipher.decrypt(user.jira_token_enc)
        gitlab = self._make_gitlab(self._gl_base, gl_tok) if gl_tok else None
        jira = self._make_jira(self._jr_base, jr_tok) if jr_tok else None
        report = await build_digest(gitlab=gitlab, jira=jira, user_name=user.display_name,
                                    llm=self._llm, model=self._model, now=now)
        await self._make_delivery(user).deliver(report)

class AgentScheduler:
    def __init__(self, *, store: Store, service: DigestService, timezone: str) -> None:
        self._store = store; self._service = service
        self._sched = AsyncIOScheduler(timezone=timezone)
    def start(self) -> None: self._sched.start()
    def shutdown(self) -> None:
        if self._sched.running: self._sched.shutdown(wait=False)
    async def schedule_all(self) -> None:
        for u in await self._store.list_active_users():
            self.schedule_user(u)
    def schedule_user(self, user: User) -> None:
        hh, mm = _hhmm(user.digest_time)
        self._sched.add_job(self._run, CronTrigger(hour=hh, minute=mm, timezone=user.timezone),
                            id=f"digest:{user.telegram_chat_id}", replace_existing=True,
                            args=[user.telegram_chat_id])
    async def _run(self, chat_id: str) -> None:
        user = await self._store.get_user(chat_id)
        if user and user.active:
            try:
                await self._service.run_for(user, now=datetime.now(_tz.utc))
            except Exception as exc:
                logger.exception("digest failed for %s: %s", chat_id, exc)

def _hhmm(value: str) -> tuple[int, int]:
    try:
        hh, mm = value.split(":"); return int(hh), int(mm)
    except (ValueError, AttributeError):
        return 17, 0
```

- [ ] **Step 4: Run test, expect pass. Commit** — `git commit -am "feat: digest service + scheduler"`

---

## Task 14: Telegram bot + wiring (`telegram/bot.py`, `main.py`)

**Files:** Create `telegram/bot.py`, `main.py`. (Integration — verified by import/structure smoke test, not unit TDD.)

- [ ] **Step 1: Implement `telegram/bot.py`**
```python
"""Telegram command handlers (spec §10)."""
from __future__ import annotations
from datetime import datetime, timezone as _tz
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from store.db import Store
from store.crypto import TokenCipher
from store.models import User

def build_application(*, token: str, store: Store, cipher: TokenCipher,
                      scheduler, service, default_time: str, default_tz: str) -> Application:
    app = Application.builder().token(token).build()

    async def _get_or_new(chat_id: str, name: str) -> User:
        u = await store.get_user(chat_id)
        if u is None:
            u = User(telegram_chat_id=chat_id, display_name=name,
                     digest_time=default_time, timezone=default_tz, active=False)
        return u

    async def start(update: Update, _: ContextTypes.DEFAULT_TYPE):
        u = await _get_or_new(str(update.effective_chat.id), update.effective_user.full_name)
        await store.upsert_user(u)
        await update.message.reply_text(
            "Chào! Liên kết token để nhận digest:\n"
            "/link_gitlab <PAT>\n/link_jira <PAT>\n"
            "/settime 17:00 · /settz Asia/Ho_Chi_Minh · /delivery telegram|html|both\n"
            "/digest (xem ngay) · /pause /resume\n"
            "⚠️ Nhớ xoá tin nhắn chứa token sau khi link.")

    async def link_gitlab(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        u = await _get_or_new(str(update.effective_chat.id), update.effective_user.full_name)
        if ctx.args:
            u.gitlab_token_enc = cipher.encrypt(" ".join(ctx.args))
        u.active = True
        await store.upsert_user(u); scheduler.schedule_user(u)
        await update.message.reply_text("Đã lưu GitLab token (mã hoá). Hãy xoá tin nhắn vừa rồi.")

    async def link_jira(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        u = await _get_or_new(str(update.effective_chat.id), update.effective_user.full_name)
        if ctx.args:
            u.jira_token_enc = cipher.encrypt(" ".join(ctx.args))
        u.active = True
        await store.upsert_user(u); scheduler.schedule_user(u)
        await update.message.reply_text("Đã lưu Jira token (mã hoá). Hãy xoá tin nhắn vừa rồi.")

    async def settime(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        u = await _get_or_new(str(update.effective_chat.id), update.effective_user.full_name)
        if ctx.args: u.digest_time = ctx.args[0]
        await store.upsert_user(u); scheduler.schedule_user(u)
        await update.message.reply_text(f"Giờ digest: {u.digest_time}")

    async def settz(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        u = await _get_or_new(str(update.effective_chat.id), update.effective_user.full_name)
        if ctx.args: u.timezone = ctx.args[0]
        await store.upsert_user(u); scheduler.schedule_user(u)
        await update.message.reply_text(f"Múi giờ: {u.timezone}")

    async def delivery(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        u = await _get_or_new(str(update.effective_chat.id), update.effective_user.full_name)
        if ctx.args and ctx.args[0] in ("telegram", "html", "both"):
            u.delivery_channel = ctx.args[0]
        await store.upsert_user(u)
        await update.message.reply_text(f"Kênh: {u.delivery_channel}")

    async def digest(update: Update, _: ContextTypes.DEFAULT_TYPE):
        u = await store.get_user(str(update.effective_chat.id))
        if u is None:
            await update.message.reply_text("Gõ /start trước nhé."); return
        await update.message.reply_text("Đang tạo digest...")
        await service.run_for(u, now=datetime.now(_tz.utc))

    async def pause(update: Update, _: ContextTypes.DEFAULT_TYPE):
        u = await store.get_user(str(update.effective_chat.id))
        if u: u.active = False; await store.upsert_user(u)
        await update.message.reply_text("Đã tạm dừng. /resume để bật lại.")

    async def resume(update: Update, _: ContextTypes.DEFAULT_TYPE):
        u = await store.get_user(str(update.effective_chat.id))
        if u: u.active = True; await store.upsert_user(u); scheduler.schedule_user(u)
        await update.message.reply_text("Đã bật lại.")

    for name, fn in [("start",start),("link_gitlab",link_gitlab),("link_jira",link_jira),
                     ("settime",settime),("settz",settz),("delivery",delivery),
                     ("digest",digest),("pause",pause),("resume",resume)]:
        app.add_handler(CommandHandler(name, fn))
    return app
```

- [ ] **Step 2: Implement `main.py`**
```python
"""Entry point. Default: Telegram bot + scheduler. --once: one HTML digest from .env tokens."""
from __future__ import annotations
import argparse, asyncio, logging
from datetime import datetime, timezone as _tz
from openai import AsyncOpenAI
from config import AppConfig
from connectors.gitlab import GitLabAdapter
from connectors.jira import JiraAdapter
from delivery.html import HtmlDelivery
from delivery.telegram import TelegramDelivery
from scheduler import AgentScheduler, DigestService
from store.crypto import TokenCipher, generate_key
from store.db import Store
from store.models import User
logging.basicConfig(level=logging.INFO)
CFG = AppConfig()

def _llm() -> AsyncOpenAI:
    return AsyncOpenAI(base_url=CFG.llm_base_url or None, api_key=CFG.llm_api_key or "not-needed")

async def run_once_html() -> None:
    cipher = TokenCipher(CFG.encryption_key or generate_key())
    service = DigestService(cipher=cipher, llm=_llm(), model=CFG.llm_model,
        gitlab_base=CFG.gitlab_base_url, jira_base=CFG.jira_base_url, report_dir=CFG.report_dir,
        make_gitlab=lambda url, tok: GitLabAdapter(url, tok),
        make_jira=lambda url, tok: JiraAdapter(url, tok),
        make_delivery=lambda user: HtmlDelivery(CFG.report_dir, "local", open_browser=True))
    user = User(telegram_chat_id="local", display_name="me", delivery_channel="html",
                gitlab_token_enc=cipher.encrypt(CFG.gitlab_token) if CFG.gitlab_token else None,
                jira_token_enc=cipher.encrypt(CFG.jira_token) if CFG.jira_token else None)
    await service.run_for(user, now=datetime.now(_tz.utc))

def run_bot() -> None:
    store = Store(CFG.database_path)
    cipher = TokenCipher(CFG.encryption_key)
    bot_holder: dict = {}

    def make_delivery(user):
        if user.delivery_channel == "html":
            return HtmlDelivery(CFG.report_dir, user.telegram_chat_id)
        return TelegramDelivery(bot_holder["bot"], user.telegram_chat_id)

    service = DigestService(cipher=cipher, llm=_llm(), model=CFG.llm_model,
        gitlab_base=CFG.gitlab_base_url, jira_base=CFG.jira_base_url, report_dir=CFG.report_dir,
        make_gitlab=lambda url, tok: GitLabAdapter(url, tok),
        make_jira=lambda url, tok: JiraAdapter(url, tok),
        make_delivery=make_delivery)
    scheduler = AgentScheduler(store=store, service=service, timezone=CFG.timezone)

    from telegram.bot import build_application
    app = build_application(token=CFG.telegram_bot_token, store=store, cipher=cipher,
        scheduler=scheduler, service=service, default_time=CFG.default_digest_time,
        default_tz=CFG.timezone)
    bot_holder["bot"] = app.bot

    async def _post_init(application):
        await store.init_db(); await scheduler.schedule_all(); scheduler.start()
    app.post_init = _post_init
    app.run_polling()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="build one HTML digest then exit")
    args = parser.parse_args()
    if args.once:
        asyncio.run(run_once_html())
    else:
        run_bot()
```

- [ ] **Step 3: Smoke test** `tests/test_smoke.py`
```python
def test_imports_and_app_build(monkeypatch):
    import store.crypto
    monkeypatch.setenv("ENCRYPTION_KEY", store.crypto.generate_key())
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    import telegram.bot, scheduler, main  # noqa: F401
```

- [ ] **Step 4: Run** `pytest -q` (all tests) — expect all pass.
- [ ] **Step 5: Commit** — `git commit -am "feat: telegram bot + wiring + --once html"`

---

## Task 15: README + final verification

**Files:** Create `README.md`

- [ ] **Step 1:** Write `README.md` covering: install (`python3.13 -m venv`), generate `ENCRYPTION_KEY` (`python -c "from store.crypto import generate_key; print(generate_key())"`), create Telegram bot via BotFather, `.env` setup, run modes (`python main.py` for bot, `python main.py --once` for local HTML), per-user commands, GitLab/Jira PAT creation steps.
- [ ] **Step 2: Full test run** — `.venv/bin/pytest -q` → all pass.
- [ ] **Step 3: HTML smoke** — set `GITLAB_TOKEN`/`JIRA_TOKEN` + bases in `.env`, run `.venv/bin/python main.py --once`, confirm an HTML file is written to `./reports/` and opens.
- [ ] **Step 4: Commit** — `git commit -am "docs: README + verification"`

---

## Self-review (done)

- **Spec coverage:** connectors (T10,11) · per-user PAT + Fernet (T1,4,14) · query-at-digest no-poll (T7,13) · rule categorize/sort/cap (T3) · LLM 4-in-1 + fallback (T6) · delivery telegram|html|both configurable (T8,9,13,14) · digest-time+tz per-user (T13,14) · error handling partial sources (T7) · tests via fakes/MockTransport (T3,6,7,8,9,10,11,13). ✓
- **Placeholders:** none — every code step is complete.
- **Type consistency:** `DigestItem.id/signal/title/detail/url/source/category/summary/due`, `DigestReport.action/waiting/fyi/headline/more/errors`, `fetch_items()`, `deliver()`, `build_digest(...)`, `DigestService.run_for(user, now=)`, `AgentScheduler.schedule_user(user)` consistent across tasks. ✓
- **Known follow-ups (post-MVP, per spec §14):** pipeline-fail root cause (②), delta-since-yesterday, Slack/Discord. The `main.py` bot/delivery wiring (`bot_holder` injection) is integration glue verified by smoke + manual run, not unit tests.
```
