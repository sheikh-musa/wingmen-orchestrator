# cc-quality review — console deploy gate-4

- **Content hash:** `b3043a50cae7f886`
- **Build:** fc-v56 (#25436 context-gauge fix)
- **Commit:** `4f8095c` — `fix(console): fc-v56 — gauge sub_tag=NULL cc_identity fallback + downed-body→OFF on session-supersession not staleness`
- **Reviewer:** cc-quality (Opus 4.8), 2026-08-18
- **Requester:** orch-console (bus #25636)

## Verdict: **PASS — clear to deploy (alert-not-block).**

The change is correct, well-tested on the Python side, renders clean, and
introduces **no regression**. It is a strict improvement over fc-v55. One
material finding is documented below as a **follow-up, not a blocker**: the
operator's specific "Quality frozen 95%" symptom will **not** visibly clear from
this deploy — see Finding 1.

## What I verified (not asserted)

1. **NULL-sub_tag fallback** (`_ctx_index`/`_ctx_for_lane` in app.py; `laneCtxIndex`/`laneCtx` in fleet.js).
   - Index keys instance readings by `sub_tag`; NULL-sub_tag writers by base `agent` via `setdefault`/`== null` guard — a base-keyed entry only *fills* a lane with no instance reading, never clobbers one.
   - Lookup order is instance-key-first, base-fallback-second in **both** the Python (`_ctx_for_lane`) and JS (`laneCtx`) — they mirror each other. Correct.
2. **Session-supersession logic** (`_ctx_from_session`; db.py `ctx_session_id`/`ctx_current_session_id` + bloat `session_id`/`current_session_id`).
   - Confirmed the coordinator `ctx_tokens` subquery and the new `ctx_session_id` subquery use **identical** `WHERE`/`ORDER BY`, so the session id genuinely corresponds to the tokens row (no cross-row mismatch).
   - Peer-session subquery is sub_tag-aware (`IS NOT DISTINCT FROM`), so NULL==NULL matches. Correct.
   - Live-verified the documented fixture: cc-fleet-health's 97% ghost (id=2193, session `36e398c5`) is correctly superseded by its new session `3524f075` (id=2203, 277 tok) → shows fresh, not 97%. **The fix works for this case.**
3. **fleet.js offline gate** — `if (l.bucket === "offline") return null;`. `l.bucket` is a real, pervasively-used field (values working/offline/idle); the gate is consistent with existing `bucket === "offline"` usages and complementary to the server-side supersession drop. Not DOM-unit-covered (no harness), reviewed by hand — correct.
4. **Version sync** — APP_BUILD/VERSION bumped to `fc-v56` across sw.js, fleet.js, irsyad.js, irsyad.html, lanes.html. Consistent.
5. **Gate artifacts** — pytest.log: **85 passed** (the "Task was destroyed but it is pending" lines are benign asyncio teardown noise from feed.py, not failures). render.log clean, both PNGs produced.
6. **Renders eyeballed** — fleet.png + lanes.png both render clean, fc-v56 badge present, tiles legible, no clutter/regression. New app.py/db.py logic has thorough unit coverage (supersession both directions, staleness-is-not-the-signal, NULL preserved, sibling non-leak, query-column presence).

## Finding 1 (MEDIUM — follow-up, NOT a deploy blocker): the flagged "Quality 95%" tile will not clear

The coordinator-card ctx gauge (app.py ~L1333) gates **only** on `_ctx_from_session`
(supersession), with **no staleness backstop** — unlike `_context_bloat`, which
still drops on `age > _CTX_STALE_DROP_S` (48h). Supersession can only fire when the
recycled body's **new session has itself written a `cc_session_costs` row**.

DB reality (verified this session): cc-quality's freshest cost row is **id=2184,
session `d1206220`, latest_context_tokens=954218 (95%), ~10h old at render**, and
there is **no** newer session row for cc-quality. My current (woken, on-demand)
session has written no cost row. So `ctx_session_id == ctx_current_session_id` →
the card shows 95%, and with no age backstop on this path it will show 95%
**indefinitely** until my next cost-row write or a manual reset.

This is the exact operator-flagged symptom, and it is **not a regression** — it's a
pre-existing gap this PR only partially closes. cc-fleet-health cleared because its
new session emitted a row; on-demand coordinators (cc-quality) wake into short
sessions that may never emit a fresh low reading, so they are the worst case.

**Recommended follow-ups (either/both, out of this PR's scope):**
- (a) Add a staleness backstop to the coordinator-card path mirroring
  `_context_bloat`'s `age > _CTX_STALE_DROP_S → (None, None)`, so a ghost is retired
  at 48h even without a newer session. Cheap, bounds the lie.
- (b) Root cause: emit a `cc_session_costs` context row on session wake/start for
  on-demand coordinators, so supersession can actually fire. This is the real fix.

## Finding 2 (NIT — non-blocking)

The coordinator `ctx_tokens` and `ctx_session_id` subqueries share identical
`WHERE`/`ORDER BY` but neither carries an `id` tiebreak; two with-context rows at an
identical `COALESCE(ended_at, created_at)` could in principle resolve to different
rows across the two correlated subqueries. Negligible in practice (both would be
current-context candidates). Consider adding `, cs.id DESC` to both for parity with
`ctx_current_session_id`.

## Bottom line

Ship it. The fix is correct and safe. But **do not report the operator's Quality-95%
tile as resolved** — it will persist post-deploy; track it under Finding 1's
follow-up.
