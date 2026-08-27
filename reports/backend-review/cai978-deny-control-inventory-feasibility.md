# CAI-978 deny-control inventory — feasibility (can it be MEASURED, no hand-list?)

**For:** orch-console (Nazim), lane_tasks #60 · bus #23664 · **By:** cc-quality (Opus 4.8), 2026-08-17
**Question:** can an inventory of the fleet's deny-controls — and *when each last actually executed* — be derived by MEASUREMENT, with no separate list a human must remember to update?
**Answer: PARTIAL, and NO on today's substrate.** Enumeration of the *scoped* set is not purely measurable; "last executed" is measurable per-shape ONLY where the control emits a persisted per-run row, which today holds for ~one instance, not for the controls #60 targets. A board built on today's substrate reproduces the exact defect. A correct one needs, per shape, two specific builds — not one measurement. Details, evidence at source, and an attack on the leaned design below.

---

## The evidence already sitting in the substrate (this is the load-bearing part)

**1. A hand-maintained deny-control registry ALREADY EXISTS — and has already rotted to uselessness.** `invariant_registry` (34 rows) is exactly an inventory of the fleet's deny-controls (MONEY-1..7, RESIDENCY-1..4, AUTHORITY-1, DEPLOY-1, TOKENS-1, SECRET-HYGIENE-1, QA-EDGE-STATE-GATE-1, …) with a `gate_status` and a `last_asserted_at` column. Measured at source:
- **All 34 rows are `gate_status='MANUAL'` (32) or `'pending'` (2)** — none is a derived status.
- **`last_asserted_at` is NULL for 29 of 34** — including *every* MONEY-*, *all* RESIDENCY-*, AUTHORITY-1, DEPLOY-1, TOKENS-1. The 5 non-null are all 2026-07-16…22 (3–4 weeks stale).
- **`grep` across `scripts/` + `nervous_system/` finds ZERO code references to `invariant_registry`.** Nothing writes or refreshes it. `seeded_by ∈ {cai, cc-infra-seeded}`, `stewarded_by=cai`.
- → This is precisely the "a control that needs remembering is a sentence" failure #60 warns against, **already realized**: a registry with a `last_asserted_at` field that is 85% NULL and 15% weeks-stale because nothing measures it and no human remembers. **RESIDENCY-1 — the control that started this whole thread — sits here with `last_asserted_at = NULL`, rendering as blank/MANUAL, not amber.** Shipping a board on this table as-is would render the fleet's most critical deny-controls as "seeded/fine," which is the defect.

**2. A MEASURED readiness board also fails — because its measurer silently stopped.** `bug_pipeline_readiness` (10 gates) is a real readiness board *with* a live updater (`scripts/run_phase0_drills.py`, `scripts/fire_drills/base.py`, `nervous_system/pipeline_clock.py`). Yet all 10 rows read `status='green'`, `days_clean=84`, `last_breach_at=NULL`, **`updated_at` frozen at 2026-07-08** — i.e. the updater has not touched the table in 5+ weeks and the board still shows green. → **Second-order form of the same defect: "board reads runner output" inherits a fresh failure mode — the runner going silent is itself an unmeasured freshness gap.** A board that trusts its feed reads green when the feed dies. Any #60 design must measure *runner liveness*, not just runner output.

**3. SQL-assertion execution is unrecorded.** `migration_ledger` (49 rows) records DDL applies (`repo, migration_name, sha256, applied_at, applied_by`) — a genuinely MEASURED apply-record — but a scan for `isol|assert|residen|20_|decommis` returns only `020_shared_feed_dedup.sql` (unrelated DDL). **`db/ceayj-tenant/20_isolation_assertions.sql` — Nazim's own A3 example — appears nowhere.** Verification/assertion SQL run out-of-band (`psql -f` in a window) leaves no row. Mechanism exists; the assertion files are not wired to it.

**4. Self-skipping tests leave no persisted signal.** No execution record anywhere (DB or `scripts/`) for `tests/residency-shipforge-app.test.ts`. `node --test` emits skip-vs-run per test, but that output is ephemeral — there is no results sink in the substrate. A test that skipped forever is indistinguishable, after the fact, from one that passed.

**5. The ONE working instance of the measurable pattern.** `challenge_enforcer_dryrun_log` (185 accreting rows: `decision_ref, logged_at, processed, review_outcome`) is a deny-control that writes a row on every decision → its "last executed" IS `max(logged_at)`, by construction. And `watchdog_monitored_callers` (CAI-771) has the mechanism to measure a caller's last-fire cadence — but covers **1** caller. So the measurable pattern is proven-in-principle and ~zero-coverage.

---

## Per-shape verdict ("last executed" is a different measurement for each — Nazim is right about that)

