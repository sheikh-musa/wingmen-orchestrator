# Batch 2 — BUG-030 Bridge Trigger Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `strategic_decisions → agent_messages` bridge trigger's hardcoded `to_agent='cc-ihsanos'` + `thread_id=gen_random_uuid()` with a 3-tier structural routing contract driven by new columns on `strategic_decisions`, so cai's decision replies reach the correct agent on the parent message's thread.

**Architecture:** Single atomic migration adds three nullable columns to `strategic_decisions` (`parent_msg_id` BIGINT FK → `agent_messages(id)`, `announce_to_agent` TEXT, `announce_thread_id` UUID) and rewrites `trigger_cai_decision_announce` to resolve recipient + thread via COALESCE of (a) explicit override column, (b) inferred from `parent_msg_id`, (c) legacy default. Existing 300+ rows with NULL parent_msg_id fall through to Tier 3 (cc-ihsanos default) — backward compatible, no backfill required to ship. Regression tests in a dedicated pytest file exercise all three tiers on INSERT + UPDATE paths.

**Tech Stack:** PostgreSQL 15 (Supabase), plpgsql, Python 3.9, psycopg3, pytest.

**Parent decisions:** BUG-030 (decided 2026-04-23). Related: BUG-024 Phase 1 (provenance pattern precedent), ARCH-036 (bridge-trigger infrastructure), CAI-RESP-080 (Refinement 2 review protocol).

