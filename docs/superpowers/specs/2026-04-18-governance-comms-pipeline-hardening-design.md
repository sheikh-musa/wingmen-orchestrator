# Governance Comms Pipeline v1 Hardening — Design Spec

**Bugs:** BUG-020, BUG-021
**Date:** 2026-04-18
**Status:** Design approved by Musa + CAI (CAI-RESP-027 tightenings folded in). Pending implementation plan.

## Problem

Today CAI files strategic decisions and posts agent_messages; CC polls agent_messages for review_requests. Two silent failure modes broke that pipeline:

**BUG-020 — missing trigger bridge `strategic_decisions → agent_messages`.** After ARCH-018/TASK-041 migrated primary comms to `agent_messages`, CAI-authored decisions (source=`claude_ai_session`) never auto-announce to CC. Every decision in the last 72h with `notified_at IS NULL` sat in `challenge_window` with no review triggered — the CAI-LEDGER series, VISION-001/002, PROD-006, ARCH-030/031/032, TASK-043. CC only saw decisions with a manually-posted companion agent_message.

**BUG-021 — `agent_messages.read_at` clobbered by Telegram notifier.** The orchestrator Telegram forwarder stamps `read_at = now()` when it forwards a message to Musa's Telegram. Semantic contract violated: `read_at` should mean "the addressed agent processed this," not "middleware saw it." Consequence: every CAI message gets auto-stamped `read_at` the moment Musa's phone pings, and CC's inbox query (`WHERE read_at IS NULL`) silently drops the entire backlog.

Together, these caused the 2026-04-18 governance blackout. Both must be fixed atomically — fixing BUG-020 alone floods CC's inbox with messages the Telegram notifier then marks read before CC sees them.

## Solution

One Supabase migration + orchestrator Python changes, deployed sequentially with verification beats between steps.

**Migration adds:**
1. `forwarded_to_telegram_at TIMESTAMPTZ` on `agent_messages` — forwarding state lives on its own column with an unambiguous name
2. `announced_by_msg_id BIGINT REFERENCES agent_messages(id)` on `strategic_decisions` — FK-based dedup (per CAI-RESP-027, replaces substring match)
3. `AFTER/BEFORE INSERT` + `BEFORE UPDATE` trigger on `strategic_decisions` that auto-inserts a matching `agent_messages` review_request row when `source='claude_ai_session' AND challenge_status='challenge_window' AND bypass_review=false AND announced_by_msg_id IS NULL`
4. Atomic per-orphan backfill loop (INSERT agent_message, then UPDATE strategic_decision with its id + notified_at — one transaction per orphan, not bulk)

**Python changes** rename `_mark_read()` → `_mark_forwarded()` in two files and stamp `forwarded_to_telegram_at` instead of `read_at`. Tests updated.

**RLS deferred** — service_role bypasses; without verified per-agent JWT claims, the policy would be dead code. Revisit when multi-agent auth lands.

## Architecture

```
┌──────────────────────┐         ┌──────────────────────┐
│ strategic_decisions  │─ INSERT/UPDATE ─→ agent_messages│
│  (source=cai_session │  trigger│  (review_request row, │
│   challenge_window,  │  fires  │   id captured)        │
│   announced_by_msg_id│         └──────────────────────┘
│   IS NULL)           │                    │
│                      │←─── announced_by_msg_id set ─────┘
└──────────────────────┘   (atomic, same txn)
                                           │
                                           ↓
                              ┌─────────────────────────┐
                              │ agent_messages_poll.py  │
                              │  reads, forwards to TG, │
                              │  stamps                 │
                              │  forwarded_to_telegram_at│
                              │  (NOT read_at)          │
                              └─────────────────────────┘
                                           │
                                           ↓
                              ┌─────────────────────────┐
                              │ CC's inbox poll          │
                              │  WHERE read_at IS NULL  │
                              │  (only CC writes read_at)│
                              └─────────────────────────┘
```

### Design choice 1: `forwarded_to_telegram_at` column vs reusing `notification_log.telegram_msg_id`

**Chose: new column on `agent_messages`.**

| | New column | Reuse notification_log |
|---|---|---|
| Forward-status query | `WHERE forwarded_to_telegram_at IS NULL` | JOIN, check telegram_msg_id IS NOT NULL |
| Write path | Single-row UPDATE | INSERT into notification_log |
| Semantic clarity | Column name = contract | Indirection — notification_log has multiple purposes |
| "Forwarded but not read" obs | Trivial 2-column AND | Join + null check |

Root cause of BUG-021 was semantic drift. Fix should make semantics impossible to misread — a purpose-named column is the simplest way.

### Design choice 2: dedup via FK column vs subject substring match

**Chose: `announced_by_msg_id BIGINT FK` on `strategic_decisions` (per CAI-RESP-027).**

