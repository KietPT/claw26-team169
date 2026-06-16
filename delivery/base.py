"""Delivery port (re-export)."""
from connectors.base import DeliveryPort  # noqa: F401


class BothDelivery:
    """Deliver a report through multiple channels (spec §9: delivery 'both')."""
    def __init__(self, *deliveries):
        self._deliveries = deliveries
    async def deliver(self, report):
        for d in self._deliveries:
            await d.deliver(report)
