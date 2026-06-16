# Demo — web UI

A small FastAPI page to configure the agent and run it, viewing the report in the
browser (and delivering to Telegram).

## The page has one form and three actions

- **💾 Lưu cấu hình** (`POST /config`) — writes the form values to the **`.env` file**
  (and applies them to the running process). This is the "config really persists" path.
- **▶️ Run now** (`POST /run`) — builds one digest immediately, shows the report in the
  browser, and pushes to Telegram if a bot token + chat id are set.
- **⏰ Run scheduler** (`POST /schedule`) — starts the cron at the configured
  `DIGEST_TIMES` / `DIGEST_DAYS`; the page shows the next fire times.

Run now / Run scheduler use whatever is currently in the form (applied live), so you
can try settings before persisting them with Lưu cấu hình.

## Docker ships only the .env *format*

The image copies `.env.example` (the format), never a real `.env` (it is in
`.dockerignore`). You create the real `.env` at runtime by filling the form and
clicking **Lưu cấu hình**.

```bash
docker build -t digest-demo .
docker run --rm -p 8000:8000 digest-demo
# open http://localhost:8000 → fill config → 💾 Lưu cấu hình → ▶️ Run now / ⏰ Run scheduler
```

> The `.env` written inside the container is ephemeral (gone when the container is
> removed). To keep it, mount a volume: `-v "$PWD/.env:/app/.env"`.

## Run locally (no Docker)

```bash
uv run uvicorn webui:app --reload --port 8000
# open http://localhost:8000
```

> Leaving GitLab or Jira blank simply skips that source. Running twice shows the
> 🆕 / "✅ Đã xong từ lần trước" diff from the snapshot in `REPORT_DIR`.
