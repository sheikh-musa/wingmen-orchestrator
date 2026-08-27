# cc-quality review — item-4a: /irsyad pane-truth + live SRE disposition (992c807)

**Reviewer:** cc-quality (Opus 4.8, `.quality_model`=claude-opus-4-8 confirmed at source)
**Requested by:** cc-fleet-health (bus #31885; routed per Nazim #31857)
**Target:** commit `992c807` on `fable/substrate-safe-fixes` (= HEAD). 3 files, +135. `nervous_system/console/app.py` — now GATED by the item-4b deploy hash I cleared.
**Verdict:** ✅ **PASS.** Clean feature; no blockers. Verified at source + execution + mutation.

## What it does
- `/irsyad` per-lane ctx% switches from the lying `cc_session_costs` gauge (`fetch_context_bloat`) to PANE-TRUTH (`fetch_pane_context` + `_pane_bloat`) — the same fix item-2 made for the main view. A no-signal lane fails closed to "—".
- New `_sre_disposition(pct, idle_verdict)` — DERIVED live (no stored field) → healthy/watching/recycling/held; wired into `_pane_entry` + the resting `_pane_header` worst banner so a red/amber card always shows "who's on it".

## The five challenges — verified source + execution
- **A — pane-truth fail-closed.** ✅ `_irsyad_payload` now `_pane_bloat(fetch_pane_context())`; folds via `_ctx_index`/`_ctx_for_lane`. `_ctx_for_lane` returns `{}` on miss → `ctx.get("pct")=None` → tile "—", never a stale/gauge value. Confirmed at source.
- **B — disposition boundaries.** ✅ healthy `<60`, watching `[60,65)`, recycling `>=65 & IDLE_EMPTY`, held `>=65 & not-idle`. The watching threshold is `round(_CTX_SOFT*100)=60`, identical to `_pane_entry`'s amber cut (`_CTX_SOFT=0.60`) — so the disposition band never disagrees with the card's green/amber/red level. Held **label**: `WORKING/STAGED`→"SRE holding (active work)"; everything else (`GHOST_WEDGED`/`UNSURE`/unknown)→"SRE holding (lane stuck?)" — never claims active work for a wedged lane. Fail-safe: unreadable pct→"watching" (never false "healthy").
- **C — coupling guard bites.** ✅ Mutation: `PANE_FIRE_K` 650→700 → `test_sre_recycle_bar_stays_coupled_to_the_daemon_fire_bar` RED (`65 == 70` fails). This is the exact test I recommended in fc-v57 (LOW#1, comment-only coupling) — now landed and effective. Real coupling holds: `PANE_FIRE_K=650 ↔ _SRE_RECYCLE_PCT=65` (`650*100//1000`).
- **D — test honesty.** ✅ Mutation: fixture `pct` 50→70 → `test_api_irsyad_folds_token_model_and_context` RED (`assert 70 == 50` at `coord["ctx_pct"]`). The folded `ctx_pct`/`ctx_tokens` are DRIVEN by the mocked `fetch_pane_context` (pane-truth seam), not a coincidental old value; the old `fetch_context_bloat` mock is fully removed.
- **E — no regression.** ✅ Whole console suite = **173 passed**. `sre_disposition` is an additive dict key; `_pane_header.worst` additively carries it; no consumer (glance/main banner/top-bloat/`_context_bloat`) breaks. (asyncio "Task was destroyed" lines = benign `feed.py` teardown noise.)

## Verify-not-assert on the shipped path (beyond the mocks)
- **No duplicate `_sre_disposition`** — exactly one def (line 1117), first introduced by this commit. (My fc-v57 note was the *review* of this feature; the code lands here — no conflicting second definition.)
- **The real feed supplies `idle_verdict`** — `build_pane_context_query` (db.py:901) SELECTs `idle_verdict`, and the live `pane_context` table has the column (values `IDLE_EMPTY/STAGED/WORKING`, all matching the code's explicit branches). So "recycling" genuinely renders in production — NOT a mock-only path. This was the load-bearing check: the tests mock `idle_verdict`, and a shipped feed lacking it would collapse every bloated lane to "lane stuck?".

## Minor (LOW, non-blocking)
- A NULL `idle_verdict` on a `>=65%` lane reads as "held (lane stuck?)". The live feed never emits NULL today (every row carries a verdict), and the "?" hedges + correctly avoids claiming active-work/recycling — a defensible fail-safe. Optionally map NULL→"watching". Truly minor.
- The coupling test reads `PANE_FIRE_K` from the working tree, where the 850→650 lowering (op#15956/#31751) is still **uncommitted drift**. The display const `65` assumes that 650 lands; the coupling test will catch a divergence either way. Awareness only — the daemon-bar commit is not this PR's scope.

## Escalation / gate note
None. Advisory/quality; no money/PII/schema fork. This is a code review to cc-fleet-health (routed per #31857) — NOT a console deploy sign-off. Because app.py is now item-4b-gated, a deploy will still require this verdict saved at `reports/console-deploy/<hash>/cc-quality-review.md` (the gate's Gate-4); this review supplies the content when that deploy is attempted.
