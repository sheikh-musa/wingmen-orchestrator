# cc-quality console deploy-gate review

- **Content hash:** `939b11c436a22d15` (recomputed from the live working tree via `console_content_hash` — matches the requester; **no drift** vs commit `4ae4e58` on the gated files)
- **Commit:** `4ae4e58` — feat(console): 5h card rolls to next 5h boundary instead of a bare dash (Musa op#20680)
- **Branch:** `fable/substrate-safe-fixes`  ·  **Build:** fc-v60 → fc-v61
- **Reviewer:** cc-quality (Opus 4.8 — `.quality_model=claude-opus-4-8` confirmed at source)
- **Date:** 2026-09-16  ·  **Requester:** orch-console (bus #40652)

## Verdict: **PASS** — safe to ship (one non-blocking deploy-hygiene note below)

Money/PII surface: **none** (pure client-side display fix on the pool-usage card countdown; no DB, no migration, no auth/role path).

## What the change does
`pool_usage.resets_5h_at` is the *next* 5h boundary, but the writer (`weekly_limit_monitor`) refreshes only ~every 15 min, so between refreshes the boundary passes and the stored value sits a few minutes in the past → `minutesToReset()` returned null → the 5H row showed a bare "—" (Musa saw this on musa2/Syed while his own key showed a countdown). New `next5hBoundary()` rolls a past value forward in fixed +5h steps to the next future boundary.

## Review points (all confirmed)

1. **Bounded loop, no infinite loop on garbage.** `next5hBoundary`: falsy → `null`; `Date.parse` with the `" "→"T"` space-form fallback, `NaN` → `null`; roll `while (t <= now && i < 100)` — hard cap 100 iterations (≈20.8 days); if still past after the cap → `null`. No unbounded path; garbage/unparsable → `null` → "—". **Confirmed.**

2. **Only the 5H row uses it; weekly still dashes-if-past by design.** Enumerated the *full* `resets_5h_at` consumer domain (grep, static/): exactly two functional call-sites — the tooltip `5h window resets in` (fleet.js:171) and `poolWindowRow("5h", …)` (fleet.js:182) — both now wrapped. Weekly (`poolWindowRow("wk", …, p.resets_at, …)` and tooltip `weekly resets in fmtReset(p.resets_at)`) and `paceAdvisory`'s runway/`daysToReset(p.resets_at)` are **untouched** and keep null/past → "—". Coloring uses `pct_5h` (a percentage), not the reset time, so the roll cannot shift card severity. **Confirmed.**

3. **Genuinely-null `resets_5h_at` still shows "—".** `next5hBoundary(null|undefined|"")` → `null`; `fmtReset(null)`/`poolWindowRow`'s `minutesToReset(null)==null` → "—". The tooltip parenthetical still prints the *raw* stored value (`esc(p.resets_5h_at)`) for debugging, unchanged. **Confirmed.**

4. **Local-vs-UTC parse is not a regression.** `next5hBoundary` uses the byte-identical parse prelude as the pre-existing `daysToReset` (bare `YYYY-MM-DD HH:MM` → local time). It parses to epoch-ms and returns an ISO-UTC string; the +5h roll is pure epoch arithmetic (tz-agnostic), and for an already-future value it returns the *same instant* (test asserts `Date.parse(next5hBoundary(fut)) === Date.parse(fut)`), which `fmtReset` re-parses to the identical minutes. Roundtrip is instant-preserving. **Confirmed — no tz regression.**

## Verification performed (verify-not-assert)
- `node tests/console/fleet_pace.test.js` → **15 passed** (incl. the two new cases: 6-min-past → ~4h54m, 12h-past → 3 steps → ~3h, future-unchanged, garbage/null → null, local space-form parses+rolls, and a poolChip case proving the 5H row + tooltip roll while the WK row stays "—"). The old "past 5h → —" assertion is correctly replaced by "unparsable 5h → —".
- `python3 -m pytest tests/console` → **184 passed** (the `Task was destroyed` lines are asyncio teardown noise, not failures).
- Version-sync (deploy Gate 1): sw.js = fleet.js = lanes.html = **fc-v61** (irsyad.js also fc-v61 — in sync though Gate 1 doesn't check it).
- Content-hash re-derived from working tree == `939b11c436a22d15`; gated-file diff vs `4ae4e58` is empty (reviewing exactly what ships).

## Accepted assumptions (not findings)
- The roll assumes the true 5h window cadence is exactly 5h and phase-aligned to the stored boundary. If Anthropic's real boundaries drift, the *displayed* countdown could be off by a few minutes — acceptable: it's a display estimate, the raw value stays visible in the tooltip, and the fallback is a benign "—". Matches the writer's fixed-5h semantics.
- If the value is >~20.8d past (cap exhausted), it renders "—", but such a card is already flagged STALE (`POOL_STALE_S=1800`), so the pathology is separately surfaced.

## ⚠️ Non-blocking deploy-hygiene note (orch-console's call)
`scripts/check_console_version_cadence.py` **warns**: fc-v61 is the **2nd** console version bump today (fc-v59 already first-seen 2026-09-16). This is the Aug-3 same-day PWA-churn class that re-churns the operator's cache over the AD↔SG relay. It is **advisory, not a deploy gate** — it does not block `deploy_console.sh`. The version bump itself is *required* to serve the new build (the fc-v6x version gate). Decision is orch-console's: ship now, or batch further console changes into fc-v61 for the rest of today. Flagging per alert-not-block (charter condition #3); no code change requested.

— cc-quality
