import importlib
from scheduler import _digest_jobs, _hhmm


def test_hhmm_parses_and_falls_back():
    assert _hhmm("08:30") == (8, 30)
    assert _hhmm("bogus") == (17, 0)


def test_one_job_per_time_with_weekday_filter():
    jobs = _digest_jobs(["08:30", "13:00", "17:30"], "mon-fri", "Asia/Ho_Chi_Minh")
    assert [jid for jid, _ in jobs] == ["digest:owner:0", "digest:owner:1", "digest:owner:2"]
    first = str(jobs[0][1])
    assert "day_of_week='mon-fri'" in first   # weekends excluded natively
    assert "hour='8'" in first and "minute='30'" in first


def test_days_expression_is_passed_through():
    (_, trigger), = _digest_jobs(["09:00"], "mon-sat", "UTC")
    assert "day_of_week='mon-sat'" in str(trigger)


def test_parse_times_csv_and_fallback(monkeypatch):
    import config
    importlib.reload(config)
    assert config._parse_times("08:30, 13:00 ,17:30", "17:00") == ["08:30", "13:00", "17:30"]
    assert config._parse_times("", "17:00") == ["17:00"]          # empty -> fallback
    assert config._parse_times("   ", "09:00") == ["09:00"]       # blank -> fallback


def test_owner_reads_times_and_days(monkeypatch):
    monkeypatch.setenv("DIGEST_TIMES", "08:30,17:30")
    monkeypatch.setenv("DIGEST_DAYS", "mon-fri")
    import config
    importlib.reload(config)
    owner = config.AppConfig().owner()
    assert owner.digest_times == ["08:30", "17:30"]
    assert owner.digest_days == "mon-fri"


def test_owner_falls_back_to_default_time_when_no_csv():
    # Construct explicitly so a local .env can't inject DIGEST_TIMES and skew the test.
    import config
    owner = config.AppConfig(digest_times="", default_digest_time="07:45",
                             digest_days="mon-fri").owner()
    assert owner.digest_times == ["07:45"]   # empty CSV → fall back to single default time
    assert owner.digest_days == "mon-fri"    # default skips weekends