Original draft used `subject LIKE decision_ref || ':%'` in the trigger. CAI flagged this as fragile — subject conventions drift, manual posts may use different formats, substring matches generate false positives. A FK column makes dedup O(1), unambiguous, and self-documenting ("is this decision announced? check the FK"). Also gives observability: `SELECT … WHERE announced_by_msg_id IS NULL` finds every un-announced decision instantly.

Extra cost: one BIGINT column + one FK. Trivial.

## Migration contents

**File:** `supabase/migrations/20260418_bug020_bug021_governance_comms_hardening.sql`

### 1. Schema changes

```sql
-- BUG-021: forwarding state gets its own column
ALTER TABLE agent_messages
  ADD COLUMN IF NOT EXISTS forwarded_to_telegram_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS agent_messages_forwarded_idx
  ON agent_messages (forwarded_to_telegram_at)
  WHERE forwarded_to_telegram_at IS NULL;

-- BUG-020: FK-based dedup (CAI-RESP-027)
ALTER TABLE strategic_decisions
  ADD COLUMN IF NOT EXISTS announced_by_msg_id BIGINT
    REFERENCES agent_messages(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS strategic_decisions_announced_idx
  ON strategic_decisions (announced_by_msg_id)
  WHERE announced_by_msg_id IS NULL;
```

Partial indexes — both queries ("find unforwarded messages", "find un-announced decisions") hit the NULL subset, which stays small relative to the full table.

`ON DELETE SET NULL` so a deleted agent_message doesn't cascade-delete a strategic_decision.

### 2. BUG-020 trigger function

Fires on INSERT, or on UPDATE when `challenge_status` transitions INTO `'challenge_window'`. Uses `announced_by_msg_id IS NULL` as the dedup gate.

```sql
CREATE OR REPLACE FUNCTION trigger_cai_decision_announce()
RETURNS TRIGGER AS $$
DECLARE
  v_msg_id BIGINT;
  v_subject TEXT;
  v_body TEXT;
BEGIN
  -- Guard: only claude_ai_session decisions in challenge_window, not already announced
  IF NEW.source IS DISTINCT FROM 'claude_ai_session'
     OR NEW.challenge_status IS DISTINCT FROM 'challenge_window'
     OR COALESCE(NEW.bypass_review, false) = true
     OR NEW.announced_by_msg_id IS NOT NULL THEN
    RETURN NEW;
  END IF;

  -- UPDATE: only fire on transition INTO challenge_window
  IF TG_OP = 'UPDATE' AND OLD.challenge_status = 'challenge_window' THEN
    RETURN NEW;
  END IF;

  v_subject := NEW.decision_ref || ': ' || NEW.title || ' — for review + challenge';
  v_body := format(
    E'Decision %s filed by CAI in challenge_window.\nFull spec: see strategic_decisions.decision_ref=%s%s\n',
    NEW.decision_ref,
    NEW.decision_ref,
    CASE WHEN NEW.parent_ref IS NOT NULL
         THEN E'\nParent: ' || NEW.parent_ref
         ELSE '' END
  );

  INSERT INTO agent_messages (
    thread_id, from_agent, to_agent, message_type,
    subject, body, requires_response
  ) VALUES (
    gen_random_uuid(), 'cai', 'cc-ihsanos', 'review_request',
    v_subject, v_body, true
  )
  RETURNING id INTO v_msg_id;

  -- Link the decision to its announcement message, mark notified
  NEW.announced_by_msg_id := v_msg_id;
  NEW.notified_at := now();

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER cai_decision_announce_insert
  BEFORE INSERT ON strategic_decisions
  FOR EACH ROW EXECUTE FUNCTION trigger_cai_decision_announce();

CREATE TRIGGER cai_decision_announce_update
  BEFORE UPDATE OF challenge_status ON strategic_decisions
  FOR EACH ROW EXECUTE FUNCTION trigger_cai_decision_announce();
```

`BEFORE` (not `AFTER`) so the function can set `NEW.announced_by_msg_id` and `NEW.notified_at` on the same row without a recursive UPDATE. The FK is satisfied because the `INSERT … RETURNING id` runs inside the same transaction and returns a committed agent_messages row id.

### 3. Backfill sweep (atomic per orphan — CC msg 197 answer #5)

**NOT a bulk UPDATE at the end.** Per-orphan: INSERT agent_message, capture id, UPDATE the single strategic_decision with that id + notified_at. If the loop is interrupted, partial progress is consistent — every processed orphan is fully linked, no "inserted but not marked" state.

