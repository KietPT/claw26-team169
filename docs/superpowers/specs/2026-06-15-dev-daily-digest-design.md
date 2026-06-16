# Dev Daily Digest — Design Spec

> Ngày: 2026-06-15 · Trạng thái: đã chốt design, chờ lập implementation plan.

## 1. Bối cảnh & vấn đề

Team dev cần một bản tóm tắt cuối ngày những việc **đang chờ chính mình**: MR cần review,
MR của mình bị comment / pipeline fail, ticket Jira đến hạn… thay vì tự đi quét GitLab/Jira.

Phương án ban đầu (Microsoft Teams agent) **không khả thi cho nhiều người dùng** vì mọi
connector của Microsoft (Graph/Teams/Azure Bot/Entra) đều cần quyền admin tổ chức mà tài
khoản công ty không có. Spec này pivot sang nguồn + kênh **user tự phục vụ, không cần admin**:
**GitLab + Jira (REST, per-user PAT) → digest → Telegram và/hoặc HTML local.**

Tái dùng kiến trúc ports-and-adapters và các pattern đã chứng minh ở
`teams-daily-report-agent` (cách scoring rule→category, wrapper LLM, cấu trúc APScheduler,
store aiosqlite). Đây là **project mới** `dev-daily-digest/` (sibling), không phụ thuộc
`msgraph`/`botbuilder`.

## 2. Mục tiêu / Không làm

**Mục tiêu (MVP):**
- Mỗi dev nhận **1 digest/ngày** vào giờ cấu hình được, gồm các mục **click thẳng tới
  reference** (MR/issue/pipeline/ticket).
- Đa người dùng, **mỗi người 1 PAT riêng** (GitLab + Jira), đúng quyền từng người.
- Kênh gửi **cấu hình được**: `telegram | html | both`.
- Chạy không cần admin/Azure/Entra; nguồn GitLab self-hosted + Jira Server/DC.

**Không làm ở MVP (YAGNI):**
- Webhook real-time GitLab/Jira (cần admin repo + endpoint public).
- "Delta so với hôm qua" / lịch sử / trend.
- Dashboard tương tác (lọc/sắp xếp/đánh dấu xong).
- Slack/Discord/email.
- Nhiều mốc digest trong ngày; đa ngôn ngữ.

## 3. Quyết định kiến trúc

1. **Query tại thời điểm digest, KHÔNG poll/lưu message.** GitLab/Jira query được bất cứ
   lúc nào → đến giờ mới gọi API cho từng user. Bỏ hẳn polling, message store, dedup, TTL.
   Chỉ lưu: user + token + tuỳ chọn.
2. **REST trực tiếp (httpx), không MCP.** Pipeline tất định, query cố định, per-user token,
   self-hosted/Server DC → REST đơn giản/rẻ/dễ test hơn. Bọc sau Port để sau này muốn thêm
   tính năng hội thoại (LLM-tool/MCP) thì dùng lại adapter.
3. **Phân loại bằng rule** (deterministic, không dùng LLM). LLM dùng cho 4 việc, **gộp tối
   đa vào 1 call `summarize`** (fallback an toàn — lỗi thì bỏ phần LLM, vẫn liệt kê item):
   (a) tóm tắt 1 dòng tiếng Việt mỗi mục; (b) **comment thread → actionable**: với MR của
   mình bị comment, biến thread thành "reviewer đang yêu cầu gì"; (c) **headline** 1 câu
   "hôm nay tập trung gì"; (d) **xếp ưu tiên** thứ tự trong nhóm 🔴 Cần xử lý.
   (Pipeline-fail → đoán nguyên nhân: để sau MVP.)
4. **Delivery qua `DeliveryPort`**, chọn kênh bằng config.

## 4. Stack

Python 3.13 (`requires-python = ">=3.13"`; không còn ràng buộc botbuilder như project Teams,
mọi deps đều có wheel đầy đủ trên 3.13) · `httpx` (async REST) · `python-telegram-bot` v21 (async; /start onboarding +
commands) · `apscheduler` (AsyncIOScheduler) · `aiosqlite` · `openai` (AsyncOpenAI, OpenAI-
compatible) · `cryptography` (Fernet, mã hoá PAT) · `python-dotenv`. Dev: `pytest`,
`pytest-asyncio`.

## 5. Cấu trúc project

