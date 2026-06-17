# Ranked-importance digest + Jira priority — Design

**Date:** 2026-06-16
**Status:** Approved (pre-implementation)

## Goal

Make the daily digest rank work by *importance to handle*, and surface that ranking
both as a top-of-report list and within the CS Ticket / Task sections.

Three concrete changes requested:

1. The report's opening `summary` becomes a **ranked list** of the most important
   items (top 5), each line `item — 1-sentence why`, ordered by importance — instead
   of a free-text 2–3 sentence paragraph.
2. The **CS Ticket** and **Task** sections are sorted by the same importance ranking.
3. A new **`priority`** signal (Jira's native priority field) feeds that ranking, so
   higher-priority tickets sort ahead within the same urgency tier.

## Decisions (locked during brainstorming)

- **Summary shape:** replace the paragraph with a ranked top-5 list (across CS+Task+MR).
- **Priority source:** Jira's native `priority` field (`Highest/High/Medium/Low/Lowest`).
  GitLab/MR has no priority → treated as Medium for ranking; MR section keeps its
  current sort.
- **Ranking rule:** deadline group first (overdue & SLA-urgent on top), then priority
  (higher first), then nearer due date.
- **Summary vs focus:** the ranked list **replaces** the existing `focus`
  ("Ưu tiên kế tiếp") list — one ranked list, no overlap.
- **List size:** top 5.
- **Priority badge:** shown on each CS/Task item.
- **Importance-sort scope:** CS + Task (Jira) only; MR unchanged.

## Components & changes

### `connectors/jira.py`
- Add `priority` to `_fields()`:
  `summary,duedate,status,description,comment,issuetype,parent,updated,priority`.
- In `_build_item`, parse `(f.get("priority") or {}).get("name")` → `DigestItem.priority`.

### `digest/models.py`
- `DigestItem`: add `priority: str | None = None` (raw Jira priority name).
- `DigestReport`: rename `focus` → `priorities` (semantics changed from "at-risk" to
  "top important"). Entry shape: `{title, url, reason, priority}`.
- `DigestReport.summary` (paragraph) is no longer populated/rendered. Keep the field
  for now (set to `""`) to avoid breaking unrelated readers, but drop it from prompt
  and rendering. (Remove entirely only if no other reader exists.)

### `digest/rules.py`
- `PRIORITY_RANK`: `{Highest:0, High:1, Medium:2, Low:3, Lowest:4}`; `None`/unknown → 2.
- `DEADLINE_SIGNALS = {JIRA_OVERDUE, JIRA_CS_SLA_DUE_SOON, JIRA_DUE_SOON, ISSUE_ASSIGNED_DUE}`.
- `priority_rank(item) -> int`, `deadline_group(item) -> int` (0 if signal in
  DEADLINE_SIGNALS else 1).
- `importance_key(item) -> tuple = (deadline_group, priority_rank, due_or_max, id)`.
- `build_report`: replace `_sort_key` with `importance_key` for each category bucket.
  Drop the pinned-first tier from the sort (CS leads via section display order; pinned
  still forces ACTION category via `categorize`, unchanged).

### `digest/summarize.py`
- **Keep:** `headline` (1 sentence), per-item `context/needed/next_step`.
- **Remove:** paragraph `summary`, LLM `focus` selection, LLM `action_order`.
- **New flow:** rules picks the top-5 items deterministically (importance-sorted,
  from ACTION then WAITING, excluding FYI). The LLM is given those 5 ids + their
  facts and returns a 1-sentence Vietnamese `reason` per id. `report.priorities` is
  assembled in deterministic order with LLM reasons grafted in.
- Prompt returns: `{"headline":..., "items":{<id>:{context,needed,next_step}},
  "priorities":{<id>:"<reason>"}}`.
- Fallback (LLM fails): `priorities` still built from the deterministic top-5 with
  empty reasons; per-item context falls back to detail (as today).

### `digest/grouping.py`
- Add `PRIORITY_LABEL` map + `priority_label(item) -> str | None`
  (e.g. `🔺🔺 Highest`, `🔺 High`, Medium → hidden/`None`, `🔻 Low`, `🔻🔻 Lowest`).
- Section sort already flows from `rules.importance_key` via `build_report`; `by_section`
  unchanged.

### `delivery/telegram.py` and `delivery/html.py`
- Header: `date + headline`. Remove the paragraph line.
- Replace the `focus` block with **"🔝 Quan trọng nhất hôm nay:"** — numbered list
  `1. <link> — reason` (prefix priority badge when present).
- `_block` (and HTML equivalent): add the priority badge to the item meta line for
  Jira items.

## Importance ranking — worked examples

Given `importance_key = (deadline_group, priority_rank, due_or_max, id)`:

- Overdue **Medium** Task (group 0, rank 2) sorts **above** a non-deadline **Highest**
  ticket (group 1, rank 0) — deadline wins.
- Two overdue items: **High** (rank 1) sorts above **Medium** (rank 2).
- Same group & priority: nearer `due` first; missing due last.

## Testing

- `test_jira.py`: `priority` present in `_fields()`; `_build_item` maps
  `fields.priority.name` → `item.priority`; missing/None handled.
- `test_rules.py`: `importance_key` ordering (the three worked examples above);
  `build_report` sorts buckets by importance; pinned no longer forces sort-first.
- `test_summarize.py`: deterministic top-5 selection; reasons grafted by id;
  hallucinated ids dropped; fallback builds `priorities` with empty reasons and never
  raises.
- `test_models.py` / delivery tests: priority badge renders for CS/Task; numbered
  ranked list renders; paragraph summary no longer emitted.

## Out of scope (YAGNI)

- Making priority ranks/labels configurable in `rules/jira-digest.yaml` (standard Jira
  priorities are hardcoded in `rules.py`).
- Applying priority ranking to the MR section.
- Per-section overflow/cap changes (existing `cap` behavior unchanged).
