# Dev Daily Digest

A **personal** agent that, at your configured time, pulls your GitLab + Jira work items,
categorizes them by rule, enriches them with one LLM call, and delivers a clickable
daily digest via Telegram and/or a local HTML file.

Data is queried at digest time — no polling, no message store, no database. You configure
your own credentials in `.env`; nothing is persisted.

## Categories

- 🔴 **Cần xử lý** (ACTION) — MRs needing your review, failed pipelines, overdue Jira, issues with due dates
- 🟡 **Đang chờ** (WAITING) — your MRs with new comments, in-progress Jira
- 🔵 **Để biết** (FYI) — your approved MRs, other Jira

## Install

Requires Python 3.13.

```bash
uv sync --extra dev
```

Run the tests:

```bash
uv run pytest -q
```

## Configuration

1. Copy the example env file and edit it:

   ```bash
   cp .env.example .env
   ```

2. Create a Telegram bot via [@BotFather](https://t.me/BotFather): send `/newbot`,
   follow the prompts, copy the token into `TELEGRAM_BOT_TOKEN`.

3. Get your Telegram chat id (message [@userinfobot](https://t.me/userinfobot)) and put it
   in `TELEGRAM_CHAT_ID` — the scheduled digest is sent there.

4. Fill in the rest of `.env`:

   | Variable | Purpose |
   | --- | --- |
   | `GITLAB_BASE_URL` | Your self-hosted GitLab, e.g. `https://git.company.vn` |
   | `JIRA_BASE_URL` | Your Jira Server/DC, e.g. `https://jira.company.vn` |
   | `DELIVERY_CHANNEL` | `telegram` \| `html` \| `both` |
   | `DEFAULT_DIGEST_TIME` | Single daily digest time, e.g. `17:00` (used if `DIGEST_TIMES` unset) |
   | `DIGEST_TIMES` | Multiple run times per day, CSV `HH:MM`, e.g. `08:30,17:30` (overrides `DEFAULT_DIGEST_TIME`) |
   | `DIGEST_DAYS` | Days to run, cron day-of-week expr; default `mon-fri` (skips weekends) |
   | `TIMEZONE` | e.g. `Asia/Ho_Chi_Minh` |
   | `FETCH_WINDOW_DAYS` | Only fetch MRs/issues updated within the last N days (default `30`) |
   | `STALE_AFTER_DAYS` | Flag MRs/tickets idle ≥ N days as stale — badge + bumped to action (default `7`) |
   | `TELEGRAM_BOT_TOKEN` | From BotFather |
   | `TELEGRAM_CHAT_ID` | Your chat id (scheduled digest destination) |
   | `REPORT_DIR` | Where HTML digests are written (default `./reports`) |
   | `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | OpenAI-compatible LLM endpoint |
   | `GITLAB_TOKEN` / `JIRA_TOKEN` | Your personal access tokens |

### Creating a GitLab Personal Access Token

GitLab → **Preferences → Access Tokens** → create a token with the `api` (or at least
`read_api`) scope. Put it in `GITLAB_TOKEN`.

### Creating a Jira Personal Access Token

Jira Server/DC → **Profile → Personal Access Tokens** → create a token. Put it in
`JIRA_TOKEN`.

## Run modes

### Telegram bot (default)

```bash
uv run python main.py
```

Starts the bot + APScheduler. You get a digest at your configured time, and can trigger
one any time with `/digest`.

### Local HTML (one-shot)

```bash
uv run python main.py --once
```

Writes a self-contained HTML digest to `./reports/local-<date>.html` and opens it in
your browser.

## Telegram commands

| Command | Effect |
| --- | --- |
| `/start` | Show help |
| `/digest` | Build and send a digest right now |
| `/pause` / `/resume` | Pause or resume the scheduled digest |

## Architecture

Ports-and-adapters. Connectors (`GitLabPort`/`JiraPort`) fetch `DigestItem`s over REST
(httpx) using your PATs from `.env`. A pure `rules` module categorizes/sorts/caps; one
`summarize` LLM call adds per-item summaries, comment→actionable rewrites, a headline,
and ACTION ordering (fallback-safe). A `DeliveryPort` renders to Telegram and/or HTML.
The single `Owner` is built from config — no persistence layer.
