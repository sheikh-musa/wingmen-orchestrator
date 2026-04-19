# ARCH-035 Three-Channel Governance Taxonomy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Structurally split governance traffic into three channels — `strategic_decisions` (constitution) / `agent_status` (current state) / narrowed `agent_messages` (Q&A only) — and end the discipline-reliant CLAIM/STATUS/HEARTBEAT prefix conventions on `agent_messages`.

**Architecture:** One migration adds `agent_status` + `agent_status_history` + forensic violations log with a `SECURITY DEFINER` BEFORE trigger that enforces identity via a Postgres GUC (`app.current_agent_id`) set by the launch script. Violations are durably logged through a `dblink` autonomous transaction that survives the caller's rolled-back tx. `agent_messages` gets a CHECK constraint narrowing `message_type` to 7 values. Launch script owns GUC directly (no RPC wrapper — that structurally defeats the tripwire per CAI-RESP-043 B1). Notifier rejects banned subject prefixes.

**Tech Stack:** Postgres (`dblink`, `pg_cron`, CHECK constraints, SECURITY DEFINER triggers), Python (`psycopg` direct connection for GUC semantics, `supabase-py` for everything else), Bash (launch script), pytest.

**Spec:** `docs/superpowers/specs/2026-04-19-arch-035-three-channel-governance-taxonomy-design.md` (commit `f6b5483`, CAI-approved via CAI-RESP-036 + 042 + 043 + 044).

---

## File Structure

### Commit 1 — docs-first (single-file)

- Modify: `WINGMEN_CONSTRAINTS.md` — new section "Three-channel governance taxonomy + launch protocol"

### Commit 2 — atomic code commit (5 files)

- Create: `supabase/migrations/20260419_arch035_three_channel_taxonomy.sql` — full migration
- Modify: `scripts/build_launch_context.py` — insert World State section (~30 lines) between agent-context and unread-inbox
- Modify: `scripts/launch_dangerous_cc.sh` — (a) GUC + UPSERT block after AGENT_ID resolution (~30 lines), (b) offline UPSERT inside `_handle_exit` trap (~15 lines)
- Modify: `nervous_system/agent_messages_poll.py` — banned-prefix rejection in `_is_routable` (~10 lines)
- Modify: `tests/test_agent_messages_poll.py` — cases for banned-prefix rejection

### Migration apply — out-of-band

Per STATUS.md BUG-020/BUG-025 pattern, apply the `.sql` via Supabase dashboard SQL editor **after** writing the file but **before** Commit 2. Live smoke test runs against the applied migration.

### Commit boundaries

- Commit 1 ships first. It is docs-only, safe to land independently (CAI-RESP-042 Q3 — docs first).
- Commit 2 ships atomically once migration is applied + smoke test passes + subagent psycopg test passes.

---

## Task 0: Subagent — psycopg GUC propagation test (gate for Task 6)

**Why first:** If direct psycopg `SET LOCAL` + INSERT in the same tx can't make the trigger see the GUC, Task 6 degrades to fail-closed (post `blocker` message on boot, agent_status stays empty). Must know before writing Task 6 code. No file changes yet — the migration isn't applied, so this test uses a standalone throwaway table.

**Files:**
- Test: inline Python script dispatched to subagent (no file committed)

- [ ] **Step 1: Dispatch subagent**

Dispatch a fresh subagent with this prompt:

> **Task: psycopg GUC propagation test for ARCH-035.**
>
> Verify that a direct psycopg2 or psycopg3 connection (NOT supabase-py) can `SET LOCAL app.current_agent_id = 'X'` and then have a BEFORE trigger `RAISE EXCEPTION` based on `current_setting('app.current_agent_id', true)` comparing against an INSERT row value — all in the same transaction.
>
> Setup (use the orchestrator's Supabase connection; `DATABASE_URL` in `.env`):
> ```sql
> CREATE TABLE _arch035_guctest (agent_id TEXT, note TEXT);
> CREATE OR REPLACE FUNCTION _arch035_guccheck() RETURNS TRIGGER AS $$
> DECLARE v TEXT;
> BEGIN
>   v := current_setting('app.current_agent_id', true);
>   IF v IS NULL OR v = '' THEN RAISE EXCEPTION 'guc_not_set'; END IF;
>   IF NEW.agent_id IS DISTINCT FROM v THEN RAISE EXCEPTION 'mismatch: NEW=% GUC=%', NEW.agent_id, v; END IF;
>   RETURN NEW;
> END; $$ LANGUAGE plpgsql;
> CREATE TRIGGER _arch035_guctest_trg BEFORE INSERT ON _arch035_guctest
>   FOR EACH ROW EXECUTE FUNCTION _arch035_guccheck();
> ```
>
> Then via psycopg (install with `pip install psycopg[binary]` if needed), run THREE separate cases on separate connections:
> 1. INSERT without SET LOCAL → expect `guc_not_set` error
> 2. `SET LOCAL app.current_agent_id = 'A'` then INSERT `('B', 'x')` → expect `mismatch` error
> 3. `SET LOCAL app.current_agent_id = 'A'` then INSERT `('A', 'x')` → expect success, row visible after COMMIT
>
> Wrap each case in explicit `BEGIN...COMMIT` or `BEGIN...ROLLBACK`. Use `conn.autocommit = False`.
>
> Clean up after: `DROP TABLE _arch035_guctest; DROP FUNCTION _arch035_guccheck();`
>
> **Report back:**
> - Result of each of the 3 cases (PASS/FAIL and actual error message)
> - Exact psycopg library version used
> - Any deviation from the expected outcomes
>
> If all 3 PASS, the launch script can use the same pattern. If any fail unexpectedly, flag which one and what happened. Under 300 words.

- [ ] **Step 2: Record outcome**

Document subagent report inline in this plan under "Task 0 outcome" below.

- **Task 0 outcome (2026-04-19):** GREEN — all 3 cases pass. **Key finding:** Supabase session pooler (port 5432, `aws-1-ap-southeast-2.pooler.supabase.com`) REJECTS parameterized `SET LOCAL app.current_agent_id = %s` with `syntax error at or near "$1"`. Working pattern is `SELECT set_config('app.current_agent_id', %s, true)` — `set_config(name, value, is_local)` is a regular Postgres function that accepts bind parameters through the pooler, and `is_local=true` gives the same transaction-scoped semantics as SET LOCAL. Direct DSN (`db.<ref>.supabase.co:5432`) is IPv6-only and unreachable from IPv4 networks — session pooler is the only viable path. Tasks 5 and 6 updated to use `set_config()`.
  - Case 1 (no GUC): PASS — `SQLSTATE 42501 guc_not_set`
  - Case 2 (mismatch): PASS — `SQLSTATE 42501 mismatch: guc=A new=B`
  - Case 3 (match): PASS — insert succeeded, row verified
  - psycopg version: 3.2.13
  - DSN used: session pooler (port 5432) per `.env`

- [ ] **Step 3: Branch decision**

- If all 3 PASS → proceed to Task 1 with Task 6 as drafted (psycopg direct path).
- If Case 3 fails (GUC not visible to trigger) → proceed to Task 1 but edit Task 6 to the fail-closed variant: launch script posts `blocker` agent_message instead of UPSERT, agent_status sits empty until BUG-024. Document in STATUS.md.
- If Cases 1 or 2 fail (errors don't fire) → stop. This would mean trigger logic is flawed — re-read spec before proceeding.

---

## Task 1: WINGMEN_CONSTRAINTS.md amendment

**Files:**
- Modify: `WINGMEN_CONSTRAINTS.md` (add new section, location: after existing governance sections)

- [ ] **Step 1: Locate insertion point**

Run:
```bash
grep -n "^## " /Users/sheikhmusa/wingmen/projects/ihsanos/WINGMEN_CONSTRAINTS.md | head -20
```

Find the last `##` governance-related section. Insert the new section immediately after it, before any Foundations/appendix sections.

- [ ] **Step 2: Add section**

Insert this block (use the Edit tool with the chosen anchor):

```markdown
## Three-channel governance taxonomy + launch protocol

Per ARCH-035 (2026-04-19). Three logical channels, each backed by a distinct shape. Putting traffic in the wrong channel is a governance bug.

### The three channels

1. **`strategic_decisions`** — the constitution. Permanent rulings: ARCH-/BUG-/CAI-RESP-/CAI-VOCAB-/etc. Has challenge-window lifecycle and parent-ref graph. Unchanged by ARCH-035.
2. **`agent_status`** — current work state. ONE ROW PER AGENT. Stamped at session-launch + session-end by the launch script. Replaces the CLAIM/STATUS/HEARTBEAT prefix conventions. Writes guarded by a SECURITY DEFINER trigger that compares `NEW.agent_id` to `current_setting('app.current_agent_id', true)`. Mismatch raises `42501` and is forensically logged to `agent_status_identity_violations` via `dblink`.
3. **`agent_messages`** — Q&A only. Narrowed by CHECK constraint to: `review_request`, `question`, `decision`, `agreed`, `challenge`, `update`, `blocker`. Status updates, claims, and heartbeats no longer belong here.

### Banned subject prefixes on `agent_messages`

The notifier (`nervous_system/agent_messages_poll.py`) drops any message whose subject matches `^(CLAIM|STATUS|HEARTBEAT|DIGEST|COMPLETE):` — no Telegram, no `forwarded_to_telegram_at` stamp. The row stays UNREAD as a tripwire so the offending agent can be coached. A nightly pg_cron job purges stale banned-prefix rows (filed as follow-up TASK).

If you find yourself wanting to post `CLAIM:` or `STATUS:` or `HEARTBEAT:` — that's `agent_status`, not `agent_messages`.

### Launch protocol (GUC identity)

The launch script (`scripts/launch_dangerous_cc.sh`) owns identity. Before any `agent_status` write, the script:

1. Resolves `AGENT_ID` from `CC_AGENT_ID` env var (default `cc-ihsanos`)
2. Opens a dedicated psycopg connection (NOT supabase-py — PostgREST pooling breaks GUC semantics). Uses Supabase session pooler DSN (port **5432**), not transaction pooler (6543, which discards GUCs between statements)
3. `BEGIN; SELECT set_config('app.current_agent_id', $AGENT_ID, true); INSERT/UPSERT agent_status ...; COMMIT;` — the pooler rejects parameterized `SET LOCAL ... = %s`, so `set_config(name, value, is_local=true)` is the only form that round-trips cleanly
4. Closes the connection — GUC evaporates. Caller cannot reuse the `set_config` later.

**No RPC wrapper that both `set_config`s and UPSERTs from a caller-controlled parameter.** That pattern is a tautology — the trigger would verify the caller set the parameter it itself provided. Defeated by construction. Per CAI-RESP-043 B1 (SHIP BLOCKER, applied).

Spoofing the launch script requires editing the script (auditable in git). Proper per-agent JWT identity lands with BUG-024 Phase 1; the GUC tripwire is documented as known-degraded interim.
```

- [ ] **Step 3: Verify grep**

Run:
```bash
grep -n "Three-channel governance taxonomy" /Users/sheikhmusa/wingmen/projects/ihsanos/WINGMEN_CONSTRAINTS.md
```
Expected: one match, on the line you just inserted.

- [ ] **Step 4: Commit**

```bash
cd /Users/sheikhmusa/wingmen/projects/ihsanos && git add WINGMEN_CONSTRAINTS.md && git commit -m "$(cat <<'EOF'
docs(constraints): three-channel governance taxonomy + launch protocol

Documents ARCH-035: strategic_decisions / agent_status / narrowed
agent_messages. Launch protocol: psycopg set_config() owned by launch
script (session pooler requires set_config, not SET LOCAL). No RPC
wrapper (CAI-RESP-043 B1). Banned subject prefixes on agent_messages.

Ref: docs/superpowers/specs/2026-04-19-arch-035-three-channel-governance-taxonomy-design.md (orchestrator repo)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

**Note:** WINGMEN_CONSTRAINTS.md lives inside the **ihsanos** repo at `/Users/sheikhmusa/wingmen/projects/ihsanos/WINGMEN_CONSTRAINTS.md`. That's the canonical copy cc-ihsanos reads per its CLAUDE.md Read Order §2. The other two worktrees (`ihsanos-layer1`, `/tmp/wingmen-wt-104`) are sister branches of the same repo and will pick up the change on their next merge from main. The orchestrator repo does NOT carry WINGMEN_CONSTRAINTS.md — if orchestrator-side CAI needs to reference the rule, that's a separate orchestrator/CLAUDE.md update and is out of scope for Commit 1. Commit 1 is ihsanos-repo-only.

---

## Task 2: Migration SQL file

**Files:**
- Create: `supabase/migrations/20260419_arch035_three_channel_taxonomy.sql`

- [ ] **Step 1: Write the migration file**

Use the Write tool. Full content:

```sql
-- ARCH-035: Three-channel governance taxonomy.
--
-- Parent: ARCH-035 (decision_ref). References:
--   docs/superpowers/specs/2026-04-19-arch-035-three-channel-governance-taxonomy-design.md
--   CAI-RESP-036 (8-item amendment)
--   CAI-RESP-042 (3-item pre-write clarification)
--   CAI-RESP-043 (B1 ship blocker + B2 dblink violations log + C2/C3/C4 polish)
--   CAI-RESP-044 (dblink + pg_cron confirmed enabled on this project)
--
-- Shape:
--   1. agent_status        — current work state, one row per agent
--   2. agent_status_history — append-only forensic log, AFTER trigger snapshot
--   3. agent_status_identity_violations — forensic log of trigger rejections
--   4. enforce_agent_status_identity() — SECURITY DEFINER BEFORE trigger comparing
--      NEW.agent_id to app.current_agent_id GUC; rejections logged via dblink
--      autonomous transaction so log survives caller's rolled-back tx
--   5. stale_agents view  — drift detection (15-min heartbeat threshold)
--   6. agent_messages CHECK constraint — narrows message_type to 7 values

-- 1. agent_status — one row per agent, current work state.
CREATE TABLE agent_status (
  agent_id                TEXT PRIMARY KEY,
  status                  TEXT NOT NULL CHECK (status IN ('idle','working','blocked','offline')),
  current_task            TEXT,
  scope_repos             TEXT[],
  scope_paths             TEXT[],
  blocked_on_msg_id       BIGINT REFERENCES agent_messages(id) ON DELETE SET NULL,
  blocked_on_decision_ref TEXT REFERENCES strategic_decisions(decision_ref) ON DELETE SET NULL,
  blocked_on_description  TEXT,
  last_commit_sha         TEXT,
  last_commit_repo        TEXT,
  last_heartbeat          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_agent_status_active ON agent_status (last_heartbeat DESC) WHERE status != 'offline';

-- 2. agent_status_history — append-only forensic snapshot (no FK to agent_status; deletes do not cascade).
CREATE TABLE agent_status_history (
  id                      BIGSERIAL PRIMARY KEY,
  agent_id                TEXT NOT NULL,
  status                  TEXT NOT NULL,
  current_task            TEXT,
  scope_repos             TEXT[],
  scope_paths             TEXT[],
  blocked_on_msg_id       BIGINT,
  blocked_on_decision_ref TEXT,
  last_commit_sha         TEXT,
  recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_agent_status_history_agent_time ON agent_status_history (agent_id, recorded_at DESC);

-- 3. AFTER trigger — snapshot every change.
CREATE OR REPLACE FUNCTION snapshot_agent_status_to_history()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO agent_status_history (
    agent_id, status, current_task, scope_repos, scope_paths,
    blocked_on_msg_id, blocked_on_decision_ref, last_commit_sha
  ) VALUES (
    NEW.agent_id, NEW.status, NEW.current_task, NEW.scope_repos, NEW.scope_paths,
    NEW.blocked_on_msg_id, NEW.blocked_on_decision_ref, NEW.last_commit_sha
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_agent_status_snapshot
  AFTER INSERT OR UPDATE ON agent_status
  FOR EACH ROW EXECUTE FUNCTION snapshot_agent_status_to_history();

-- 4a. Identity violations forensic log.
CREATE TABLE agent_status_identity_violations (
  id              BIGSERIAL PRIMARY KEY,
  claimed_agent   TEXT,
  guc_value       TEXT,
  violation_type  TEXT NOT NULL CHECK (violation_type IN ('guc_not_set','identity_mismatch')),
  attempted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  session_user    TEXT NOT NULL DEFAULT session_user,
  operation       TEXT NOT NULL
);
CREATE INDEX idx_violations_recent ON agent_status_identity_violations (attempted_at DESC);

-- 4b. Autonomous-tx helper (dblink) — log survives caller's rolled-back transaction.
--     CAI-RESP-044 Q1 confirmed dblink 1.2 + pg_cron 1.6.4 enabled on this project.
CREATE EXTENSION IF NOT EXISTS dblink;
CREATE EXTENSION IF NOT EXISTS pg_cron;

CREATE OR REPLACE FUNCTION log_agent_status_identity_violation(
  p_claimed TEXT, p_guc TEXT, p_type TEXT, p_op TEXT
) RETURNS VOID AS $$
BEGIN
  PERFORM dblink_exec(
    'dbname=' || current_database(),
    format(
      $sql$INSERT INTO agent_status_identity_violations
           (claimed_agent, guc_value, violation_type, operation)
           VALUES (%L, %L, %L, %L)$sql$,
      p_claimed, p_guc, p_type, p_op
    )
  );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 4c. Identity tripwire — SECURITY DEFINER BEFORE trigger.
CREATE OR REPLACE FUNCTION enforce_agent_status_identity()
RETURNS TRIGGER AS $$
DECLARE
  v_guc_agent TEXT;
BEGIN
  v_guc_agent := current_setting('app.current_agent_id', true);
  IF v_guc_agent IS NULL OR v_guc_agent = '' THEN
    PERFORM log_agent_status_identity_violation(NEW.agent_id, v_guc_agent, 'guc_not_set', TG_OP);
    RAISE EXCEPTION 'agent_status write rejected: app.current_agent_id GUC not set (use SET LOCAL at session-start)'
      USING ERRCODE = '42501';
  END IF;
  IF NEW.agent_id IS DISTINCT FROM v_guc_agent THEN
    PERFORM log_agent_status_identity_violation(NEW.agent_id, v_guc_agent, 'identity_mismatch', TG_OP);
    RAISE EXCEPTION 'agent_status write rejected: NEW.agent_id=% but GUC app.current_agent_id=% (identity mismatch)', NEW.agent_id, v_guc_agent
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER trg_agent_status_identity
  BEFORE INSERT OR UPDATE ON agent_status
  FOR EACH ROW EXECUTE FUNCTION enforce_agent_status_identity();

-- 5. stale_agents view — drift detection, not prevention.
CREATE OR REPLACE VIEW stale_agents AS
SELECT agent_id, status, current_task,
       last_heartbeat, (now() - last_heartbeat) AS heartbeat_age
FROM agent_status
WHERE status != 'offline' AND last_heartbeat < now() - interval '15 minutes';

-- 6. updated_at auto-bump.
CREATE OR REPLACE FUNCTION bump_agent_status_updated_at()
RETURNS TRIGGER AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_agent_status_updated_at BEFORE UPDATE ON agent_status
  FOR EACH ROW EXECUTE FUNCTION bump_agent_status_updated_at();

-- 7. agent_messages CHECK constraint (A2 + CAI-RESP-042 Q1 — 7 values including 'blocker').
ALTER TABLE agent_messages
  ADD CONSTRAINT agent_messages_message_type_check
  CHECK (message_type IN ('review_request','question','decision','agreed','challenge','update','blocker'));
```

- [ ] **Step 2: Verify file**

Run:
```bash
wc -l /Users/sheikhmusa/wingmen/orchestrator/supabase/migrations/20260419_arch035_three_channel_taxonomy.sql
```
Expected: ~150 lines.

Do NOT git-add yet — Commit 2 stages all 5 files atomically at the end.

---

## Task 3: Apply migration + run smoke test

**Files:** none modified; verifies Task 2 file behaves correctly.

- [ ] **Step 1: Apply via Supabase dashboard**

Open Supabase dashboard SQL editor for project `tscuymavysscrvoberrr`. Paste the full contents of `supabase/migrations/20260419_arch035_three_channel_taxonomy.sql`. Run.

Expected: `CREATE TABLE`, `CREATE INDEX`, `CREATE FUNCTION`, `CREATE TRIGGER`, `CREATE VIEW`, `CREATE EXTENSION`, `ALTER TABLE` — all success. If `agent_messages` has any existing row with `message_type` not in the 7-value set, the CHECK will fail. Per CAI-RESP-042 Q1 confirmation, no backfill should be needed — but if this does fail, STOP and file a separate cleanup task. Do NOT widen the CHECK.

- [ ] **Step 2: Smoke test — all 8 cases**

Paste each block into the SQL editor in sequence. Confirm each matches the expected outcome before moving on.

**Case 1 — tripwire fails without GUC + dblink logs:**
```sql
INSERT INTO agent_status (agent_id, status) VALUES ('cc-smoketest','working');
-- expect: ERROR 42501 "GUC not set"
SELECT violation_type, claimed_agent FROM agent_status_identity_violations
 WHERE claimed_agent='cc-smoketest' ORDER BY id DESC LIMIT 1;
-- expect: ('guc_not_set', 'cc-smoketest')
```

**Case 2 — tripwire fails on mismatch + dblink logs:**
```sql
BEGIN;
SET LOCAL app.current_agent_id = 'cc-smoketest';
INSERT INTO agent_status (agent_id, status) VALUES ('cc-evil','working');
-- expect: ERROR 42501 "identity mismatch"
ROLLBACK;
SELECT violation_type, claimed_agent, guc_value FROM agent_status_identity_violations
 WHERE claimed_agent='cc-evil' ORDER BY id DESC LIMIT 1;
-- expect: ('identity_mismatch', 'cc-evil', 'cc-smoketest')
```

**Case 3 — tripwire passes with matching GUC:**
```sql
BEGIN;
SET LOCAL app.current_agent_id = 'cc-smoketest';
INSERT INTO agent_status (agent_id, status, current_task)
VALUES ('cc-smoketest','working','smoke');
COMMIT;
SELECT count(*) FROM agent_status WHERE agent_id='cc-smoketest';
-- expect: 1
SELECT count(*) FROM agent_status_history WHERE agent_id='cc-smoketest';
-- expect: 1 (AFTER trigger snapshot)
```

**Case 4 — UPDATE appends history:**
```sql
BEGIN;
SET LOCAL app.current_agent_id = 'cc-smoketest';
UPDATE agent_status SET status='blocked', current_task='waiting on cai'
WHERE agent_id='cc-smoketest';
COMMIT;
SELECT count(*) FROM agent_status_history WHERE agent_id='cc-smoketest';
-- expect: 2
```

**Case 5 — stale_agents excludes fresh entry:**
```sql
SELECT * FROM stale_agents WHERE agent_id='cc-smoketest';
-- expect: 0 rows
```

**Case 6 — CHECK accepts 'blocker':**
```sql
INSERT INTO agent_messages (thread_id, from_agent, to_agent, message_type, subject, body, requires_response)
VALUES (gen_random_uuid(),'cc-smoketest','cai','blocker','smoketest','b',true);
-- expect: row inserted
```

**Case 7 — CHECK rejects unknown type:**
```sql
INSERT INTO agent_messages (thread_id, from_agent, to_agent, message_type, subject, body, requires_response)
VALUES (gen_random_uuid(),'cc-smoketest','cai','nonsense','smoketest','b',true);
-- expect: ERROR 23514 check constraint violation
```

**Case 7b — status CHECK rejects 'reviewing' (CAI-RESP-043 C3 — dropped from enum):**
```sql
BEGIN;
SET LOCAL app.current_agent_id = 'cc-smoketest';
UPDATE agent_status SET status='reviewing' WHERE agent_id='cc-smoketest';
-- expect: ERROR 23514 check constraint violation on agent_status_status_check
ROLLBACK;
```

**Case 8 — cleanup (REQUIRED before leaving):**
```sql
BEGIN;
SET LOCAL app.current_agent_id = 'cc-smoketest';
DELETE FROM agent_status WHERE agent_id='cc-smoketest';
COMMIT;
DELETE FROM agent_status_history WHERE agent_id='cc-smoketest';
DELETE FROM agent_messages WHERE from_agent='cc-smoketest';
DELETE FROM agent_status_identity_violations
 WHERE claimed_agent IN ('cc-smoketest','cc-evil');
SELECT count(*) FROM agent_status;
-- expect: 0 (or only pre-existing rows from other agents — verify empty of smoketest rows)
```

- [ ] **Step 3: Record outcome**

If all 8 cases match expectations → proceed. If any fail, STOP and diagnose before writing more code. Do not proceed on "close enough."

---

## Task 4: build_launch_context.py — World State section

**Files:**
- Modify: `scripts/build_launch_context.py`
- (No test — this is integration code that calls Supabase; manual verification via dry-run in Step 3)

- [ ] **Step 1: Locate insertion point**

The new section goes between the existing "Agent context" section (ends around line 76) and "Unread inbox" section (starts at line 78). Read the file and confirm the boundary:

```bash
sed -n '74,82p' /Users/sheikhmusa/wingmen/orchestrator/scripts/build_launch_context.py
```

- [ ] **Step 2: Insert World State section**

Use Edit with `old_string` matching the exact transition between sections. Target the boundary:

```python
    else:
        parts.append("(no context row — first boot)")

    # ── 2. Unread inbox (requires_response first) ─────────────────────────────
```

Replace with:

```python
    else:
        parts.append("(no context row — first boot)")

    # ── 2. World state (agent_status snapshot) ────────────────────────────────
    # ARCH-035: every CC's boot briefing shows what every other agent is
    # doing. Replaces the discipline-based "scroll through agent_messages
    # CLAIMs" pattern.
    status_rows = (
        client.table("agent_status")
        .select(
            "agent_id,status,current_task,scope_repos,blocked_on_msg_id,"
            "blocked_on_decision_ref,last_heartbeat"
        )
        .order("updated_at", desc=True)
        .execute()
        .data
    )
    parts.append(f"\n## World State ({len(status_rows)} agents)")
    if not status_rows:
        parts.append("(no agents currently registered)")
    else:
        for s in status_rows:
            line = f"  - {s['agent_id']}: {s['status']}"
            if s.get("current_task"):
                line += f" | {s['current_task']}"
            if s.get("blocked_on_msg_id"):
                line += f" | blocked_on_msg={s['blocked_on_msg_id']}"
            if s.get("blocked_on_decision_ref"):
                line += f" | blocked_on={s['blocked_on_decision_ref']}"
            line += f" | hb={s['last_heartbeat']}"
            parts.append(line)

    # ── 3. Unread inbox (requires_response first) ─────────────────────────────
```

Note: renumber `── 2. Unread inbox` → `── 3. Unread inbox`, and the existing `── 3. Strategic decisions` below it stays `── 3` — update to `── 4. Strategic decisions` for consistency. The section numbering is informational only (comments, not code), but keep it coherent.

- [ ] **Step 3: Renumber downstream comment headers**

Edit the next two section comments:

```python
    # ── 3. Strategic decisions (accepted + challenge_window, scoped repos) ────
```
→
```python
    # ── 4. Strategic decisions (accepted + challenge_window, scoped repos) ────
```

And:

```python
    # ── 4. Mark messages forwarded + bump heartbeat (BUG-021) ───────────────
```
→
```python
    # ── 5. Mark messages forwarded + bump heartbeat (BUG-021) ───────────────
```

- [ ] **Step 4: Dry-run verify**

Pre-populate a smoketest agent_status row via psql/dashboard (GUC-guarded):
```sql
BEGIN;
SET LOCAL app.current_agent_id = 'cc-smoketest';
INSERT INTO agent_status (agent_id, status, current_task)
VALUES ('cc-smoketest','working','build_launch_context smoke');
COMMIT;
```

Then:
```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m scripts.build_launch_context --agent cc-ihsanos --dry-run | grep -A20 "World State"
```
Expected: `## World State (N agents)` header followed by at least one line including `cc-smoketest: working | build_launch_context smoke`.

- [ ] **Step 5: Clean up smoketest row**

```sql
BEGIN;
SET LOCAL app.current_agent_id = 'cc-smoketest';
DELETE FROM agent_status WHERE agent_id='cc-smoketest';
COMMIT;
DELETE FROM agent_status_history WHERE agent_id='cc-smoketest';
```

Do NOT commit yet — Commit 2 stages all files atomically.

---

## Task 5: launch_dangerous_cc.sh — GUC + UPSERT at session launch

**Files:**
- Modify: `scripts/launch_dangerous_cc.sh`

**Precondition:** Task 0 subagent outcome is PASS on all 3 cases (psycopg direct works). If Task 0 reported fail-closed outcome, SKIP this task and instead follow the "fail-closed variant" note at the end of Step 1.

- [ ] **Step 1: Locate insertion point**

The new block goes after AGENT_ID is resolved (line 32) and after the context is built (line 97), but before `claude --dangerously-skip-permissions` is invoked (line 392). The natural boundary: insert AFTER "Section 2 — Build session context block" ends (line ~98), BEFORE "Section 3 — Start background heartbeat loop" begins (line ~100).

Run:
```bash
sed -n '96,102p' /Users/sheikhmusa/wingmen/orchestrator/scripts/launch_dangerous_cc.sh
```

- [ ] **Step 2: Insert GUC + UPSERT block**

Use Edit. Target the transition:

```bash
if [ -n "$LAUNCH_CONTEXT" ]; then
    echo -e "${TEAL}  Context assembled: $(echo "$LAUNCH_CONTEXT" | wc -l | tr -d ' ') lines, $(echo -n "$LAUNCH_CONTEXT" | wc -c | tr -d ' ') chars${RESET}"
else
    echo -e "${AMBER}  No context to inject.${RESET}"
fi
echo ""

# ── 3. Start background heartbeat loop ────────────────────────────────────────
```

Replace with:

```bash
if [ -n "$LAUNCH_CONTEXT" ]; then
    echo -e "${TEAL}  Context assembled: $(echo "$LAUNCH_CONTEXT" | wc -l | tr -d ' ') lines, $(echo -n "$LAUNCH_CONTEXT" | wc -c | tr -d ' ') chars${RESET}"
else
    echo -e "${AMBER}  No context to inject.${RESET}"
fi
echo ""

# ── 2.5 ARCH-035 — register agent_status with GUC identity tripwire ─────────
# Opens a dedicated psycopg connection (NOT supabase-py — PostgREST pooling
# breaks GUC semantics). SET LOCAL + UPSERT in one transaction; trigger
# compares NEW.agent_id to app.current_agent_id and raises 42501 on mismatch.
# Launch script is the only place GUC is set; no RPC wrapper (CAI-RESP-043 B1).

echo -e "${BOLD}▶ Registering agent_status for ${AGENT_ID}...${RESET}"
"$VENV_PY" -c "
import os, sys
sys.path.insert(0, '$ORCH_DIR')
from dotenv import load_dotenv
load_dotenv('$ORCH_DIR/.env')
try:
    import psycopg
except ImportError:
    sys.stderr.write('psycopg not installed — skipping agent_status UPSERT\n')
    sys.exit(0)

dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
if not dsn:
    sys.stderr.write('DATABASE_URL not set — skipping agent_status UPSERT\n')
    sys.exit(0)

agent_id = '$AGENT_ID'
try:
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(\"SELECT set_config('app.current_agent_id', %s, true)\", (agent_id,))
            cur.execute(
                '''
                INSERT INTO agent_status (agent_id, status, current_task, last_heartbeat, updated_at)
                VALUES (%s, 'working', 'session-launch', now(), now())
                ON CONFLICT (agent_id) DO UPDATE SET
                  status = EXCLUDED.status,
                  current_task = EXCLUDED.current_task,
                  last_heartbeat = EXCLUDED.last_heartbeat,
                  updated_at = EXCLUDED.updated_at
                ''',
                (agent_id,)
            )
        conn.commit()
    sys.stderr.write(f'launch: agent_status registered for {agent_id} (status=working)\n')
except Exception as e:
    sys.stderr.write(f'launch: agent_status UPSERT failed: {e}\n')
    sys.stderr.write('launch: continuing without agent_status registration — will show in stale_agents\n')
" 2>&1 | grep -E '^launch:' || true
echo ""

# ── 3. Start background heartbeat loop ────────────────────────────────────────
```

**Fail-closed variant** (only if Task 0 Step 3 branched to fail-closed): replace the INSERT with:
```sql
INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, requires_response, thread_id)
VALUES ('$AGENT_ID','cai','blocker','BUG-024-DEPS: agent_status writes blocked pending Phase 1','psycopg GUC propagation test failed (Task 0 Case 3). Launch script cannot safely UPSERT agent_status. Blocker until BUG-024 Phase 1 ships per-agent identity.',true, gen_random_uuid());
```
Use supabase-py here (no GUC needed for `agent_messages`). Document the fail-closed path in STATUS.md.

- [ ] **Step 3: psycopg install check**

Run:
```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -c "import psycopg; print(psycopg.__version__)"
```

If `ModuleNotFoundError`, install:
```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/pip install 'psycopg[binary]>=3.1' && grep -q '^psycopg' requirements.txt || echo 'psycopg[binary]>=3.1' >> requirements.txt
```

Verify `requirements.txt` has `psycopg[binary]>=3.1`. Stage the requirements.txt change for Commit 2.

- [ ] **Step 4: DATABASE_URL env var check**

Run:
```bash
grep -E '^(DATABASE_URL|SUPABASE_DB_URL)=' /Users/sheikhmusa/wingmen/orchestrator/.env | head -2
```

Expected: at least one of `DATABASE_URL` or `SUPABASE_DB_URL` is set with a `postgresql://...` connection string (not the HTTP PostgREST URL — the raw Postgres connection string from Supabase dashboard → Project Settings → Database → Connection string → URI).

If neither is set, add `DATABASE_URL=postgresql://...` to `.env` (Musa owns `.env`; do NOT commit `.env` — just add the line and move on). If the user doesn't have it available, stop and ask.

---

## Task 6: launch_dangerous_cc.sh — shutdown trap sets status='offline'

**Files:**
- Modify: `scripts/launch_dangerous_cc.sh`

- [ ] **Step 1: Locate insertion point**

Inside `_handle_exit()` (line 279+), after `session_end_ts` is computed (~line 290) but before the existing `sb.table('agents').update(...)` block (~line 352). The cleanest boundary is right before the "Post session-end agent_message" section.

Run:
```bash
sed -n '328,355p' /Users/sheikhmusa/wingmen/orchestrator/scripts/launch_dangerous_cc.sh
```

- [ ] **Step 2: Insert offline UPSERT**

Use Edit with this exact old_string/new_string. The key is: match the `# Post session-end agent_message` comment line that anchors the existing block, and insert the new block IMMEDIATELY BEFORE it.

**old_string** (unchanged existing code — use as Edit anchor):

```bash
    # Post session-end agent_message
    local subject
    subject="Session ${outcome}: ${REPO_NAME} (${duration_seconds}s) — $(date -u '+%Y-%m-%d %H:%M UTC')"
```

**new_string** (insert the ARCH-035 block before the anchor):

```bash
    # ARCH-035: flip agent_status to offline (psycopg direct for GUC).
    # Survives clean exit + SIGTERM (trap fires). Does NOT survive kill -9 —
    # stale_agents view catches that via 15-min heartbeat threshold.
    "$VENV_PY" -c "
import os, sys
sys.path.insert(0, '$ORCH_DIR')
from dotenv import load_dotenv
load_dotenv('$ORCH_DIR/.env')
try:
    import psycopg
except ImportError:
    sys.exit(0)

dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
if not dsn:
    sys.exit(0)

try:
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(\"SELECT set_config('app.current_agent_id', %s, true)\", ('$AGENT_ID',))
            cur.execute(
                '''
                UPDATE agent_status
                   SET status = 'offline',
                       current_task = NULL,
                       last_heartbeat = now(),
                       updated_at = now()
                 WHERE agent_id = %s
                ''',
                ('$AGENT_ID',)
            )
        conn.commit()
except Exception as e:
    sys.stderr.write(f'exit: agent_status offline UPSERT failed: {e}\n')
" 2>/dev/null || true

    # Post session-end agent_message
    local subject
    subject="Session ${outcome}: ${REPO_NAME} (${duration_seconds}s) — $(date -u '+%Y-%m-%d %H:%M UTC')"
```

- [ ] **Step 3: Verify shape**

Run:
```bash
grep -n "agent_status" /Users/sheikhmusa/wingmen/orchestrator/scripts/launch_dangerous_cc.sh
```
Expected: 2 regions — one near line ~100 (launch UPSERT), one inside `_handle_exit` near line ~330 (offline UPSERT).

- [ ] **Step 4: Dry-run**

No automated test — this fires on exit. Will be verified live in Task 9.

---

## Task 7: agent_messages_poll.py — banned-prefix rejection

**Files:**
- Modify: `nervous_system/agent_messages_poll.py`
- Modify: `tests/test_agent_messages_poll.py`

### Step 1 — Write the failing test (RED)

- [ ] **Step 1a: Locate existing test module shape**

Read `tests/test_agent_messages_poll.py` to see the existing test pattern and pick the right anchor:
```bash
head -60 /Users/sheikhmusa/wingmen/orchestrator/tests/test_agent_messages_poll.py
```

Note the existing imports and how `_is_routable` / `_format_telegram` are tested, if at all. If `_is_routable` has no tests yet, add a fresh `TestIsRoutable` class.

- [ ] **Step 1b: Add failing tests**

Append to `tests/test_agent_messages_poll.py`:

```python
# ARCH-035: banned-prefix rejection in _is_routable
class TestBannedPrefixRejection:
    """Per ARCH-035: CLAIM/STATUS/HEARTBEAT/DIGEST/COMPLETE prefixes on
    agent_messages subjects belong in agent_status, not agent_messages.
    The poller drops them (no Telegram, no forwarded_to_telegram_at stamp).
    The row stays UNREAD as a tripwire so the sending agent can be coached."""

    def _row(self, subject: str, from_agent: str = "cc-ihsanos", to_agent: str = "cai"):
        return {
            "id": 1,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "message_type": "update",
            "subject": subject,
            "body": "x",
            "requires_response": False,
        }

    def test_claim_prefix_rejected(self):
        from nervous_system.agent_messages_poll import _is_routable
        assert _is_routable(self._row("CLAIM: working on BUG-042")) is False

    def test_status_prefix_rejected(self):
        from nervous_system.agent_messages_poll import _is_routable
        assert _is_routable(self._row("STATUS: idle")) is False

    def test_heartbeat_prefix_rejected(self):
        from nervous_system.agent_messages_poll import _is_routable
        assert _is_routable(self._row("HEARTBEAT: 2026-04-19T12:00Z")) is False

    def test_digest_prefix_rejected(self):
        from nervous_system.agent_messages_poll import _is_routable
        assert _is_routable(self._row("DIGEST: session-end summary")) is False

    def test_complete_prefix_rejected(self):
        from nervous_system.agent_messages_poll import _is_routable
        assert _is_routable(self._row("COMPLETE: TASK-042 shipped")) is False

    def test_normal_subject_still_routed(self):
        from nervous_system.agent_messages_poll import _is_routable
        assert _is_routable(self._row("BUG-042: trigger rewrite for review")) is True

    def test_lowercase_prefix_not_rejected(self):
        # Intentional — only uppercase convention is banned.
        from nervous_system.agent_messages_poll import _is_routable
        assert _is_routable(self._row("claim: normal message")) is True

    def test_prefix_mid_subject_not_rejected(self):
        # Only leading prefix is banned.
        from nervous_system.agent_messages_poll import _is_routable
        assert _is_routable(self._row("re: CLAIM: normal message")) is True

    def test_missing_subject_not_rejected(self):
        # Row with null subject — don't crash on .match(None)
        from nervous_system.agent_messages_poll import _is_routable
        r = self._row("")
        r["subject"] = None
        # Row has to_agent='cai' so it IS normally routable — banned-prefix
        # check should not false-positive on None.
        assert _is_routable(r) is True
```

**Note:** `_is_routable` is currently a nested function inside `poll_agent_messages`. Step 2 will hoist it to module scope so these tests can import it. That's intentional — test forces correct structure.

- [ ] **Step 1c: Run tests, verify RED**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m pytest tests/test_agent_messages_poll.py::TestBannedPrefixRejection -v
```
Expected: FAIL with `ImportError: cannot import name '_is_routable'` (nested function) — this is the expected RED. If it errors differently (e.g. regex AttributeError), fix before proceeding.

### Step 2 — Make tests pass (GREEN)

- [ ] **Step 2a: Hoist `_is_routable` to module scope + add banned-prefix check**

Edit `nervous_system/agent_messages_poll.py`.

Add near the top of the file, after the `_CC_PREFIX = "cc-"` line (line ~37):

```python
import re

# ARCH-035: subjects on agent_messages starting with any of these prefixes
# belong in agent_status, not agent_messages. The poller drops them so the
# row stays UNREAD as a tripwire for the sending agent. Interim: nightly
# pg_cron purges after 24h (filed as follow-up TASK).
_BANNED_PREFIX_RE = re.compile(r'^(CLAIM|STATUS|HEARTBEAT|DIGEST|COMPLETE):')


def _is_routable(r: dict) -> bool:
    """Return True if this row should reach Musa's Telegram.

    Filters:
    1. Banned-prefix rows dropped (ARCH-035) — left UNREAD as tripwire.
    2. CC-to-CC peer traffic dropped.
    3. cai → cc-* relayed.
    4. Everything else: only TELEGRAM_ROUTED_TARGETS addressees.
    """
    subject = r.get("subject") or ""
    if _BANNED_PREFIX_RE.match(subject):
        logger.info(
            f"poll: dropping banned-prefix msg id={r.get('id')} "
            f"subject={subject[:60]}"
        )
        return False

    from_a = r.get("from_agent", "")
    to_a = r.get("to_agent")
    if _is_cc_to_cc(from_a, to_a):
        return False
    if from_a == "cai" and bool(to_a) and to_a.startswith(_CC_PREFIX):
        return True
    return to_a in TELEGRAM_ROUTED_TARGETS
```

- [ ] **Step 2b: Delete the nested `_is_routable` inside `poll_agent_messages`**

Use Edit with this exact old_string/new_string. The nested function definition + its 2-line callsite context is the target; replace with a blank line before the callsite so indentation stays clean.

**old_string:**

```python
        def _is_routable(r: dict) -> bool:
            from_a = r.get("from_agent", "")
            to_a = r.get("to_agent")
            if _is_cc_to_cc(from_a, to_a):
                return False
            # cai messages addressed to any cc-* agent relay through Musa
            if from_a == "cai" and bool(to_a) and to_a.startswith(_CC_PREFIX):
                return True
            return to_a in TELEGRAM_ROUTED_TARGETS

        routable = [r for r in rows if _is_routable(r)]
```

**new_string:**

```python
        routable = [r for r in rows if _is_routable(r)]
```

The nested definition is gone; the callsite now binds to the module-level `_is_routable` hoisted in Step 2a. Indentation is preserved (8 leading spaces — still inside `poll_agent_messages`).

- [ ] **Step 2c: Run tests, verify GREEN**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m pytest tests/test_agent_messages_poll.py -v
```
Expected: all 9 banned-prefix tests PASS, plus all pre-existing tests in the file still PASS. If a pre-existing test breaks, fix — do not proceed.

- [ ] **Step 2d: Full suite regression check**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m pytest -x 2>&1 | tail -15
```
Expected: same pass/fail baseline as STATUS.md reports (355 pass, 7 pre-existing failures). No NEW failures. If a new failure appears, diagnose before moving on.

---

## Task 8: Live wireup verification

**Files:** none modified. Exercises the full chain end-to-end against live Supabase.

**Precondition:** Tasks 2–7 complete. Migration applied. All Python files modified but NOT committed yet.

- [ ] **Step 1: Launch a fresh CC session with synthetic agent_id**

From a terminal (NOT the current cc-ihsanos session — launch a second one):
```bash
CC_AGENT_ID=cc-smoketest-live bash /Users/sheikhmusa/wingmen/orchestrator/scripts/launch_dangerous_cc.sh
```

Expected in stderr/stdout:
- `launch: agent_status registered for cc-smoketest-live (status=working)` (or fail-closed variant)
- `## World State (N agents)` section in context block with at least the registering row

- [ ] **Step 2: Verify agent_status populated**

In the current session (cc-ihsanos), run:
```sql
SELECT agent_id, status, current_task, last_heartbeat
  FROM agent_status WHERE agent_id='cc-smoketest-live';
```
Expected: one row, status='working', current_task='session-launch'.

- [ ] **Step 3: Send a banned-prefix test message**

```sql
INSERT INTO agent_messages (thread_id, from_agent, to_agent, message_type, subject, body, requires_response)
VALUES (gen_random_uuid(), 'cc-smoketest-live', 'cai', 'update', 'CLAIM: testing ARCH-035 banned prefix', 'smoke', false);
```

Wait 30–60s for the notifier poll cycle.

Check notifier logs:
```bash
tail -50 /var/log/wingmen-orchestrator.log 2>/dev/null | grep -i "banned-prefix\|dropping"
```
(Adjust path if orchestrator logs elsewhere — check `~/Library/Logs/` or launchd stdout file.)

Expected: `poll: dropping banned-prefix msg id=<N> subject=CLAIM: testing ARCH-035 banned prefix`

Verify the row is still unread:
```sql
SELECT id, read_at, forwarded_to_telegram_at FROM agent_messages
  WHERE subject='CLAIM: testing ARCH-035 banned prefix';
```
Expected: both NULL.

- [ ] **Step 4: Exit the smoketest CC session**

Type `/exit` (or Ctrl-D) in the smoketest CC session. Wait for the trap to complete (it will print session-end diagnostics).

Verify status flipped:
```sql
SELECT agent_id, status, current_task, last_heartbeat
  FROM agent_status WHERE agent_id='cc-smoketest-live';
```
Expected: status='offline', current_task=NULL.

Check history has both rows:
```sql
SELECT status, current_task, recorded_at FROM agent_status_history
  WHERE agent_id='cc-smoketest-live' ORDER BY recorded_at;
```
Expected: at least 2 rows — 'working'/'session-launch' then 'offline'/NULL.

- [ ] **Step 5: Cleanup**

```sql
BEGIN;
SET LOCAL app.current_agent_id = 'cc-smoketest-live';
DELETE FROM agent_status WHERE agent_id='cc-smoketest-live';
COMMIT;
DELETE FROM agent_status_history WHERE agent_id='cc-smoketest-live';
DELETE FROM agent_messages WHERE from_agent='cc-smoketest-live';
```

---

## Task 9: Commit 2 — atomic code commit

**Files:** all modified in Tasks 2, 4, 5, 6, 7 plus requirements.txt.

- [ ] **Step 1: Stage all Commit-2 files**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git add supabase/migrations/20260419_arch035_three_channel_taxonomy.sql
git add scripts/build_launch_context.py
git add scripts/launch_dangerous_cc.sh
git add nervous_system/agent_messages_poll.py
git add tests/test_agent_messages_poll.py
git add requirements.txt
```

- [ ] **Step 2: Pre-commit diff review**

```bash
git diff --cached --stat
```
Expected: exactly 6 files, counts roughly matching:
- migration: ~150 lines new
- build_launch_context.py: ~30 lines added, 2 renumbered comments
- launch_dangerous_cc.sh: ~60 lines added across 2 regions
- agent_messages_poll.py: ~30 lines added, ~8 deleted
- test_agent_messages_poll.py: ~50 lines added
- requirements.txt: 1 line added

```bash
git diff --cached | head -100
```
Sanity-scan for secrets, accidental .env changes, or stray debug prints.

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(arch-035): three-channel governance taxonomy

Structurally split governance traffic: strategic_decisions (constitution),
agent_status (current state, GUC-guarded), agent_messages (Q&A only, CHECK
narrowed to 7 values).

- Migration: agent_status + agent_status_history (AFTER trigger snapshot)
  + agent_status_identity_violations forensic log via dblink autonomous tx
  + SECURITY DEFINER trigger enforcing app.current_agent_id GUC match
  + stale_agents view + CHECK constraint on agent_messages.message_type
- build_launch_context.py: new World State section in boot briefing
- launch_dangerous_cc.sh: psycopg-direct SET LOCAL + UPSERT at launch,
  offline UPSERT in EXIT trap. No RPC wrapper (CAI-RESP-043 B1).
- agent_messages_poll.py: drop banned-prefix subjects
  (CLAIM|STATUS|HEARTBEAT|DIGEST|COMPLETE), leave row UNREAD as tripwire.

Specs: docs/superpowers/specs/2026-04-19-arch-035-three-channel-governance-taxonomy-design.md
Parents: ARCH-035, CAI-RESP-036, CAI-RESP-042, CAI-RESP-043, CAI-RESP-044

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Push**

```bash
git push origin main
```

Expected: push succeeds; launchd-managed orchestrator restart NOT required (notifier auto-picks up, launch script is re-read per-invocation).

---

## Task 10: STATUS.md update + session digest + CAI ping

**Files:**
- Modify: `STATUS.md`

- [ ] **Step 1: Append ARCH-035 section to STATUS.md**

Read current STATUS.md top section (`Last Completed`). Add a new block above BUG-025 entry:

```markdown
## Last Completed (2026-04-19 — ARCH-035 three-channel taxonomy)

### ARCH-035 — shipped
Plan: `docs/superpowers/plans/2026-04-19-arch-035-three-channel-taxonomy.md`
Spec: `docs/superpowers/specs/2026-04-19-arch-035-three-channel-governance-taxonomy-design.md`
Migration: `supabase/migrations/20260419_arch035_three_channel_taxonomy.sql` (commit `<SHA>`, applied live via dashboard)
Docs commit: `<SHA>` WINGMEN_CONSTRAINTS.md amendment
Code commit: `<SHA>` 6-file atomic (migration + adapters + notifier + tests)

**Shape delivered:**
- `agent_status` table: 1 row/agent, 4-value status CHECK ('idle','working','blocked','offline'), GUC-guarded writes
- `agent_status_history` table: AFTER-trigger append-only snapshot
- `agent_status_identity_violations` table: dblink autonomous-tx forensic log
- `stale_agents` view: 15-min heartbeat drift surface
- `agent_messages` CHECK: 7 legal message_type values including 'blocker'
- Banned prefixes rejected by notifier: `^(CLAIM|STATUS|HEARTBEAT|DIGEST|COMPLETE):`

**Live verification:** [fill in actual numbers from Task 8 outcome]

**Known-degraded until BUG-024 Phase 1:** GUC tripwire relies on launch-script trust. Spoofing requires editing `scripts/launch_dangerous_cc.sh` (auditable). Proper per-agent JWT identity replaces the GUC when BUG-024 ships.

**Follow-ups filed:** (1) 90-day TTL cron for agent_status_history; (2) 24h banned-prefix purge cron; (3) Telegram digest of stale_agents; (4) tool-call-boundary heartbeat hook.

Next P0: ARCH-036 (priority column on narrowed agent_messages, blocked on #75 → now unblocked).
```

Replace `<SHA>` placeholders with actual SHAs from `git log -3 --oneline`.

- [ ] **Step 2: Commit + push STATUS.md**

```bash
git add STATUS.md && git commit -m "$(cat <<'EOF'
chore: update STATUS.md — ARCH-035 three-channel taxonomy shipped

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)" && git push origin main
```

- [ ] **Step 3: Post session digest + completion msg to CAI**

Per memory `feedback_session_digest.md` — post structured JSON digest to CAI via `agent_messages`. Save as `/tmp/arch035_digest.py` (filling in the three SHAs from `git log -3 --oneline`) then run `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python /tmp/arch035_digest.py`.

```python
import os, json, uuid
from dotenv import load_dotenv; load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
from supabase import create_client

DOCS_SHA = "REPLACE_WITH_COMMIT_1_SHA"
CODE_SHA = "REPLACE_WITH_COMMIT_2_SHA"
STATUS_SHA = "REPLACE_WITH_STATUS_COMMIT_SHA"

sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
digest = {
    "arch": "ARCH-035",
    "shipped": True,
    "commits": [DOCS_SHA, CODE_SHA, STATUS_SHA],
    "migration_applied": True,
    "smoke_cases_passed": 8,
    "live_verification": {
        "agent_status_populated": True,
        "banned_prefix_dropped": True,
        "offline_flip_on_exit": True,
    },
    "follow_ups_filed": ["ttl_cron", "purge_cron", "stale_digest", "toolcall_hb"],
    "known_degraded": "GUC tripwire until BUG-024 Phase 1",
    "next": "ARCH-036 priority column (unblocked)",
}
sb.table('agent_messages').insert({
    "thread_id": str(uuid.uuid4()),
    "from_agent": "cc-ihsanos",
    "to_agent": "cai",
    "message_type": "update",
    "subject": "ARCH-035 shipped — three-channel taxonomy live",
    "body": json.dumps(digest, indent=2),
    "requires_response": False,
}).execute()
print("digest posted")
```

- [ ] **Step 4: Update TaskList**

Mark task #85 (`ARCH-035: Present design + write spec`) and #75 (`ARCH-035: agent_status table + channel split`) completed. Unblock #76 (`ARCH-036 priority column`).

---

## Verification Checklist (end of implementation)

- [ ] Task 0 subagent reported 3/3 PASS (or fail-closed variant documented in STATUS.md)
- [ ] WINGMEN_CONSTRAINTS.md section committed (Commit 1)
- [ ] Migration SQL file written AND applied via dashboard
- [ ] Smoke test 8/8 cases passed
- [ ] build_launch_context.py World State section shows in `--dry-run`
- [ ] launch_dangerous_cc.sh GUC+UPSERT and offline-UPSERT both present
- [ ] agent_messages_poll.py banned-prefix tests all PASS; full suite no new failures
- [ ] Live end-to-end: smoketest session populated agent_status, banned-prefix msg dropped, offline-flip on exit
- [ ] Commit 2 (6 files) pushed
- [ ] STATUS.md updated + pushed
- [ ] Session digest posted to CAI
- [ ] Follow-up TASKs filed for: TTL cron, purge cron, stale digest, toolcall heartbeat

---

## Known Limitations (shipping as-is, documented)

1. **`stale_agents` noisy for long sessions.** v0 only stamps `last_heartbeat` at session-launch + session-end. Any active session >15 min appears as stale. Read `agent_status.current_task` + `updated_at` directly until the tool-call-boundary heartbeat hook ships (follow-up TASK).
2. **THIS session's cc-ihsanos row empty until next launch.** Current session was launched without the GUC. Per CAI-RESP-042 Q2, we skip self-UPSERT this session. Forensic gap of a few hours, acceptable.
3. **Banned-prefix rows accumulate until purge cron lands.** Intentional tripwire behaviour for short term. 24h TTL cron filed as follow-up.
4. **Identity trust = launch-script trust.** GUC tripwire catches accidental/careless impersonation and sql-injection-style RPC abuse. Does NOT catch an attacker who edits the launch script. Proper per-agent JWT identity lands with BUG-024 Phase 1.
