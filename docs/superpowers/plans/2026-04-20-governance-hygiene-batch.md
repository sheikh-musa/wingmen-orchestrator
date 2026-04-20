# Governance Hygiene Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fold six governance-hygiene fixes (schema FK for supersession, trigger rewrite to stop hygiene-flip announce storms, auto-close announces when parent decisions ship, banned-prefix purge cron, agent_status_history TTL cron, and a `skipped_at` stamp on the Telegram notifier's None-skip path) into one Supabase migration + one Python code change, so the governance substrate stops depending on discipline to stay clean.

**Architecture:** One migration file (`20260420_governance_hygiene_batch.sql`) with four sections — schema (A), data fix (B), trigger rewrite (C), pg_cron jobs (D). One Python file change (`nervous_system/agent_messages_poll.py` L223-225) to stamp the new `skipped_at` column on routing-skip. One Python verification script (`scripts/verify_governance_hygiene_batch.py`) replaying the 13-row storm against the rewritten trigger + the auto-close path. Same apply protocol as ARCH-035 / ARCH-036 / LEDGER-049: CAI runs `apply_migration` via Supabase MCP after adversarial review.

**Tech Stack:** Supabase Postgres 14+ (CHECK constraints, BEFORE/AFTER triggers, pg_cron), Python 3.9 (`psycopg`, `supabase-py`), pytest.

**Spec sources (all in `agent_messages` thread `4af8f733-4ba4-48fd-91f0-ce0616b1a70b`):**
- msg 339 — CAI's GOVERNANCE-CLEANUP-001 task block (Step 2 scope)
- msg 346 — CAI's ACK + Q1-Q4 answers on Step 1 pre-audit
- msg 374 — Step 2 scope expansion (superseded enum, FK column, 4-case trigger matrix)
- msg 376 — FK ON DELETE RESTRICT, explicit trigger condition matrix
- msg 378 — Final Q1/Q2/Q3 answers: OLD-side trigger guard, banned-prefix regex, dedicated `skipped_at` column

Parent strategic_decisions: GOVERNANCE-CLEANUP-001 (not yet filed as a decision_ref — this plan is the working reference).

---

## Background — what each item fixes and why

### A. Schema additions

**A.1 `'superseded'` enum value.** Current `strategic_decisions_challenge_status_check` allows `unchallenged | challenge_window | challenged | accepted | overridden | cai_review_requested | informational | implemented`. No value denotes "this decision was replaced by a later decision with a lineage link." In Step 1 we flipped CAI-LEDGER-004 to `overridden` as a fallback; `overridden` actually means "Musa overrode the decision," a different governance event. Adding `superseded` separates those semantics.

**A.2 `superseded_by_decision_ref TEXT REFERENCES strategic_decisions(decision_ref) ON DELETE RESTRICT`.** Makes supersession lineage a first-class queryable relationship instead of commit-history archaeology. RESTRICT is belt-and-suspenders — `strategic_decisions` has no soft-delete/hard-delete path today, so the constraint never fires in normal ops, but it catches any future accidental `DELETE FROM strategic_decisions` against the pattern. Per CAI msg 376: "SET NULL would be a quiet amanah failure. RESTRICT forces the deleter into a deliberate choice."

**A.3 `agent_messages.skipped_at TIMESTAMPTZ`.** The notifier's `_format_telegram` returns None on P3 suppression (and other non-routable shapes); the current L223-225 `continue` skips the row without stamping anything, leaving it in the poll hot-set forever. CAI msg 378 rejected overloading `forwarded_to_telegram_at`: "Stamping it on a suppressed message would be false." Dedicated column preserves observability (future dashboards can distinguish "% routed" from "% suppressed") at the cost of one nullable TIMESTAMPTZ.

### B. Data fix

**B.1 CAI-LEDGER-004 re-flip `overridden` → `superseded` + populate FK `CAI-LEDGER-004-REV01`.** Corrects the interim vocabulary chosen in Step 1 once the correct value exists. Pure data-fix, one row.

**B.2 Bulk-close announce-noise rows msgs 360-372** (13 rows). These were legitimate acceptance-path announces fired by BUG-025's existing trigger when Step 1's flip transaction flipped pre-BUG-020 backlog rows from `challenge_window` → `accepted`. Not a trigger bug — the trigger fired correctly per its BUG-025 spec. The rows were preserved un-closed through Step 1 as regression fixtures; the Step 2 verification script (Task 2) asserts the rewritten trigger would NOT have produced them. Post-verification, we close them as part of the migration's post-schema DML so the test for "challenge_window remaining = 3" stays valid.

### C. Trigger rewrite

**C.1 BEFORE INSERT/UPDATE announce trigger — add OLD-side guard.** Per CAI msg 378 Q1 answer. Current `trigger_cai_decision_announce()` (in `20260419_bug025_acceptance_path_announce.sql`) only dedups via `NEW.announced_by_msg_id IS NOT NULL`. That catches "this row already announced" on UPDATE — BUT the 13-row storm row-set had `announced_by_msg_id IS NULL` on all 13, because those rows were filed pre-BUG-020 (before the column existed) and never auto-announced. Step 1's UPDATE then legitimately matched the BUG-025 acceptance-path guard. The fix: suppress announce on UPDATE if `OLD.execution_status = 'implemented'` — i.e., "don't announce a decision that has already shipped, regardless of whether it was previously announced." OR-combined with the existing `OLD.announced_by_msg_id IS NOT NULL` check. Semantics: "never announce something already announced OR already implemented."

**C.2 New AFTER UPDATE OF `execution_status` trigger — auto-close announce.** When a decision transitions `execution_status IS DISTINCT FROM 'implemented'` → `execution_status = 'implemented'`, stamp `responded_at = now()` on the linked announce row (via `announced_by_msg_id`). Closes the loop so BUG-025-style tripwires (requires_response=true) don't persist after the decision ships. Separate trigger (not folded into the BEFORE trigger) because it writes to a DIFFERENT table (`agent_messages`), not the row being modified.

### D. pg_cron jobs

**D.1 Banned-prefix purge (daily 03:15 UTC).** Per CAI msg 378 Q2. The ARCH-035 banned-prefix regex `^(CLAIM|STATUS|HEARTBEAT|DIGEST|COMPLETE):` filters these rows from the Telegram notifier — they're left UNREAD as a tripwire for the sending agent. Once 24h has elapsed and nobody has read or routed them, the row is stale evidence; pg_cron deletes. Criterion: matches banned-prefix AND `read_at IS NULL` AND `forwarded_to_telegram_at IS NULL` AND `skipped_at IS NULL` AND `created_at < now() - interval '24 hours'`. Four-NULL intersection = "nobody acted, nobody will."

**D.2 agent_status_history 90-day TTL (daily 04:00 UTC).** Per CAI-RESP-042 follow-up referenced in msg 339. The AFTER-trigger snapshot table from ARCH-035 grows unbounded; 90 days of forensic history is the retention window agreed in CAI-RESP-042.

### E. App code — stamp skipped_at (Python, separate commit)

`nervous_system/agent_messages_poll.py` L222-225: when `_format_telegram` returns None (P3 or non-routable shape), add `await _mark_skipped(supabase, msg_id)` before the `continue`. New helper parallel to `_mark_forwarded` but stamping `skipped_at`. Prevents the poll hot-set from re-processing these rows every 5 minutes.

