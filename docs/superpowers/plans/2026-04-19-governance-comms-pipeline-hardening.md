# Governance Comms Pipeline v1 Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the BUG-020 strategic_decisions→agent_messages trigger and the BUG-021 `forwarded_to_telegram_at` rename so CAI decisions auto-announce to CC and middleware stops clobbering `read_at`.

**Architecture:** One atomic Supabase migration (columns + trigger + backfill) plus two Python notifier edits, deployed sequentially with verification between each beat. Inline execution preferred over subagents — the task set is small (9 tasks) and tightly coupled through deploy order.

**Tech Stack:** Supabase Postgres 14+, Python 3.9 (orchestrator), pytest+pytest-asyncio, Node.js (ad-hoc verification queries), git.

**Spec:** `docs/superpowers/specs/2026-04-18-governance-comms-pipeline-hardening-design.md`

---

## File map

| File | Change | Task |
|------|--------|------|
| `supabase/migrations/20260419_bug020_bug021_governance_comms_hardening.sql` | **Create** — full migration (schema + trigger + backfill) | 1 |
| `tests/test_agent_messages_poll.py` | **Modify** — rename 1 test, add 2 tests | 2 |
| `nervous_system/agent_messages_poll.py` | **Modify** — rename `_mark_read`→`_mark_forwarded`, change column, update SELECT, drop cc-* guard | 3 |
| `scripts/build_launch_context.py` | **Modify** — bulk update column + log message | 4 |
| *(deploy steps — no file changes, verification only)* | | 5–8 |
| `~/wingmen/projects/ihsanos/CLAUDE.md` | **Modify (separate repo)** — revert two-query inbox workaround | 9 |

## Commit strategy

One commit per task (Tasks 1–4, 9). Tasks 5–8 are deploy operations, not commits. Task 9 lands in the ihsanos repo.

**Migration date:** The spec is dated 2026-04-18; the migration filename and this plan use **2026-04-19** (today). If implementation slips past today, rename the migration file to match the actual apply date before running Task 5.

---

## Task 1: Write migration SQL

**Files:**
- Create: `supabase/migrations/20260419_bug020_bug021_governance_comms_hardening.sql`

**Reference:** Spec §"Migration contents" — all SQL is transcribed directly below, no interpretation needed.

- [ ] **Step 1: Create the migration file**

Write the file with this exact content:

```sql
-- BUG-020 + BUG-021: Governance comms pipeline v1 hardening.
-- Fixes 2026-04-18 governance blackout. See:
--   docs/superpowers/specs/2026-04-18-governance-comms-pipeline-hardening-design.md
--
-- Adds:
--   1. agent_messages.forwarded_to_telegram_at   (BUG-021 — replaces read_at clobber)
--   2. strategic_decisions.announced_by_msg_id   (BUG-020 — FK dedup for trigger)
--   3. Partial indexes on the NULL subsets of both columns
--   4. trigger_cai_decision_announce() function + two triggers
--   5. Per-orphan atomic backfill DO block
--
-- RLS deferred (service_role bypasses; dead policies worse than none).

-- ── 1. Schema changes ───────────────────────────────────────────────────────

ALTER TABLE agent_messages
  ADD COLUMN IF NOT EXISTS forwarded_to_telegram_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS agent_messages_forwarded_idx
  ON agent_messages (forwarded_to_telegram_at)
  WHERE forwarded_to_telegram_at IS NULL;

ALTER TABLE strategic_decisions
  ADD COLUMN IF NOT EXISTS announced_by_msg_id BIGINT
    REFERENCES agent_messages(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS strategic_decisions_announced_idx
  ON strategic_decisions (announced_by_msg_id)
  WHERE announced_by_msg_id IS NULL;

-- ── 2. BUG-020 trigger function + triggers ──────────────────────────────────

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

-- ── 3. Backfill sweep (atomic per orphan) ───────────────────────────────────

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

- [ ] **Step 2: Syntax check**

Verify the file parses. If `psql` is available:

Run: `psql --set ON_ERROR_STOP=1 --single-transaction --command '\i supabase/migrations/20260419_bug020_bug021_governance_comms_hardening.sql'` against a disposable DB.

If no disposable DB: visually confirm the file matches the spec verbatim (no copy-paste drift). Skipping this is acceptable — Step 5 (live apply) will catch any syntax error before it reaches production effect.

- [ ] **Step 3: Commit**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git add supabase/migrations/20260419_bug020_bug021_governance_comms_hardening.sql
git commit -m "feat(db): BUG-020/021 governance comms hardening migration

Adds forwarded_to_telegram_at on agent_messages, announced_by_msg_id
FK on strategic_decisions, BUG-020 trigger, and per-orphan backfill.
RLS deferred per spec.

See docs/superpowers/specs/2026-04-18-governance-comms-pipeline-hardening-design.md"
```

---

## Task 2: Update test file (RED phase for Python changes)

**Files:**
- Modify: `tests/test_agent_messages_poll.py`

Rename one test and add two new ones. These will FAIL until Task 3 ships the Python changes. That is the intended red state.

- [ ] **Step 1: Rename the telegram failure test**

