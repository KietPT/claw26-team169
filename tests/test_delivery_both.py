from delivery.base import BothDelivery
from digest.models import DigestReport

class _Capture:
    def __init__(self): self.calls = 0
    async def deliver(self, report): self.calls += 1

async def test_both_delivers_to_all():
    a, b = _Capture(), _Capture()
    await BothDelivery(a, b).deliver(DigestReport(date="2026-06-15", user_name="T"))
    assert a.calls == 1 and b.calls == 1
