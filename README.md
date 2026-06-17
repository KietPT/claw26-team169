# Personal Daily Digest

> Một **personal agent** giúp bạn không bỏ lỡ ticket Jira quan trọng: vào đúng thời điểm
> bạn chọn trong ngày, agent gom các ticket bạn đang theo dõi, tóm tắt ngắn ngữ cảnh, cảnh
> báo ticket ưu tiên cao / sắp trễ deadline, rồi gửi một bản digest gọn gàng qua Telegram.

## Mô tả agent

**Vấn đề.** Trong công việc hàng ngày, phần lớn trao đổi và theo dõi tiến độ diễn ra trên
Jira. Mỗi người thường phải follow hàng chục ticket cùng lúc và chỉ nhận thông báo rời rạc
qua email. Khi số lượng email quá nhiều, các cập nhật quan trọng dễ bị trôi, dẫn đến *miss
communicate*, phản hồi trễ, hoặc bỏ lỡ ticket sắp tới hạn. Việc tự mở Jira lọc từng ticket
mỗi ngày tốn thời gian và dễ sai sót.

**Người dùng mục tiêu.** Các kỹ sư, QA, PM, team lead — bất kỳ ai làm việc với Jira hàng
ngày và phải theo dõi nhiều ticket cùng lúc.

**Cách agent giải quyết.**
- *Input:* Jira Personal Access Token + Telegram bot token / chat id, và thời điểm muốn nhận
  digest trong ngày (cấu hình qua web UI hoặc file `.env`).
- *Xử lý:* vào đúng thời điểm đã cấu hình, agent gọi Jira API để gom các ticket bạn đang
  follow / được giao, phân loại theo rule (Cần xử lý / Đang chờ / Để biết), sau đó dùng **một
  lần gọi LLM** để tóm tắt ngắn ngữ cảnh từng ticket và làm nổi bật ticket ưu tiên cao hoặc có
  nguy cơ trễ deadline.
- *Output:* một bản digest gọn, click được, bắn qua **Telegram** (và/hoặc xuất file HTML cục bộ).
- *AgentBase:* agent được đóng gói Docker và deploy như **Custom Agent** trên GreenNode
  AgentBase; LLM dùng endpoint OpenAI-compatible của MaaS; lịch chạy do scheduler nội bộ
  (APScheduler) đảm nhiệm.

**Giá trị mang lại.** Chạy hàng ngày, agent thay bạn tổng hợp toàn bộ ticket đang theo dõi
vào một thông báo duy nhất — tiết kiệm thời gian lọc email/Jira thủ công mỗi sáng, giảm rủi ro
bỏ sót ticket quan trọng và trễ deadline, giúp bạn tập trung ngay vào việc cần xử lý.

> Dữ liệu được truy vấn tại thời điểm tạo digest — không polling, không lưu message, không
> database. Bạn tự cấu hình credential trong `.env`; không có gì bị lưu lại lâu dài.

## Phân loại ticket

- 🔴 **Cần xử lý** (ACTION) — Jira quá hạn, ticket có due date sắp đến (và MR cần review,
  pipeline fail nếu bật GitLab)
- 🟡 **Đang chờ** (WAITING) — Jira đang in-progress (và MR của bạn có comment mới)
- 🔵 **Để biết** (FYI) — Jira còn lại (và MR đã được approve)

> Mặc định agent tập trung vào **Jira**. GitLab tắt sẵn (chỉ chạy trong private network); để
> bật, đặt `GITLAB_ENABLED=true` cùng `GITLAB_BASE_URL` / `GITLAB_TOKEN` trong `.env`.

---

# Chạy từ local