Replace the existing `test_telegram_failure_does_not_mark_read` at lines ~267-295 with the renamed version. New test name asserts on the new column semantics.

Find:

```python
    @pytest.mark.asyncio
    async def test_telegram_failure_does_not_mark_read(self):
        """If Telegram send fails, the message stays unread for retry."""
        msg = _make_msg(6, subject="CC-UPDATE-031: failed send")
```

Replace with:

```python
    @pytest.mark.asyncio
    async def test_telegram_failure_does_not_mark_forwarded(self):
        """If Telegram send fails, forwarded_to_telegram_at stays NULL for retry."""
        msg = _make_msg(6, subject="CC-UPDATE-031: failed send")
```

(The test body stays identical — it already asserts `update_call_count == 0`, which holds regardless of column name.)

- [ ] **Step 2: Add test_successful_forward_stamps_forwarded_to_telegram_at**

At the end of `class TestPollAgentMessages` (append before the closing of the file at line ~351, inside the class), add:

```python
    @pytest.mark.asyncio
    async def test_successful_forward_stamps_forwarded_to_telegram_at(self):
        """After a successful Telegram send, forwarded_to_telegram_at is set."""
        msg = _make_msg(100, to_agent="musa", subject="BUG-021: positive case")
        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.is_.return_value = sb
        sb.order.return_value = sb
        sb.eq.return_value = sb
        sb.limit.return_value = sb
        sb.insert.return_value = sb
        sb.update.return_value = sb
        sb.execute = AsyncMock(side_effect=[
            MagicMock(data=[msg]),       # agent_messages select
            MagicMock(data=[]),          # dedup empty
            MagicMock(data=[]),          # log insert
            MagicMock(data=[]),          # mark forwarded
        ])

        bot = AsyncMock()
        sent_mock = MagicMock()
        sent_mock.message_id = 100
        bot.send_message = AsyncMock(return_value=sent_mock)

        await poll_agent_messages(sb, bot=bot, musa_chat_id="123456")

        update_calls = [
            call for call in sb.update.call_args_list
        ]
        assert any(
            "forwarded_to_telegram_at" in str(call)
            for call in update_calls
        ), f"Expected update with forwarded_to_telegram_at, got: {update_calls}"
```

- [ ] **Step 3: Add test_forwarding_does_not_modify_read_at (regression guard)**

Immediately after the previous test, add:

```python
    @pytest.mark.asyncio
    async def test_forwarding_does_not_modify_read_at(self):
        """BUG-021 regression guard: middleware must never write read_at."""
        msg = _make_msg(101, to_agent="musa", subject="BUG-021: regression guard")
        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.is_.return_value = sb
        sb.order.return_value = sb
        sb.eq.return_value = sb
        sb.limit.return_value = sb
        sb.insert.return_value = sb
        sb.update.return_value = sb
        sb.execute = AsyncMock(side_effect=[
            MagicMock(data=[msg]),
            MagicMock(data=[]),
            MagicMock(data=[]),
            MagicMock(data=[]),
        ])

        bot = AsyncMock()
        sent_mock = MagicMock()
        sent_mock.message_id = 101
        bot.send_message = AsyncMock(return_value=sent_mock)

        await poll_agent_messages(sb, bot=bot, musa_chat_id="123456")

        # No update call may contain read_at as a key
        for call in sb.update.call_args_list:
            args, _kwargs = call
            if args:
                payload = args[0]
                assert "read_at" not in payload, (
                    f"BUG-021 regression: middleware wrote read_at: {payload}"
                )
```

- [ ] **Step 4: Run the test file — confirm 3 failures (RED)**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && pytest tests/test_agent_messages_poll.py -v`

Expected:
- `test_telegram_failure_does_not_mark_forwarded` — PASS (body unchanged, only renamed)
- `test_successful_forward_stamps_forwarded_to_telegram_at` — FAIL (code still writes `read_at`, not `forwarded_to_telegram_at`)
- `test_forwarding_does_not_modify_read_at` — FAIL (code still writes `read_at`)

If all three pass, something is wrong — the Python code must not have been touched yet. Stop and investigate.

- [ ] **Step 5: Commit the tests**

```bash
git add tests/test_agent_messages_poll.py
git commit -m "test(agent_messages_poll): add BUG-021 column assertions

- Rename test_telegram_failure_does_not_mark_read to …_mark_forwarded
- Add test_successful_forward_stamps_forwarded_to_telegram_at
- Add test_forwarding_does_not_modify_read_at (regression guard)

