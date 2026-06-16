from datetime import datetime, timezone
from scheduler import DigestService
from config import Owner
from digest.models import DigestReport

class CaptureDelivery:
    def __init__(self): self.sent = []
    async def deliver(self, report): self.sent.append(report)

class NoLLM:
    class chat:
        class completions:
            @staticmethod
            async def create(**kw): raise RuntimeError("x")

class _FakeSrc:
    async def fetch_items(self): return []

async def test_service_builds_and_delivers():
    owner = Owner(chat_id="1", display_name="T", gitlab_token="glt", jira_token="",
                  delivery_channel="html")
    cap = CaptureDelivery()
    svc = DigestService(llm=NoLLM(), model="m",
                        gitlab_base="https://g", jira_base="https://j", report_dir="./reports",
                        make_gitlab=lambda url, tok: _FakeSrc(),
                        make_jira=lambda url, tok: _FakeSrc(),
                        make_delivery=lambda o: cap)
    await svc.run_for(owner, now=datetime(2026, 6, 15, 17, 0, tzinfo=timezone.utc))
    assert len(cap.sent) == 1 and isinstance(cap.sent[0], DigestReport)
