# Single-user personal agent — design

**Date:** 2026-06-15
**Status:** Approved for planning

## Problem

The agent is a **personal** tool, but the current architecture is multi-user: it
persists each Telegram user (keyed by `chat_id`) in SQLite, stores their GitLab/Jira
PATs encrypted with Fernet, and accepts tokens interactively via `/link_gitlab` /
`/link_jira`. This forces an `ENCRYPTION_KEY` and a database for a single-owner use case.

## Goal

Each owner configures their own credentials in `.env` at startup. No token storage, no
encryption key, no database. The Telegram bot serves exactly one owner.

## Design

### Configuration (`.env` + `config.py`)

- **Remove:** `ENCRYPTION_KEY`, `DATABASE_PATH`.
- **Add:** `TELEGRAM_CHAT_ID` — the owner's chat id, so scheduled digests know where to
  send (no DB to remember it). Obtain via `@userinfobot` or by inspecting an update.
- **Keep:** `GITLAB_TOKEN`, `JIRA_TOKEN`, `DEFAULT_DIGEST_TIME`, `TIMEZONE`,
  `DELIVERY_CHANNEL`, `TELEGRAM_BOT_TOKEN`, `FETCH_WINDOW_DAYS`, `REPORT_DIR`, LLM vars.

### Owner model

Replace the persisted `User` with a plain `Owner` dataclass built from config:

```
Owner(chat_id, display_name, gitlab_token, jira_token,
      digest_time, timezone, delivery_channel)
```

Tokens are plaintext (read from `.env`). Add a `CFG.owner()` helper in `config.py`.

### Remove `store/` package

Delete `store/db.py`, `store/crypto.py`, `store/models.py`. Drop `aiosqlite` and
`cryptography` from `pyproject.toml` dependencies.

### `scheduler.py`

- `DigestService`: drop the `cipher` dependency. `run_for(owner, now)` uses
  `owner.gitlab_token` / `owner.jira_token` directly (no decrypt).
- `AgentScheduler`: drop `store`. Schedule the single owner; `_run` invokes the service
  for that owner directly. `pause()` / `resume()` toggle an in-memory flag (default
  active); `_run` no-ops while paused.

### `tgbot/bot.py`

Build handlers around the single owner (no `store`, no `cipher`):

- **Keep:** `/start` (help), `/digest` (build & send now), `/pause` / `/resume`
  (in-memory toggle).
- **Remove:** `/link_gitlab`, `/link_jira`, `/settime`, `/settz`, `/delivery` — all now
  configured via `.env`.

### `main.py`

- `run_bot()`: no `Store` / cipher. Build the owner from `CFG`, wire scheduler + bot.
- `run_once_html()`: use plaintext tokens from `CFG` directly.

## Testing

- **Delete:** `tests/test_store.py`, `tests/test_crypto.py`.
- **Update:** `tests/test_service.py` and `tests/conftest.py` for the new `run_for(owner)`
  signature and `Owner` fixture.
- Existing connector / rules / render / html tests are unaffected.

## Out of scope

- Multi-user support (explicitly dropped).
- Persisting any state across restarts.