---

## File map

| File | Change | Task |
|------|--------|------|
| `supabase/migrations/20260420_governance_hygiene_batch.sql` | **Create** — sections A (schema), B (data-fix), C (triggers), D (pg_cron) | 1 |
| `scripts/verify_governance_hygiene_batch.py` | **Create** — 6-case live verification matrix | 2 |
| *(post as review_request to CAI — no file changes)* | | 3 |
| *(integrate review notes — may modify files created in Tasks 1-2)* | | 4 |
| *(commit migration + verification script)* | | 5 |
| *(CAI applies migration via MCP — no file changes)* | | 6 |
| *(run verification matrix against applied migration)* | | 7 |
| `nervous_system/agent_messages_poll.py` | **Modify** — add `_mark_skipped` helper, call it on L223-225 None-skip path | 8 |
| `tests/test_agent_messages_poll.py` | **Modify** — add unit test for `_mark_skipped` called on P3 / None path | 9 |
| *(commit app code)* | | 10 |
| `STATUS.md` | **Modify** — append "governance hygiene batch shipped" entry | 11 |
| *(post `work_outputs` row + digest to CAI)* | | 12 |

## Commit strategy

Three commits, sequenced:

1. **Commit 1 (after CAI review)** — migration SQL + verification script. Atomic.
2. **Commit 2 (after apply + smoke)** — app code: `agent_messages_poll.py` + `test_agent_messages_poll.py`. Atomic.
3. **Commit 3** — `STATUS.md` update + digest post.

Migration apply is out-of-band via CAI MCP between Commits 1 and 2 (same protocol as ARCH-035 / ARCH-036 / LEDGER-049). Task 6 blocks Tasks 7-8 until CAI reports applied.

---

## Task 1: Write migration SQL

**Files:**
- Create: `supabase/migrations/20260420_governance_hygiene_batch.sql`

- [ ] **Step 1: Create the migration file with full contents**

Write the file with this exact content:

```sql
-- Governance hygiene batch — composes 6 structural fixes into one migration.
--
-- Parent: GOVERNANCE-CLEANUP-001 thread in agent_messages (thread_id
-- 4af8f733-4ba4-48fd-91f0-ce0616b1a70b). Negotiated across msgs 339, 346,
-- 374, 376, 378. See docs/superpowers/plans/2026-04-20-governance-hygiene-batch.md.
--
-- Sections:
--   A. Schema — 'superseded' enum value, superseded_by_decision_ref FK,
--               agent_messages.skipped_at column
--   B. Data fix — CAI-LEDGER-004 vocabulary correction, bulk-close msgs 360-372
--   C. Trigger rewrite — OLD-side guard + new AFTER trigger for auto-close
--   D. pg_cron — banned-prefix purge (24h) + agent_status_history TTL (90d)
--
-- Applied via CAI Supabase MCP (same protocol as ARCH-035/036, LEDGER-049).
-- Authors: cc-ihsanos-3 (spec + SQL), cai (adversarial review).

BEGIN;

-- ══════════════════════════════════════════════════════════════════════
-- Section A — Schema additions
-- ══════════════════════════════════════════════════════════════════════

-- A.1 Add 'superseded' to challenge_status CHECK.
ALTER TABLE strategic_decisions
  DROP CONSTRAINT IF EXISTS strategic_decisions_challenge_status_check;

ALTER TABLE strategic_decisions
  ADD CONSTRAINT strategic_decisions_challenge_status_check
  CHECK (challenge_status = ANY (ARRAY[
    'unchallenged'::text,
    'challenge_window'::text,
    'challenged'::text,
    'accepted'::text,
    'overridden'::text,
    'cai_review_requested'::text,
    'informational'::text,
    'implemented'::text,
    'superseded'::text
  ]));

-- A.2 Add superseded_by_decision_ref FK column (ON DELETE RESTRICT per CAI msg 376).
ALTER TABLE strategic_decisions
  ADD COLUMN IF NOT EXISTS superseded_by_decision_ref TEXT
    REFERENCES strategic_decisions(decision_ref) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS strategic_decisions_superseded_idx
  ON strategic_decisions (superseded_by_decision_ref)
  WHERE superseded_by_decision_ref IS NOT NULL;

-- A.3 Add agent_messages.skipped_at (notifier None-skip stamp — per CAI msg 378 Q3).
ALTER TABLE agent_messages
  ADD COLUMN IF NOT EXISTS skipped_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS agent_messages_skipped_idx
  ON agent_messages (skipped_at)
  WHERE skipped_at IS NOT NULL;

-- ══════════════════════════════════════════════════════════════════════
-- Section B — Data fix
-- ══════════════════════════════════════════════════════════════════════

-- B.1 CAI-LEDGER-004 vocabulary correction: overridden → superseded with lineage.
UPDATE strategic_decisions
   SET challenge_status = 'superseded',
       superseded_by_decision_ref = 'CAI-LEDGER-004-REV01',
       updated_at = now()
 WHERE decision_ref = 'CAI-LEDGER-004'
   AND challenge_status = 'overridden';

-- B.2 Bulk-close announce-noise rows from Step 1 hygiene-flip transaction.
--     These are legitimate BUG-025 acceptance-path announces that fired on
--     pre-BUG-020 backlog rows (never previously announced). Trigger rewrite
--     in Section C prevents future recurrence; this closes the 13 rows
--     preserved as regression fixtures for Task 2's verification script.
UPDATE agent_messages
   SET responded_at = now(),
       response_ref = 'GOVERNANCE-CLEANUP-001-hygiene-flip'
 WHERE id BETWEEN 360 AND 372
   AND responded_at IS NULL;

-- ══════════════════════════════════════════════════════════════════════
-- Section C — Trigger rewrite
-- ══════════════════════════════════════════════════════════════════════

-- C.1 Rewrite BEFORE INSERT/UPDATE announce trigger with OLD-side guard.
--     Semantics: "never announce something already announced OR already implemented."
--     Supersedes the trigger function in 20260419_bug025_acceptance_path_announce.sql.
--     INSERT/UPDATE triggers from 20260419_bug020_bug021_governance_comms_hardening.sql
--     (cai_decision_announce_insert, cai_decision_announce_update) carry over — the
--     function they call is replaced.

CREATE OR REPLACE FUNCTION trigger_cai_decision_announce()
RETURNS TRIGGER AS $$
DECLARE
  v_msg_id BIGINT;
  v_subject TEXT;
  v_body TEXT;
  v_message_type TEXT;
  v_requires_response BOOLEAN;
BEGIN
  -- Shared early exits (INSERT + UPDATE).
  IF NEW.source IS DISTINCT FROM 'claude_ai_session'
     OR NEW.challenge_status NOT IN ('challenge_window', 'accepted')
     OR COALESCE(NEW.bypass_review, false) = true
     OR NEW.announced_by_msg_id IS NOT NULL THEN
    RETURN NEW;
  END IF;

  -- UPDATE-only OLD-side guard (CAI msg 378 Q1):
  -- suppress if row was ALREADY announced or ALREADY implemented BEFORE this UPDATE.
  -- Prevents hygiene-flip announce storms on pre-BUG-020 backlog rows.
  IF TG_OP = 'UPDATE' THEN
    IF OLD.announced_by_msg_id IS NOT NULL
       OR OLD.execution_status = 'implemented' THEN
      RETURN NEW;
    END IF;
  END IF;

  -- Branch message shape on challenge_status (BUG-025 behaviour, preserved).
  IF NEW.challenge_status = 'challenge_window' THEN
    v_message_type := 'review_request';
    v_subject := NEW.decision_ref || ': ' || NEW.title || ' — for review + challenge';
    v_requires_response := true;
  ELSE
    -- challenge_status = 'accepted'
    v_message_type := 'decision';
    v_subject := NEW.decision_ref || ': ' || NEW.title;
    v_requires_response := false;
  END IF;

  v_body := format(
    E'Decision %s filed by CAI (status: %s).\nFull spec: see strategic_decisions.decision_ref=%s%s\n',
    NEW.decision_ref,
    NEW.challenge_status,
    NEW.decision_ref,
    CASE WHEN NEW.parent_ref IS NOT NULL
         THEN E'\nParent: ' || NEW.parent_ref
         ELSE '' END
  );

  INSERT INTO agent_messages (
    thread_id, from_agent, to_agent, message_type,
    subject, body, requires_response
  ) VALUES (
    gen_random_uuid(), 'cai', 'cc-ihsanos', v_message_type,
    v_subject, v_body, v_requires_response
  )
  RETURNING id INTO v_msg_id;

  NEW.announced_by_msg_id := v_msg_id;
  NEW.notified_at := now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- C.2 New AFTER UPDATE trigger — auto-close the announce row when the parent
--     decision transitions into execution_status='implemented'.
--     Separate function because it writes to agent_messages (not the row being
--     modified) — BEFORE triggers can't reliably do this while preserving the
--     NEW assignment contract.

CREATE OR REPLACE FUNCTION trigger_cai_decision_autoclose_announce()
RETURNS TRIGGER AS $$
BEGIN
  -- Fire only on the transition INTO 'implemented'.
  IF NEW.execution_status = 'implemented'
     AND (OLD.execution_status IS DISTINCT FROM 'implemented')
     AND NEW.announced_by_msg_id IS NOT NULL THEN
    UPDATE agent_messages
       SET responded_at = now(),
           response_ref = 'auto-closed-on-implementation:' || NEW.decision_ref
     WHERE id = NEW.announced_by_msg_id
       AND responded_at IS NULL;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS cai_decision_autoclose_announce ON strategic_decisions;
CREATE TRIGGER cai_decision_autoclose_announce
  AFTER UPDATE OF execution_status ON strategic_decisions
  FOR EACH ROW EXECUTE FUNCTION trigger_cai_decision_autoclose_announce();

-- ══════════════════════════════════════════════════════════════════════
-- Section D — pg_cron jobs
-- ══════════════════════════════════════════════════════════════════════

-- D.1 Banned-prefix purge (mirrors nervous_system/agent_messages_poll.py L45
--     _BANNED_PREFIX_RE; regex kept in sync by convention, not constraint).
SELECT cron.schedule(
  'governance_banned_prefix_purge_24h',
  '15 3 * * *',
  $CRON$
    DELETE FROM agent_messages
     WHERE subject ~ '^(CLAIM|STATUS|HEARTBEAT|DIGEST|COMPLETE):'
       AND read_at IS NULL
       AND forwarded_to_telegram_at IS NULL
       AND skipped_at IS NULL
       AND created_at < now() - interval '24 hours'
  $CRON$
);

-- D.2 agent_status_history 90-day TTL (CAI-RESP-042 follow-up).
SELECT cron.schedule(
  'agent_status_history_90d_ttl',
  '0 4 * * *',
  $CRON$
    DELETE FROM agent_status_history
     WHERE created_at < now() - interval '90 days'
  $CRON$
);

COMMIT;
```

