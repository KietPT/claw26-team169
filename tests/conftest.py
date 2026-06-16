from digest.models import DigestItem, SignalType

class FakeSource:
    def __init__(self, items=None, error: Exception | None = None):
        self._items = items or []; self._error = error
    async def fetch_items(self):
        if self._error: raise self._error
        return self._items

def mk_item(signal, id="gitlab:mr:1", title="MR !1", detail="d", url="u", source="gitlab", due=None):
    return DigestItem(id=id, signal=signal, title=title, detail=detail, url=url, source=source, due=due)
