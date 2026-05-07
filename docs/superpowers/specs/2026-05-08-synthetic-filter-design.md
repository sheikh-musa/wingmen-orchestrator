# BUG-PIPELINE-SYNTHETIC-FILTER-001 — Design Spec

**Date:** 2026-05-08
**Owner:** cc-orchestrator
**Parent decisions:** BUG-PIPELINE-SYNTHETIC-FILTER-001 (id 770), CAI-RESP-141 (id 803, clarifications)
**Target ship:** 2026-05-15

## Goal

Block synthetic E2E test bug reports at dispatch time so they don't consume ralphy slots or trigger queue_stall noise. Two-phase rollout (shadow → enforce) with operator-controlled cutover. Complementary to PR #28 (intake-side `is_test` flag).

## Background

- **Prior art:** PR #28 added `bug_reports.is_test` boolean (NOT NULL DEFAULT false), backfilled 19 rows via 3 patterns (`reporter LIKE '% (Test)'` endswith / `reporter LIKE 'cc-%-e2e'` / `description ILIKE '%please ignore%'`), cancelled 21 in-flight test jobs, and added `.eq("is_test", False)` filter to `bug_reports_poll`. Status='new' on those rows; not rejected.
- **Cai's spec:** dispatch-time auto-reject filter with status='rejected' contract, separate from intake-side is_test. Three classification rules, two-phase rollout, hot-disable flag.
- **CAI-RESP-141 clarifications:** rule (c) dropped (schema mismatch — no `repro_steps` column); `rejected_at`+`rejected_by` columns added; boot_briefing dual rows; manual cutover gate.

## Architecture

Two-layer synthetic detection:

```
INTAKE                                DISPATCH
───────                               ────────
bug_pipeline.create_bug_report        nervous_system/bug_reports_poll
  └─ _detect_is_test                    └─ synthetic_filter.classify
     (PR #28 rules — sticky flag)         (cai's a + b — gates dispatch)
     sets is_test=true                    sets status='rejected' (enforce)
                                          OR logs only (shadow)
                                          writes notification_log entry
                                          updates boot_briefing counter
```

The two layers act on different fields, run at different times, and apply different rules. Intake-side is a denormalized hint for forensics queries; dispatch-side is the structured, mode-gated rejection. The poll loop respects both: `is_test=true` rows are filtered before classification (cheaper); rows with `is_test=false` are then classified by cai's rules. A row may end up `(is_test=true, status='rejected')` if both fire — `status` is canonical for "did dispatch reject this."

**New module: `nervous_system/synthetic_filter.py`** — single responsibility. Pure `classify(bug)` returning matched rule + reason. Side-effecting `apply_classification` writes notification_log + (enforce mode only) updates `bug_reports.status`. Poll loop is the only caller.

## Classification rules (per CAI-RESP-141)

Two rules, OR-semantics:

- **(a) `description ~* '^E2E test bug report\.?\s*$'`** — exact placeholder phrase, case-insensitive, optional trailing period and whitespace. Catches the literal placeholder some E2E suites emit.
- **(b) `reporter_name LIKE '%(Test)%'`** — substring match (broader than PR #28's endswith). Catches "Some User (Test)", "(Test) Account", "Foo (Test) Bar".

Rule (c) (empty description + empty repro_steps) **dropped** per CAI-RESP-141 CL1 — no `repro_steps` column on `bug_reports`, and the rule was speculative defense-in-depth with FP risk on terse-but-real bugs.

## Data shapes

### In-memory

```python
@dataclass(frozen=True)
class SyntheticClassification:
    rule: Literal["a_e2e_placeholder", "b_test_reporter"]
    matched_text: str   # actual reporter/description that matched, for forensics
    reason: str = "synthetic_e2e_test"   # mirrors bug_reports.rejection_reason
```

### `bug_reports` schema additions (additive migration)

```sql
ALTER TABLE bug_reports
  ADD COLUMN IF NOT EXISTS rejected_at  TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS rejected_by  TEXT;
```

Existing columns reused (verified live):
- `rejection_reason` TEXT — already exists.
- `status` CHECK constraint already includes `'rejected'`.

### `notification_log` entry (text JSON in `message_text`)

```json
{
  "bug_id": "<uuid>",
  "rule": "b_test_reporter",
  "matched_text": "BAPA Admin (Test)",
  "mode": "shadow",
  "would_reject": true,
  "reporter_name": "...",
  "description_excerpt": "..."
}
```

Outer fields: `source='synthetic_filter'`, `decision_ref='BUG-PIPELINE-SYNTHETIC-FILTER-001'`, `channel='bug_reports'`, `recipient=<bug_id>`.

### `boot_briefing` counter (view UNION arms — NOT a writable table)

`boot_briefing` is a `VIEW` over multiple underlying tables (verified live). Two new UNION ALL arms compute synthetic_filter counts on-read from `notification_log`:

```sql
UNION ALL
SELECT 'synthetic_filter'::text AS source,
       'filtered_24h'::text     AS key,
       json_build_object(
         'count',   count(*),
         'last_at', max(created_at),
         'mode',    'enforce'
       ) AS context
  FROM notification_log
 WHERE source = 'synthetic_filter'
   AND (message_text::jsonb->>'mode') = 'enforce'
   AND created_at >= now() - interval '24 hours'
HAVING count(*) > 0     -- omit row when no matches in window

UNION ALL
SELECT 'synthetic_filter'::text AS source,
       'shadow_24h'::text       AS key,
       json_build_object(
         'count',   count(*),
         'last_at', max(created_at),
         'mode',    'shadow'
       ) AS context
  FROM notification_log
 WHERE source = 'synthetic_filter'
   AND (message_text::jsonb->>'mode') = 'shadow'
   AND created_at >= now() - interval '24 hours'
HAVING count(*) > 0
```

Migration uses `CREATE OR REPLACE VIEW boot_briefing AS <full existing definition + two new UNION arms>`. View self-heals automatically — no Python-side counter writes, no race conditions, no drift.

## Control flow

`nervous_system/bug_reports_poll.py::poll_bug_reports`:

```
if not _filter_enabled():                       # ENABLED=false → skip filter entirely
  fetch bugs, dispatch existing path
  return

fetch bugs (status='new', is_test=false, job_id IS NULL)   # PR #28's filter still active
for bug in bugs:
  classification = synthetic_filter.classify(bug)
  if classification is None:
    dispatch normally
    continue

  await synthetic_filter.apply_classification(supabase, bug, classification, mode=_filter_mode())
  if _filter_mode() == "enforce":
    continue   # rejected, skip dispatch
  # shadow: log fired, dispatch normally
  dispatch normally
```

`apply_classification` performs (in this order):
1. Insert into `notification_log` (source='synthetic_filter', message_text=JSON with mode/rule/etc).
2. **Enforce only:** UPDATE `bug_reports` SET status='rejected', rejection_reason='synthetic_e2e_test', rejected_at=now(), rejected_by='cc-orchestrator-filter'.

(No boot_briefing write — the view computes counts on-read from notification_log.)

## Mode resolution (env flags)

```python
def _filter_enabled() -> bool:
    return os.environ.get("ORCHESTRATOR_SYNTHETIC_FILTER_ENABLED", "true").lower() \
           not in ("false","0","no","off")

def _filter_mode() -> Literal["shadow", "enforce"]:
    return "enforce" if os.environ.get("ORCHESTRATOR_SYNTHETIC_FILTER_ENFORCE","false").lower() \
           in ("true","1","yes","on") else "shadow"
```

- **Defaults:** `ENABLED=true`, `ENFORCE=false` → shadow mode active on first deploy. No behavior change vs. pre-PR baseline (other than logging).
- **Mode read once per poll cycle** — not per bug. Within a cycle, all bugs use the same mode.
- **Pattern matches:** existing `RALPH_RUNNER_ENABLED`, `AUTOCC_POLL_ENABLED`, `ARCH030_ESCALATION_ENABLED` precedents.

## Migration (additive, pre-apply per CAI-RESP-102)

`supabase/migrations/20260508_bug_reports_synthetic_filter.sql` — four sections:

1. ADD COLUMN IF NOT EXISTS `rejected_at TIMESTAMPTZ`, `rejected_by TEXT` on `bug_reports`.
2. Backfill: UPDATE rows where status ∈ ('new','diagnosing') AND (rule a OR rule b OR `is_test=true`) → status='rejected', rejection_reason='synthetic_e2e_test', rejected_at=now(), rejected_by='cc-orchestrator-filter-backfill'. Union with `is_test=true` per rule-scope decision (cleans up PR #28 residuals).
3. `CREATE OR REPLACE VIEW boot_briefing AS <full existing definition + 2 new UNION ALL arms for synthetic_filter filtered_24h / shadow_24h>`. View change is the only "schema drift" artifact; the count-from-notification_log logic lives in SQL.
4. DO $$ block — assertion gate: count rows with `rejected_by='cc-orchestrator-filter-backfill'`, RAISE WARNING if zero, NOTICE otherwise. Also assert the boot_briefing view exists and has the two new arms (count UNION arms via `pg_get_viewdef`).

Idempotent (`ADD COLUMN IF NOT EXISTS`, UPDATE WHERE excludes already-rejected, CREATE OR REPLACE VIEW). No DROP / no data loss.

## Phased rollout

| Phase | When | Ops |
|---|---|---|
| Day 0 ship | Migration applied, code deployed, orch restarted. ENABLED=true ENFORCE=false → shadow. | `scripts/restart_orch.sh` |
| Days 0-2 | Shadow window. Operator monitors `boot_briefing.shadow_24h` + tails `notification_log` for `source='synthetic_filter' mode=shadow`. Targets: 5+ classifications, zero false positives. | manual review |
| Day 2+ cutover | Operator flips `ORCHESTRATOR_SYNTHETIC_FILTER_ENFORCE=true` in `.env`, restarts orch. Filter switches to enforce. | `.env` edit + restart |
| Any time | Hot-disable: flip `ORCHESTRATOR_SYNTHETIC_FILTER_ENABLED=false`, restart. Filter short-circuits. Reverts to PR #28-only behavior. | `.env` edit + restart |

## Testing

### Pure-unit (`tests/test_synthetic_filter.py`, no DB)

- `TestClassify::test_rule_a_e2e_placeholder_phrase` — positive (with/without trailing period, case-insensitive); negative ("E2E test bug report — actually broken" — extra text)
- `TestClassify::test_rule_b_test_reporter_substring` — positive ("Some User (Test)", "(Test) Account", "Foo (Test) Bar"); negative ("MyTest User", "Test User" without parens)
- `TestClassify::test_rule_c_dropped` — empty description + empty page_url + empty screenshot_url returns None (confirms drop)
- `TestClassify::test_normal_bug_returns_none`
- `TestMode::test_default_shadow` — neither env var set → enabled=true, mode=shadow
- `TestMode::test_enforce_when_flag_true`
- `TestMode::test_disabled_short_circuits`

### Live-DB integration (`tests/test_synthetic_filter_integration.py`, DATABASE_URL gated)

- `test_rejected_at_column_exists`, `test_rejected_by_column_exists` — schema assertions
- `test_shadow_mode_classifies_logs_does_not_reject` — INSERT synthetic, run poll, assert: status still 'new', notification_log entry mode=shadow, would_reject=true
- `test_enforce_mode_classifies_logs_rejects` — same shape, mode=enforce, assert: status='rejected', rejection_reason='synthetic_e2e_test', rejected_at NOT NULL, rejected_by='cc-orchestrator-filter', notification_log entry mode=enforce
- `test_normal_bug_dispatches_in_both_modes` — real-shaped bug, both modes, assert: status='diagnosing', no notification_log entry
- `test_filter_disabled_short_circuits` — ENABLED=false, synthetic bug, assert: dispatch happens, no notification_log entry
- `test_boot_briefing_view_exposes_synthetic_filter_count` — enforce mode, INSERT 2 synthetic via classifier, SELECT * FROM boot_briefing WHERE source='synthetic_filter' AND key='filtered_24h', assert context->>'count' = '2'
- `test_boot_briefing_view_omits_filtered_24h_when_zero` — no enforce-mode entries in 24h window → no `filtered_24h` row in view (HAVING count(*) > 0 clause)
- `test_backfill_flips_existing_synthetic_to_rejected` — pre/post migration snapshot

All integration tests use `psycopg.connect(autocommit=False)` + `c.rollback()` in finally to avoid polluting live DB.

### TDD discipline

Per superpowers:test-driven-development — every classifier rule starts with a failing test. Migration column additions start with failing schema-assertion tests. No production code lands without seeing its test fail first.

## File layout

```
supabase/migrations/20260508_bug_reports_synthetic_filter.sql   NEW
nervous_system/synthetic_filter.py                              NEW (~120 lines)
nervous_system/bug_reports_poll.py                              MODIFIED (~30 lines added)
tests/test_synthetic_filter.py                                  NEW (pure-unit)
tests/test_synthetic_filter_integration.py                      NEW (live-DB)
docs/superpowers/specs/2026-05-08-synthetic-filter-design.md   NEW (this doc)
.env.example                                                    MODIFIED (document the two flags)
```

## Out of scope

- Removing `is_test` column or `_detect_is_test` from intake (preserved per rule-scope-A).
- Adding `repro_steps` column (rule c dropped per CAI-RESP-141 CL1).
- Automated FP detector for cutover (manual gate per CL4).
- Reporting UI for boot_briefing — existing query patterns suffice.

## Risk register

- **Shadow→enforce premature cutover** — operator flips ENFORCE=true before zero-FP-on-5-samples. Mitigation: cutover gate is in human hands; this spec documents the gate but doesn't enforce it. Worst case: hot-disable via ENABLED=false reverts.
- **Rule (b) substring FP** — "(Test)" appears in a legitimate bug description. Mitigation: rule (b) only matches `reporter_name`, not `description`. Reporter names with literal "(Test)" parens are vanishingly unlikely outside test fixtures.
- **Notification_log volume in shadow mode** — every dispatch eligible bug gets logged in shadow. Mitigation: bug_reports_poll already limits 3 per cycle; shadow is 48h-bounded. Expected volume: <50 entries.
- **Backfill collision with concurrent intake** — migration runs while orch is live. Mitigation: migration is one transaction, fast (<1s for 19+ row update). Concurrent inserts use DEFAULT status='new' which the migration's WHERE picks up next cycle anyway.