```
dev-daily-digest/
├── pyproject.toml
├── .env.example
├── README.md
├── config.py                 # AppConfig từ env
├── main.py                   # wiring: telegram long-poll + scheduler; hoặc --once (html)
├── scheduler.py              # APScheduler: cron per-user → build digest → deliver
├── connectors/
│   ├── base.py               # GitLabPort, JiraPort (Protocol) + DTO thô
│   ├── gitlab.py             # GitLabAdapter (httpx, GITLAB_BASE_URL)
│   └── jira.py               # JiraAdapter (httpx, JIRA_BASE_URL, PAT bearer)
├── digest/
│   ├── models.py             # DigestItem, SignalType, Category, DigestReport
│   ├── rules.py              # map signal → Category + sắp xếp (THUẦN, test được)
│   ├── summarize.py          # LLM tóm tắt 1 dòng (reuse llm wrapper) + fallback
│   └── builder.py            # gọi connectors → DigestItem[] → rules → summarize → DigestReport
├── delivery/
│   ├── base.py               # DeliveryPort (Protocol): async deliver(report)
│   ├── telegram.py           # TelegramDelivery: message HTML + nút URL per-item
│   └── html.py               # HtmlDelivery: file HTML self-contained vào REPORT_DIR
├── store/
│   ├── db.py                 # aiosqlite: bảng users
│   ├── crypto.py             # Fernet encrypt/decrypt PAT
│   └── models.py             # User dataclass
├── telegram/
│   └── bot.py                # command handlers: /start /link_* /settime /settz /delivery /digest /pause /resume
└── tests/
    ├── test_rules.py         # mapping signal→category, sắp xếp
    ├── test_summarize.py     # fallback khi LLM lỗi
    ├── test_crypto.py        # encrypt/decrypt round-trip
    ├── test_html.py          # render HTML có link đúng
    └── conftest.py           # FakeGitLabPort / FakeJiraPort + fixtures
```

## 6. Domain model

```python
class SignalType(Enum):
    MR_NEEDS_MY_REVIEW       # GitLab: mình là reviewer/assignee, MR opened
    MR_MINE_COMMENTED        # MR của mình có comment/thread chưa giải quyết
    MR_MINE_PIPELINE_FAILED  # pipeline fail trên MR của mình
    MR_MINE_APPROVED         # MR của mình đã được approve / ready
    ISSUE_ASSIGNED_DUE       # GitLab issue assigned, sắp/đã đến hạn
    JIRA_OVERDUE             # Jira assignee=mình, quá hạn/đến hạn hôm nay
    JIRA_IN_PROGRESS         # Jira đang làm
    JIRA_OTHER               # Jira assigned khác

class Category(Enum):      # 3 nhóm hiển thị
    ACTION    # 🔴 Cần xử lý
    WAITING   # 🟡 Đang chờ
    FYI       # 🔵 Để biết

@dataclass
class DigestItem:
    signal: SignalType
    title: str            # vd "MR !142 · backend-api"
    detail: str           # raw context để LLM tóm tắt
    url: str              # link click tới reference
    source: str           # "gitlab" | "jira"
    category: Category = FYI
    summary: str | None = None   # LLM điền (fallback = detail rút gọn)

@dataclass
class DigestReport:
    date: str; user_name: str
    action: list[DigestItem]; waiting: list[DigestItem]; fyi: list[DigestItem]
    headline: str = ""   # LLM: 1 câu "hôm nay tập trung gì"

@dataclass
class User:
    telegram_chat_id: str            # định danh chính (chế độ telegram)
    display_name: str
    gitlab_token_enc: bytes | None
    jira_token_enc: bytes | None
    digest_time: str = "17:00"
    timezone: str = "Asia/Ho_Chi_Minh"
    delivery_channel: str = "telegram"   # telegram | html | both
    active: bool = True
```

## 7. Phân loại (rule, deterministic)

| SignalType | Category |
|---|---|
| MR_NEEDS_MY_REVIEW, MR_MINE_PIPELINE_FAILED, JIRA_OVERDUE, ISSUE_ASSIGNED_DUE (≤ hôm nay) | 🔴 ACTION |
| MR_MINE_COMMENTED, JIRA_IN_PROGRESS | 🟡 WAITING |
| MR_MINE_APPROVED, JIRA_OTHER | 🔵 FYI |

Trong mỗi nhóm sắp theo độ ưu tiên (ACTION trước, quá hạn lâu hơn lên trước). Cap mỗi
nhóm (vd 10 mục) + dòng "còn N mục khác".

## 8. Truy vấn connector (MVP)

**GitLab (REST v4, header `PRIVATE-TOKEN: <PAT>`, base = GITLAB_BASE_URL):**
- `GET /merge_requests?reviewer_username=<me>&state=opened` → MR cần review.
- `GET /merge_requests?author_username=<me>&state=opened` → MR của mình; với mỗi MR đọc
  `pipeline.status`, `approvals`. Nếu có thread chưa resolve →
  `GET /merge_requests/<iid>/discussions` lấy nội dung để LLM biến thành actionable (①).