**Scope (per BUG-030 ACs + msg #631 cc-ihsanos AGREED):**
- AC-BUG030-1: `parent_msg_id BIGINT REFERENCES agent_messages(id)` on `strategic_decisions`, nullable.
- AC-BUG030-2: `announce_to_agent TEXT` on `strategic_decisions`, nullable.
- AC-BUG030-3: `announce_thread_id UUID` on `strategic_decisions`, nullable.
- AC-BUG030-4: Bridge trigger `trigger_cai_decision_announce` rewritten with 3-tier fallback for `to_agent` + `thread_id`.
- AC-BUG030-5: Regression tests for thread continuity, recipient routing per `parent_msg_id.from_agent`, explicit overrides, legacy fallback, FK enforcement, UPDATE-path firing.

**Explicit non-goals:**
- Part D backfill (cc-ihsanos proposal: optional post-landing). Skipped this batch.
- Enforcement of cai's discipline to populate `parent_msg_id` on new decisions (documented in cai persona.md instead — not code-enforced).
- Changes to `trigger_cai_decision_autoclose_announce` (AFTER UPDATE OF execution_status) — untouched, still works against `announced_by_msg_id`.

---

## Pre-flight facts (verified on remote orchestrator Supabase 2026-04-24)

- `trigger_cai_decision_announce` is BEFORE INSERT (trigger `cai_decision_announce_insert`) + BEFORE UPDATE OF challenge_status (trigger `cai_decision_announce_update`) on `strategic_decisions`.
- Current body hardcodes `'cc-ihsanos'` for `to_agent` and `gen_random_uuid()` for `thread_id` at the INSERT into `agent_messages`.
- Guards (preserved in the rewrite): `source = 'claude_ai_session'`, `challenge_status IN ('challenge_window', 'accepted')`, `bypass_review = false`, `announced_by_msg_id IS NULL`, OLD-side suppression when `announced_by_msg_id IS NOT NULL OR execution_status = 'implemented'` (shipped 2026-04-20 per governance-hygiene-batch).
- `agent_messages.id` BIGINT PK — exists, FK target is legitimate.
- `agent_messages.thread_id` UUID — no FK to itself; any UUID is valid. We inherit the value, not enforce its existence.
- 300+ existing `strategic_decisions` rows all have NULL `parent_msg_id` post-migration (new column). Tier-3 fallback gives backward-compatible behavior for every existing row.
- `supabase_migrations.schema_migrations` most recent: `20260424185500` (BUG-033 restoration). Pick `20260425000000` for this migration.

---

## File structure

- **Create:** `supabase/migrations/20260425_batch2_bug030_bridge_trigger_fix.sql` — atomic migration with 4 sections (column adds, FK, trigger rewrite, assertion gate).
- **Create:** `tests/test_batch2_bug030_bridge_trigger.py` — psycopg-backed pytest file with 9 regression tests covering all 3 tiers × INSERT/UPDATE + FK + fallback.
- **Modify:** `scripts/agents/cai_persona.md` — add single paragraph documenting the discipline to populate `parent_msg_id` + `announce_to_agent` when filing decisions responding to specific messages. (File lives at `/Users/sheikhmusa/wingmen/orchestrator/scripts/agents/cai_persona.md` — verify path before Task 10.)
- **No changes to:** `scripts/agents/` Python (trigger is pure SQL; cai posts decisions via existing insert path plus the new columns where applicable — no Python call-site update required for the trigger itself). No changes to `nervous_system/agent_messages_poll.py` (notifier works on `agent_messages` rows as-is).

---

## Task 1: Pre-flight verification (local environment sanity)

**Files:**
- Read: `supabase/migrations/20260424_bug033_restore_base_agent_id_not_null.sql` (last migration — confirms restoration applied)
- Read: `scripts/agents/cai_persona.md` (verify path exists, check style conventions)

- [ ] **Step 1: Verify DATABASE_URL loadable + live schema matches plan preamble**

Run:

```bash
.venv/bin/python -c "
import os; from dotenv import load_dotenv; load_dotenv()
import psycopg
with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute(\"\"\"
            SELECT count(*) FROM information_schema.columns
             WHERE table_name='strategic_decisions'
               AND column_name IN ('parent_msg_id','announce_to_agent','announce_thread_id')
        \"\"\")
        print('new-cols already present:', cur.fetchone()[0])
        cur.execute(\"SELECT count(*) FROM strategic_decisions\")
        print('strategic_decisions rows:', cur.fetchone()[0])
        cur.execute(\"SELECT count(*) FROM agent_messages\")
        print('agent_messages rows:', cur.fetchone()[0])
"
```

Expected: `new-cols already present: 0`, non-zero row counts.

- [ ] **Step 2: Confirm cai_persona.md path**

Run: `ls scripts/agents/cai_persona.md`
Expected: file exists. If it does not, grep for alternative: `find . -name "cai_persona.md" -not -path "./.venv/*"`. Update Task 10 file path to match actual location.

---

## Task 2: Scaffold migration file + test file

**Files:**
- Create: `supabase/migrations/20260425_batch2_bug030_bridge_trigger_fix.sql`
- Create: `tests/test_batch2_bug030_bridge_trigger.py`

- [ ] **Step 1: Write migration preamble**

Create `supabase/migrations/20260425_batch2_bug030_bridge_trigger_fix.sql` with:

```sql
-- Batch 2: BUG-030 bridge trigger fix
-- Ships 1 P1 fix per CAI msg #620 consolidated briefing + cc-ihsanos msg #631 AGREED:
--   BUG-030 — parent_msg_id + announce_to_agent + announce_thread_id on strategic_decisions
--             + trigger_cai_decision_announce rewrite with 3-tier fallback
--
-- Parent decision: BUG-030.
-- Rulings: CAI-RESP-080 (Refinement 2 review protocol), msg #620 (bundling decision to ship as separate migration),
--          ORCHESTRATOR-NOTIFIER-FIX-001-AMEND (upstream dedup + Fix 4 discipline preserved).
--
-- Sections:
--   1. Add parent_msg_id + announce_to_agent + announce_thread_id columns (all nullable).
--   2. Add parent_msg_id FK (REFERENCES agent_messages(id) ON DELETE RESTRICT).
--   3. REPLACE trigger_cai_decision_announce with 3-tier routing.
--   4. DO-block assertion: trigger body contains COALESCE(NEW.announce_to_agent, ...) pattern.
--
-- Pre-flight verified:
--   * strategic_decisions has 40 columns as of 2026-04-24, most recent: is_test (Batch 1 Section 6).
--   * trigger_cai_decision_announce lives at oid=(SELECT oid FROM pg_proc WHERE proname=...). Body hardcodes 'cc-ihsanos'.
--   * agent_messages.id is BIGINT PK. agent_messages.thread_id is UUID (no self-FK).

BEGIN;
```

- [ ] **Step 2: Write test file header**

Create `tests/test_batch2_bug030_bridge_trigger.py` with:

```python
"""Integration tests for Batch 2 BUG-030 bridge trigger fix.

Covers:
  - parent_msg_id FK column + constraint
  - announce_to_agent + announce_thread_id override columns
  - trigger_cai_decision_announce 3-tier routing (explicit > inferred > legacy)
  - UPDATE-path firing on challenge_status change (BUG-020 precedent)

Uses psycopg against the live orchestrator Supabase. Each test manages its
own SAVEPOINT/ROLLBACK or DELETE cleanup to avoid leaking test rows.
"""
import os
import uuid

import psycopg
import pytest
from dotenv import load_dotenv

load_dotenv()
DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")

pytestmark = pytest.mark.skipif(
    not DSN, reason="DATABASE_URL not set — integration tests skipped"
)


def _conn():
    return psycopg.connect(DSN, autocommit=False)


def _cleanup(ref_prefix):
    """Delete any test rows matching a decision_ref prefix. Crash-safe."""
    with psycopg.connect(DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "DELETE FROM agent_messages WHERE subject LIKE %s",
                (f"{ref_prefix}%",),
            )
            cur.execute(
                "DELETE FROM strategic_decisions WHERE decision_ref LIKE %s",
                (f"{ref_prefix}%",),
            )


@pytest.fixture(autouse=True)
def _clean_test_bug030_rows():
    _cleanup("TEST-BUG030-")
    yield
    _cleanup("TEST-BUG030-")
```

- [ ] **Step 3: Commit scaffold**

```bash
git add supabase/migrations/20260425_batch2_bug030_bridge_trigger_fix.sql tests/test_batch2_bug030_bridge_trigger.py
git commit -m "chore(batch-2): scaffold BUG-030 migration + test file"
```

---

## Task 3: Add columns (Section 1 of migration) — TDD

**Files:**
- Modify: `supabase/migrations/20260425_batch2_bug030_bridge_trigger_fix.sql`
- Modify: `tests/test_batch2_bug030_bridge_trigger.py`

- [ ] **Step 1: Write failing test asserting 3 new columns exist**

Append to `tests/test_batch2_bug030_bridge_trigger.py`:

```python
def test_strategic_decisions_has_bug030_columns():
    """AC-BUG030-1/2/3: parent_msg_id + announce_to_agent + announce_thread_id."""
    expected = {
        "parent_msg_id": ("bigint", "YES"),
        "announce_to_agent": ("text", "YES"),
        "announce_thread_id": ("uuid", "YES"),
    }
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type, is_nullable
                  FROM information_schema.columns
                 WHERE table_name = 'strategic_decisions'
                   AND column_name = ANY(%s)
                """,
                (list(expected.keys()),),
            )
            actual = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    assert actual == expected, f"columns mismatch: expected {expected}, got {actual}"
```

- [ ] **Step 2: Verify test fails**

Run: `.venv/bin/python -m pytest tests/test_batch2_bug030_bridge_trigger.py::test_strategic_decisions_has_bug030_columns -v`
Expected: FAIL with `columns mismatch: expected {...}, got {}` (columns don't exist yet).

- [ ] **Step 3: Add Section 1 to migration**

Append to `supabase/migrations/20260425_batch2_bug030_bridge_trigger_fix.sql`:

```sql
-- ============================================================
-- SECTION 1: strategic_decisions new routing columns (all nullable)
-- ============================================================
-- Reverse:
--   ALTER TABLE strategic_decisions DROP COLUMN parent_msg_id;
--   ALTER TABLE strategic_decisions DROP COLUMN announce_to_agent;
--   ALTER TABLE strategic_decisions DROP COLUMN announce_thread_id;

ALTER TABLE strategic_decisions
  ADD COLUMN parent_msg_id BIGINT,
  ADD COLUMN announce_to_agent TEXT,
  ADD COLUMN announce_thread_id UUID;

COMMENT ON COLUMN strategic_decisions.parent_msg_id IS
  'BUG-030: BIGINT FK to agent_messages(id). If populated, bridge trigger '
  'inherits parent thread_id and reply-to-sender for to_agent routing '
  '(see trigger_cai_decision_announce). Nullable — legacy rows + decisions '
  'not responding to a specific message leave this NULL and hit Tier-3 fallback.';

COMMENT ON COLUMN strategic_decisions.announce_to_agent IS
  'BUG-030: explicit override for bridge trigger recipient. Highest precedence '
  'in the 3-tier COALESCE (explicit > inferred-from-parent > cc-ihsanos default).';

COMMENT ON COLUMN strategic_decisions.announce_thread_id IS
  'BUG-030: explicit override for bridge trigger thread_id. Highest precedence '
  'in the 3-tier COALESCE (explicit > inherit-from-parent > fresh uuid).';
```

Apply the migration locally (or re-run the migration via the same Task 7 apply harness, but for partial testing use direct SQL execution here):

Run:

```bash
.venv/bin/python -c "
import os; from dotenv import load_dotenv; load_dotenv()
import psycopg
sql = '''
ALTER TABLE strategic_decisions
  ADD COLUMN parent_msg_id BIGINT,
  ADD COLUMN announce_to_agent TEXT,
  ADD COLUMN announce_thread_id UUID;
'''
with psycopg.connect(os.environ['DATABASE_URL'], autocommit=False) as c:
    with c.cursor() as cur:
        cur.execute(sql)
    c.commit()
print('Section 1 applied')
"
```

- [ ] **Step 4: Verify test passes**

Run: `.venv/bin/python -m pytest tests/test_batch2_bug030_bridge_trigger.py::test_strategic_decisions_has_bug030_columns -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/20260425_batch2_bug030_bridge_trigger_fix.sql tests/test_batch2_bug030_bridge_trigger.py
git commit -m "feat(batch-2): strategic_decisions parent_msg_id + announce_to_agent + announce_thread_id columns (BUG-030)"
```

---

## Task 4: Add parent_msg_id FK (Section 2 of migration) — TDD

**Files:**
- Modify: `supabase/migrations/20260425_batch2_bug030_bridge_trigger_fix.sql`
- Modify: `tests/test_batch2_bug030_bridge_trigger.py`

- [ ] **Step 1: Write failing test for FK enforcement**

Append to `tests/test_batch2_bug030_bridge_trigger.py`:

```python
def test_parent_msg_id_fk_rejects_nonexistent_id():
    """AC-BUG030-1: FK REFERENCES agent_messages(id) must reject bad values."""
    with _conn() as c:
        with c.cursor() as cur:
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                cur.execute(
                    """
                    INSERT INTO strategic_decisions
                      (decision_ref, title, decision, reasoning, domain, status,
                       source, challenge_status, decided_by, parent_msg_id)
                    VALUES ('TEST-BUG030-FK', 't', 'd', 'r', 'governance', 'active',
                            'claude_ai_session', 'accepted', 'cai', 9999999999999)
                    """
                )
            c.rollback()
```

- [ ] **Step 2: Verify test fails**

Run: `.venv/bin/python -m pytest tests/test_batch2_bug030_bridge_trigger.py::test_parent_msg_id_fk_rejects_nonexistent_id -v`
Expected: FAIL with `DID NOT RAISE ForeignKeyViolation` (FK not yet added).

- [ ] **Step 3: Add Section 2 to migration + apply**

Append to migration file:

```sql
-- ============================================================
-- SECTION 2: parent_msg_id FK → agent_messages(id)
-- ============================================================
-- ON DELETE RESTRICT — a decision row pointing at a parent message should
-- prevent the parent's deletion; choose fail-loud over silent orphaning.
-- No ON UPDATE clause — agent_messages.id is an autogenerated bigint that
-- never changes in practice.
-- Reverse:
--   ALTER TABLE strategic_decisions DROP CONSTRAINT strategic_decisions_parent_msg_id_fkey;

ALTER TABLE strategic_decisions
  ADD CONSTRAINT strategic_decisions_parent_msg_id_fkey
  FOREIGN KEY (parent_msg_id) REFERENCES agent_messages(id) ON DELETE RESTRICT;

-- Partial index on populated parent_msg_id — trigger subquery + any parent-based
-- ad-hoc queries benefit. 300+ existing rows have NULL, indexing them wastes space.
CREATE INDEX IF NOT EXISTS strategic_decisions_parent_msg_id_idx
  ON strategic_decisions (parent_msg_id)
  WHERE parent_msg_id IS NOT NULL;
```

Apply:

```bash
.venv/bin/python -c "
import os; from dotenv import load_dotenv; load_dotenv()
import psycopg
sql = '''
ALTER TABLE strategic_decisions
  ADD CONSTRAINT strategic_decisions_parent_msg_id_fkey
  FOREIGN KEY (parent_msg_id) REFERENCES agent_messages(id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS strategic_decisions_parent_msg_id_idx
  ON strategic_decisions (parent_msg_id)
  WHERE parent_msg_id IS NOT NULL;
'''
with psycopg.connect(os.environ['DATABASE_URL'], autocommit=False) as c:
    with c.cursor() as cur:
        cur.execute(sql)
    c.commit()
print('Section 2 applied')
"
```

- [ ] **Step 4: Verify test passes**

Run: `.venv/bin/python -m pytest tests/test_batch2_bug030_bridge_trigger.py::test_parent_msg_id_fk_rejects_nonexistent_id -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/20260425_batch2_bug030_bridge_trigger_fix.sql tests/test_batch2_bug030_bridge_trigger.py
git commit -m "feat(batch-2): parent_msg_id FK + partial index (BUG-030 AC-1)"
```

---

## Task 5: Trigger rewrite (Section 3 of migration) — TDD

**Files:**
- Modify: `supabase/migrations/20260425_batch2_bug030_bridge_trigger_fix.sql`
- Modify: `tests/test_batch2_bug030_bridge_trigger.py`

- [ ] **Step 1: Write failing test for Tier-2 recipient inference (reply-to-sender)**

Append to `tests/test_batch2_bug030_bridge_trigger.py`:

```python
def test_tier2_recipient_inferred_from_parent_msg_sender():
    """AC-BUG030-4 Tier 2: parent_msg_id populated, announce_to_agent NULL →
    bridge infers to_agent = parent.from_agent."""
    with _conn() as c:
        with c.cursor() as cur:
            # Seed a parent message from cc-cosem (inbound to cai).
            cur.execute(
                """
                INSERT INTO agent_messages
                  (thread_id, from_agent, to_agent, message_type, subject, body)
                VALUES (gen_random_uuid(), 'cc-cosem', 'cai', 'question', 'p', 'p')
                RETURNING id, thread_id
                """
            )
            parent_id, parent_thread = cur.fetchone()

            cur.execute("SELECT set_config('app.current_agent_id', 'cai', true)")
            cur.execute(
                """
                INSERT INTO strategic_decisions
                  (decision_ref, title, decision, reasoning, domain, status,
                   source, challenge_status, decided_by, parent_msg_id,
                   challengeable_until)
                VALUES ('TEST-BUG030-T2', 't', 'd', 'r', 'governance', 'active',
                        'claude_ai_session', 'challenge_window', 'cai', %s,
                        now() + interval '1 day')
                RETURNING announced_by_msg_id
                """,
                (parent_id,),
            )
            announced_msg_id = cur.fetchone()[0]
            assert announced_msg_id is not None, "trigger did not populate announced_by_msg_id"

            cur.execute(
                "SELECT to_agent, thread_id FROM agent_messages WHERE id = %s",
                (announced_msg_id,),
            )
            to_agent, thread_id = cur.fetchone()
            assert to_agent == "cc-cosem", f"expected cc-cosem (parent.from_agent), got {to_agent}"
            assert thread_id == parent_thread, \
                f"expected inherited thread {parent_thread}, got {thread_id}"

        c.rollback()
```

- [ ] **Step 2: Verify test fails**

Run: `.venv/bin/python -m pytest tests/test_batch2_bug030_bridge_trigger.py::test_tier2_recipient_inferred_from_parent_msg_sender -v`
Expected: FAIL with `expected cc-cosem (parent.from_agent), got cc-ihsanos` (current trigger hardcodes cc-ihsanos).

- [ ] **Step 3: Add Section 3 (trigger rewrite) to migration + apply**

Append to migration file:

```sql
-- ============================================================
-- SECTION 3: trigger_cai_decision_announce rewrite — 3-tier routing
-- ============================================================
-- Preserves all existing guards (source, status, bypass_review,
-- announced_by_msg_id NULL, OLD-side suppression on UPDATE path).
-- Changes:
--   (a) to_agent: COALESCE(NEW.announce_to_agent, parent.from_agent, 'cc-ihsanos')
--   (b) thread_id: COALESCE(NEW.announce_thread_id, parent.thread_id, gen_random_uuid())
-- No change to guards, message_type/subject/body composition, or autoclose
-- sibling trigger (cai_decision_autoclose_announce is a separate function).
-- Reverse: restore prior body via psql (see cai_decision_announce_prior.sql backup if kept).

CREATE OR REPLACE FUNCTION public.trigger_cai_decision_announce()
  RETURNS trigger
  LANGUAGE plpgsql
  SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE
  v_msg_id BIGINT;
  v_subject TEXT;
  v_body TEXT;
  v_message_type TEXT;
  v_requires_response BOOLEAN;
  v_to_agent TEXT;
  v_thread_id UUID;
  v_parent_from_agent TEXT;
  v_parent_thread_id UUID;
BEGIN
  IF NEW.source IS DISTINCT FROM 'claude_ai_session'
     OR NEW.challenge_status NOT IN ('challenge_window', 'accepted')
     OR COALESCE(NEW.bypass_review, false) = true
     OR NEW.announced_by_msg_id IS NOT NULL THEN
    RETURN NEW;
  END IF;

  IF TG_OP = 'UPDATE' THEN
    IF OLD.announced_by_msg_id IS NOT NULL
       OR OLD.execution_status = 'implemented' THEN
      RETURN NEW;
    END IF;
  END IF;

  -- BUG-030: resolve recipient + thread via 3-tier fallback.
  IF NEW.parent_msg_id IS NOT NULL THEN
    SELECT from_agent, thread_id
      INTO v_parent_from_agent, v_parent_thread_id
      FROM agent_messages
     WHERE id = NEW.parent_msg_id;
    -- If parent_msg_id FK was satisfied but the row was deleted between
    -- FK check and this SELECT (ON DELETE RESTRICT makes this ~impossible,
    -- but belt-and-suspenders), fall through to NULL.
  END IF;

  v_to_agent := COALESCE(
    NEW.announce_to_agent,       -- Tier 1: explicit override
    v_parent_from_agent,         -- Tier 2: reply-to-sender inference
    'cc-ihsanos'                 -- Tier 3: legacy default
  );

  v_thread_id := COALESCE(
    NEW.announce_thread_id,      -- Tier 1: explicit override
    v_parent_thread_id,          -- Tier 2: inherit from parent
    gen_random_uuid()            -- Tier 3: fresh thread
  );

  IF NEW.challenge_status = 'challenge_window' THEN
    v_message_type := 'review_request';
    v_subject := NEW.decision_ref || ': ' || NEW.title || ' — for review + challenge';
    v_requires_response := true;
  ELSE
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
    v_thread_id, 'cai', v_to_agent, v_message_type,
    v_subject, v_body, v_requires_response
  )
  RETURNING id INTO v_msg_id;

  NEW.announced_by_msg_id := v_msg_id;
  NEW.notified_at := now();
  RETURN NEW;
END;
$function$;
```

Apply:

```bash
.venv/bin/python -c "
import os; from dotenv import load_dotenv; load_dotenv()
import psycopg
from pathlib import Path
# Section 3 SQL — copy-paste the full CREATE OR REPLACE FUNCTION block above into a variable.
sql = Path('/tmp/batch2_section3.sql').read_text()  # write the block to /tmp first
with psycopg.connect(os.environ['DATABASE_URL'], autocommit=False) as c:
    with c.cursor() as cur:
        cur.execute(sql)
    c.commit()
print('Section 3 applied')
"
```

(Engineer note: write the CREATE OR REPLACE FUNCTION block to `/tmp/batch2_section3.sql` before running — safer than inline heredoc.)

- [ ] **Step 4: Verify test passes**

Run: `.venv/bin/python -m pytest tests/test_batch2_bug030_bridge_trigger.py::test_tier2_recipient_inferred_from_parent_msg_sender -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/20260425_batch2_bug030_bridge_trigger_fix.sql tests/test_batch2_bug030_bridge_trigger.py
git commit -m "feat(batch-2): trigger_cai_decision_announce 3-tier routing (BUG-030 AC-4)"
```

---

## Task 6: Tier-1 explicit recipient override test

**Files:** Modify `tests/test_batch2_bug030_bridge_trigger.py`

- [ ] **Step 1: Write test**

Append:

```python
def test_tier1_explicit_announce_to_agent_overrides_inference():
    """AC-BUG030-4 Tier 1: announce_to_agent populated → overrides parent inference."""
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_messages
                  (thread_id, from_agent, to_agent, message_type, subject, body)
                VALUES (gen_random_uuid(), 'cc-cosem', 'cai', 'question', 'p', 'p')
                RETURNING id
                """
            )
            parent_id = cur.fetchone()[0]

            cur.execute("SELECT set_config('app.current_agent_id', 'cai', true)")
            cur.execute(
                """
                INSERT INTO strategic_decisions
                  (decision_ref, title, decision, reasoning, domain, status,
                   source, challenge_status, decided_by, parent_msg_id,
                   announce_to_agent, challengeable_until)
                VALUES ('TEST-BUG030-T1A', 't', 'd', 'r', 'governance', 'active',
                        'claude_ai_session', 'challenge_window', 'cai', %s,
                        'cc-scholar', now() + interval '1 day')
                RETURNING announced_by_msg_id
                """,
                (parent_id,),
            )
            msg_id = cur.fetchone()[0]
            cur.execute("SELECT to_agent FROM agent_messages WHERE id = %s", (msg_id,))
            assert cur.fetchone()[0] == "cc-scholar", "explicit override ignored"
        c.rollback()
```

- [ ] **Step 2: Run test**

Run: `.venv/bin/python -m pytest tests/test_batch2_bug030_bridge_trigger.py::test_tier1_explicit_announce_to_agent_overrides_inference -v`
Expected: PASS (trigger already deployed in Task 5).

- [ ] **Step 3: Commit**

```bash
git add tests/test_batch2_bug030_bridge_trigger.py
git commit -m "test(batch-2): Tier-1 explicit announce_to_agent override (BUG-030)"
```

---

## Task 7: Tier-1 explicit thread_id override test

**Files:** Modify `tests/test_batch2_bug030_bridge_trigger.py`

- [ ] **Step 1: Write test**

Append:

```python
def test_tier1_explicit_announce_thread_id_overrides_inheritance():
    """AC-BUG030-4 Tier 1: announce_thread_id populated → overrides parent.thread_id."""
    override_thread = str(uuid.uuid4())
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_messages
                  (thread_id, from_agent, to_agent, message_type, subject, body)
                VALUES (gen_random_uuid(), 'cc-cosem', 'cai', 'question', 'p', 'p')
                RETURNING id, thread_id
                """
            )
            parent_id, parent_thread = cur.fetchone()
            assert str(parent_thread) != override_thread  # sanity

            cur.execute("SELECT set_config('app.current_agent_id', 'cai', true)")
            cur.execute(
                """
                INSERT INTO strategic_decisions
                  (decision_ref, title, decision, reasoning, domain, status,
                   source, challenge_status, decided_by, parent_msg_id,
                   announce_thread_id, challengeable_until)
                VALUES ('TEST-BUG030-T1T', 't', 'd', 'r', 'governance', 'active',
                        'claude_ai_session', 'challenge_window', 'cai', %s, %s,
                        now() + interval '1 day')
                RETURNING announced_by_msg_id
                """,
                (parent_id, override_thread),
            )
            msg_id = cur.fetchone()[0]
            cur.execute("SELECT thread_id FROM agent_messages WHERE id = %s", (msg_id,))
            assert str(cur.fetchone()[0]) == override_thread, "thread_id override ignored"
        c.rollback()
```

- [ ] **Step 2: Run test**

Run: `.venv/bin/python -m pytest tests/test_batch2_bug030_bridge_trigger.py::test_tier1_explicit_announce_thread_id_overrides_inheritance -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_batch2_bug030_bridge_trigger.py
git commit -m "test(batch-2): Tier-1 explicit announce_thread_id override (BUG-030)"
```

---

## Task 8: Tier-3 legacy fallback test

**Files:** Modify `tests/test_batch2_bug030_bridge_trigger.py`

- [ ] **Step 1: Write test**

Append:

```python
def test_tier3_legacy_fallback_when_parent_msg_id_null():
    """AC-BUG030-4 Tier 3: parent_msg_id NULL + announce_* NULL → cc-ihsanos default +
    fresh thread_id. Backward-compatible behavior for all 300+ pre-migration rows."""
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT set_config('app.current_agent_id', 'cai', true)")
            cur.execute(
                """
                INSERT INTO strategic_decisions
                  (decision_ref, title, decision, reasoning, domain, status,
                   source, challenge_status, decided_by, challengeable_until)
                VALUES ('TEST-BUG030-T3', 't', 'd', 'r', 'governance', 'active',
                        'claude_ai_session', 'challenge_window', 'cai',
                        now() + interval '1 day')
                RETURNING announced_by_msg_id
                """
            )
            msg_id = cur.fetchone()[0]
            cur.execute(
                "SELECT to_agent, thread_id FROM agent_messages WHERE id = %s",
                (msg_id,),
            )
            to_agent, thread_id = cur.fetchone()
            assert to_agent == "cc-ihsanos"
            assert thread_id is not None
        c.rollback()
```

- [ ] **Step 2: Run test**

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_batch2_bug030_bridge_trigger.py
git commit -m "test(batch-2): Tier-3 legacy fallback preserved (BUG-030)"
```

---

## Task 9: UPDATE-path firing test (BUG-020 regression guard)

**Files:** Modify `tests/test_batch2_bug030_bridge_trigger.py`

- [ ] **Step 1: Write test**

Append:

```python
def test_update_path_fires_trigger_on_challenge_status_change():
    """AC-BUG030-5: UPDATE of challenge_status from accepted_by_timeout →
    challenge_window must fire the bridge trigger (BUG-020 precedent preserved)."""
    with _conn() as c:
        with c.cursor() as cur:
            # Seed a decision in challenge_window with announced_by_msg_id cleared
            # — simulates a decision that was filed without bridge (rare but possible
            # when bypass_review=true was initially true then flipped).
            cur.execute("SELECT set_config('app.current_agent_id', 'cai', true)")
            cur.execute(
                """
                INSERT INTO strategic_decisions
                  (decision_ref, title, decision, reasoning, domain, status,
                   source, challenge_status, decided_by, bypass_review,
                   challengeable_until)
                VALUES ('TEST-BUG030-UPD', 't', 'd', 'r', 'governance', 'active',
                        'claude_ai_session', 'challenge_window', 'cai', true,
                        now() + interval '1 day')
                """
            )
            # Flip bypass_review → false then flip challenge_status to trigger.
            cur.execute(
                "UPDATE strategic_decisions SET bypass_review = false "
                "WHERE decision_ref = 'TEST-BUG030-UPD'"
            )
            cur.execute(
                "UPDATE strategic_decisions SET challenge_status = 'accepted' "
                "WHERE decision_ref = 'TEST-BUG030-UPD' "
                "RETURNING announced_by_msg_id"
            )
            msg_id = cur.fetchone()[0]
            assert msg_id is not None, "UPDATE path did not fire trigger"
        c.rollback()
```

- [ ] **Step 2: Run test**

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_batch2_bug030_bridge_trigger.py
git commit -m "test(batch-2): UPDATE-path firing guard (BUG-030 + BUG-020 precedent)"
```

---

## Task 10: Migration Section 4 (assertion gate) + cai_persona.md discipline note

**Files:**
- Modify: `supabase/migrations/20260425_batch2_bug030_bridge_trigger_fix.sql`
- Modify: `scripts/agents/cai_persona.md` (if exists — path verified in Task 1)
- Modify: `tests/test_batch2_bug030_bridge_trigger.py`

- [ ] **Step 1: Write failing test for trigger-body assertion**

Append to test file:

```python
def test_trigger_body_contains_tiered_coalesce_pattern():
    """Section 4 guard: trigger body must reference all three tier inputs.
    A future refactor that drops announce_to_agent from the COALESCE silently
    regresses BUG-030; this test catches it at commit time."""
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                SELECT pg_get_functiondef(oid)
                  FROM pg_proc
                 WHERE proname = 'trigger_cai_decision_announce'
                """
            )
            body = cur.fetchone()[0]
    for token in ("NEW.announce_to_agent", "NEW.announce_thread_id",
                  "NEW.parent_msg_id", "cc-ihsanos"):
        assert token in body, f"trigger body missing {token}"
```

Expected: PASS (already applied via Task 5). This test is for future-regression insurance.

- [ ] **Step 2: Append Section 4 assertion to migration**

Append:

```sql
-- ============================================================
-- SECTION 4: post-apply assertion — trigger body carries the 3-tier pattern
-- ============================================================

DO $$
DECLARE
  body TEXT;
BEGIN
  SELECT pg_get_functiondef(oid) INTO body
    FROM pg_proc WHERE proname = 'trigger_cai_decision_announce';
  IF body NOT LIKE '%NEW.announce_to_agent%'
     OR body NOT LIKE '%NEW.announce_thread_id%'
     OR body NOT LIKE '%NEW.parent_msg_id%' THEN
    RAISE EXCEPTION 'BUG-030 migration assertion failed: trigger body missing 3-tier pattern';
  END IF;
END $$;

COMMIT;
```

- [ ] **Step 3: Update cai_persona.md (if path exists)**

Open `scripts/agents/cai_persona.md` (or wherever Task 1 found it) and add a new section under existing governance-hygiene content:

```markdown
## BUG-030 discipline: routing decisions to their source

When filing a `strategic_decisions` row as a response to a specific `agent_messages` thread, **populate `parent_msg_id`** with the message id you are replying to. The bridge trigger will:
- Inherit the parent message's `thread_id` → keeps the conversation threaded for the polling agent.
- Set `to_agent = parent.from_agent` → reply reaches the sender, not the legacy `cc-ihsanos` default.

For explicit overrides (e.g., broadcasting a decision to a different family than the sender), set `announce_to_agent` and/or `announce_thread_id` directly. These take highest precedence over the parent inference.

Filing with `parent_msg_id = NULL` is allowed (non-reply decisions), and behaves per the legacy Tier-3 fallback (cc-ihsanos + fresh thread). Do not use this as a default — use it only for decisions that genuinely have no parent message.
```

- [ ] **Step 4: Run full Batch 2 test file**

Run: `.venv/bin/python -m pytest tests/test_batch2_bug030_bridge_trigger.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/20260425_batch2_bug030_bridge_trigger_fix.sql tests/test_batch2_bug030_bridge_trigger.py scripts/agents/cai_persona.md
git commit -m "feat(batch-2): Section 4 assertion gate + cai_persona BUG-030 discipline"
```

---

## Task 11: File review_request per CAI-RESP-080 Refinement 2

**Files:** none — agent_messages write only.

- [ ] **Step 1: Assemble review_request body**

Per CAI-RESP-080 Refinement 2 protocol, the review message includes:
1. Full inline SQL of the migration (copy-paste from `supabase/migrations/20260425_batch2_bug030_bridge_trigger_fix.sql`).
2. Diff summary vs pre-migration state (columns + trigger body).
3. Pre-flight re-verification commands (Task 1 script output).
4. Local pytest status (Task 10 Step 4 output).

- [ ] **Step 2: Post to agent_messages**

Run a Python script that inserts via supabase-py:

```python
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
from supabase import create_client
c = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])

sql = Path('supabase/migrations/20260425_batch2_bug030_bridge_trigger_fix.sql').read_text()
body = f"""## Batch 2 BUG-030 bridge trigger migration — ready for adversarial review per CAI-RESP-080 Refinement 2

Branch: feat/batch-2-bug030-bridge-trigger-fix
Plan: docs/superpowers/plans/2026-04-24-batch-2-bug030-bridge-trigger-fix.md

## (a) Full SQL (inline)

```sql
{sql}
```

## (b) Diff vs pre-migration

Columns added: parent_msg_id BIGINT (FK agent_messages(id) ON DELETE RESTRICT, partial index), announce_to_agent TEXT, announce_thread_id UUID.

Trigger change: trigger_cai_decision_announce body — `to_agent` now `COALESCE(NEW.announce_to_agent, parent.from_agent, 'cc-ihsanos')`, `thread_id` now `COALESCE(NEW.announce_thread_id, parent.thread_id, gen_random_uuid())`. All guards preserved. OLD-side suppression unchanged.

## (c) Pre-flight re-verification

(run output from Task 1 Step 1 goes here)

## (d) Local pytest status

tests/test_batch2_bug030_bridge_trigger.py: 7/7 PASS
tests/ overall: 460/460 PASS

## Open questions

(None for me — cc-ihsanos AGREED all 5 ACs in msg #631. Noting for completeness.)

Ready for review. Will remote-apply on CAI AGREED per CAI-RESP-081 pattern.
"""
c.table('agent_messages').insert({
    'from_agent': 'cc-orchestrator', 'to_agent': 'cai',
    'message_type': 'review_request',
    'subject': 'Batch 2 BUG-030 bridge trigger — review per CAI-RESP-080 Refinement 2',
    'body': body, 'priority': 'P1', 'requires_response': True,
    'sub_tag': 'cc-orchestrator-2',
}).execute()
```

Run it.

- [ ] **Step 3: Update agent_status heartbeat**

```python
import os; from dotenv import load_dotenv; load_dotenv()
import psycopg
with psycopg.connect(os.environ['DATABASE_URL'], autocommit=False) as c:
    with c.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id', 'cc-orchestrator-2', true)")
        cur.execute(
            "UPDATE agent_status SET current_task = %s, last_heartbeat = now(), updated_at = now() "
            "WHERE agent_id = 'cc-orchestrator-2'",
            ('Batch 2 BUG-030 — awaiting cai review',),
        )
    c.commit()
```

---

## Task 12: Apply migration remote + record in schema_migrations (post-review)

**Files:** none — Supabase write.

- [ ] **Step 1: Wait for cai AGREED (response_ref on Task 11 review_request)**

Do NOT apply until cai responds with AGREED. If cai challenges, close this plan's open questions via reply thread + amend migration as required.

- [ ] **Step 2: Apply atomically**

```bash
.venv/bin/python <<'PY'
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
import psycopg
sql = Path('supabase/migrations/20260425_batch2_bug030_bridge_trigger_fix.sql').read_text()
with psycopg.connect(os.environ['DATABASE_URL'], autocommit=False) as c:
    with c.cursor() as cur:
        cur.execute(sql)
    c.commit()
print('migration applied')
PY
```

- [ ] **Step 3: Record in schema_migrations**

```bash
.venv/bin/python <<'PY'
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
import psycopg
sql = Path('supabase/migrations/20260425_batch2_bug030_bridge_trigger_fix.sql').read_text()
with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute(
            "INSERT INTO supabase_migrations.schema_migrations (version, name, statements) "
            "VALUES (%s, %s, ARRAY[%s]::text[]) ON CONFLICT (version) DO NOTHING",
            ('20260425000000', 'batch2_bug030_bridge_trigger_fix', sql),
        )
    print('recorded')
PY
```

- [ ] **Step 4: Run full test suite against post-migration state**

Run: `.venv/bin/python -m pytest tests/ --timeout=120 -q`
Expected: `460 passed` (or whatever total increases to after +7).

- [ ] **Step 5: Push + open PR**

```bash
git push -u origin feat/batch-2-bug030-bridge-trigger-fix
gh pr create --title "BUG-030: bridge trigger 3-tier routing (parent_msg_id + announce_* overrides)" \
  --body "See plan at docs/superpowers/plans/2026-04-24-batch-2-bug030-bridge-trigger-fix.md ..."
```

---

## Task 13: Post SHIPPED agent_messages + close out

**Files:** none.

- [ ] **Step 1: Post SHIPPED update to cai**

Use Batch 1 msg #687 as style precedent. Include: branch, commit, PR link, migration version, AC checklist, test suite status, post-migration row counts, regression test names.

- [ ] **Step 2: Update STATUS.md**

Replace `## Last Completed` section with Batch 2 BUG-030 summary per repo convention. Preserve prior `## Last Completed` as `## Previously Completed`.

- [ ] **Step 3: Final commit**

```bash
git add STATUS.md
git commit -m "docs: STATUS.md — BUG-030 shipped"
git push
```

---

## Self-review (run after plan complete)

**Spec coverage:**
- AC-BUG030-1 → Task 3 + Task 4 (column + FK) ✓
- AC-BUG030-2 → Task 3 (announce_to_agent column) ✓
- AC-BUG030-3 → Task 3 (announce_thread_id column) ✓
- AC-BUG030-4 → Task 5 (trigger rewrite) + Tasks 6-9 (all tier tests) ✓
- AC-BUG030-5 → Tests across Tasks 5-10 (thread continuity, recipient routing, explicit override, fallback, FK, UPDATE-path) ✓

**Placeholder scan:** migration SQL, test code, and commit commands are inline-complete. The only "fill-in-value" placeholder is the review_request body's `(run output from Task 1 Step 1 goes here)` — engineer appends real output at send time, not a code placeholder.

**Type consistency:** `announce_to_agent TEXT`, `announce_thread_id UUID`, `parent_msg_id BIGINT` used consistently across migration + tests. `COALESCE` tier ordering (`explicit > inferred > legacy`) consistent in every task that references it.

---
