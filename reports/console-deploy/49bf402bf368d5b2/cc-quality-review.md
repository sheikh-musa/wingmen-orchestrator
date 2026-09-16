# cc-quality review — console content `49bf402bf368d5b2`

**Verdict: PASS** (ship-clear for `scripts/deploy_console.sh`; deploy is Musa/orch-console's to run — I do not deploy).

- **Reviewer:** cc-quality (Head of Quality) — model `claude-opus-4-8` (CAI-1170 auditor carve-out; `.quality_model` confirmed at source).
- **Commit under review:** `2922f13942c6fe18450faecd671543c687fae995` on `fable/substrate-safe-fixes`
  — *"feat(console): pool chips show hours-to-reset per token (Musa op#20644) + 5h window cards (op#20657)"* (author cc-fleet-health, co-author Fable 5.1).
- **Content hash:** `console_content_hash` recomputed at this checkout = **`49bf402bf368d5b2`** — MATCHES the requested hash, so this review is keyed to exactly the content that will ship. The hash covers the whole console backend package (item-4b / Nazim #31843), and I reviewed the backend diff, not just the static/renders.
- **Request:** orch-console bus #40516 (P1), Musa op#20644 / op#20657.
- **Build id:** fc-v60 (lockstep across sw.js VERSION == fleet.js APP_BUILD == lanes.html badge == irsyad.js APP_BUILD).
- **Reviewed:** 2026-09-16.

## What ships

Each Max pool (Musa / musa2 / Syed) now renders as a compact per-KEY **card** in the monitoring
strip: key pill + status_7d, a **weekly** row (bar + % + "resets in …") and a **5h** row (bar + % +
"window resets in …" from `resets_5h_at`), then the pace/projected/runway advisory line. Card level =
**worse** of the two windows; stale still greys out.

Gated files touched by the commit (all within the hashed set): `static/fleet.js`, `static/fleet.html`,
`static/sw.js`, `static/lanes.html`, `static/irsyad.js`, `db.py`, `hosted_view.py`. Non-gated:
`tests/console/fleet_pace.test.js`, `tests/console/test_app.py`. The other 24 files in the hashed
manifest are **unchanged** by this commit — no unreviewed backend change rides under this hash.

## Verification (verify-not-assert)

Reviewed at pinned HEAD `2922f13` (confirmed `HEAD == commit`; the reviewed files are clean in the
working tree — the only dirty paths are unrelated `state/*.json` + `reports/` runtime state outside the
console package).

1. **Formatters (`minutesToReset`/`hoursToReset`/`fmtReset`)** — reuse `daysToReset`'s parse, so
   null/unparsable/**past** → `null` → `"—"`. `fmtReset`: `< 48h` → `"Xh Ym"`, `>= 48h` → `"Xd Yh"`.
   Threshold and unit math correct; minutes/hours floored to whole units. Bar fill clamped
   `Math.max(0, Math.min(100, round(pct)))` — cannot overflow.
2. **Worse-of-two colouring** — `worst = Math.max(Number(pct_7d), pct_5h == null ? 0 : Number(pct_5h))`,
   `cls = stale ? "stale" : poolLevel(worst)` (good <75 / warn 75–89 / bad ≥90). Each row also carries
   its **own** window level independently, so a green weekly + red 5h shows both while the card border
   goes red. Matches the op#20657 "glance per key" intent.
3. **Stale preserved** — `stale` (>1800s) still wins over level and greys the card; verified by test
   ("stale wins over level").
4. **Null-safety of the SELECT change (the flagged risk) — CLEARED, verified on live substrate:**
   - `pool_usage.resets_5h_at` **exists** (timestamptz, nullable) on the live table — the commit's
     "column verified present" claim holds. The exact `build_pool_usage_query` SELECT executes clean on
     live (all 3 pools return; runway NULL rows handled). The column is in fact **already populated**
     with real future stamps, so the live 5h row renders a real countdown, not just a dash.
   - `db.py` maps via `dict(r)` (name-keyed dict-row cursor) — the trailing column is purely additive,
     no positional unpacking to break, and it surfaces `resets_5h_at` to the `/api` path by name.
   - `hosted_view.py` (both `build_minimized_payload` and `_clone_pool_usage`) guard explicitly:
     `str(r[9]) if r[9] is not None else None`. NULL → `None` → fleet.js `poolWindowRow` → `"—"`.
5. **Hosted-view parity** — the hosted view serves `fleet.js` verbatim, and both hosted pool reads add
   `resets_5h_at`, so the hosted surface renders the identical cards. `status` key fallback
   (`p.status_7d || p.status`) covers the hosted payload's `status` vs the `/api` `status_7d`.
6. **No horizontal overflow @ ~400px** — `.poolcard`/`.poolwin` are `flex-wrap: wrap` with
   `min-width: 0`; the bar is `flex: 1 1 60px` (shrinkable) and the only `nowrap` element (`.poolreset`)
   wraps to its own line if needed. Summed min widths fit ≤320px. Consistent with the requester's
   iPhone-13 render eyeball.
7. **ES5** — shipped `fleet.js` additions use `var` + function declarations only (test files use
   `const`, but tests are neither served nor shipped to the browser).
8. **Build-id lockstep** — fc-v60 across sw.js / fleet.js / lanes.html / irsyad.js; `test_console_build_id_lockstep` enforces sw==fleet==lanes.

### Tests re-run by me at pinned HEAD
- `node tests/console/fleet_pace.test.js` → **13 passed** (formatters, both rows, "—", worse-of-two, stale, runway unchanged).
- `.venv/bin/python -m pytest tests/console/` → **184 passed** (served fleet.js carries the helpers/`resets_5h_at`; backend rows expose `resets_5h_at` via fake cursors; build-id lockstep). The asyncio "Task was destroyed" lines are harmless feed-teardown noise, not failures.

## Non-blocking observations (LOW — not gating)
- **48h band cosmetic:** a weekly reset between 24–48h out shows e.g. `"47h 30m"` rather than
  `"1d 23h"`. Intentional per the documented 48h threshold and consistent across the tests; noted only.
- **Pre-existing (out of scope):** `build_minimized_payload` does `int(r[1])`/`int(r[2])` on
  `pct_7d`/`pct_5h`; a NULL pct would raise. Unchanged by this commit and pool rows always carry both —
  flagging only so it isn't mistaken for something this change introduced.

## Charter footing
Advisory, alert-not-block. This PASS clears the deploy_console.sh Gate-4 review for content
`49bf402bf368d5b2`. No governance/money/schema fork here (additive read of an existing nullable column;
no migration rides the deploy). Deploy remains Musa/orch-console's action.

— cc-quality