- `GET /issues?assignee_username=<me>&state=opened&due_date=...` → issue đến hạn.
- Username lấy 1 lần qua `GET /user`.

**Jira (Server/DC, header `Authorization: Bearer <PAT>`, base = JIRA_BASE_URL):**
- `GET /rest/api/2/search?jql=assignee = currentUser() AND statusCategory != Done ORDER BY duedate ASC`.
- Phân loại theo `duedate` (quá hạn/hôm nay → ACTION) và `statusCategory` (In Progress → WAITING).

Gọi GitLab + Jira **song song** (`asyncio.gather`). Link mục = `web_url` (GitLab) /
`<JIRA_BASE_URL>/browse/<key>` (Jira).

## 9. Data flow

```
[cron theo digest_time + tz của user]  (hoặc /digest, hoặc --once)
  → đọc User, giải mã PAT
  → builder: gather(gitlab.fetch(me), jira.fetch(me)) → DigestItem[]
  → rules.categorize + sort + cap
  → summarize (1 LLM call: tóm tắt 1 dòng/mục + comment→actionable + headline +
     xếp ưu tiên nhóm ACTION; lỗi → bỏ phần LLM, vẫn liệt kê item + link)
  → DeliveryPort theo delivery_channel:
       telegram → message HTML + nút URL, gửi vào chat_id
       html     → ghi REPORT_DIR/<user>-<date>.html (mở bằng webbrowser khi local)
       both     → cả hai
```

## 10. Onboarding & cấu hình

**Deployment (`.env`):** `GITLAB_BASE_URL`, `JIRA_BASE_URL`, `DELIVERY_CHANNEL`,
`DEFAULT_DIGEST_TIME=17:00`, `TIMEZONE`, `TELEGRAM_BOT_TOKEN`, `ENCRYPTION_KEY` (Fernet),
`REPORT_DIR=./reports`, `LLM_BASE_URL/API_KEY/MODEL`, và (chế độ html 1 người)
`GITLAB_TOKEN`/`JIRA_TOKEN`.

**Per-user (chế độ telegram, DM bot):** `/start` (hướng dẫn) · `/link_gitlab <PAT>` ·
`/link_jira <PAT>` (bot xác nhận + nhắc xoá tin nhắn token) · `/settime 17:30` ·
`/settz <tz>` · `/delivery telegram|html|both` · `/digest` (chạy ngay) · `/pause` `/resume`.

**Hai chế độ chạy:**
- `telegram`/`both`: `main.py` chạy long-poll bot + AsyncIOScheduler tạo cron per-user.
- `html` (1 người, local): `python main.py --once` → token từ `.env` → ghi + mở file HTML;
  không cần bot. (Có thể chạy cron OS dùng `DEFAULT_DIGEST_TIME`.)

## 11. Bảo mật

- PAT **mã hoá Fernet at-rest** (key từ `ENCRYPTION_KEY`); không bao giờ log token.
- Token nhập trong DM riêng tư; bot nhắc người dùng xoá tin nhắn chứa token sau khi link.
- HTTPS tới GitLab/Jira; verify TLS.
- Mỗi user chỉ thấy dữ liệu theo PAT của chính mình.

## 12. Error handling

- PAT sai/hết hạn (401/403) → digest ghi "GitLab/Jira: cần `/link` lại", không crash.
- Một nguồn lỗi (vd Jira 5xx/timeout) → vẫn gửi phần nguồn còn lại + ghi chú lỗi nhẹ.
- LLM lỗi → bỏ tóm tắt, vẫn liệt kê item (title + link).
- Job 1 user lỗi → log, không ảnh hưởng user khác (`max_instances=1`, `coalesce=True`).

## 13. Testing

Inject `GitLabPort`/`JiraPort` (Protocol) bằng fake → không cần token thật:
- `test_rules.py`: mapping SignalType→Category, thứ tự sắp xếp, cap nhóm.
- `test_summarize.py`: LLM lỗi → fallback giữ liệt kê.
- `test_crypto.py`: Fernet encrypt→decrypt round-trip.
- `test_html.py`: HTML render đúng số mục + chứa link click được.

## 14. Mở rộng tương lai (ngoài MVP)

Delta hôm-qua (thêm bảng seen state); webhook real-time; export HTML kèm Telegram đã có sẵn
qua `both`; Slack/Discord adapter (thêm `DeliveryPort`); tính năng hội thoại "hỏi GitLab/Jira"
(thêm lớp LLM-tool/MCP dùng lại connector adapter).