Yêu cầu **Python 3.13** và [`uv`](https://docs.astral.sh/uv/).

## 1. Cài dependencies

```bash
uv sync --extra dev
```

Chạy test để chắc chắn môi trường ổn:

```bash
uv run pytest -q
```

## 2. Cấu hình credential

Có **hai cách** cấu hình — chọn một:

### Cách A — Web UI (khuyến nghị để demo)

```bash
uv run uvicorn webui:app --reload --port 8000
# mở http://localhost:8000
```

Trang web có một form và ba nút:

| Nút | Tác dụng |
| --- | --- |
| 💾 **Lưu cấu hình** | Ghi các giá trị trong form vào file `.env` (và áp dụng ngay cho tiến trình đang chạy). |
| ▶️ **Run now** | Tạo một digest ngay lập tức, hiển thị report trên trình duyệt, đẩy qua Telegram nếu đã có bot token + chat id. |
| ⏰ **Run scheduler** | Khởi động cron theo `DIGEST_TIMES` / `DIGEST_DAYS`; trang sẽ hiển thị các mốc chạy kế tiếp. |

> Mỗi ô input có tooltip hướng dẫn từng bước (cách lấy Jira token, tạo Telegram bot, lấy
> chat id). Run now / Run scheduler dùng giá trị hiện có trong form (áp dụng live), nên bạn
> có thể thử cấu hình trước khi bấm 💾 Lưu cấu hình để lưu lại.

### Cách B — file `.env` thủ công

```bash
cp .env.example .env
```

Rồi điền các biến (xem bảng [Biến môi trường](#biến-môi-trường) bên dưới).

## 3. Chạy agent

### Telegram bot + scheduler (mặc định)

```bash
uv run python main.py
```

Khởi động bot + APScheduler. Bạn sẽ nhận digest vào đúng thời điểm đã cấu hình, và có thể
tạo digest bất kỳ lúc nào bằng lệnh `/digest`.

### HTML cục bộ (one-shot)

```bash
uv run python main.py --once
```

Ghi một file HTML digest tự chứa vào `./reports/local-<ngày>.html` và mở trên trình duyệt.

---

## Lấy credential

### Jira Personal Access Token

- **Jira Server/DC:** vào **Profile → Personal Access Tokens** → tạo token → đặt vào
  `JIRA_TOKEN` (để trống `JIRA_EMAIL`, auth bằng Bearer PAT).
- **Jira Cloud:** tạo API token tại <https://id.atlassian.com/manage-profile/security/api-tokens>,
  đặt vào `JIRA_TOKEN` và điền email vào `JIRA_EMAIL` (auth bằng Basic).

### Telegram bot token

Tạo bot qua [@BotFather](https://t.me/BotFather): gửi `/newbot`, làm theo hướng dẫn, copy
token vào `TELEGRAM_BOT_TOKEN`.

### Telegram chat id

Nhắn cho [@userinfobot](https://t.me/userinfobot) để lấy chat id, đặt vào `TELEGRAM_CHAT_ID`
— đây là nơi digest theo lịch được gửi tới.

### (Tùy chọn) GitLab Personal Access Token

GitLab → **Preferences → Access Tokens** → tạo token với scope `api` (hoặc tối thiểu
`read_api`) → đặt vào `GITLAB_TOKEN` và bật `GITLAB_ENABLED=true`.

---

## Biến môi trường

| Biến | Mục đích |
| --- | --- |
| `JIRA_BASE_URL` | Jira Server/DC hoặc Cloud, ví dụ `https://jira.company.vn` |
| `JIRA_TOKEN` | Personal Access Token của bạn |
| `JIRA_EMAIL` | Chỉ điền cho Jira Cloud (Basic auth); để trống nếu Server/DC |
| `TELEGRAM_BOT_TOKEN` | Token từ BotFather |
| `TELEGRAM_CHAT_ID` | Chat id của bạn (đích nhận digest theo lịch) |
| `DELIVERY_CHANNEL` | `telegram` \| `html` \| `both` |
| `DEFAULT_DIGEST_TIME` | Giờ chạy digest mặc định, ví dụ `17:00` (dùng khi `DIGEST_TIMES` để trống) |
| `DIGEST_TIMES` | Nhiều mốc chạy/ngày, CSV `HH:MM`, ví dụ `08:30,17:30` (ghi đè `DEFAULT_DIGEST_TIME`) |
| `DIGEST_DAYS` | Ngày chạy, biểu thức cron day-of-week; mặc định `mon-fri` (bỏ cuối tuần) |
| `TIMEZONE` | Ví dụ `Asia/Ho_Chi_Minh` |
| `FETCH_WINDOW_DAYS` | Chỉ lấy ticket/MR cập nhật trong N ngày gần nhất (mặc định `30`) |
| `STALE_AFTER_DAYS` | Đánh dấu ticket/MR không cập nhật ≥ N ngày là stale — gắn badge + đẩy lên Cần xử lý (mặc định `7`) |
| `REPORT_DIR` | Nơi lưu file HTML digest (mặc định `./reports`) |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | Endpoint LLM dạng OpenAI-compatible |
| `GITLAB_ENABLED` | `true` để bật nguồn GitLab (mặc định tắt) |
| `GITLAB_BASE_URL` / `GITLAB_TOKEN` | GitLab self-hosted + PAT (khi bật) |

---

## Lệnh Telegram

| Lệnh | Tác dụng |
| --- | --- |
| `/start` | Hiển thị trợ giúp |
| `/digest` | Tạo và gửi digest ngay |
| `/pause` / `/resume` | Tạm dừng / chạy lại digest theo lịch |

---

## Chạy bằng Docker

Image chỉ copy `.env.example` (định dạng), không bao giờ chứa `.env` thật (đã nằm trong
`.dockerignore`). Bạn tạo `.env` thật lúc runtime bằng cách điền form rồi bấm 💾 Lưu cấu hình.

```bash
docker build -t digest-demo .
docker run --rm -p 8080:8080 digest-demo
# mở http://localhost:8080 → điền config → 💾 Lưu cấu hình → ▶️ Run now / ⏰ Run scheduler
```

> `.env` ghi bên trong container là tạm thời (mất khi container bị xóa). Để giữ lại, mount
> volume: `-v "$PWD/.env:/app/.env"`.

---

## Kiến trúc

Ports-and-adapters. Các connector (`GitLabPort` / `JiraPort`) lấy `DigestItem` qua REST
(httpx) bằng PAT trong `.env`. Module `rules` thuần phân loại / sắp xếp / giới hạn; **một**
lần gọi LLM trong `summarize` thêm tóm tắt từng item, viết lại comment thành việc cần làm,
sinh headline và sắp thứ tự nhóm ACTION (an toàn fallback nếu LLM lỗi). `DeliveryPort` render
ra Telegram và/hoặc HTML. `Owner` duy nhất được dựng từ config — không có lớp persistence.
