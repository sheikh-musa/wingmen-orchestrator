# cc-quality review — console content hash `b60ca472ebc75e0d`

**Verdict: PASS** — clear to push. No P0/P1. One non-blocking out-of-scope note.

- **Reviewer:** cc-quality (Head of Quality), model `claude-opus-4-8` (`.quality_model` carve-out confirmed).
- **Date:** 2026-09-27
- **Request:** cc-substrate (worktree orchestrator.wt-cleanup), branch `fix/operator-asks-tracking-ratified` — real push-blocking gate (op#12457).
- **Scope of gate:** only `nervous_system/console/db.py` changed inside the gated manifest.

## Identifiers verified at source (I recomputed/inspected, did not rely on the pasted diff)

| Field | Value | Verified |
|---|---|---|
| Branch / HEAD | `fix/operator-asks-tracking-ratified` @ `27e9228` | ✓ |
| Content hash | `b60ca472ebc75e0d` | ✓ recomputed via `console_content_hash` |
| Console files changed vs `286c96d` | `nervous_system/console/db.py` only | ✓ |
| GATE-1 version-sync | `sw = fleet = lanes = fc-v67` | ✓ synced |
| Static/JS/HTML delta | none | ✓ backend-only → no version bump, no render to eyeball |

The actual `git diff 286c96d -- db.py` matches the diff cc-substrate provided, byte-for-byte.

## The change — `build_asks_query()`

Adds a new top-priority branch derived from `a.waiting_on_operator` (migration 072): the SELECT `CASE` gains `WHEN a.waiting_on_operator THEN 'waiting_on_musa'` as the first arm, the ORDER BY `CASE` gains `WHEN a.waiting_on_operator THEN 0` (pushing the existing 0/1/2/3 ranks to 1/2/3/4), and the SELECT returns two additive columns `a.waiting_on_operator, a.chase_by`.

### Checks performed

1. **Migration 072 is live (no 500-the-board risk).** Queried `information_schema` on the substrate: `operator_asks.waiting_on_operator` = `boolean NOT NULL DEFAULT false`, `operator_asks.chase_by` = `timestamptz NULL` both EXIST. So the new `SELECT a.waiting_on_operator, a.chase_by` and the `CASE` reference resolve — the query cannot hard-error the asks board (the class of bug that darks a feed when a query SELECTs a not-yet-migrated column). `NOT NULL DEFAULT false` also means `WHEN a.waiting_on_operator` is always a clean boolean, never NULL-fallthrough.

2. **Column insertion is additive-safe (no positional break).** The two new columns are inserted at positions 4–5, which shifts the derived status column's ordinal. Traced the consumer: `fetch_asks()` → `_query()`, which maps every row with `dict(r)` — **by column name, not by index**. The status column is explicitly aliased `AS status` (unchanged), and `waiting_on_operator`/`chase_by` are distinct names with no collision. So every consumer reads by name and is unaffected by the ordinal shift.

3. **SELECT-label ↔ ORDER-BY-rank consistency.** Verified the two CASE ladders agree per row-state: `waiting_on_musa`→0, `needs_you`→1, `on_nazim`→2, `delegate_done`→3, `in_progress`/`pending`→4. `waiting_on_operator` and the thread-derived states are mutually consistent (a `thread_id IS NULL` row leaves the LEFT JOIN's `l.*` NULL, so the `needs_you` arm can't fire for it). The change is a clean +1 shift inserting the new top rank; relative ordering of all prior states is preserved. Matches the docstring ("waiting_on_musa FIRST, then needs_you, then on_nazim, then freshest movement").

4. **"Status derived live, never stored" invariant preserved.** `waiting_on_operator` is a stored structural *input* fact (set at open time, framed exactly like the pre-existing `delegated_to`/`thread_id`/`closed_at`), NOT a cached status. The *status string* is still derived live in SQL on every `/api/fleet` poll — it structurally cannot go stale. This does not reintroduce the `operator_backlog` staleness class.

5. **Additive response, no UI break.** The two returned columns are additive; any current UI consuming `/api/fleet` is unaffected until it opts into reading them. No frontend/static changed, so nothing to render-eyeball and no version bump.

6. **Tests.** Ran `tests/console/test_app.py -k ask` at this HEAD (worktree venv): **9 passed** (exercises `/api/fleet` → `fetch_asks` → the dict mapping + status labels end-to-end). (asyncio feed-teardown "Task was destroyed" lines are benign.)

## Non-blocking note (out of this gate's scope)

`waiting_on_operator`'s clear/lifecycle lives in migration 072 + `scripts/asks_open.py` (non-console, ratified separately by orch-console bus #43869) — not in this `db.py` read-path. `db.py` correctly reflects the live column value. Given the board shows only OPEN asks (`closed_at IS NULL`) and only the operator's swipe closes an ask, a flagged-open ask genuinely awaits the operator, so it can't go harmfully stale via this query. Worth confirming, in the migration-072 review, that the flag has a clear-path for an open ask that stops needing the operator without being closed — but that's outside this console-file gate and not a push blocker.

## Result

**PASS at `b60ca472ebc75e0d`.** The query change is correct, additive-safe (by-name consumption), the migration it depends on is live, the derived-status invariant holds, GATE-1 is synced, and the asks tests pass. Clear to push.

— cc-quality
