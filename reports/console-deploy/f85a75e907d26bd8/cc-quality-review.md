# cc-quality review — console fc-v53 (op#13186 bloat-accuracy / cliff-truth)

**Verdict: ⛔ FAIL — do NOT ship as-is.** The bloat-fix CODE is correct and I endorse it (fail-closed pct→pane_k→drop, no fc-v52 revert, 201 tests green, migration 043 well-formed). But it **cannot deploy as committed**: it fails its OWN version-sync gate, and the migration dependency is mischaracterized in a way that would dark the bloat feed. Two required fixes below — both trivial; re-review is a version-diff after.

- **Reviewer:** cc-quality (Head of Quality) — content-hash GATE-4.
- **Note on provenance:** the formal orch-console→cc-quality request was not yet on my bus when I reviewed; I acted on the operator's relay + the identified commit. Reviewed **`edaf9f5`** ("op#13186 fc-v53 — pane-truth at the cliff", parent 1ddc452 = fc-v52), which is HEAD (sw.js=fc-v53). Content hash `f85a75e907d26bd8`.
- **diff SHA-256:** `08aad9f0532ac4b231222eee40342d4fb43557fdfe224d7e81209938bb049b4b`
- **Reviewed (UTC):** 2026-08-15T00:08:10Z
- **Method:** verify-not-assert — read the app.py/db.py/publisher diff + migration 043, **reproduced deploy_console GATE-1** against the committed files, re-ran the suites.

---

## The code is correct (endorsed)
- **Fail-closed cliff-truth** (`_pane_entry` + new `_pct_to_level`): precedence is pct (`{N}% context used`, authoritative at the cliff) → else pane_k hint → else **DROPPED, never green**. `_pct_to_level` rejects `None`/non-int/`pct<=0`/`pct>100` (bad data → None, never a false reading). `src` marks which signal won. This correctly fixes op#13186 (a 100%-context lane published NULL pane_k and read as not-bloated; the pct line now catches it).
- **No fc-v52 revert:** console_assign.py present, `_handle_assign` + `_drain_board` intact, Command Surface untouched — the overlap was surgical (edited functions were fc-v51-identical; fc-v52's adjacent rework is untouched).
- **Migration 043** (`pane_context.pct smallint`, nullable): additive, `ADD COLUMN IF NOT EXISTS`, reversible, ops-cache class (no PII/money/governance/residency), RLS/grants inherited, apply via direct psycopg (decision-962 — matches the substrate rule). Well-formed.
- **Publisher** (`pane_bloat_signal.py`) parses `{N}% context used` → pct. End-to-end loop sound.
- **Tests:** `pytest tests/console/ tests/test_pane_bloat_signal.py tests/test_auto_recycle_on_bloat.py` → **201 passed** (re-ran; matches the builder).

## ⛔ BLOCKER 1 — the deploy fails its own GATE-1 (version-sync)
`deploy_console.sh` GATE-1 requires `sw.js VERSION == fleet.js APP_BUILD == lanes.html badge`. The fc-v53 commit bumped **only sw.js + fleet.js**; **lanes.html is still fc-v52** (and irsyad.html/js too — regressing the fc-v52 sync). I reproduced GATE-1 against the committed files:
```
sw.js=fc-v53  fleet.js=fc-v53  lanes.html=fc-v52  →  version constants OUT OF SYNC → fail (exit 3)
```
So `deploy_console.sh` aborts at GATE-1 before it ever reaches this GATE-4. **Required fix: bump `lanes.html` (gate-checked) to fc-v53** — and `irsyad.html`/`irsyad.js` for coherence (not gate-checked, but leaving them at fc-v52 re-opens the version-lag I flagged fc-v48→v51 and that fc-v52 had just closed).

## ⛔ BLOCKER 2 — "043 unapplied = no regression" is FALSE; the feed goes DARK
db.py now **unconditionally** selects the new column: `SELECT session, base, pane_k, pct, … ORDER BY pct DESC …`. If migration 043 is not applied, that query throws `column "pct" does not exist`; `_fleet_payload` catches it → `pane_rows=[]` → **UNKNOWN header + empty top-bloat glance on every poll**. That is strictly WORSE than fc-v52 (which showed real bloat via pane_k) — a regression, not the claimed "behaves exactly as fc-v52." **Consequences / required:**
1. **Strict ordering is load-bearing:** apply 043 **and verify the column exists** (`\d pane_context`) BEFORE fc-v53 serves traffic. If 043 apply fails or is skipped, the bloat feed darks — there is no graceful fallback.
2. **Correct the risk note** in the deploy plan: without 043 it is not "inert"; it is a dark feed.
3. **Recommended hardening (so a migration hiccup can't dark the feed):** make the read resilient to a missing `pct` — feature-detect the column, or `COALESCE`/catch-and-retry-without-pct — so the query degrades to fc-v52 behavior instead of erroring. Optional for this ship IF the ordering is enforced+verified, but it removes a real operational footgun.

## Bottom line
The op#13186 cliff-truth fix is well-built and correctly fail-closed — I endorse the logic, the migration design, and the no-revert. But **do not deploy `edaf9f5` as-is**: it fails deploy_console GATE-1 (lanes.html unbumped), and its migration dependency darks the feed if 043 isn't applied-and-verified first (contrary to the "no regression" framing). Fix the two and it ships clean:
1. bump lanes.html (+ irsyad) to fc-v53;
2. apply + verify migration 043 before deploy, and correct the "no regression" characterization (ideally add the missing-column resilience).
Re-review after the version bump is a 30-second diff.

— cc-quality
