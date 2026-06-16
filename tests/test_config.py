import importlib


def test_reads_env_values(monkeypatch):
    # Explicit env vars win over any local .env (load_dotenv uses override=False),
    # so this is robust whether or not a real .env exists on disk.
    monkeypatch.setenv("DELIVERY_CHANNEL", "both")
    monkeypatch.setenv("DEFAULT_DIGEST_TIME", "09:30")
    monkeypatch.setenv("TIMEZONE", "UTC")
    import config
    importlib.reload(config)
    c = config.AppConfig()
    assert c.delivery_channel == "both"
    assert c.default_digest_time == "09:30"
    assert c.timezone == "UTC"