- [ ] **Step 2: Verify file lands in the right location**

Run:
```bash
ls -la supabase/migrations/20260420_governance_hygiene_batch.sql
```

Expected: file exists, sits lexicographically after `20260420_arch036_priority_column.sql` so Supabase applies it after ARCH-036.

- [ ] **Step 3: Parse-check the SQL**

Run:
```bash
source .venv/bin/activate && python -c "
import os, psycopg
from dotenv import load_dotenv
load_dotenv('.env')
with open('supabase/migrations/20260420_governance_hygiene_batch.sql') as f:
    sql = f.read()
# Dry-parse without executing: use EXPLAIN or a rollback transaction.
with psycopg.connect(os.environ['DATABASE_URL']) as conn:
    conn.autocommit = False
    with conn.cursor() as cur:
        try:
            cur.execute(sql)
            conn.rollback()
            print('PARSE+EXEC OK (rolled back, not committed)')
        except Exception as e:
            print(f'PARSE/EXEC FAIL: {e}')
            conn.rollback()
"
```

Expected: `PARSE+EXEC OK (rolled back, not committed)`. If any error, fix the SQL before proceeding. This step actually runs the migration inside a rolled-back transaction to catch constraint violations, FK issues, and cron.schedule errors before sending it to CAI — cheap local smoke test.

Note: `cron.schedule` on pg_cron returns an integer job_id even on a rolled-back transaction; if a cron job with the same name already exists the call fails. If that happens, the rollback is clean. To catch duplicate-name errors without side-effect, you can temporarily replace the cron.schedule lines with `cron.unschedule + cron.schedule` during the check, then restore — but don't ship the `unschedule` in the committed migration (it would break the first apply on a clean DB).

- [ ] **Step 4: Commit check — DO NOT COMMIT YET**

The migration is NOT committed at this step. Task 5 commits it only after CAI's adversarial review (Task 3) + any integrations (Task 4).

Run:
```bash
git status supabase/migrations/20260420_governance_hygiene_batch.sql
```

Expected: file listed as untracked.

---

## Task 2: Write verification script

**Files:**
- Create: `scripts/verify_governance_hygiene_batch.py`

- [ ] **Step 1: Create the verification script**

Write the file with this exact content:

```python
"""
Governance hygiene batch — 6-case live verification matrix.

Run AFTER the migration is applied via CAI MCP. Each case inserts a test
fixture row into strategic_decisions (and related tables), asserts trigger
behaviour, then rolls back the transaction to leave the DB clean.

Usage:
    python scripts/verify_governance_hygiene_batch.py

Exit code 0 = all cases pass. Non-zero = at least one case failed.

Cases:
    1. Schema: 'superseded' enum value accepted by CHECK constraint
    2. Schema: superseded_by_decision_ref FK constraint enforced (RESTRICT)
    3. Schema: agent_messages.skipped_at column exists + nullable
    4. Trigger: fresh CAI decision INSERT fires announce (BUG-025 preserved)
    5. Trigger: hygiene-flip UPDATE on already-implemented row does NOT fire
       announce (13-row storm regression test — msg 378 Section F item 11)
    6. Trigger: execution_status transition INTO 'implemented' auto-closes
       the linked announce row via the new AFTER trigger

Each case uses a SAVEPOINT/ROLLBACK pattern so fixture rows never persist.
"""

from __future__ import annotations

import os
import sys
import uuid

import psycopg
from dotenv import load_dotenv


def _agent_id_guc(cur: psycopg.Cursor, agent_id: str) -> None:
    """Set app.current_agent_id GUC for ARCH-035 identity trigger."""
    cur.execute("SELECT set_config('app.current_agent_id', %s, true)", (agent_id,))


def case_1_superseded_enum(cur: psycopg.Cursor) -> tuple[bool, str]:
    """CHECK constraint allows 'superseded' as challenge_status."""
    ref = f"VERIFY-CASE1-{uuid.uuid4().hex[:8]}"
    try:
        cur.execute(
            """
            INSERT INTO strategic_decisions
                (decision_ref, title, decision, reasoning, domain,
                 challenge_status, source)
            VALUES (%s, 't', 'd', 'r', 'architecture', 'superseded',
                    'claude_ai_session')
            """,
            (ref,),
        )
        return True, "CHECK accepts 'superseded'"
    except psycopg.errors.CheckViolation as e:
        return False, f"CHECK rejects 'superseded': {e}"


def case_2_fk_restrict(cur: psycopg.Cursor) -> tuple[bool, str]:
    """FK superseded_by_decision_ref enforces existence; RESTRICT on delete."""
    child_ref = f"VERIFY-CASE2-CHILD-{uuid.uuid4().hex[:8]}"
    try:
        cur.execute(
            """
            INSERT INTO strategic_decisions
                (decision_ref, title, decision, reasoning, domain,
                 challenge_status, source, superseded_by_decision_ref)
            VALUES (%s, 't', 'd', 'r', 'architecture', 'superseded',
                    'claude_ai_session', 'DOES-NOT-EXIST-12345')
            """,
            (child_ref,),
        )
        return False, "FK accepted a non-existent superseded_by_decision_ref"
    except psycopg.errors.ForeignKeyViolation:
        return True, "FK correctly rejects non-existent parent"


def case_3_skipped_at_column(cur: psycopg.Cursor) -> tuple[bool, str]:
    """agent_messages.skipped_at exists, nullable, TIMESTAMPTZ."""
    cur.execute(
        """
        SELECT data_type, is_nullable
          FROM information_schema.columns
         WHERE table_name = 'agent_messages'
           AND column_name = 'skipped_at'
        """
    )
    row = cur.fetchone()
    if row is None:
        return False, "skipped_at column missing"
    data_type, is_nullable = row
    if data_type != "timestamp with time zone":
        return False, f"skipped_at wrong type: {data_type}"
    if is_nullable != "YES":
        return False, "skipped_at should be nullable"
    return True, "skipped_at column present (timestamptz, nullable)"


def case_4_fresh_announce_fires(cur: psycopg.Cursor) -> tuple[bool, str]:
    """Fresh CAI-filed decision INSERT produces an agent_messages announce."""
    ref = f"VERIFY-CASE4-{uuid.uuid4().hex[:8]}"
    cur.execute(
        """
        INSERT INTO strategic_decisions
            (decision_ref, title, decision, reasoning, domain,
             challenge_status, source)
        VALUES (%s, 'verify case 4', 'd', 'r', 'architecture',
                'challenge_window', 'claude_ai_session')
        RETURNING announced_by_msg_id
        """,
        (ref,),
    )
    (msg_id,) = cur.fetchone()
    if msg_id is None:
        return False, "INSERT did not produce announced_by_msg_id"
    cur.execute(
        "SELECT subject, requires_response FROM agent_messages WHERE id = %s",
        (msg_id,),
    )
    row = cur.fetchone()
    if row is None:
        return False, f"announce msg {msg_id} not found in agent_messages"
    subject, requires_response = row
    if not subject.startswith(ref):
        return False, f"announce subject missing ref prefix: {subject}"
    if not requires_response:
        return False, "challenge_window announce should require response"
    return True, f"announce fired correctly (msg_id={msg_id})"


def case_5_hygiene_flip_suppressed(cur: psycopg.Cursor) -> tuple[bool, str]:
    """OLD.execution_status='implemented' UPDATE does NOT fire a new announce.

    Regression test for the 13-row storm from Step 1. Insert a row simulating
    pre-BUG-020 backlog (announced_by_msg_id=NULL, execution_status set
    directly on INSERT to bypass the initial announce), then flip its
    challenge_status challenge_window → accepted and assert NO new announce.
    """
    ref = f"VERIFY-CASE5-{uuid.uuid4().hex[:8]}"
    # Insert with bypass_review=true so the initial INSERT does NOT announce,
    # then we manually flip the columns to simulate the pre-BUG-020 backlog
    # shape: implemented + challenge_window + announced_by_msg_id NULL.
    cur.execute(
        """
        INSERT INTO strategic_decisions
            (decision_ref, title, decision, reasoning, domain,
             challenge_status, source, bypass_review, execution_status)
        VALUES (%s, 'verify case 5', 'd', 'r', 'architecture',
                'challenge_window', 'claude_ai_session', true, 'implemented')
        """,
        (ref,),
    )
    # Now clear bypass_review so the guard logic has to rely on OLD-side check.
    cur.execute(
        "UPDATE strategic_decisions SET bypass_review=false WHERE decision_ref=%s",
        (ref,),
    )
    # Snapshot agent_messages count before the hygiene flip.
    cur.execute(
        "SELECT COUNT(*) FROM agent_messages WHERE subject LIKE %s",
        (f"{ref}:%",),
    )
    (before_count,) = cur.fetchone()

    cur.execute(
        """
        UPDATE strategic_decisions
           SET challenge_status = 'accepted'
         WHERE decision_ref = %s
        """,
        (ref,),
    )

    cur.execute(
        "SELECT COUNT(*) FROM agent_messages WHERE subject LIKE %s",
        (f"{ref}:%",),
    )
    (after_count,) = cur.fetchone()

    if after_count > before_count:
        return False, (
            f"hygiene flip produced {after_count - before_count} announce(s) "
            f"— OLD-side guard not working"
        )
    return True, "hygiene flip correctly suppressed (0 new announces)"


def case_6_autoclose_on_implementation(cur: psycopg.Cursor) -> tuple[bool, str]:
    """execution_status transition to 'implemented' auto-closes the announce."""
    ref = f"VERIFY-CASE6-{uuid.uuid4().hex[:8]}"
    # Fresh CAI decision — trigger fires an announce with requires_response=true.
    cur.execute(
        """
        INSERT INTO strategic_decisions
            (decision_ref, title, decision, reasoning, domain,
             challenge_status, source)
        VALUES (%s, 'verify case 6', 'd', 'r', 'architecture',
                'challenge_window', 'claude_ai_session')
        RETURNING announced_by_msg_id
        """,
        (ref,),
    )
    (msg_id,) = cur.fetchone()
    if msg_id is None:
        return False, "initial INSERT did not fire announce"

    # Confirm announce starts unclosed.
    cur.execute("SELECT responded_at FROM agent_messages WHERE id = %s", (msg_id,))
    (responded_at,) = cur.fetchone()
    if responded_at is not None:
        return False, "announce already closed before implementation"

    # Transition to implemented.
    cur.execute(
        "UPDATE strategic_decisions SET execution_status='implemented' WHERE decision_ref=%s",
        (ref,),
    )

    cur.execute(
        "SELECT responded_at, response_ref FROM agent_messages WHERE id = %s",
        (msg_id,),
    )
    row = cur.fetchone()
    if row is None:
        return False, "announce row disappeared"
    responded_at, response_ref = row
    if responded_at is None:
        return False, "auto-close did not stamp responded_at"
    if response_ref is None or not response_ref.startswith("auto-closed-on-implementation:"):
        return False, f"response_ref missing or wrong shape: {response_ref}"
    return True, f"auto-close stamped responded_at + response_ref={response_ref}"


CASES = [
    ("Case 1 — superseded enum", case_1_superseded_enum),
    ("Case 2 — FK RESTRICT", case_2_fk_restrict),
    ("Case 3 — skipped_at column", case_3_skipped_at_column),
    ("Case 4 — fresh announce fires", case_4_fresh_announce_fires),
    ("Case 5 — hygiene flip suppressed", case_5_hygiene_flip_suppressed),
    ("Case 6 — auto-close on implementation", case_6_autoclose_on_implementation),
]


def main() -> int:
    load_dotenv(".env")
    dsn = os.environ["DATABASE_URL"]
    agent_id = "cc-ihsanos-3"

    total, passed = 0, 0
    failures: list[str] = []

    with psycopg.connect(dsn, autocommit=False) as conn:
        for name, case in CASES:
            total += 1
            with conn.cursor() as cur:
                cur.execute("SAVEPOINT case_start")
                try:
                    _agent_id_guc(cur, agent_id)
                    ok, detail = case(cur)
                except Exception as e:
                    ok, detail = False, f"exception: {e!r}"
                cur.execute("ROLLBACK TO SAVEPOINT case_start")
            status = "PASS" if ok else "FAIL"
            print(f"  [{status}] {name}: {detail}")
            if ok:
                passed += 1
            else:
                failures.append(f"{name}: {detail}")
        conn.rollback()

    print(f"\n{passed}/{total} cases passed.")
    if failures:
        print("\nFailures:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Syntax-check the script**

Run:
```bash
source .venv/bin/activate && python -c "import ast; ast.parse(open('scripts/verify_governance_hygiene_batch.py').read()); print('OK')"
```

Expected: `OK`.

- [ ] **Step 3: DO NOT RUN the script yet**

The migration is not applied — running now would fail on Cases 1, 2, 3, 5, 6. Task 7 runs it after apply.

---

## Task 3: Post plan + SQL + verification script as review_request to CAI

**Files:** (no file changes — governance message post)

- [ ] **Step 1: Post review_request to CAI**

Run:
```bash
source .venv/bin/activate && python << 'PYEOF'
import os, psycopg
from dotenv import load_dotenv
load_dotenv('.env')

