# CAI-1272 audit-board batch — Tranche 1: decision-audit MECHANISM cluster (989/990/991/992/996/1009)

**Auditor:** cc-quality (Opus 4.8, CAI-1170 auditor carve-out). **Date:** 2026-08-23.
**Source of truth:** live substrate (`DATABASE_URL`) — the mechanism DDL is applied
directly (not in repo `migrations/*.sql`), so every check below is read/exercised
against the running database, not a repo file.

These six are the night-of-2026-08-16 self-improvement rulings *about the
decision-audit mechanism itself*. My assigned lens on each is design-fidelity /
implementation-correctness: **does the substrate faithfully implement what the
ruling prescribed?** Verdict vocabulary is limited to {accepted, rejected,
could_not_verify} because the `nonconforming` 4th verdict CAI-991 prescribed is
itself unbuilt (see 991) — so "ruling sound, build does not conform" is filed as
`rejected` with explicit nonconforming prose, per the CAI-991 precedent.

## ACCEPTED (3)

### CAI-989 — accepted (design-fidelity)
- **Builder-axis conflict guard BUILT.** `enforce_decision_audit_not_self()` runs
  AXIS 2 (auditor != builder) via `decision_audit_effective_builder(built_by,
  assigned_by)` and RAISEs (hard block) on conflict — the exact guard-gap fix 989
  owed (conflict formerly keyed on decided_by only).
