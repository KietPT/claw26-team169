"""Single LLM call enriching a DigestReport: per-item context/needed/next_step,
headline, and a 1-sentence reason for each pre-ranked top item. Fallback-safe (spec §3, §13).

Ranking is deterministic (rules.importance_key via build_report). The LLM only supplies
prose: it may NOT reorder the priority list — it receives the fixed ids and returns reasons.
"""
from __future__ import annotations
import json, logging, re
from digest.models import DigestReport
logger = logging.getLogger(__name__)

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

def _loads_lenient(content: str | None) -> dict:
    """Parse the model's JSON, tolerating reasoning preambles and markdown fences.
    Reasoning models (e.g. minimax-m2.5) emit a <think>...</think> block before the JSON
    (so content does not start with '{'), and some models wrap it in ```json fences. Strip
    the think block, then parse; on failure fall back to the outermost {...} slice. Returns
    {} when nothing parses (caller then uses _fallback)."""
    s = _THINK.sub("", content or "").strip()
    if not s:
        return {}
    try:
        return json.loads(s)
    except ValueError:
        pass
    i, j = s.find("{"), s.rfind("}")
    if 0 <= i < j:
        try:
            return json.loads(s[i:j + 1])
        except ValueError:
            pass
    return {}

PROMPT = """Write a work digest for a developer (in Vietnamese, concise, action-oriented).
Headline: 1 sentence summarizing "what to focus on today".
summary: 2-3 sentences (Vietnamese) synthesizing today's workload and where the slipping risk is.
  Consider ALL items below (including FYI) so the summary reflects the full workload.
For each id in "enrich_ids" below (the action/waiting items only — NOT the FYI items),
write 3 short parts so the reader instantly knows what to do:
- "context": 1-2 sentences describing what is happening.
- "needed": what is being waited on or requested? If the detail contains review comments,
  specify what the reviewer asks to fix/do. If no clear request, use "".
- "next_step": the concrete next action (e.g. "address review comments then re-request review").
Do NOT write these 3 parts for ids that are not in enrich_ids.
Do not fabricate people or statuses not present in the detail.
priorities: you are given a FIXED, already-ranked list of the most important item ids
  ("priority_ids" below, in order of importance). DO NOT reorder or add/remove ids.
  For EACH given id, write a 1-sentence Vietnamese reason why it is urgent
  (cite due date / SLA / overdue / priority). Return them keyed by id.
Items (JSON): {items}
enrich_ids (write context/needed/next_step ONLY for these): {enrich_ids}
priority_ids (FIXED order, reasons only): {priority_ids}
Return ONLY JSON:
{{"headline":"...","summary":"...","items":{{"<id>":{{"context":"...","needed":"...","next_step":"..."}}}},"priorities":{{"<id>":"<reason>"}}}}"""

async def _complete(llm, model: str, content: str) -> str | None:
    """Call the chat API and return the raw text. Robust to model differences: try with the
    json_object response_format first, then retry WITHOUT it — some models/endpoints reject
    that param and return an empty/error body (the classic 'Expecting value: line 1 column 1'
    parse failure). _loads_lenient parses plain-text JSON either way, so the retry is safe."""
    msgs = [{"role": "user", "content": content}]
    try:
        resp = await llm.chat.completions.create(
            model=model, temperature=0, response_format={"type": "json_object"}, messages=msgs)
        return resp.choices[0].message.content
    except Exception as exc:
        logger.warning("summarize: json_object call failed (%s) — retrying without response_format", exc)
        resp = await llm.chat.completions.create(model=model, temperature=0, messages=msgs)
        return resp.choices[0].message.content


def _all(report: DigestReport):
    return [*report.action, *report.waiting, *report.fyi]

def _top(report: DigestReport):
    """Deterministic top-5 across ACTION then WAITING (already importance-sorted, FYI excluded)."""
    return (report.action + report.waiting)[:5]

def _enrichable(report: DigestReport):
    """Items that get full LLM prose (context/needed/next_step): ACTION + WAITING only.
    FYI is informational and the largest, lowest-value group — it falls back to
    context-from-detail so the expensive LLM output tokens are spent only where they help."""
    return [*report.action, *report.waiting]

def _fallback(report: DigestReport) -> None:
    """When LLM fails or is missing: ensure at least context + a 1-sentence summary from detail,
    and that report.priorities is built (with empty reasons) from the deterministic top items."""
    for it in _all(report):
        base = (it.detail or it.title)[:140]
        if not it.summary:
            it.summary = base
        if not it.context:
            it.context = base
    if not report.priorities:
        report.priorities = [{"title": it.title, "url": it.url,
                              "priority": it.priority, "reason": ""} for it in _top(report)]

async def enrich(report: DigestReport, *, llm, model: str) -> None:
    from digest import rules
    top = _top(report)
    # All items go into the prompt INPUT (cheap prefill) so headline/summary stay holistic,
    # but per-item prose is requested only for ACTION+WAITING (enrich_ids) to cut output tokens.
    items = [{"id": i.id, "title": i.title, "detail": i.detail[:500],
              "category": i.category.value, "at_risk": rules.is_at_risk(i),
              "due": i.due.isoformat() if i.due else "", "idle_days": i.idle_days,
              "priority": i.priority, "status": i.signal.value} for i in _all(report)]
    enrich_targets = _enrichable(report)
    enrich_ids = [i.id for i in enrich_targets]
    priority_ids = [i.id for i in top]
    try:
        content = await _complete(llm, model, PROMPT.format(
            items=json.dumps(items, ensure_ascii=False),
            enrich_ids=json.dumps(enrich_ids, ensure_ascii=False),
            priority_ids=json.dumps(priority_ids, ensure_ascii=False)))
        data = _loads_lenient(content)
        enriched = data.get("items", {}) or {}
        for it in enrich_targets:
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
        reasons = data.get("priorities", {}) or {}
        report.priorities = [{"title": it.title, "url": it.url, "priority": it.priority,
                              "reason": str(reasons.get(it.id) or "").strip()} for it in top]
    except Exception as exc:  # never break the digest
        logger.warning("summarize failed: %s", exc)
    _fallback(report)