with open('supabase/migrations/20260420_governance_hygiene_batch.sql') as f:
    sql = f.read()
with open('scripts/verify_governance_hygiene_batch.py') as f:
    verify = f.read()

body = f"""[posted by cc-ihsanos-3]

Re: msg 378 — Step 2 design doc + SQL + verification script for adversarial review. Pre-apply review, not commit. Same pattern as LEDGER-049 msg 340 handoff.

═══ PLAN DOC ═══
docs/superpowers/plans/2026-04-20-governance-hygiene-batch.md (committed only after your review).

═══ MIGRATION SQL (full body, hash-identify when applying) ═══

```sql
{sql}
```

═══ VERIFICATION SCRIPT (full body) ═══

```python
{verify}
```

═══ PRE-FLIGHT ═══

I dry-ran the SQL locally inside a rolled-back transaction via psycopg — parse OK, no constraint violations, cron jobs register correctly (local rollback of cron.schedule is clean).

═══ HARDENING CANDIDATES YOU MAY WANT TO FLAG ═══

Inviting the same class of review that caught LEDGER-049's 8a1aafb hardening delta (search_path pin, ERRCODE, etc.):

1. search_path pin on the two trigger functions — currently inherits session search_path. Consider `SET search_path = public, pg_temp` as a proconfig attribute on both functions. Low cost, closes CVE-2018-1058 class at the function boundary.
2. No explicit ERRCODE on any RAISE — there are no RAISE EXCEPTION calls in this migration. Nothing to pin.
3. Idempotency: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` handles re-apply; `CREATE OR REPLACE FUNCTION` handles function re-apply; `DROP TRIGGER IF EXISTS ... CREATE TRIGGER` handles the new AFTER trigger. But `cron.schedule('name', ...)` is NOT idempotent — calling it twice with the same name errors. If the migration may re-apply on a DB that already has these cron jobs, wrap the two cron.schedule calls with a guard. Proposed shape:
   ```sql
   DO $$ BEGIN
     IF NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname='governance_banned_prefix_purge_24h') THEN
       PERFORM cron.schedule(...);
     END IF;
   END $$;
   ```
4. DROP CONSTRAINT + ADD CONSTRAINT on challenge_status_check is not wrapped in a DO block — Postgres is atomic on this inside a transaction, so fine, but if anything in Section A fails, the enum is re-added successfully and subsequent sections see the new enum. That's actually desirable (the whole BEGIN/COMMIT is one txn) — flagging only so you can confirm my mental model.
5. B.2 uses `id BETWEEN 360 AND 372`: brittle if the noise rows got cleaned up outside-of-band. Guard: `AND responded_at IS NULL` already present — closing an already-closed row would be harmless but surprising. Consider adding `AND response_ref IS NULL` too.
6. Section D banned-prefix regex is duplicated from `nervous_system/agent_messages_poll.py` L45. If the Python regex changes, the cron job drifts. Options: (a) ship as-is with a convention comment, (b) create a helper function `is_banned_prefix(text)` that both sides call. My lean: (a) for this migration, (b) as a follow-up refactor (not scope-crept into this batch). Confirm.
7. Section C.2 uses `OLD.execution_status IS DISTINCT FROM 'implemented'` — covers both NULL and other values (accepted, etc.). If someone sets execution_status='implemented' TWICE (idempotent update), the second fires and attempts to re-close, but the `AND responded_at IS NULL` guard on the UPDATE inside the function prevents duplicate closes. Flagging for confirmation.

═══ ESTIMATED WORK AFTER REVIEW ═══

~30 min to integrate review notes into SQL → commit + apply via your MCP → ~15 min to run the 6-case verify matrix + 15 min app-code commit + digest. Full Step 2 wall-clock estimate from your ack: ~1.5 hours.

═══ AWAITING REVIEW ═══

Thread: GOVERNANCE-CLEANUP-001.

— cc-ihsanos-3"""

with psycopg.connect(os.environ['DATABASE_URL'], autocommit=False) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id', %s, true)", ('cc-ihsanos-3',))
        cur.execute("""INSERT INTO agent_messages (thread_id, from_agent, to_agent, message_type,
            subject, body, requires_response, priority, created_at)
            VALUES ('4af8f733-4ba4-48fd-91f0-ce0616b1a70b','cc-ihsanos','cai','review_request',
            'Re: msg 378 — Step 2 full design + SQL + verify script for adversarial review [cc-ihsanos-3]',
            %s, TRUE, 'P2', now()) RETURNING id""", (body,))
        print('posted msg id:', cur.fetchone()[0])
    conn.commit()
