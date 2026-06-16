"""Ports for data sources and delivery (spec §3)."""
from __future__ import annotations
from typing import Protocol
from digest.models import DigestItem, DigestReport

class GitLabPort(Protocol):
    async def fetch_items(self) -> list[DigestItem]: ...
class JiraPort(Protocol):
    async def fetch_items(self) -> list[DigestItem]: ...
class DeliveryPort(Protocol):
    async def deliver(self, report: DigestReport) -> None: ...
