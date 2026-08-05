# Weekly Substrate Audit — design (op#10385)

_A standing weekly practice: when weekly Max headroom would otherwise reset away (use-it-or-lose-it), spend it on a Fable-5 ultracode audit of the substrate + repos → ranked, actionable improvements. Read-only / propose-don't-apply._

## Trigger (built + tested: `scripts/weekly_substrate_audit_gate.py`)
- Runs in the pre-reset window each Wednesday (before the earliest pool reset).
- GO iff best-pool headroom ≥ **30%** (utilization ≤70%), on the pool about to reset. Runs on THAT pool's token, never a near-spent one. SKIP cleanly otherwise (no harm; next week).
- Verified 2026-08-04: Musa 96% used (skip), **Syed 37% headroom → GO**, resets 2026-08-05 10:00Z.

## Audit (Fable-5, ultracode multi-agent Workflow)
Fan out one focused auditor per target; each returns structured findings; then a synthesis/triage stage dedupes + ranks. **Budget-capped** to stay inside the chosen pool's headroom.

**Targets (prioritised; cap ~12 agents/run — rotate lower-priority targets across weeks):**
1. Substrate core — `wingmen_orch.py`, `nervous_system/` (ingest, tg_out, agent bus, watchdogs, monitors), `scripts/` + `scripts/lib/` (leases, reset/recycle, send scripts), migrations.
2. Active client repos (from REPOS.json + live lanes): ihsanos, irsyad silo, cosem-platform/exams/adcda, shipforge, storefront, branditqr, hifz-companion.
3. Lower-priority / rotate: dawah-pipeline, dookana (frozen), cosem-video-pipeline.

**Finding dimensions per target:** correctness/bugs · security (authz, secrets, RLS, injection) · residency/tenancy (TENANT-RESIDENCY-001) · tech-debt · simplification · cost/efficiency (token + compute) · test-coverage gaps · dead/duplicate code.

**Verify stage:** each candidate finding adversarially checked (real vs plausible) before it makes the report — no unverified findings.

## Output discipline (avoid audit-fatigue)
- Ranked, **deduped, top-N actionable** report (default N=10) — most-severe first, each with file:line + a concrete fix + effort estimate. Not a wall of text.
- Land to `reports/weekly-substrate-audit-<date>.md`; I triage top items into the fleet board and relay the top few to the operator.
- **Propose-don't-apply:** the audit NEVER changes code. Actionable items go through normal review/gates (money/residency/security via cai). Track what actually ships week-over-week.

## Recurrence (Phase 2 — after first run proves the shape)
launchd/cron weekly (Wed pre-reset, Mini-local time for the ~10:00Z Syed window), invoking the gate → on GO, a headless Fable-5 audit run → report + bus/nazim summary. Dead-man's-switch on the runner ([[feedback_monitors_need_deadmans_switch]]).

## First run
Tomorrow (2026-08-05) AM before the 10:00Z Syed reset: re-check gate → run scoped audit on Syed → triaged report → relay top-N to operator. Then finalise the launchd wrapper.