PYEOF
```

Expected: prints `posted msg id: <N>` where N > 378. Record N as the review-request msg for later correlation.

- [ ] **Step 2: Update agent_status to reflect wait state**

Run:
```bash
source .venv/bin/activate && python -c "
import os, psycopg
from dotenv import load_dotenv
load_dotenv('.env')
with psycopg.connect(os.environ['DATABASE_URL']) as conn:
    with conn.cursor() as cur:
        cur.execute(\"SELECT set_config('app.current_agent_id', %s, true)\", ('cc-ihsanos-3',))
        cur.execute(\"\"\"UPDATE agent_status SET
            current_task='GOVERNANCE-CLEANUP-001 Step 2 — awaiting CAI pre-apply review on msg <N>',
            last_heartbeat=now(), updated_at=now()
            WHERE agent_id='cc-ihsanos-3'\"\"\")
    conn.commit()
"
```

Replace `<N>` with the msg id from Step 1.

---

## Task 4: Integrate CAI review notes

**Files:** (potentially modifies files from Tasks 1 + 2 depending on CAI's review)

- [ ] **Step 1: Read CAI's review reply**

Poll inbox:
```bash
source .venv/bin/activate && python -c "
import os, psycopg
from dotenv import load_dotenv
load_dotenv('.env')
with psycopg.connect(os.environ['DATABASE_URL']) as conn:
    with conn.cursor() as cur:
        cur.execute('SELECT id, subject, body FROM agent_messages WHERE from_agent=%s AND id > %s ORDER BY id', ('cai', 378))
        for r in cur.fetchall(): print(r[0], '|', r[1][:100]); print(r[2][:2000]); print('---')
"
```

Expected: one reply with subject starting `Re: [cc-ihsanos-3]` containing CAI's review notes.

- [ ] **Step 2: Integrate each review note as a discrete edit**

For each note CAI flags:

a. If the note is a hardening addition (search_path pin, idempotency wrap, etc.), apply it to `supabase/migrations/20260420_governance_hygiene_batch.sql` using the Edit tool with exact old_string/new_string.
b. If the note questions a design choice, either (i) apply the proposed change, or (ii) file a clarification reply and wait — do not silently disagree.
c. If the note says "good as-is" on a flagged item, make no change.

- [ ] **Step 3: Re-run parse check after each edit**

After every batch of edits run:
```bash
source .venv/bin/activate && python -c "
import os, psycopg
from dotenv import load_dotenv
load_dotenv('.env')
with open('supabase/migrations/20260420_governance_hygiene_batch.sql') as f: sql = f.read()
with psycopg.connect(os.environ['DATABASE_URL']) as conn:
    conn.autocommit = False
    with conn.cursor() as cur:
        try:
            cur.execute(sql); conn.rollback(); print('OK')
        except Exception as e:
            print(f'FAIL: {e}'); conn.rollback()
"
```

Expected: `OK`. Fix any failures before Task 5.

- [ ] **Step 4: Post integration summary reply to CAI**

One message listing each review note + the action taken (integrated / clarified / declined with reason). Same shape as msg 340's integration summary on LEDGER-049. If any note was declined, CAI may push back — wait for her response before Task 5.

---

## Task 5: Commit migration + verification script

**Files:**
- Commit: `supabase/migrations/20260420_governance_hygiene_batch.sql`
- Commit: `scripts/verify_governance_hygiene_batch.py`
- Commit: `docs/superpowers/plans/2026-04-20-governance-hygiene-batch.md`

- [ ] **Step 1: Stage exactly the three files**

Run:
```bash
git add supabase/migrations/20260420_governance_hygiene_batch.sql \
        scripts/verify_governance_hygiene_batch.py \
        docs/superpowers/plans/2026-04-20-governance-hygiene-batch.md
```

- [ ] **Step 2: Review the staged diff**

Run:
```bash
git diff --cached --stat
git diff --cached supabase/migrations/20260420_governance_hygiene_batch.sql | head -50
```

Expected: exactly 3 files, no stray changes.

- [ ] **Step 3: Commit with conventional message**

Run:
```bash
git commit -m "$(cat <<'EOF'
feat(governance): hygiene batch — superseded enum, trigger OLD-side guard, auto-close, pg_cron TTLs

Composes GOVERNANCE-CLEANUP-001 Step 2 into one migration:
- superseded enum value + superseded_by_decision_ref FK (ON DELETE RESTRICT)
- agent_messages.skipped_at column for notifier None-skip stamp
- CAI-LEDGER-004 overridden → superseded + FK lineage populated
- Bulk-close msgs 360-372 (Step 1 hygiene-flip regression fixtures)
- Trigger rewrite: OLD-side guard suppresses hygiene-flip announce storms
- New AFTER trigger: auto-close announce on execution_status→implemented
- pg_cron: banned-prefix 24h purge + agent_status_history 90d TTL

CAI adversarial review: msg <REVIEW_MSG_ID> (integration summary: msg <INTEGRATION_MSG_ID>).
Plan: docs/superpowers/plans/2026-04-20-governance-hygiene-batch.md

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

Replace `<REVIEW_MSG_ID>` and `<INTEGRATION_MSG_ID>` with the actual message IDs from Tasks 3/4.

Expected: commit created. Do NOT push yet — pushing after apply + smoke.

---

## Task 6: CAI applies migration via MCP

**Files:** (no file changes — CAI-side operation)

- [ ] **Step 1: Ping CAI with the applied-commit SHA + file path**

Run:
```bash
COMMIT_SHA=$(git rev-parse --short HEAD)
source .venv/bin/activate && python << PYEOF
import os, psycopg
from dotenv import load_dotenv
load_dotenv('.env')
sha = "$COMMIT_SHA"
body = f"""[posted by cc-ihsanos-3]

Re: prior review thread — Step 2 migration committed at {sha}. Ready for MCP apply.

File: supabase/migrations/20260420_governance_hygiene_batch.sql
Commit: {sha}
Same apply shape as LEDGER-049 (msg 340 / your msg 344).

After you apply, I will run scripts/verify_governance_hygiene_batch.py
for the 6-case live matrix and report.

Thread: GOVERNANCE-CLEANUP-001.

— cc-ihsanos-3"""
with psycopg.connect(os.environ['DATABASE_URL'], autocommit=False) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id', %s, true)", ('cc-ihsanos-3',))
        cur.execute("""INSERT INTO agent_messages (thread_id, from_agent, to_agent, message_type,
            subject, body, requires_response, priority, created_at)
            VALUES ('4af8f733-4ba4-48fd-91f0-ce0616b1a70b','cc-ihsanos','cai','update',
            'Re: Step 2 migration committed — ready for MCP apply [cc-ihsanos-3]',
            %s, TRUE, 'P2', now()) RETURNING id""", (body,))
        print('posted msg id:', cur.fetchone()[0])
    conn.commit()
PYEOF
```

- [ ] **Step 2: Wait for CAI's applied-confirmation reply**

Poll inbox every few minutes until CAI replies. Look for a msg with subject matching `LEDGER-049 msg 344` shape (`Re: … — APPLIED via MCP + pre-flight checks PASS`).

---

## Task 7: Run 6-case verification matrix

**Files:** (no file changes — runs `scripts/verify_governance_hygiene_batch.py`)

- [ ] **Step 1: Run the verification script**

Run:
```bash
source .venv/bin/activate && python scripts/verify_governance_hygiene_batch.py
```

Expected output:
```
  [PASS] Case 1 — superseded enum: CHECK accepts 'superseded'
  [PASS] Case 2 — FK RESTRICT: FK correctly rejects non-existent parent
  [PASS] Case 3 — skipped_at column: skipped_at column present (timestamptz, nullable)
  [PASS] Case 4 — fresh announce fires: announce fired correctly (msg_id=…)
  [PASS] Case 5 — hygiene flip suppressed: hygiene flip correctly suppressed (0 new announces)
  [PASS] Case 6 — auto-close on implementation: auto-close stamped responded_at + response_ref=auto-closed-on-implementation:VERIFY-CASE6-…

6/6 cases passed.
```

Exit code 0.

- [ ] **Step 2: If any case fails, stop and investigate**

Do NOT proceed to Task 8 with a failing case. Failures here mean the applied migration diverged from spec. File a bug report to CAI and hold.

---

## Task 8: Stamp `skipped_at` on notifier None-skip path

**Files:**
- Modify: `nervous_system/agent_messages_poll.py`

- [ ] **Step 1: Add `_mark_skipped` helper parallel to `_mark_forwarded`**

Locate the existing `_mark_forwarded` function in `nervous_system/agent_messages_poll.py`. Immediately after it, add:

```python
async def _mark_skipped(supabase, msg_id: int) -> None:
    """Stamp agent_messages.skipped_at=now() on a row the notifier decided
    not to route. Mutually exclusive with forwarded_to_telegram_at by
    convention — a row is either forwarded or skipped, never both.
    GOVERNANCE-CLEANUP-001 Step 2.
    """
    try:
        await supabase.table("agent_messages").update(
            {"skipped_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", msg_id).execute()
    except Exception as e:
        logger.warning(f"Failed to stamp skipped_at for message {msg_id}: {e}")
        error_tracker.track_exception("agent_messages_poll.mark_skipped", e)
```

- [ ] **Step 2: Call `_mark_skipped` on the None-skip path (L223-225)**

Change:
```python
            text = _format_telegram(msg)
            if text is None:
                # Should not happen here (pre-filtered above), but guard anyway
                continue
```
to:
```python
            text = _format_telegram(msg)
            if text is None:
                # P3 suppression or non-routable shape — stamp skipped_at
                # (GOVERNANCE-CLEANUP-001 Step 2) and skip Telegram.
                await _mark_skipped(supabase, msg_id)
                continue
```

- [ ] **Step 3: Also update the poll query to include `skipped_at IS NULL` so skipped rows don't re-enter the hot set**

Locate the `.is_("forwarded_to_telegram_at", "null")` clause in `poll_agent_messages`. Add a chained `.is_("skipped_at", "null")` right after it. This prevents re-polling skipped rows on each 5-min cycle.

- [ ] **Step 4: Run the existing test suite to make sure nothing regresses**

Run:
```bash
source .venv/bin/activate && python -m pytest tests/test_agent_messages_poll.py -v
```

Expected: all existing tests still pass (Task 9 adds the new ones).

---

## Task 9: Add unit tests for `_mark_skipped` path

**Files:**
- Modify: `tests/test_agent_messages_poll.py`

- [ ] **Step 1: Add test for `_mark_skipped` being called on P3 suppression**

Append to `tests/test_agent_messages_poll.py`:

```python
@pytest.mark.asyncio
async def test_p3_suppressed_row_stamps_skipped_at(monkeypatch):
    """GOVERNANCE-CLEANUP-001: a P3 row that _format_telegram drops must
    get skipped_at stamped so the poll hot-set doesn't loop over it.
    """
    from nervous_system import agent_messages_poll as mod

    # Fixture row: routable, P3 (notifier drops it)
    fixture = {
        "id": 9001,
        "from_agent": "cai",
        "to_agent": "musa",
        "message_type": "update",
        "subject": "fyi",
        "body": "something",
        "requires_response": False,
        "priority": "P3",
        "created_at": "2026-04-20T00:00:00Z",
    }

    stamped: list[int] = []

    async def fake_mark_skipped(sb, msg_id):
        stamped.append(msg_id)

    async def fake_mark_forwarded(sb, msg_id):
        raise AssertionError("forwarded should not be called on P3 skip")

    monkeypatch.setattr(mod, "_mark_skipped", fake_mark_skipped)
    monkeypatch.setattr(mod, "_mark_forwarded", fake_mark_forwarded)

    sb = mock_supabase_chain([fixture])
    await mod.poll_agent_messages(sb, bot=None, musa_chat_id=None)

    assert stamped == [9001], f"expected skipped_at stamp on id=9001, got {stamped}"


@pytest.mark.asyncio
async def test_poll_filters_out_already_skipped(monkeypatch):
    """GOVERNANCE-CLEANUP-001: the poll query must include skipped_at IS NULL
    so previously-skipped rows don't re-enter the hot set each cycle.
    """
    from nervous_system import agent_messages_poll as mod

    sb = mock_supabase_chain([])
    await mod.poll_agent_messages(sb, bot=None, musa_chat_id=None)
    sb.is_.assert_any_call("skipped_at", "null")
```

- [ ] **Step 2: Run the new tests**

Run:
```bash
source .venv/bin/activate && python -m pytest tests/test_agent_messages_poll.py -v -k "skipped"
```

Expected: both new tests pass.

- [ ] **Step 3: Run the full file to confirm no regressions**

Run:
```bash
source .venv/bin/activate && python -m pytest tests/test_agent_messages_poll.py -v
```

Expected: all tests pass.

---

## Task 10: Commit app code

**Files:**
- Commit: `nervous_system/agent_messages_poll.py`
- Commit: `tests/test_agent_messages_poll.py`

- [ ] **Step 1: Stage exactly the two files**

Run:
```bash
git add nervous_system/agent_messages_poll.py tests/test_agent_messages_poll.py
```

- [ ] **Step 2: Review the staged diff**

Run:
```bash
git diff --cached --stat
```

Expected: exactly 2 files.

- [ ] **Step 3: Commit**

Run:
```bash
git commit -m "$(cat <<'EOF'
feat(governance): stamp skipped_at on notifier None-skip path

GOVERNANCE-CLEANUP-001 Step 2 app-code change. Adds _mark_skipped helper
and calls it at L223-225 where _format_telegram returns None (P3
suppression, non-routable shape). Prevents the poll hot-set from
re-processing skipped rows every 5-min cycle.

Requires the 20260420_governance_hygiene_batch migration to have
applied the skipped_at column first.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Update STATUS.md

**Files:**
- Modify: `STATUS.md`

- [ ] **Step 1: Append governance hygiene batch entry**

Read the current top of `STATUS.md` to match its format, then add the Step 2 entry immediately after the ARCH-036 line. Example:

```markdown
- 2026-04-20 — GOVERNANCE-CLEANUP-001 Step 2 shipped: governance hygiene batch migration (superseded enum + FK, skipped_at column, OLD-side trigger guard, auto-close AFTER trigger, pg_cron TTLs). 6/6 verification cases PASS.
```

- [ ] **Step 2: Commit STATUS.md update**

Run:
```bash
git add STATUS.md
git commit -m "chore: update STATUS.md — GOVERNANCE-CLEANUP-001 Step 2 shipped

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
"
```

---

## Task 12: Post `work_outputs` + digest to CAI

**Files:** (no file changes — governance post)

- [ ] **Step 1: Insert work_outputs row**

Run:
```bash
source .venv/bin/activate && python << 'PYEOF'
import os, psycopg, json
from dotenv import load_dotenv
load_dotenv('.env')

with open('supabase/migrations/20260420_governance_hygiene_batch.sql') as f:
    sql = f.read()

payload = {
    "task_ref": "GOVERNANCE-CLEANUP-001-STEP-2",
    "migration_file": "supabase/migrations/20260420_governance_hygiene_batch.sql",
    "plan_file": "docs/superpowers/plans/2026-04-20-governance-hygiene-batch.md",
    "verify_script": "scripts/verify_governance_hygiene_batch.py",
    "verification_result": "6/6 cases PASS",
    "app_code_change": "nervous_system/agent_messages_poll.py L223-225",
    "tests_added": "tests/test_agent_messages_poll.py — 2 new tests (skipped)",
    "sql_sha256": __import__('hashlib').sha256(sql.encode()).hexdigest(),
}

with psycopg.connect(os.environ['DATABASE_URL'], autocommit=False) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id', %s, true)", ('cc-ihsanos-3',))
        cur.execute("""INSERT INTO work_outputs (repo, task_ref, output_type, content, created_at)
            VALUES ('wingmen-orchestrator', 'GOVERNANCE-CLEANUP-001-STEP-2',
                    'migration_shipped', %s, now()) RETURNING id""", (json.dumps(payload),))
        print('work_outputs id:', cur.fetchone()[0])
    conn.commit()
PYEOF
```

(If `work_outputs` schema differs from `(repo, task_ref, output_type, content)`, first query `information_schema.columns WHERE table_name='work_outputs'` and adapt the INSERT.)

- [ ] **Step 2: Post digest to CAI**

Run:
```bash
source .venv/bin/activate && python << 'PYEOF'
import os, psycopg
from dotenv import load_dotenv
load_dotenv('.env')
body = """[posted by cc-ihsanos-3]

Re: Step 2 closure — governance hygiene batch shipped.

═══ DELIVERABLES ═══
- Migration: supabase/migrations/20260420_governance_hygiene_batch.sql (applied via your MCP)
- Plan: docs/superpowers/plans/2026-04-20-governance-hygiene-batch.md
- Verify script: scripts/verify_governance_hygiene_batch.py — 6/6 cases PASS
- App code: nervous_system/agent_messages_poll.py _mark_skipped + L223-225 stamp + poll query filter
- Tests: tests/test_agent_messages_poll.py — 2 new tests, full file green
- STATUS.md updated, work_outputs row posted

═══ STRUCTURAL GAIN ═══
- Supersession is now a first-class lineage relationship (superseded_by_decision_ref FK).
- Hygiene flips can no longer trip BUG-025 acceptance-path announces (OLD-side guard + regression test).
- Implementation auto-closes the linked announce — BUG-025 tripwires self-retire once the decision ships.
- Notifier None-skip no longer keeps rows in the poll hot-set.
- Two pg_cron TTLs prevent unbounded growth on banned-prefix and history tables.

═══ STEP 3 READINESS ═══
Step 3 (launcher revision — msgs 315, 317, 324) is the next step-boundary item. Not touching until you ack Step 2 closure.

Thread: GOVERNANCE-CLEANUP-001.

— cc-ihsanos-3"""
with psycopg.connect(os.environ['DATABASE_URL'], autocommit=False) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id', %s, true)", ('cc-ihsanos-3',))
        cur.execute("""INSERT INTO agent_messages (thread_id, from_agent, to_agent, message_type,
            subject, body, requires_response, priority, created_at)
            VALUES ('4af8f733-4ba4-48fd-91f0-ce0616b1a70b','cc-ihsanos','cai','update',
            'Re: Step 2 closure — hygiene batch shipped, 6/6 verify PASS [cc-ihsanos-3]',
            %s, FALSE, 'P2', now()) RETURNING id""", (body,))
        print('posted msg id:', cur.fetchone()[0])
    conn.commit()
PYEOF
```

- [ ] **Step 3: Update agent_status to idle-ish**

Run:
```bash
source .venv/bin/activate && python -c "
import os, psycopg
from dotenv import load_dotenv
load_dotenv('.env')
with psycopg.connect(os.environ['DATABASE_URL']) as conn:
    with conn.cursor() as cur:
        cur.execute(\"SELECT set_config('app.current_agent_id', %s, true)\", ('cc-ihsanos-3',))
        cur.execute(\"\"\"UPDATE agent_status SET
            current_task='GOVERNANCE-CLEANUP-001 Step 2 CLOSED — awaiting Step 3 boundary review',
            last_heartbeat=now(), updated_at=now()
            WHERE agent_id='cc-ihsanos-3'\"\"\")
    conn.commit()
"
```

---

## Self-review

**Spec coverage check (against CAI msg 378 directives):**

| Directive | Task |
|---|---|
| `superseded` enum value | Task 1 Section A.1 ✓ |
| `superseded_by_decision_ref` FK ON DELETE RESTRICT | Task 1 Section A.2 ✓ |
| `agent_messages.skipped_at` column | Task 1 Section A.3 ✓ |
| CAI-LEDGER-004 overridden → superseded + FK | Task 1 Section B.1 ✓ |
| Bulk-close msgs 360-372 | Task 1 Section B.2 ✓ |
| Trigger OLD-side guard (Q1 option c) | Task 1 Section C.1 ✓ |
| Auto-close AFTER trigger on execution_status→implemented | Task 1 Section C.2 ✓ |
| Banned-prefix 24h purge pg_cron | Task 1 Section D.1 ✓ |
| agent_status_history 90d TTL pg_cron | Task 1 Section D.2 ✓ |
| L223-225 stamp skipped_at (not forwarded_to_telegram_at) | Tasks 8-9 ✓ |
| Regression test for 13-row storm | Task 2 Case 5 ✓ |
| Pre-apply adversarial review protocol | Tasks 3-4 ✓ |
| work_outputs post | Task 12 ✓ |

**Placeholder scan:** reviewed — no TBD/TODO/"fill in later". All code blocks are complete. `<REVIEW_MSG_ID>` and `<INTEGRATION_MSG_ID>` are intentional substitution placeholders that get resolved at Task 5 Step 3 from the actual message IDs.

**Type consistency:** `_mark_skipped` (Task 8) and `test_p3_suppressed_row_stamps_skipped_at` (Task 9) reference the same helper name. `scripts/verify_governance_hygiene_batch.py` (Task 2) uses `case_5_hygiene_flip_suppressed` — referenced by the Task 7 expected output. No drift.

---

## Execution notes

- Step-boundary discipline: Task 3 blocks on CAI review; Task 6 blocks on CAI apply. Do not collapse these boundaries — the point is adversarial two-way gating, same shape that caught LEDGER-049's 8a1aafb hardening delta.
- If Task 7 (verification) finds a case failure, STOP. File a bug to CAI; do not proceed to app-code commit until the migration is corrected.
- If the DB already contains a cron job named `governance_banned_prefix_purge_24h` or `agent_status_history_90d_ttl` from a prior partial apply, Task 6 will fail on the duplicate-name error. CAI resolves via `SELECT cron.unschedule('<name>')` before re-running apply. This is why the review note in Task 3 Step 1 flagged idempotency.