```sql
DO $$
DECLARE
  v_row RECORD;
  v_msg_id BIGINT;
  v_subject TEXT;
  v_body TEXT;
BEGIN
  FOR v_row IN
    SELECT id, decision_ref, title, parent_ref
    FROM strategic_decisions
    WHERE source = 'claude_ai_session'
      AND challenge_status = 'challenge_window'
      AND COALESCE(bypass_review, false) = false
      AND announced_by_msg_id IS NULL
      AND notified_at IS NULL
    ORDER BY created_at ASC
  LOOP
    v_subject := v_row.decision_ref || ': ' || v_row.title
                  || ' — for review + challenge (backfilled)';
    v_body := format(
      E'Decision %s backfilled by BUG-020 migration.\nFull spec: see strategic_decisions.decision_ref=%s%s',
      v_row.decision_ref, v_row.decision_ref,
      CASE WHEN v_row.parent_ref IS NOT NULL
           THEN E'\nParent: ' || v_row.parent_ref
           ELSE '' END
    );

    INSERT INTO agent_messages (
      thread_id, from_agent, to_agent, message_type,
      subject, body, requires_response
    ) VALUES (
      gen_random_uuid(), 'cai', 'cc-ihsanos', 'review_request',
      v_subject, v_body, true
    )
    RETURNING id INTO v_msg_id;

    UPDATE strategic_decisions
    SET announced_by_msg_id = v_msg_id,
        notified_at = now()
    WHERE id = v_row.id;
  END LOOP;
END $$;
```

### 4. BUG-021 read_at backfill

Moot. All 10 originally-identified orphan messages (IDs 180, 184, 186–193) already have `responded_at` set. No `read_at` reset needed.

### 5. RLS — deferred

**Do NOT ship RLS in this migration.** Reason: the orchestrator uses `service_role` which bypasses all RLS. Without verified per-agent JWT claims (`auth.jwt()->>'agent_id'`), an RLS policy that checks "current agent = to_agent" would never fire — a dead policy that looks protective but isn't.

Dead security policies are worse than no policy. They give false confidence, and future readers won't know the check is inert.

**Revisit when:** per-agent JWT auth lands (likely when multi-CC routing or tenant agents ship). At that point verify the claim extraction works in a test before adding the policy.

## Python changes

### `nervous_system/agent_messages_poll.py`

- Rename `_mark_read()` → `_mark_forwarded()`
- Change UPDATE payload: `{"read_at": …}` → `{"forwarded_to_telegram_at": …}`
- Callers at lines 213 and 236 — update to new name
- Polling SELECT stays `read_at IS NULL` (CC-directed messages should only be marked read by CC), but add `forwarded_to_telegram_at IS NULL` as the *notifier's own* filter so it doesn't re-forward already-sent messages

### `scripts/build_launch_context.py`

- Bulk update on boot briefing (line ~140): change column from `read_at` to `forwarded_to_telegram_at`
- Semantic matches: boot briefing forwards the inbox summary to Musa, it does not process messages on behalf of any agent

### `tests/test_agent_messages_poll.py`

- Rename `test_telegram_failure_does_not_mark_read` → `test_telegram_failure_does_not_mark_forwarded`
- Update assertion: `forwarded_to_telegram_at IS NULL` on Telegram failure
- Add `test_successful_forward_stamps_forwarded_to_telegram_at` (positive case)
- Add `test_forwarding_does_not_modify_read_at` (regression guard for BUG-021)

## Deploy procedure (with verification beats)

**Verification beat between each step is mandatory. Do NOT proceed past a failing verify.**

### Step 1: Migration

Apply `20260418_bug020_bug021_governance_comms_hardening.sql` against orchestrator Supabase.

**Verify — migration smoke test:**
```sql
-- Columns exist
SELECT column_name FROM information_schema.columns
 WHERE table_name='agent_messages' AND column_name='forwarded_to_telegram_at';
-- Expect: 1 row

SELECT column_name FROM information_schema.columns
 WHERE table_name='strategic_decisions' AND column_name='announced_by_msg_id';
-- Expect: 1 row

-- Triggers exist
SELECT tgname FROM pg_trigger
 WHERE tgname IN ('cai_decision_announce_insert','cai_decision_announce_update');
-- Expect: 2 rows

-- Backfill ran: every CAI challenge_window decision has announced_by_msg_id
SELECT COUNT(*) FROM strategic_decisions
 WHERE source='claude_ai_session' AND challenge_status='challenge_window'
   AND COALESCE(bypass_review,false)=false
   AND announced_by_msg_id IS NULL;
-- Expect: 0
```

Old Python keeps working — it's writing to `read_at`, a still-valid column. No downtime.

### Step 2: Python deploy

Push notifier + build_launch_context changes. Restart via `scripts/restart_orch.sh`.

**Verify — live forward writes new column:**

Post a throwaway test message to `agent_messages` (`to_agent='cc-ihsanos'`, subject="verify BUG-021 fix"), wait for Telegram delivery, then check:
```sql
SELECT id, read_at, forwarded_to_telegram_at
FROM agent_messages
WHERE subject='verify BUG-021 fix';
-- Expect: forwarded_to_telegram_at IS NOT NULL, read_at IS NULL
```