| Shape | Measurable without a hand-list? | What it needs | Today |
|---|---|---|---|
| (a) self-skipping test suites | Yes **iff** a runner-sink persists per-test run-vs-skip | a test-results sink + CI wiring (`node --test` already emits the signal) | **NO** — ephemeral, no sink |
| (b) SQL assertion files | Yes **iff** assertion applies write to `migration_ledger` (or a sibling) | route `psql -f` assertion runs through the ledger | **NO** — assertions absent from the ledger |
| (c) fail-closed code gates | Yes — the `challenge_enforcer` pattern (gate writes a per-run row) | each gate emits an exercise row; **distinguish "gate ran" from "deny branch fired"** — a gate whose deny path never fires is unexercised on the axis that matters | **PARTIAL** — 1 instance |
| (d) runtime deny paths | Yes — CAI-771 watchdog / a structured "denied" event | each deny path emits a telemetry row on a real crossing | **~NO** — 1 caller covered |

A design covering only (a) is the "quietly scoped to the easy shape" partial you flagged; the boundary above is the full four.

---

## Attack on the leaned design (tag-in-control + board reads runner output)

**Where you're right (I concede it):** colocation beats a separate registry for enumeration *drift*. A tag authored at the control's own site, deleted with the control, cannot leave an orphan row and lives where the author is already editing. `invariant_registry`'s 85%-NULL hand-list is the direct proof that a *separate* registry rots. So on the drift axis, tag-in-control > registry. Agreed.

**Where it does not hold up — two residual failures, and these are the findings you asked me to produce:**

- **(i) The untagged-new-control gap is SILENT, and the board cannot self-detect it.** The board measures *presence* of tagged controls, not *absence* of untagged ones. A deny-control written without the tag renders as **nothing — not amber.** That is still a "needs remembering" failure; it is merely relocated (from "remember to update the registry" to "remember to tag the control") and made **invisible** — worse than the loud registry, which at least shows a stale row. **This is the crux: a tag-only design is a prettier drift.** It becomes honest ONLY when paired with an independent **completeness measurement** — a lint that enumerates *candidate* deny-controls by grep/AST (tests with `{skip}`+deny semantics, `*_assertions.sql`, fail-closed `raise/deny/throw` branches guarding money/residency/authority paths) and flags any candidate that carries no exercise-tag/sink. That lint is the measurable backstop that catches the forgotten tag. Without it, do not ship — it reproduces #60 in a new place.

- **(ii) The tag declares "deny-control"; it does not record when it last ran.** Freshness still requires each shape's runner to emit a persisted per-control exercise row into a common sink (the `challenge_enforcer` shape). Tag and sink are two separate builds; the tag alone gives you a prettier NULL (see: `invariant_registry.last_asserted_at`). And per evidence #2, the sink-reader must *also* measure runner-liveness, or a stopped runner reads green.

---

## Recommendation (single-ledger, per my charter — do NOT fork an N+1 list)

The existing ledgers ARE the substrate: `invariant_registry` (enumeration), `migration_ledger` (shape-b execution), CAI-771 `watchdog_monitored_callers` (shape-d), `challenge_enforcer_dryrun_log` (the working pattern). My charter (condition 4) binds me to reference these, not fork them. The honest #60 build is:
1. **Make `invariant_registry.last_asserted_at` MEASURED** — each control writes it (or a sibling exercise-row) on *real* exercise, via the per-shape sink. This converts the existing hand-list into the measured substrate instead of adding a new one.
2. **The completeness-lint** (attack finding i) — grep/AST enumeration of candidate deny-controls flagged when they lack a tag/sink. This is the measurable half that makes enumeration honest.
3. **A runner-liveness check** (evidence #2) — amber when the *measurer* itself has gone stale, not only when a control has.
Board status is then: `green` only if (asserted-real AND fresh AND measurer-live); `amber/UNEXERCISED` if (NULL last_asserted_at OR stale OR skip-detected OR candidate-without-tag OR measurer-silent). Never `green` on absence-of-signal.

## Bottom line
**PARTIAL / NO-as-things-stand.** Yes, it can be measured without a hand-list — but only per-shape, with (a)+(b)+(c)+(d) each needing an exercise-sink, plus a completeness-lint and a runner-liveness check to make the tag-in-control design honest. That is buildable and worth building; it is *not* a single measurement and *not* free, and shipping the amber pixel on the substrate as it stands today (`invariant_registry` hand-NULL, `bug_pipeline_readiness` frozen-green) would be the counter-example the row is meant to prevent.

**Could-not-measure (honest):** I did not locate the `challenge_enforcer_dryrun_log` writer in `scripts/`+`nervous_system/` (185 rows accrete from a writer outside those dirs) — so I assert the *pattern* is viable, not that this repo already implements it for the #60-scoped controls. And I did not exhaustively enumerate every fleet deny-control by hand — that enumeration is precisely the not-measurable-without-a-tag problem this report is about.

— cc-quality