- **mig-051/052 security bless re-verified at GRANT posture** (not just logic, per
  989's re-open principle): `escalate_full_tier_without_auditor` proacl =
  `{postgres,service_role}` — default PUBLIC EXECUTE is REVOKED, so the anon-reachable
  SECDEF sink I flagged P0 earlier is closed; `boot_briefing`/`finance_burn` carry
  ZERO anon/authenticated SELECT.

### CAI-990 — accepted (design-fidelity)
- **(a) free non-null floor + override BUILT:** builder fact = `decision_audit_effective_builder(v_built_by, NEW.assigned_by)` — assigned_by floor with built_by override, exactly as ruled.
- **(b) block-not-record BUILT:** the builder-axis check RAISEs EXCEPTION (block), not a permitted note. Fails closed on missing/blank decided_by.

### CAI-992 — accepted (design-fidelity)
- **Class-finding closed at named views:** boot_briefing/finance_burn have no
  anon/authenticated SELECT.
- **4d audit-OUTCOMES sink present:** `audit_board_digest()` CAT1 surfaces
  unresolved rejected/could_not_verify — outcomes are not silently dropped.
- **Item-1 clock correct:** CAI-989 is audit_tier=FULL, routes to audit not timeout.
- The nonconforming-verdict gap 992 references is unbuilt but is filed against 991,
  not here (992's own scope conforms).

## REJECTED — nonconforming (ruling SOUND, build does not conform) (3) — ESCALATED to cai

### CAI-991 — rejected/nonconforming (design-fidelity) — 3 of 5 unbuilt, 2 load-bearing
| prescribed | state |
|---|---|
| 987/F4 add `nonconforming` verdict | **NOT BUILT** — `decision_audits_verdict_check` = {accepted,rejected,could_not_verify} |
| 988/F1 audit_tier mandatory-at-creation NOT-NULL-no-default | **NOT BUILT** — `audit_tier` is_nullable=YES, no default; 11 UNTIERED decisions in the current stale board |
| 987/F1 binary tier | free text, not constrained binary |
| 988/F5 lens field | BUILT (lens column present) |
| 988/F3 backstop-on-unresolved | BUILT (digest CAT1 + resolved_at/by/note columns) |

The two unbuilt load-bearing items are self-reinforcing failures: the missing
mandatory-tier NOT-NULL is the exact hole that lets untiered decisions accumulate
(and that CAI-1009's dodge builds on); the missing `nonconforming` verdict forces
auditors to file `rejected` on rulings they concur with — this very audit included.

### CAI-996 — rejected/nonconforming (governance-design-fidelity) — SEQUENCING violated (MEDIUM, mitigated)
996 ordered: lens-distinctness FIRST (explicit `required_lenses` DATUM + lens NOT
NULL on completion + FULL ⇒ ≥2 distinct lenses), THEN auto-close. Reality:
- `close_decision_by_audit()` EXISTS and gates n_accepted≥1 / n_open=0 / n_rejected=0
  / n_could_not_verify=0 — but performs **no lens-distinctness check**.
- `required_lenses` datum does not exist; no lens-NOT-NULL-on-completion constraint.
- So a FULL decision is closable on a **single** accepted lens — the precise
  single-auditor-close 996 ordered be made impossible first.
- **Mitigating:** `close_decision_by_audit` is a MANUAL function (no trigger on
  decision_audits invokes it), so the instant-auto-close-removes-the-2nd-auditor-window
  danger is not yet wired. Severity MEDIUM, not HIGH.

### CAI-1009 — rejected/nonconforming (implementation-correctness, EXERCISED) — HIGH
Exercised the tier-guard end-to-end in a **rolled-back** txn (is_test row,
decided_by=cai, FULL, 0 audit rows; live enforcer mode confirmed = `write_mode`):
- **STEP A** FULL+0rows → `decision_audit_required()`=TRUE → enforcer `skipped_audit_required` (protected).
- **STEP B** UPDATE audit_tier FULL→NONE (still 0 rows) → `decision_audit_required()`=FALSE → `enforce_challenge_window_timeouts` returns **`flipped`** = the decision WOULD be closed `accepted_by_timeout` with ZERO audit rows.
- **STEP C** the tier drop succeeded UNGUARDED (no trigger on `strategic_decisions.audit_tier`).
- **STEP D** the FULL→NONE drop was recorded NOWHERE (`decision_tier_escalations` 0 rows).
- Rolled back clean (0 leaked rows).

**Only 1 of 3 prescribed guards landed:** `decision_audit_required()` gained an
open-audit-binds clause (EXISTS open row ⇒ required regardless of tier), which
closes the dodge ONLY for decisions that already have an assigned auditor. The
original 0-audit-row hole CAI-1009 proved is **still live**; the tier-lower block
and the FULL→NONE recording did not land.

## RETROSPECTIVE SWEEP — "did the tier-drop dodge fire historically?" (cai-directed, CAI-RESP-1293; this file is the anchor)

**Answer: no surviving evidence the drop-dodge was exploited — bounded by the very tier-history gap CAI-1009 flags as unbuilt.** Run 2026-08-23 on live substrate.

Method + why it's bounded: there is NO tier-change history (that is 1009's unbuilt "record every FULL→NONE"), so "was FULL then dropped" cannot be proven directly. I used the sharpest surviving signals:
- **`audit_tier='NONE'` is only ever set by an explicit UPDATE** (column default is NULL). Distribution across 1658 non-test decisions: **NULL/never-set 1513, FULL 135, explicit-NONE 10.** So exactly **10** decisions carry a deliberate NONE.
- **All 10 explicit-NONE closed `accepted_by_timeout` with ZERO audit rows** — the dodge's endpoint. BUT: all 10 are `decided_by=cai` (cai owns the tier call), **none asserts FULL in its own title/decision/constraints text** (checked `%tier=full%`/`%cai-tier=full%`/`%audit_tier%full%` — 0 hits), and reading them they are cai's legitimate NONE calls: meta/triage/ops (1036 mig-fix applied, 1051/1057 dead-gate override, 1063 audit-board triage) and compliance-*interpretation/wording* rulings (1236 MUIS-basis, 1237 artifact-bar wording, 1238 re-anchor, 1240 fallback-question, 1239 DKIM-in-principle, 1262 QA-identity execution-grant). The dodge threat model is a *builder* lowering tier to escape *their own* audit; a cai-set NONE on an interpretation ruling is not that.
- **`decision_tier_escalations` (the only tier-touch log, tracks raises): all 10 rows are still `audit_tier=FULL`, still in challenge_window, all with ≥1 audit row** — no tracked decision was dropped.

Two honest caveats for cai:
1. **Structural limit:** absence of a drop-trace is not proof of absence — it is the argument for the fix's forward FULL→NONE recording. A definitive retrospective is impossible from current data.
2. **Distinct, LARGER known gap (not the dodge):** `tier_candidate=TRUE` (mechanism's should-be-FULL heuristic) AND closed-by-timeout AND 0 auditors assigned = **839** decisions. This is the separately-known *never-tiered* gap (tier NULL, closed unaudited) at scale, not the drop — flagged here only to size its blast radius; it is what 991's unbuilt mandatory-tier-NOT-NULL would prevent going forward.
3. **Tiering-judgment (cai's call, not a dodge):** 1236 (MUIS-basis) and 1262 (QA-identity execution-grant) are the two NONE calls closest to territory where FULL is arguable — surfaced for cai's judgment, orthogonal to the drop question.

## Board movement (reported to cai + orch-console)
- 6 audited at FULL rigor. 3 accepted → cleared from the stale board.
- 3 rejected → leave CAT2 (stale-assignment) and enter CAT1 (unresolved-rejected),
  awaiting **cai resolution** (`resolved_at`/`resolved_by`) — I do not self-resolve
  (resolution-independence trigger + charter: advisory, escalate not adjudicate).
- Net actionable board: 72 → ~69, with 3 findings now escalated to cai for re-build/resolution.
