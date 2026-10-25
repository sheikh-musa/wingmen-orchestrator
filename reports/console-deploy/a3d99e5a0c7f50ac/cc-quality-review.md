# cc-quality review — needs-Musa hero + migration 118 (op#33598, orch-console #75387 blocker)

- **Content hash:** `a3d99e5a0c7f50ac` (console manifest-gated path set — recomputed at source at HEAD = `a3d99e5a0c7f50ac`). Supersedes my PASS at `d999f511ff960788` (#75389).
- **Branch:** `lane/console-revamp` @ `f9bd350f15ea6234308f9c87c40688a9fec5a7ea`
- **Base:** `f8f68c7` (my prior-cleared head). 4 commits on top: `0b75ba7`+`69e0e2c` (an UNRELATED lanes-boot-guard merge — see note below), `cded33e` (feat), `f9bd350` (version bump).
- **Reviewer:** cc-quality (ran on claude-opus-4-8) · 2026-10-25
- **Verdict:** **PASS on the code + migration design — with ONE CRITICAL, EMPIRICALLY-PROVEN deploy-sequencing precondition: migration 118 MUST be applied and verified live BEFORE this code deploys, or `/api/asks-board` 500s and the whole open board goes dark.** Clear for Gate-4 **only once 118 is live.** Migration 118 is itself cleared to apply (additive, safe, seeds land on real rows).

INDEPENDENT pass verified at source on the PR head, not a sign-off on the lane's green.

## ⛔ CRITICAL — migration-before-code (deploy-sequencing, empirically reproduced)
`build_fleet_asks_query` now **unconditionally** selects `a.needs_operator_decision, a.operator_summary` and references `needs_operator_decision` in the `wait_kind` CASE, the `status` CASE, and the pin-to-top `ORDER BY`. There is **NO feature-detection** (unlike fc-v54's `_pane_context_has_pct`).

**I verified live on the substrate:** migration 118 is **NOT applied** — `information_schema.columns` shows neither `needs_operator_decision` nor `operator_summary` on `operator_asks` — and running the EXACT shipped query errors:
```
ERROR: column a.needs_operator_decision does not exist
```
So a **code-first deploy breaks the entire open board** (not just the hero): `fetch_fleet_asks()` → 500 → `/api/asks-board` 500 → board dark. The lane's "the UI is undefined-safe without it" is true for the **client JS only** (`a.needs_operator_decision` → `undefined` → falsy); it does **not** cover the **server SQL**, which fails before any payload is produced. `deploy_console.sh` does NOT apply migrations, so this ordering must be enforced out-of-band.

**Required sequence:** orch-console applies migration 118 (apply-gate pending in bus #75398, sha `acfe968e15d08e83`) via `apply_migration.py` → verify both columns live → THEN deploy fc-v81. This is the fc-v53 / operator_asks (hash `1700334a`) migration-before-code lesson again.

## Migration 118 — WELL-FORMED, cleared to apply
- Additive `ADD COLUMN IF NOT EXISTS` (idempotent); `needs_operator_decision boolean NOT NULL DEFAULT false` (constant default = metadata-only in PG11+, no table rewrite); `operator_summary text` nullable; `SET LOCAL lock_timeout='5s'`.
- Explicit column GRANTs: `console_readonly` → SELECT only; `service_role` → SELECT/INSERT/UPDATE/REFERENCES. **Verified live:** `operator_asks` grants are `console_readonly=SELECT`, `service_role=full`, **anon/authenticated = NONE** — so the new columns carry no anon/auth exposure (new-column grant checklist satisfied).
- 24 one-time `UPDATE … WHERE id = <n>` seeds (never identity INSERTs → no sequence corruption), idempotent. **Verified live: all 24 seed ids are present AND open (`closed_at IS NULL, triage_state='ask'`)** — the seed lands on exactly the real 2026-10-25 reconcile shortlist, zero no-ops, zero already-closed. Applied via `apply_migration.py` (not `supabase db push`), per the header. Seed summary *content fidelity* vs each ask is an operator/domain judgment (Musa sees them on his own console and can correct); I verify the mechanism is sound and the lines are plain-language with no agent code-names/secrets.

## Code — correct
- **heroCard (the Musa-facing surface, #75387):** renders `operator_summary || text` through **`esc()`** (XSS-safe), meta is **dates only** (asked / due / "decide by"), and emits **no `delegated_to` code-name and no status tag**. Node test locks all of it: hides the internal saga text when a summary is present, asserts no `cc-cosem-1` in the card, falls back to sanitized text when summary is null, and escapes a hostile `<img onerror=…>` summary. Other tabs keep `askCard`'s fleet-internal detail. **Render-confirmed** (the assigned aesthetic eyeball): three hero shapes read clean and plain — summary-present, fallback-to-sanitized-text, no-dates waiting row.
- **app.py `counts.needs_musa`** now ORs `waiting_on_operator OR needs_operator_decision` — matches the client `needsMusa()` exactly, resolving my #75389 minor-2 (server/client count can no longer disagree). Test asserts `needs_musa == 2` on a payload with one waiting + one flagged-only row.
- **asks_triage.py** `--needs-musa` is correctly tri-state (`CASE WHEN %s='on' THEN true WHEN %s='off' THEN false ELSE needs_operator_decision END` → absent leaves it untouched) and `--musa-summary` is `COALESCE(%s, operator_summary)` write-through, on all three actions; all values are bound params. conftest's ephemeral `operator_ledger_db` fixture mirrors migration 118.
- **Version** fc-v80→fc-v81 consistent across asks.html / fleet.js / sw.js / lanes.html (GATE-1 version-sync will pass).

## Empirical (my own, at f9bd350)
- Content hash recomputed = `a3d99e5a0c7f50ac` ✓.
- **node** `asks_board.test.js` → pass (incl. the 4 new heroCard locks).
- **console pytest** `test_db.py` + `test_app.py` → **116 passed** (no-DB Option B).
- **`tests/test_asks_triage.py` → 33 passed against a real ephemeral Postgres** (pg_dsn fixture, migration-118 schema) — the tri-state flag + summary COALESCE writes are wet-proven, not just mocked.
- Live read-only checks: migration 118 unapplied (the ⛔ above); 24 seed ids all live+open; grants anon/auth-free.

## Minor / non-blocking
1. **Branch scope:** commits `0b75ba7`/`69e0e2c` merge in `fleet-health/lanes-exact-match-boot-guard` (lanes boot-guard + `tests/test_lanes_boot_guard.py`) — outside the console gate's content hash and already carried on its own origin branch; not re-gated here, flagged for awareness.
2. The saved **`asks-desktop.png` is mobile-width** (1170px ≈ 390 logical @3×), identical to `asks-iphone.png` — the desktop viewport isn't actually captured in the artifact. The lane's DOM verifier reports no-overflow at 390+1440 (18/18); the hero aesthetic (my assigned check) reads clean regardless. Suggest the harness save a true 1440 capture next cycle.

**Bottom line:** code + migration are correct and clear. **Do NOT deploy fc-v81 until migration 118 is applied and both columns verified live** (route to orch-console, who owns the #75398 apply-gate and the deploy) — I reproduced the 500 live.
