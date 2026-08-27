# cc-quality Gate-4 review — console content-hash `1b4a78eee5b52c7a`

**Verdict: PASS** (deploy-cleared). **Auditor:** cc-quality (Opus 4.8). **Date:** 2026-08-24.
**Commit:** fcd71f1 (HEAD) — never-blank #2b: port the /fleet never-blank fix to the standalone /irsyad view (Nazim #32534).
**Requester:** cc-fleet-health #32545 (low-pri; /fleet already shipped). Delta on the already-PASSED /fleet fix (8397d4b, hash `9ca8a095f8c2ffd8`).

Content hash re-computed at this HEAD = `1b4a78eee5b52c7a`.

## Delta scope — /fleet is UNTOUCHED (my prior PASS stands)
fcd71f1 touches only: `_irsyad_payload` (app.py, +12), `irsyad.js` (+49), `irsyad.html` (+3 CSS), a new test
`irsyad_lanectx.test.js` (+95), and version bumps (sw.js/fleet.js/lanes.html). **fleet.js diff since 8397d4b is
VERSION-ONLY** (`APP_BUILD 'fc-v58' → 'fc-v59'` — verified via `git diff 8397d4b fcd71f1 -- fleet.js`); the
`_fleet_payload` / `_pane_bloat` callers, `_pane_entry`, db.py, auto_recycle, and mig-057 are all unchanged. So the
/fleet header/card separation I PASSED under hash `9ca8a095f8c2ffd8` carries unchanged, and all the substrate-level
properties (pane_entry LIVE<660 / LAST-KNOWN<3600 / aged-out gating; upsert freeze-vs-bump; mig-057 additive+fail-safe)
are inherited, not re-introduced.

## Tests (run myself at HEAD)
- `node tests/console/irsyad_lanectx.test.js` → **11 assertions passed**.
- `pytest tests/console/test_app.py` → **85 passed** (the asyncio "Task was destroyed" line is teardown noise, not a failure).

## KEY INVARIANT (allow_stale scoped to the card feed) — HOLDS, by construction for /irsyad
The /irsyad view has **no aggregate ctx header/glance/alert** for a stale reading to leak into:
- **Server:** `_irsyad_payload` uses the `allow_stale=True` feed (`bloat = _pane_bloat(_pane_rows, allow_stale=True)`)
  ONLY through `_ctx_index(bloat)` — a PER-LANE index (keyed by sub_tag/agent) consumed at `_ctx_for_lane` to attach
  each lane's own `ctx_pct/level/tokens/stale/idle`. There is NO aggregate ctx-health/alert field in the payload.
- **Client:** grep of irsyad.js for any aggregate alert/banner/health/worst/reduce/some over lane ctx = ZERO. Each
  lane renders its own card; there is no fleet-wide ctx banner.
- Therefore allow_stale=True on the only feed is safe — a LAST-KNOWN reading reaches ONLY the individual lane card
  (dimmed, age-stamped `~{k}k · {age}`), never an alert. This is structurally cleaner than /fleet (which needed the
  explicit live-only glance/header separation; /irsyad has no such aggregate to protect).

## Backend 3-line attach (scrutinized directly — no unit seam)
Mirrors the already-PASSED `_fleet_payload` attach: switches the /irsyad worker feed to `allow_stale=True`, builds
`_idle_by_sess = {session: idle_verdict}`, and attaches `"ctx_stale": ctx.get("stale")` + `"ctx_idle": _idle_by_sess.get(sess)`
per lane. Fail-safe: on a `fetch_pane_context` exception the except branch sets `_pane_rows = []` → `_idle_by_sess` empty
→ `ctx_idle` falls back to `None` (honest label, no crash). Correct.

## Client (irsyadCtxDisplay) — never-blank 4-state
`irsyadCtxDisplay(r)` → {off, live, stale, label}, mirroring the PASSED /fleet `ctxDisplayFrom`:
- `off` (offline, unchanged), `label` (honest idle/low/n-a from `ctx_idle` via `idleLabel`), `stale` (dimmed meter +
  `~{k}k · {age}`, age VISIBLE, a token count not a live %), `live` (colored meter). Never a bare "context —".
- `irsyad.html` adds `.ctx.stale{opacity:.62}` to visually distinguish last-known from live. Unit-tested (11 assertions).

## Version sync
fc-v59 synced across sw.js VERSION, fleet.js APP_BUILD, lanes.html badge, AND irsyad.js APP_BUILD (which was drifted at
fc-v56 — now corrected). Four-way consistent.

## Non-blocking
- A stale /irsyad card renders a level-colored (red/amber) but dimmed meter — consistent with the /fleet stale card;
  since /irsyad has no aggregate alert, no live-only guard is needed. Fine.

**PASS — cleared for the console deploy.** Pure port of the PASSED /fleet never-blank onto /irsyad's own client; /fleet
untouched (version-only); the allow_stale card-scoping holds by construction (/irsyad has no aggregate ctx alert);
never-blank 4-state honest; fc-v59 four-way synced; tests green at HEAD (run independently). This addresses note-1 from
my `9ca8a095f8c2ffd8` review (irsyad worker cards no longer blank on no-signal).
