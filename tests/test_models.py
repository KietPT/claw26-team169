# tests/test_models.py
from digest.models import DigestItem, DigestReport, Category, SignalType

def test_item_defaults():
    it = DigestItem(id="gitlab:mr:1", signal=SignalType.MR_NEEDS_MY_REVIEW,
                    title="MR !1", detail="d", url="u", source="gitlab")
    assert it.category is Category.FYI and it.summary is None

def test_report_defaults():
    r = DigestReport(date="2026-06-15", user_name="Thinh")
    assert r.action == [] and r.headline == "" and r.more == {}
