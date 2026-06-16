"""Single LLM call enriching a DigestReport: per-item summary, comment→actionable,
headline, ACTION ordering. Fallback-safe (spec §3, §13)."""
from __future__ import annotations
import json, logging
from digest import rules
from digest.models import DigestReport
logger = logging.getLogger(__name__)

PROMPT = """Write a work digest for a developer (in Vietnamese, concise, action-oriented).
Headline: 1 sentence summarizing "what to focus on today".
summary: 2-3 sentences synthesizing today's workload and where the slipping risk is.
For EACH item, write 3 short parts so the reader instantly knows what to do:
- "context": 1-2 sentences describing what is happening.
- "needed": what is being waited on or requested? If the detail contains review comments,
  specify what the reviewer asks to fix/do. If no clear request, use "".
- "next_step": the concrete next action (e.g. "address review comments then re-request review").
Do not fabricate people or statuses not present in the detail.
action_order: re-order ACTION item ids by handling priority.
focus: pick ONLY from items where at_risk=true, ordered most-likely-to-slip first, at most 5.
  Each entry: the item id + a short Vietnamese reason it is urgent (cite due/SLA/overdue/idle).
  This answers "what to prioritize NEXT to avoid being late". If none are at risk, return [].
Items (JSON): {items}
Return ONLY JSON:
{{"headline":"...","summary":"...","items":{{"<id>":{{"context":"...","needed":"...","next_step":"..."}}}},"action_order":["<id>"],"focus":[{{"id":"<id>","reason":"..."}}]}}"""

def _all(report: DigestReport):
    return [*report.action, *report.waiting, *report.fyi]

def _fallback(report: DigestReport) -> None:
    """When LLM fails or is missing: ensure at least context + a 1-sentence summary from detail."""
    for it in _all(report):
        base = (it.detail or it.title)[:140]
        if not it.summary:
            it.summary = base
        if not it.context:
            it.context = base

async def enrich(report: DigestReport, *, llm, model: str) -> None:
    items = [{"id": i.id, "title": i.title, "detail": i.detail[:500],
              "category": i.category.value, "at_risk": rules.is_at_risk(i),
              "due": i.due.isoformat() if i.due else "", "idle_days": i.idle_days,
              "status": i.signal.value} for i in _all(report)]
    try:
        resp = await llm.chat.completions.create(
            model=model, temperature=0, response_format={"type": "json_object"},
            messages=[{"role": "user", "content": PROMPT.format(items=json.dumps(items, ensure_ascii=False))}])
        data = json.loads(resp.choices[0].message.content or "{}")
        enriched = data.get("items", {}) or {}
        for it in _all(report):
            parts = enriched.get(it.id)
            if not isinstance(parts, dict):
                continue
            ctx = str(parts.get("context") or "").strip()
            need = str(parts.get("needed") or "").strip()
            step = str(parts.get("next_step") or "").strip()
            if ctx:
                it.context = it.summary = ctx
            if need:
                it.needed = need
            if step:
                it.next_step = step
        report.headline = str(data.get("headline") or "").strip()
        report.summary = str(data.get("summary") or "").strip()
        order = data.get("action_order") or []
        if order:
            by_id = {i.id: i for i in report.action}
            report.action = [by_id[i] for i in order if i in by_id] + \
                            [i for i in report.action if i.id not in set(order)]
        # Resolve focus ids to real items (drops anything hallucinated), keep LLM order.
        by_id_all = {i.id: i for i in _all(report)}
        focus = []
        for f in (data.get("focus") or []):
            it = by_id_all.get(f.get("id")) if isinstance(f, dict) else None
            if it is not None:
                focus.append({"title": it.title, "url": it.url,
                              "reason": str(f.get("reason") or "").strip()})
        report.focus = focus
    except Exception as exc:  # never break the digest
        logger.warning("summarize failed: %s", exc)
    _fallback(report)
