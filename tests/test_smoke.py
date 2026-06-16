def test_imports_and_app_build(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    import tgbot.bot, scheduler, main  # noqa: F401