Tests fail until Task 3 ships the _mark_forwarded rename."
```

---

## Task 3: Update agent_messages_poll.py

**Files:**
- Modify: `nervous_system/agent_messages_poll.py`

Four changes in this file:
1. Rename `_mark_read` → `_mark_forwarded`, change column written
2. Update polling SELECT to also filter on `forwarded_to_telegram_at IS NULL`
3. Remove the `cc-*` guard around the call (the new column is safe for all targets)
4. Update the `_already_notified` call site to use the new function name

- [ ] **Step 1: Replace the `_mark_read` function**

Find at lines ~277-285:

```python
async def _mark_read(supabase, msg_id: int) -> None:
    """Set read_at on an agent_messages row."""
    try:
        await supabase.table("agent_messages").update(
            {"read_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", msg_id).execute()
    except Exception as e:
        logger.error(f"Failed to mark agent_message {msg_id} as read: {e}")
        error_tracker.track_exception("agent_messages_poll.mark_read", e)
```

Replace with:

```python
async def _mark_forwarded(supabase, msg_id: int) -> None:
    """Set forwarded_to_telegram_at on an agent_messages row.

    BUG-021: middleware must NEVER write read_at — that column is reserved
    for the addressed agent's own processing stamp. Forwarding state lives
    on its own column so semantics stay honest.
    """
    try:
        await supabase.table("agent_messages").update(
            {"forwarded_to_telegram_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", msg_id).execute()
    except Exception as e:
        logger.error(f"Failed to mark agent_message {msg_id} as forwarded: {e}")
        error_tracker.track_exception("agent_messages_poll.mark_forwarded", e)
```

- [ ] **Step 2: Update the polling SELECT (add forwarded filter)**

Find at lines ~127-130:

```python
        result = await supabase.table("agent_messages").select(
            "id, from_agent, to_agent, message_type, subject, body, "
            "requires_response, created_at"
        ).is_("read_at", "null").order("created_at", desc=False).execute()
```

Replace with:

```python
        result = await supabase.table("agent_messages").select(
            "id, from_agent, to_agent, message_type, subject, body, "
            "requires_response, created_at"
        ).is_("read_at", "null").is_(
            "forwarded_to_telegram_at", "null"
        ).order("created_at", desc=False).execute()
```

Rationale: the notifier should skip messages it has already forwarded, even if the addressed agent hasn't stamped `read_at` yet.

- [ ] **Step 3: Drop the cc-* guard around the mark call**

Find at lines ~208-213:

```python
            # Only mark read for musa/broadcast — direct targets that don't
            # need to poll themselves. For cc-* relay targets, leave read_at
            # null so the agent can detect and process the message itself.
            to_agent = msg.get("to_agent", "")
            if not to_agent.startswith(_CC_PREFIX):
                await _mark_read(supabase, msg_id)
```

Replace with:

```python
            # BUG-021: mark forwarded unconditionally. The new column records
            # middleware activity (we sent it to Telegram) and is safe to set
            # for every target — unlike read_at, which belongs to the agent.
            await _mark_forwarded(supabase, msg_id)
```

- [ ] **Step 4: Update the dedup-path mark call**

Find at lines ~235-237:

```python
            # Ensure read_at is set even if a previous run notified but didn't mark read
            await _mark_read(supabase, msg_id)
            return True
```

Replace with:

```python
            # Ensure forwarded_to_telegram_at is set even if a previous run
            # notified but didn't mark it — prevents re-forwarding on restart.
            await _mark_forwarded(supabase, msg_id)
            return True
```

- [ ] **Step 5: Update the module docstring (cleanup)**

Find at lines ~17-19:

```python
After notifying, messages are marked read_at=NOW() and logged to
notification_log with a dedup_key to prevent double-sends on restart.
"""
```

Replace with:

```python
After notifying, messages are stamped forwarded_to_telegram_at=NOW() and
logged to notification_log with a dedup_key to prevent double-sends on
restart. BUG-021: do NOT write read_at — that belongs to the addressed agent.
"""
```

- [ ] **Step 6: Run the test file — confirm all pass (GREEN)**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && pytest tests/test_agent_messages_poll.py -v`

Expected: all tests PASS, including the two new ones added in Task 2.

If `test_successful_forward_stamps_forwarded_to_telegram_at` still fails, check that Step 1 actually changed the column string in the dict literal (not just the function name).

If an unrelated test broke: most likely the polling SELECT change (Step 2) now exercises `.is_` twice on the mock chain — the existing mocks already return `sb` from `.is_.return_value`, so chaining should still work. If a specific test fails, read its error and patch the mock.

- [ ] **Step 7: Run the full orchestrator test suite**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && pytest -x --timeout=60 2>&1 | tail -40`

Expected: all green. If anything else broke, investigate before proceeding.

- [ ] **Step 8: Commit**

```bash
git add nervous_system/agent_messages_poll.py
git commit -m "fix(notifier): BUG-021 stamp forwarded_to_telegram_at, not read_at

- Rename _mark_read → _mark_forwarded
- Update polling SELECT to filter on both read_at IS NULL AND
  forwarded_to_telegram_at IS NULL
- Drop the cc-* guard — the new column is safe to set for all targets
- Update module docstring

Tests added in previous commit now pass."
```

---

## Task 4: Update build_launch_context.py

**Files:**
- Modify: `scripts/build_launch_context.py`

Single-location change — the bulk update in `build_launch_context` now stamps the new column. This script runs at boot briefing time and forwards the inbox summary to Musa; it is middleware, not an agent, so it must use the forwarding column.

- [ ] **Step 1: Replace the bulk update**

Find at lines ~136-142:

```python
    # ── 4. Mark messages as read + bump heartbeat ────────────────────────────
    if not dry_run and inbox:
        ids_to_mark = [m["id"] for m in inbox]
        client.table("agent_messages").update(
            {"read_at": now_ts}
        ).in_("id", ids_to_mark).execute()
        print(f"build_launch_context: marked {len(ids_to_mark)} message(s) as read", file=sys.stderr)
```

Replace with:

```python
    # ── 4. Mark messages forwarded + bump heartbeat (BUG-021) ───────────────
    # Boot briefing forwards the inbox summary to Musa via Telegram — it is
    # middleware, not an agent. Stamp forwarded_to_telegram_at, never read_at.
    if not dry_run and inbox:
        ids_to_mark = [m["id"] for m in inbox]
        client.table("agent_messages").update(
            {"forwarded_to_telegram_at": now_ts}
        ).in_("id", ids_to_mark).execute()
        print(
            f"build_launch_context: stamped forwarded_to_telegram_at on "
            f"{len(ids_to_mark)} message(s)",
            file=sys.stderr,
        )
```

- [ ] **Step 2: Check for any other read_at writes in the file**

Run: `grep -n read_at /Users/sheikhmusa/wingmen/orchestrator/scripts/build_launch_context.py`

Expected: any results should be **read-only** references (e.g., a `.is_("read_at", "null")` select filter), not write paths. If there is another `.update({"read_at": …})` not covered above, replace it the same way.

- [ ] **Step 3: Run the orchestrator test suite again**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && pytest -x --timeout=60 2>&1 | tail -20`

Expected: all green. `build_launch_context.py` has no dedicated tests, but a broader smoke must stay clean.

- [ ] **Step 4: Commit**

```bash
git add scripts/build_launch_context.py
git commit -m "fix(boot): BUG-021 stamp forwarded_to_telegram_at in boot briefing

The boot briefing forwards the inbox summary to Musa via Telegram.
It is middleware, not an agent — it must not write read_at."
```

---

## Task 5: Deploy Step 1 — pre-flight preview + apply migration

**Files:** none (deploy operation).

Spec §"Deploy procedure" Step 1 + pre-flight preview.

- [ ] **Step 1: Run pre-flight preview against orchestrator Supabase**

From `/Users/sheikhmusa/wingmen/orchestrator`, use the same env the orchestrator uses. A one-liner Node script pattern works here since that dir has `@supabase/supabase-js` installed:

Run:
```bash
cd /Users/sheikhmusa/wingmen/orchestrator && node -e "
require('dotenv').config({path:'.env'});
const {createClient}=require('@supabase/supabase-js');
const c=createClient(process.env.ORCHESTRATOR_SUPABASE_URL,process.env.ORCHESTRATOR_SUPABASE_SERVICE_KEY);
(async()=>{
  const {data,error}=await c.from('strategic_decisions')
    .select('decision_ref,title,notified_at,bypass_review,created_at')
    .eq('source','claude_ai_session')
    .eq('challenge_status','challenge_window')
    .is('announced_by_msg_id',null)
    .is('notified_at',null)
    .order('created_at',{ascending:true});
  if(error){console.log('ERR',error.message);return;}
  console.log('ROWS:',data.length);
  for(const r of data){console.log(' ',r.decision_ref,'|',r.title);}
})();
"
```

Expected: `ROWS: 0` (CAI's manual backfill at 2026-04-18 11:33 UTC set `notified_at` on 21 rows).

If non-zero: inspect the list. Any row that shouldn't be announced (e.g., something meant to be bypass_review) must be corrected via:
```sql
UPDATE strategic_decisions SET bypass_review = true WHERE decision_ref = '<REF>';
```
Re-run the preview until the list is what you expect. Only then proceed.

Note: the preview runs the column filter on `announced_by_msg_id` — but that column does not exist yet. Since the query treats it as a column reference, you will get an error (`column does not exist`). **Adjust the preview: drop `announced_by_msg_id IS NULL` before migration, include it after.** The effective pre-migration query reduces to:

```javascript
// Pre-migration preview (announced_by_msg_id does not yet exist):
const {data,error}=await c.from('strategic_decisions')
  .select('decision_ref,title,notified_at,bypass_review,created_at')
  .eq('source','claude_ai_session')
  .eq('challenge_status','challenge_window')
  .is('notified_at',null)
  .order('created_at',{ascending:true});
```

This is equivalent: without the column, `announced_by_msg_id IS NULL` is universally true for every existing row, so dropping the predicate doesn't change the result set.

- [ ] **Step 2: Apply the migration**

Apply `supabase/migrations/20260419_bug020_bug021_governance_comms_hardening.sql` via the orchestrator's usual path. Check CLAUDE.md for the project convention — likely via the Supabase MCP or the dashboard SQL editor. If uncertain, paste the SQL into the Supabase SQL editor for the orchestrator project and run once.

- [ ] **Step 3: Smoke test — columns, triggers, backfill result**

Run (Node, from orchestrator):
```bash
cd /Users/sheikhmusa/wingmen/orchestrator && node -e "
require('dotenv').config({path:'.env'});
const {createClient}=require('@supabase/supabase-js');
const c=createClient(process.env.ORCHESTRATOR_SUPABASE_URL,process.env.ORCHESTRATOR_SUPABASE_SERVICE_KEY);
(async()=>{
  // Column 1
  const {data:c1}=await c.rpc('exec_sql',{sql:
    \"SELECT 1 FROM information_schema.columns WHERE table_name='agent_messages' AND column_name='forwarded_to_telegram_at'\"}).catch(()=>({data:null}));
  console.log('agent_messages.forwarded_to_telegram_at exists:', !!(c1 && c1.length));

  // Column 2 via select attempt
  const {error:e2}=await c.from('strategic_decisions').select('announced_by_msg_id').limit(1);
  console.log('strategic_decisions.announced_by_msg_id exists:', !e2);

  // Orphan check
  const {data:orphans}=await c.from('strategic_decisions')
    .select('decision_ref')
    .eq('source','claude_ai_session')
    .eq('challenge_status','challenge_window')
    .is('announced_by_msg_id',null);
  console.log('un-announced CAI challenge_window decisions:', orphans?.length ?? 'ERR');
})();
"
```

If your orchestrator doesn't expose an `exec_sql` RPC, use the simpler fallback:

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && node -e "
require('dotenv').config({path:'.env'});
const {createClient}=require('@supabase/supabase-js');
const c=createClient(process.env.ORCHESTRATOR_SUPABASE_URL,process.env.ORCHESTRATOR_SUPABASE_SERVICE_KEY);
(async()=>{
  const {error:e1}=await c.from('agent_messages').select('forwarded_to_telegram_at').limit(1);
  console.log('agent_messages.forwarded_to_telegram_at exists:', !e1, e1?.message ?? '');
  const {error:e2}=await c.from('strategic_decisions').select('announced_by_msg_id').limit(1);
  console.log('strategic_decisions.announced_by_msg_id exists:', !e2, e2?.message ?? '');
  const {data}=await c.from('strategic_decisions')
    .select('decision_ref')
    .eq('source','claude_ai_session')
    .eq('challenge_status','challenge_window')
    .is('announced_by_msg_id',null);
  console.log('un-announced CAI challenge_window decisions:', data?.length);
})();
"
```

Expected:
- Both columns exist: `true`
- un-announced count: `0`

If either column is missing: migration did not fully apply — investigate before Step 6.
If un-announced count > 0: backfill DO block did not run or did not complete. Inspect Supabase logs.

**Do not proceed to Task 6 until this step passes.**

---

## Task 6: Deploy Step 2 — Python deploy + live forward verify

- [ ] **Step 1: Push commits**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git log --oneline -5   # confirm Tasks 1–4 commits are present
git push origin main
```

- [ ] **Step 2: Restart orchestrator**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && ./scripts/restart_orch.sh
```

Watch the tail of the orchestrator logs for 30 seconds to confirm no startup crash. Expected: orchestrator comes up cleanly, APScheduler starts, agent_messages_poll runs on its normal cadence without errors.

- [ ] **Step 3: Post a throwaway test message**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && node -e "
require('dotenv').config({path:'.env'});
const {createClient}=require('@supabase/supabase-js');
const c=createClient(process.env.ORCHESTRATOR_SUPABASE_URL,process.env.ORCHESTRATOR_SUPABASE_SERVICE_KEY);
(async()=>{
  const {data,error}=await c.from('agent_messages').insert({
    thread_id:'00000000-0000-0000-0000-000000000bug',
    from_agent:'cai',
    to_agent:'musa',
    message_type:'update',
    subject:'BUG-021-VERIFY: forwarder column stamping',
    body:'Throwaway — delete after smoke test.',
    requires_response:false
  }).select('id').single();
  if(error){console.log('ERR',error.message);return;}
  console.log('inserted msg id:',data.id);
})();
"
```

Record the returned id as `$MSG_ID`.

- [ ] **Step 4: Wait for forwarder cycle (≤6 minutes), then verify column state**

Wait ~5-6 minutes for the 5-minute agent_messages_poll tick, or manually trigger the poll if the orchestrator exposes a way. Then:

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && node -e "
require('dotenv').config({path:'.env'});
const {createClient}=require('@supabase/supabase-js');
const c=createClient(process.env.ORCHESTRATOR_SUPABASE_URL,process.env.ORCHESTRATOR_SUPABASE_SERVICE_KEY);
(async()=>{
  const {data}=await c.from('agent_messages')
    .select('id,read_at,forwarded_to_telegram_at')
    .eq('subject','BUG-021-VERIFY: forwarder column stamping')
    .single();
  console.log(JSON.stringify(data,null,2));
})();
"
```

Expected:
```json
{
  "id": <N>,
  "read_at": null,
  "forwarded_to_telegram_at": "2026-04-19T..."
}
```

- `read_at IS NULL` — middleware did **not** clobber it (BUG-021 fix verified)
- `forwarded_to_telegram_at` populated — middleware correctly stamped the new column

If `read_at` is populated: **revert the Python deploy immediately**, investigate. Likely Task 3 missed a write path.

- [ ] **Step 5: Clean up the test message**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && node -e "
require('dotenv').config({path:'.env'});
const {createClient}=require('@supabase/supabase-js');
const c=createClient(process.env.ORCHESTRATOR_SUPABASE_URL,process.env.ORCHESTRATOR_SUPABASE_SERVICE_KEY);
(async()=>{
  await c.from('agent_messages').delete()
    .eq('subject','BUG-021-VERIFY: forwarder column stamping').throwOnError();
  console.log('deleted');
})();
"
```

---

## Task 7: Deploy Step 3 — trigger live test

Test the BUG-020 trigger end-to-end by inserting a throwaway strategic_decisions row that matches the trigger guard.

- [ ] **Step 1: Insert a test decision**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && node -e "
require('dotenv').config({path:'.env'});
const {createClient}=require('@supabase/supabase-js');
const c=createClient(process.env.ORCHESTRATOR_SUPABASE_URL,process.env.ORCHESTRATOR_SUPABASE_SERVICE_KEY);
(async()=>{
  const {data,error}=await c.from('strategic_decisions').insert({
    decision_ref:'BUG-020-VERIFY',
    title:'Trigger smoke test — delete after verify',
    source:'claude_ai_session',
    challenge_status:'challenge_window',
    bypass_review:false,
    body:'Throwaway decision to smoke-test the BUG-020 trigger. Delete me.'
  }).select('id,announced_by_msg_id,notified_at').single();
  if(error){console.log('ERR',error.message);return;}
  console.log(JSON.stringify(data,null,2));
})();
"
```

Expected in the returned row:
- `announced_by_msg_id` — NOT NULL (trigger populated it)
- `notified_at` — NOT NULL (trigger populated it)

If either is null: the trigger did not fire. Check `SELECT * FROM pg_trigger WHERE tgname LIKE 'cai_decision_announce%'` and inspect the function body. Do not proceed until resolved.

- [ ] **Step 2: Verify the announcement message exists**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && node -e "
require('dotenv').config({path:'.env'});
const {createClient}=require('@supabase/supabase-js');
const c=createClient(process.env.ORCHESTRATOR_SUPABASE_URL,process.env.ORCHESTRATOR_SUPABASE_SERVICE_KEY);
(async()=>{
  const {data}=await c.from('agent_messages')
    .select('id,from_agent,to_agent,message_type,subject,requires_response')
    .like('subject','BUG-020-VERIFY:%');
  console.log(JSON.stringify(data,null,2));
})();
"
```

Expected: 1 row with `from_agent='cai'`, `to_agent='cc-ihsanos'`, `message_type='review_request'`, `requires_response=true`, subject starting with `BUG-020-VERIFY:`.

- [ ] **Step 3: Clean up test rows (ordered, transactional)**

The spec requires agent_message first, then strategic_decision, both in one transaction. Using Node + service_role, simulate a transaction via sequential deletes (Supabase PostgREST does not expose explicit transactions, but sequential service_role deletes during a quiet moment are acceptable for this smoke test):

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && node -e "
require('dotenv').config({path:'.env'});
const {createClient}=require('@supabase/supabase-js');
const c=createClient(process.env.ORCHESTRATOR_SUPABASE_URL,process.env.ORCHESTRATOR_SUPABASE_SERVICE_KEY);
(async()=>{
  const r1=await c.from('agent_messages').delete().like('subject','BUG-020-VERIFY:%');
  console.log('agent_messages deleted:', r1.error?.message ?? 'ok');
  const r2=await c.from('strategic_decisions').delete().eq('decision_ref','BUG-020-VERIFY');
  console.log('strategic_decisions deleted:', r2.error?.message ?? 'ok');
})();
"
```

Expected: both `ok`. FK `ON DELETE SET NULL` clears the reference on the strategic_decisions row when the agent_message is deleted, so the second delete proceeds cleanly.

---

## Task 8: Deploy Step 4 — end-to-end live check

Confirm a real CAI decision flow now works without manual intervention. This task runs "by observation" — no new code or migration.

- [ ] **Step 1: Observe the next real CAI decision**

When CAI files the next `claude_ai_session` decision (during normal governance traffic), within the transaction:
- `strategic_decisions.announced_by_msg_id` populates
- `agent_messages` gets a new `review_request` row
- The orchestrator forwards it to Musa's Telegram on the next poll cycle
- `agent_messages.forwarded_to_telegram_at` stamps; `read_at` stays NULL
- CC's next inbox poll on `read_at IS NULL AND to_agent='cc-ihsanos'` surfaces the message

- [ ] **Step 2: Confirm inbox visibility via the ihsanos CC poll path**

From ihsanos:
```bash
cd /Users/sheikhmusa/wingmen/projects/ihsanos && node -e "
require('dotenv').config({path:'.env.local'});
const {createClient}=require('@supabase/supabase-js');
const c=createClient(process.env.ORCHESTRATOR_SUPABASE_URL,process.env.ORCHESTRATOR_SUPABASE_SERVICE_KEY);
(async()=>{
  const {data}=await c.from('agent_messages')
    .select('id,subject,requires_response,responded_at,read_at,forwarded_to_telegram_at')
    .eq('to_agent','cc-ihsanos')
    .is('read_at',null)
    .order('created_at',{ascending:false})
    .limit(10);
  console.log(JSON.stringify(data,null,2));
})();
"
```

Expected: the new review_request is present with `read_at: null` and `forwarded_to_telegram_at: <timestamp>`.

If the message is missing but the `forwarded_to_telegram_at IS NOT NULL / read_at IS NULL` row exists: the pipeline is working; CC's inbox query just needs to be re-run (cache).

---

## Task 9: Revert ihsanos CLAUDE.md two-query workaround

**Files:**
- Modify: `/Users/sheikhmusa/wingmen/projects/ihsanos/CLAUDE.md`

Once Tasks 5–8 pass, remove the temporary two-query pattern from ihsanos and return to a single clean inbox check.

- [ ] **Step 1: Edit CLAUDE.md**

Open `/Users/sheikhmusa/wingmen/projects/ihsanos/CLAUDE.md`. Find the "Read Order" item 5:

```
5. **Check `agent_messages` at session start AND at every turn** — run TWO queries against Supabase `agent_messages`:
   - **General inbox:** `to_agent='cc-ihsanos' AND read_at IS NULL` — catches new messages not yet forwarded to Musa
   - **Pending responses:** `to_agent='cc-ihsanos' AND requires_response=true AND responded_at IS NULL` — catches review_requests whose `read_at` was clobbered by the Telegram notifier (BUG-021) before cc-ihsanos could process them
   Both queries must run. Act on results from either before proceeding. `read_at` alone is unreliable — the orchestrator Telegram notifier stamps it when forwarding to Musa, not when cc-ihsanos processes the message (BUG-021, 2026-04-18). (PIPELINE_CONSTRAINTS §8 — Communication Latency)
```

Replace with:

```
5. **Check `agent_messages` at session start AND at every turn** — query Supabase `agent_messages` for `to_agent='cc-ihsanos' AND read_at IS NULL`. Act on results before proceeding. (PIPELINE_CONSTRAINTS §8 — Communication Latency)
```

(BUG-021 is fixed as of 2026-04-19 — the Telegram notifier now stamps `forwarded_to_telegram_at`, not `read_at`. The two-query workaround is no longer needed.)

- [ ] **Step 2: Commit in ihsanos repo**

```bash
cd /Users/sheikhmusa/wingmen/projects/ihsanos
git add CLAUDE.md
git commit -m "chore(claude): revert BUG-021 two-query workaround

Orchestrator notifier now stamps forwarded_to_telegram_at (BUG-021 fix
shipped 2026-04-19). read_at is clean again — single-query inbox check
is enough."
```

- [ ] **Step 3: Verify ihsanos CLAUDE.md renders cleanly**

Run: `grep -A2 'Check \`agent_messages\`' /Users/sheikhmusa/wingmen/projects/ihsanos/CLAUDE.md | head -5`

Expected: single-query instruction only, no mention of the two-query workaround.

---

## Task 10: Post completion notice + session digest

Per CAI msg 239 ("Post a completion agent_message when all 4 deploy steps pass their verify beats") and per user memory `feedback_session_digest.md` (every shipping session files a digest to CAI).

- [ ] **Step 1: Post completion agent_message to CAI**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && node -e "
require('dotenv').config({path:'.env'});
const {createClient}=require('@supabase/supabase-js');
const c=createClient(process.env.ORCHESTRATOR_SUPABASE_URL,process.env.ORCHESTRATOR_SUPABASE_SERVICE_KEY);
(async()=>{
  const body=[
    'BUG-020 + BUG-021 shipped. All 4 deploy beats passed:',
    '  1. Migration applied, columns + triggers present, backfill count = 0',
    '  2. Python deploy — live forward stamps forwarded_to_telegram_at, read_at untouched',
    '  3. Trigger live test — BUG-020-VERIFY inserted → announced_by_msg_id + notified_at populated in-txn',
    '  4. ihsanos CLAUDE.md reverted to single-query inbox check',
    '',
    'Governance comms pipeline v1 hardening complete. Ready for next P0 work — LEDGER Option B (Qurban unblock) paused 2026-04-18, now clear to resume within the 3-day CAI-RESP-033 timebox.'
  ].join('\\n');
  const {data,error}=await c.from('agent_messages').insert({
    thread_id:'00000000-0000-0000-0000-000000000021',
    from_agent:'cc-ihsanos',
    to_agent:'cai',
    message_type:'update',
    subject:'BUG-020 + BUG-021 shipped — governance comms pipeline v1 complete',
    body,
    requires_response:false
  }).select('id').single();
  if(error){console.log('ERR',error.message);return;}
  console.log('posted msg id:', data.id);
})();
"
```

- [ ] **Step 2: File session digest (per feedback_session_digest.md)**

After the final commit is pushed in both repos, post a digest JSON to `agent_messages` with this exact shape:

```bash
# Gather inputs
cd /Users/sheikhmusa/wingmen/orchestrator
ORCH_SHA=$(git rev-parse --short HEAD)
cd /Users/sheikhmusa/wingmen/projects/ihsanos
IHSAN_SHA=$(git rev-parse --short HEAD)
SESSION_ID=$(date +%Y%m%d-%H%M%S-bug020-021)

cd /Users/sheikhmusa/wingmen/orchestrator && node -e "
require('dotenv').config({path:'.env'});
const {createClient}=require('@supabase/supabase-js');
const c=createClient(process.env.ORCHESTRATOR_SUPABASE_URL,process.env.ORCHESTRATOR_SUPABASE_SERVICE_KEY);
(async()=>{
  const digest={
    session_id:'$SESSION_ID',
    commit_sha:{orchestrator:'$ORCH_SHA',ihsanos:'$IHSAN_SHA'},
    deploy_url:'https://ihsanos.com',
    summary:'BUG-020 + BUG-021 shipped — governance comms pipeline v1 hardening',
    files_changed:[
      'orchestrator: supabase/migrations/20260419_bug020_bug021_governance_comms_hardening.sql (new)',
      'orchestrator: nervous_system/agent_messages_poll.py',
      'orchestrator: scripts/build_launch_context.py',
      'orchestrator: tests/test_agent_messages_poll.py',
      'ihsanos: CLAUDE.md'
    ],
    deleted_files:[],
    new_exports:[],
    schema_changes:[
      'agent_messages.forwarded_to_telegram_at TIMESTAMPTZ (new)',
      'strategic_decisions.announced_by_msg_id BIGINT FK→agent_messages(id) (new)',
      'trigger_cai_decision_announce() function + cai_decision_announce_insert/update triggers (new)',
      'partial indexes on both NULL subsets (new)'
    ],
    tests_added:2,
    tests_passing:null,
    follow_ups:[
      'LEDGER Option B (Qurban unblock) — resume within CAI-RESP-033 3-day timebox',
      'RLS on read_at + forwarded_to_telegram_at — revisit when per-agent JWT auth lands'
    ]
  };
  const body=JSON.stringify(digest,null,2);
  const {data,error}=await c.from('agent_messages').insert({
    thread_id:'00000000-0000-0000-0000-000000000021',
    from_agent:'cc-ihsanos',
    to_agent:'cai',
    message_type:'update',
    subject:'Session digest: '+('$ORCH_SHA')+' — BUG-020/021 governance comms hardening',
    body,
    requires_response:false
  }).select('id').single();
  if(error){console.log('ERR',error.message);return;}
  console.log('digest posted msg id:', data.id);
})();
"
```

Before running, set `tests_passing` to the real count from `pytest --tb=no -q 2>&1 | tail -3` — paste the total passed count into the JSON.

- [ ] **Step 3: Update STATUS.md in both repos**

Per CLAUDE.md contracts in each repo:

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
# Append a section to STATUS.md marking BUG-020/021 complete
# Then:
git add STATUS.md && git commit -m "chore: STATUS.md — BUG-020/021 shipped"

cd /Users/sheikhmusa/wingmen/projects/ihsanos
# Same for ihsanos
git add STATUS.md && git commit -m "chore: STATUS.md — BUG-021 inbox workaround reverted"
```

---

## Self-review checklist (author ran before handoff)

**Spec coverage:**
- ✅ `forwarded_to_telegram_at` column — Task 1 Step 1
- ✅ `announced_by_msg_id` column — Task 1 Step 1
- ✅ Partial indexes — Task 1 Step 1
- ✅ Trigger function (BUG-020) — Task 1 Step 1
- ✅ INSERT + UPDATE triggers — Task 1 Step 1
- ✅ `BEFORE UPDATE OF` syntax — Task 1 Step 1 (fallback is documented in the spec; default form is used)
- ✅ Per-orphan atomic backfill — Task 1 Step 1
- ✅ RLS deferred — explicit in spec, not in plan
- ✅ Python: `_mark_read` → `_mark_forwarded` — Task 3 Step 1
- ✅ Python: polling SELECT filter — Task 3 Step 2
- ✅ Python: drop cc-* guard — Task 3 Step 3
- ✅ Python: `_already_notified` dedup path — Task 3 Step 4
- ✅ `build_launch_context.py` bulk update — Task 4 Step 1
- ✅ Tests: rename + 2 new — Task 2
- ✅ Pre-flight preview — Task 5 Step 1
- ✅ Migration apply + smoke test — Task 5 Steps 2–3
- ✅ Python deploy + live forward verify — Task 6
- ✅ Trigger live test — Task 7
- ✅ Ihsanos CLAUDE.md revert — Task 9
- ✅ Completion agent_message + session digest — Task 10
- ✅ Rollback procedure — referenced in spec; not re-copied into plan

**Placeholder scan:** No TBDs, no "similar to task N", every step has either code or an exact command. ✓

**Type/name consistency:**
- `_mark_forwarded` (Python function) — consistent across Tasks 2, 3
- `forwarded_to_telegram_at` (column) — consistent across Tasks 1, 2, 3, 4, 5, 6
- `announced_by_msg_id` (column) — consistent across Tasks 1, 5, 7
- `trigger_cai_decision_announce` (PG function) — consistent across Task 1, Task 7
- `cai_decision_announce_insert` / `cai_decision_announce_update` (trigger names) — consistent ✓

Plan ready for execution.