If `read_at` is populated: revert Python immediately, investigate before proceeding.

### Step 3: Trigger live test

With Python confirmed clean, test BUG-020 trigger end-to-end. Insert a throwaway `strategic_decisions` row (`source='claude_ai_session', challenge_status='challenge_window', bypass_review=false`) and verify:
```sql
SELECT announced_by_msg_id, notified_at FROM strategic_decisions
 WHERE decision_ref='BUG-021-VERIFY';
-- Expect: both NOT NULL

SELECT COUNT(*) FROM agent_messages
 WHERE subject LIKE 'BUG-021-VERIFY:%';
-- Expect: 1
```

Then delete the test decision and its announcement message.

### Step 4: ihsanos cleanup

In the ihsanos repo, revert the temporary two-query inbox workaround in `CLAUDE.md` back to a single clean `WHERE read_at IS NULL AND to_agent='cc-ihsanos'`. Separate commit in that repo.

**Verify:** CC's next boot briefing shows an inbox that matches the orchestrator's view — no "disappeared" messages.

## Rollback procedure

Failure scenarios and recovery:

### Rollback A: migration succeeds, Python deploy goes bad

**Symptom:** Step 2 verify fails (e.g., new Python has a bug, Telegram forwards break, `forwarded_to_telegram_at` not stamped).

**Action:** Revert Python deploy (`git revert` + `scripts/restart_orch.sh`). Old Python resumes stamping `read_at` — fine, migration is backwards-compatible. Leave migration in place.

**Known consequence:** BUG-021 re-manifests until Python is redeployed correctly. BUG-020 remains fixed (triggers are independent of Python).

### Rollback B: BUG-020 trigger misbehaves after migration

**Symptom:** Trigger produces spurious `agent_messages` rows, or blocks legitimate `strategic_decisions` inserts.

**Action:** Disable triggers (don't drop — keeps forensic state):
```sql
ALTER TABLE strategic_decisions DISABLE TRIGGER cai_decision_announce_insert;
ALTER TABLE strategic_decisions DISABLE TRIGGER cai_decision_announce_update;
```
Investigate, patch, re-enable.

### Rollback C: full migration revert (worst case)

Use only if trigger logic is fundamentally broken AND disabling isn't enough.

```sql
DROP TRIGGER IF EXISTS cai_decision_announce_insert ON strategic_decisions;
DROP TRIGGER IF EXISTS cai_decision_announce_update ON strategic_decisions;
DROP FUNCTION IF EXISTS trigger_cai_decision_announce();

-- Columns can stay — no harm leaving orphan columns
-- If you must remove them:
-- ALTER TABLE strategic_decisions DROP COLUMN announced_by_msg_id;
-- ALTER TABLE agent_messages DROP COLUMN forwarded_to_telegram_at;
```

Backfill-created agent_messages rows remain — they are real review_requests CC should still process. Do not delete them.

### Rollback D: backfill created unwanted announcements

If backfill fires for decisions that shouldn't have been announced (e.g., a CAI session was filed as `claude_ai_session` but was meant to be `bypass_review`), manually unwind:
```sql
-- Identify the erroneous announcements
SELECT sd.decision_ref, sd.announced_by_msg_id
FROM strategic_decisions sd
WHERE sd.decision_ref IN ('DEC-REF-1', 'DEC-REF-2');

-- Delete the corresponding agent_messages (cascades SET NULL on strategic_decisions.announced_by_msg_id via FK)
DELETE FROM agent_messages WHERE id IN (…);

-- Optionally re-mark as notified to prevent re-backfill
UPDATE strategic_decisions SET notified_at = now(), bypass_review = true
 WHERE decision_ref IN ('DEC-REF-1', 'DEC-REF-2');
```

## Out of scope

- BUG-022 (concurrent-CC claim/lock) — already shipped
- Per-agent JWT auth + RLS enforcement — revisit with multi-CC routing
- `read_at` semantics cleanup beyond this fix — column keeps its meaning, just stops being clobbered
- ihsanos CLAUDE.md revert — separate follow-up commit after orchestrator deploy verified

## Success criteria

- ✅ Every new CAI decision with `source='claude_ai_session' AND challenge_status='challenge_window'` produces an atomic announcement with FK link populated
- ✅ Telegram notifier stamps `forwarded_to_telegram_at`; `read_at` untouched by middleware
- ✅ CC's `WHERE read_at IS NULL` inbox query surfaces the same messages CAI sends
- ✅ All pre-existing CAI challenge_window orphans have `announced_by_msg_id` set
- ✅ `test_forwarding_does_not_modify_read_at` passes (regression guard)
- ✅ Rollback procedure documented and verified dry-run-able (no surprise dependencies)
