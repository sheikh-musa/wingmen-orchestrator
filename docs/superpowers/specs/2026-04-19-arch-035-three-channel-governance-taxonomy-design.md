# ARCH-035 — Three-Channel Governance Taxonomy

**Spec date:** 2026-04-19
**Status:** Approved (Musa scope A; CAI-RESP-036 + CAI-RESP-042 + CAI-RESP-043 binding)
**Parent decisions:** ARCH-035 (decision_ref), CAI-RESP-036 (8-item amendment), CAI-RESP-042 (3-item pre-write clarification), CAI-RESP-043 (spec review: B1 blocker + B2 add + C2/C3/C4 polish), CAI-RESP-044 (dblink+pg_cron confirmed enabled; proceed to writing-plans)
**Implementation owner:** cc-ihsanos
**Estimated time:** ~1.5 days, single session, full vertical slice

## Goal

End the discipline-reliant CLAIM/STATUS/HEARTBEAT prefix conventions on `agent_messages` by structurally splitting governance traffic into three channels with distinct shapes. Reduce `agent_messages` volume 60-70%; surface "what is each agent doing right now" as a queryable table instead of a chronological scroll.

## Architecture

Three logical channels, each backed by a different shape:

```
┌────────────────────────────────────────────────────────────────┐
│ Channel 1 — strategic_decisions  (UNCHANGED — "constitution")  │
│   Permanent rulings: ARCH-/BUG-/CAI-RESP-/CAI-VOCAB-/etc.      │
│   Challenge-window lifecycle, parent-ref graph                 │
├────────────────────────────────────────────────────────────────┤
│ Channel 2 — agent_status         (NEW — current work state)    │
│   One row per agent. v0 stamps at session-launch + session-    │
│   end. Status, current_task, scope, blocked_on, last commit.   │
│   Replaces CLAIM/STATUS/HEARTBEAT prefix conventions.          │
│   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━│
│ Channel 2b — agent_status_history (NEW — append-only forensic) │
│   AFTER trigger snapshots every change. 90-day TTL via cron    │
│   (follow-up TASK).                                            │
├────────────────────────────────────────────────────────────────┤
│ Channel 3 — agent_messages       (NARROWED via CHECK)          │
│   Q&A only: review_request, question, decision, agreed,        │
│   challenge, update, blocker  (7 values, CAI-RESP-042 Q1).     │
│   Notifier rejects ^(CLAIM|STATUS|HEARTBEAT|DIGEST|COMPLETE):  │
└────────────────────────────────────────────────────────────────┘
```

### Identity tripwire (B2)

`agent_status` writes pass through a `SECURITY DEFINER` BEFORE trigger that compares `NEW.agent_id` to `current_setting('app.current_agent_id', true)`. Mismatch raises `42501`. CC launch script sets the GUC at session-start via `SET LOCAL` **from a psycopg connection the launch script owns** — no RPC-wrapper shortcut that would defeat the check (CAI-RESP-043 B1). Spoofing requires editing the launch script (auditable). Documented as known-degraded interim until BUG-024 Phase 1 lands proper per-agent JWT identity.

**Rejected attempts are logged to `agent_status_identity_violations`** via dblink autonomous transaction (CAI-RESP-043 B2) so the forensic record survives the caller's rolled-back transaction.

### Heartbeat (B1)

Event-only, not time-guaranteed. `stale_agents` view surfaces drift (`last_heartbeat < now() - interval '15min' AND status != 'offline'`). Drift is **detected, not prevented**.

**v0 scope (this ship):** `last_heartbeat` is stamped once at session-launch (File 3 UPSERT) and once at session-end (File 4 shutdown trap). The tool-call-boundary heartbeat that was the long-term design is NOT in this ship — CC has no native tool-call hook framework in this repo yet. Consequence: any session longer than 15 min will appear in `stale_agents` until the tool-call hook is added. Tracked as a follow-up TASK. Operator workaround until then: read `agent_status.current_task` + `updated_at` directly, don't rely on `stale_agents` for long-session monitoring.

### Forensic preservation (B3)

`agent_status_history` is append-only, populated by an AFTER INSERT/UPDATE trigger on `agent_status`. Existing debugging workflows (e.g., the 2026-04-18 governance-blackout trace) that relied on `agent_messages` history can rebuild from `agent_status_history`. 90-day TTL deferred as a follow-up TASK to keep initial migration small.

## Schema (migration 1 of 1)

**File:** `supabase/migrations/20260419_arch035_three_channel_taxonomy.sql`

```sql
-- 1. agent_status — one row per agent, current work state
CREATE TABLE agent_status (
  agent_id                TEXT PRIMARY KEY,
  status                  TEXT NOT NULL CHECK (status IN ('idle','working','blocked','offline')),  -- CAI-RESP-043 C3: dropped 'reviewing' — use current_task='reviewing X' instead
  current_task            TEXT,
  scope_repos             TEXT[],
  scope_paths             TEXT[],
  blocked_on_msg_id       BIGINT REFERENCES agent_messages(id) ON DELETE SET NULL,
  blocked_on_decision_ref TEXT REFERENCES strategic_decisions(decision_ref) ON DELETE SET NULL,  -- A4
  blocked_on_description  TEXT,
  last_commit_sha         TEXT,
  last_commit_repo        TEXT,
  last_heartbeat          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_agent_status_active ON agent_status (last_heartbeat DESC) WHERE status != 'offline';

-- 2. agent_status_history — append-only forensic log (B3)
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

-- 3. AFTER trigger — snapshot every change to history
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

-- 4a. Identity violations forensic log (CAI-RESP-043 B2 — capture every rejected attempt)
CREATE TABLE agent_status_identity_violations (
  id              BIGSERIAL PRIMARY KEY,
  claimed_agent   TEXT,                                    -- NEW.agent_id the caller asserted
  guc_value       TEXT,                                    -- app.current_agent_id at rejection time (may be NULL)
  violation_type  TEXT NOT NULL CHECK (violation_type IN ('guc_not_set','identity_mismatch')),
  attempted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  session_user    TEXT NOT NULL DEFAULT session_user,      -- postgres role at rejection
  operation       TEXT NOT NULL                            -- TG_OP: INSERT/UPDATE
);
CREATE INDEX idx_violations_recent ON agent_status_identity_violations (attempted_at DESC);

-- 4b. Autonomous-transaction helper (dblink) — logs violations durably even when the
--     outer transaction aborts. Standard Postgres pattern for trigger-side audit.
--     CAI-RESP-044 Q1 confirmed dblink + pg_cron are allowlisted on this Supabase project.
CREATE EXTENSION IF NOT EXISTS dblink;
CREATE EXTENSION IF NOT EXISTS pg_cron;  -- readiness for follow-up TTL + purge crons (CAI-RESP-044)

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

-- 4c. Identity tripwire — SECURITY DEFINER trigger compares agent_id to GUC,
--     logs rejections via dblink (so the log persists even though RAISE rolls back
--     the caller's transaction), then RAISEs.
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

-- 5. stale_agents view (B1 — drift detected, not prevented)
CREATE OR REPLACE VIEW stale_agents AS
SELECT agent_id, status, current_task,
       last_heartbeat, (now() - last_heartbeat) AS heartbeat_age
FROM agent_status
WHERE status != 'offline' AND last_heartbeat < now() - interval '15 minutes';

-- 6. updated_at auto-bump
CREATE OR REPLACE FUNCTION bump_agent_status_updated_at()
RETURNS TRIGGER AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_agent_status_updated_at BEFORE UPDATE ON agent_status
  FOR EACH ROW EXECUTE FUNCTION bump_agent_status_updated_at();

-- 7. agent_messages CHECK constraint (A2 + CAI-RESP-042 Q1 — narrowed channel, 7 values)
ALTER TABLE agent_messages
  ADD CONSTRAINT agent_messages_message_type_check
  CHECK (message_type IN ('review_request','question','decision','agreed','challenge','update','blocker'));
```

**Notes:**
- No RLS policies on `agent_status` — interim trust model is the GUC tripwire (B2). Real RLS lands with BUG-024 Phase 1.
- `agent_status_history` has no FK to `agent_status(agent_id)` — deleting an agent does not cascade. Forensic preservation.
- 90-day TTL cron on `agent_status_history` deferred to follow-up TASK (pg_cron implementation named per CAI-RESP-043 C4).
- The CHECK constraint validates against existing rows immediately. CAI-RESP-042 Q1 confirmed `'blocker'` is in the legal set, so no backfill needed.
- **No `set_agent_id_and_upsert_status` RPC** — per CAI-RESP-043 B1 that pattern structurally defeats the identity tripwire (caller-controlled parameter feeding both `set_config` and `NEW.agent_id`). Launch script owns GUC directly.
- `dblink` required for the violations log (B2) — verify Supabase has it enabled before migration applies (Q1 in agreed response).

## Wireup (rollout order — CAI-RESP-036 A3)

Sequenced as 6 steps, two git commits, one PR.

### Commit 1 — `WINGMEN_CONSTRAINTS.md` amendment (docs first, per CAI-RESP-042 Q3)

Add a new section "Three-channel governance taxonomy + launch protocol":
- Defines the 3 channels (strategic_decisions / agent_status / agent_messages) and what belongs where
- Documents the GUC launch protocol (`SET LOCAL app.current_agent_id = '<agent_id>'` at session-start, before any agent_status write)
- Lists banned subject prefixes for agent_messages (`CLAIM:`, `STATUS:`, `HEARTBEAT:`, `DIGEST:`, `COMPLETE:`)
- Notes BUG-024 Phase 1 as the eventual identity hardening; GUC tripwire is documented as known-degraded interim

### Commit 2 — Migration + adapters + script wiring + notifier (atomic code commit)

**File 1 — Migration** (above).

**File 2 — `scripts/build_launch_context.py` adapter** (~30 lines added)

Insert a new section between "Agent context" (line ~55) and "Unread inbox" (line ~78):

```python
# ── 2.5 World state (agent_status snapshot) ───────────────
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
```

So every CC's boot briefing shows what every other agent is doing — replaces the discipline-based "scroll through agent_messages CLAIMs" pattern.

**File 3 — `scripts/launch_dangerous_cc.sh` GUC + UPSERT wiring** (subagent task)

After AGENT_ID is resolved (line 32) and before `claude -p "$CONTEXT_BLOCK"` is invoked, add a Python snippet that:
1. Opens a dedicated psycopg connection (NOT supabase-py) so GUC semantics are under our control
2. `SET LOCAL app.current_agent_id = $AGENT_ID`
3. UPSERTs `agent_status` with `agent_id=$AGENT_ID, status='working', current_task='session-launch'` on that same connection before close
4. Closes connection (GUC evaporates with it; caller cannot reuse the set_config to spoof a later write)

**CAI-RESP-043 B1 (SHIP BLOCKER, applied):** NO RPC wrapper that both sets the GUC and UPSERTs the row — that pattern is a tautology (the function trusts the caller-controlled parameter then verifies it set_config'd the caller-controlled parameter). Defeated by construction. The launch script **must** be the source of the GUC, and the GUC **must** come from an out-of-band source (here: `AGENT_ID` resolved from `CC_AGENT_ID` env var, which the launch script reads before invoking anything else).

**Subagent's first job — PostgREST propagation test.** Confirm that a direct psycopg2/psycopg3 connection (NOT supabase-py's pooled PostgREST path) can `SET LOCAL` + INSERT in the same transaction and have the trigger see the GUC. Two outcomes:

- **(a) psycopg direct works** (expected — raw libpq sessions preserve SET LOCAL within a transaction): ship as drafted. Launch script uses psycopg directly with `DATABASE_URL` or Supabase connection-string env var, wraps `SET LOCAL` + INSERT in one `BEGIN...COMMIT` block.
- **(b) psycopg direct also fails** (unexpected): **fail closed per CAI-RESP-043.** Ship the migration + adapters + notifier changes, but the launch script does NOT attempt to UPSERT agent_status. Instead it posts a `blocker` agent_message on every boot: `"agent_status writes blocked pending BUG-024 Phase 1"`. `agent_status` sits empty for all agents until BUG-024 ships proper per-agent identity. File as `BUG-024-DEPS` dependency row.

Correctness beats convenience — "agent_status empty for a few days" is strictly better than "agent_status populated with caller-asserted identities that look verified but aren't."

**File 4 — `scripts/launch_dangerous_cc.sh` shutdown trap** (~15 lines)

Add a `trap` that runs on EXIT (clean exit, SIGTERM, parent disconnect). Same Python snippet as above but `status='offline'`, `current_task=NULL`. Survives `kill -9`? No — trap doesn't fire. The `stale_agents` view catches that case via 15-min heartbeat threshold (B1).

**File 5 — `nervous_system/agent_messages_poll.py` reject list** (~10 lines added to `_is_routable`)

```python
import re
_BANNED_PREFIX = re.compile(r'^(CLAIM|STATUS|HEARTBEAT|DIGEST|COMPLETE):')

def _is_routable(r: dict) -> bool:
    if _BANNED_PREFIX.match(r.get('subject') or ''):
        logger.info(
            f"poll: dropping banned-prefix msg id={r['id']} "
            f"subject={r['subject'][:60]}"
        )
        return False
    # ... existing checks ...
```

Drop, log at INFO, and **do NOT stamp `forwarded_to_telegram_at`** — leave the offending message UNREAD as a tripwire so the agent that sent it can be coached. Per A1.

**CAI-RESP-043 C2 — purge policy chosen: (a) time-based.** Banned-prefix rows accumulating as unread tripwires risks silent accumulation. Picked option (a): a nightly pg_cron job purges `agent_messages` rows whose subject matches the banned-prefix regex AND `read_at IS NULL AND forwarded_to_telegram_at IS NULL AND created_at < now() - interval '24 hours'`. Filed as a follow-up TASK to land with the stale_agents Telegram digest.

**Why not option (b) counter-message.** CAI's option (b) had the notifier post back `from_agent='cai', to_agent=<offender>, message_type='update', subject='Rejected: ...'`. That reintroduces the exact attack surface BUG-024 is designed to close — a server-side process posting as `cai` without any identity verification is indistinguishable (to a reader) from the msg 252 impersonation pattern. The teaching-signal is a real loss; accepted, deferred. When BUG-024 Phase 1 lands per-agent identity, option (b) becomes safe and we can revisit.

## Verification

### Smoke test (post-migration apply, pre-commit)

Uses synthetic `cc-smoketest` agent_id per CAI-RESP-042 Q2 amendment.

```sql
-- 1. Identity tripwire FAILS without GUC + logs violation via dblink
INSERT INTO agent_status (agent_id, status) VALUES ('cc-smoketest','working');
-- expect: ERROR 42501 "GUC not set"
SELECT violation_type, claimed_agent FROM agent_status_identity_violations
 WHERE claimed_agent='cc-smoketest' ORDER BY id DESC LIMIT 1;
-- expect: ('guc_not_set', 'cc-smoketest')  -- B2: dblink persisted the log

-- 2. Identity tripwire FAILS with mismatched GUC + logs violation
BEGIN;
SET LOCAL app.current_agent_id = 'cc-smoketest';
INSERT INTO agent_status (agent_id, status) VALUES ('cc-evil','working');
-- expect: ERROR 42501 "identity mismatch"
ROLLBACK;
SELECT violation_type, claimed_agent, guc_value FROM agent_status_identity_violations
 WHERE claimed_agent='cc-evil' ORDER BY id DESC LIMIT 1;
-- expect: ('identity_mismatch', 'cc-evil', 'cc-smoketest')

-- 3. Identity tripwire PASSES with matching GUC
BEGIN;
SET LOCAL app.current_agent_id = 'cc-smoketest';
INSERT INTO agent_status (agent_id, status, current_task)
VALUES ('cc-smoketest','working','smoke');
-- expect: row inserted; agent_status_history row mirrors via snapshot trigger
COMMIT;

-- 4. UPSERT updates row + appends history row
BEGIN;
SET LOCAL app.current_agent_id = 'cc-smoketest';
UPDATE agent_status SET status='blocked', current_task='waiting on cai'
WHERE agent_id='cc-smoketest';
COMMIT;
SELECT count(*) FROM agent_status_history WHERE agent_id='cc-smoketest';
-- expect: 2

-- 5. stale_agents view excludes fresh entry
SELECT * FROM stale_agents WHERE agent_id='cc-smoketest';
-- expect: 0 rows
-- (15-min staleness backdate test deferred to follow-up; cannot easily synthesize)

-- 6. CHECK constraint accepts 'blocker' (CAI-RESP-042 Q1)
INSERT INTO agent_messages (thread_id, from_agent, to_agent, message_type, subject, body, requires_response)
VALUES (gen_random_uuid(),'cc-smoketest','cai','blocker','smoketest','b',true);
-- expect: row inserted

-- 7. CHECK rejects unknown type
INSERT INTO agent_messages (thread_id, from_agent, to_agent, message_type, subject, body, requires_response)
VALUES (gen_random_uuid(),'cc-smoketest','cai','nonsense','smoketest','b',true);
-- expect: ERROR 23514 check constraint violation

-- 7b. status CHECK rejects 'reviewing' (CAI-RESP-043 C3 — dropped from enum)
BEGIN;
SET LOCAL app.current_agent_id = 'cc-smoketest';
UPDATE agent_status SET status='reviewing' WHERE agent_id='cc-smoketest';
-- expect: ERROR 23514 check constraint violation on agent_status_status_check
ROLLBACK;

-- 8. Cleanup
BEGIN;
SET LOCAL app.current_agent_id = 'cc-smoketest';
DELETE FROM agent_status WHERE agent_id='cc-smoketest';
COMMIT;
DELETE FROM agent_status_history WHERE agent_id='cc-smoketest';
DELETE FROM agent_messages WHERE from_agent='cc-smoketest';
DELETE FROM agent_status_identity_violations
 WHERE claimed_agent IN ('cc-smoketest','cc-evil');
SELECT * FROM agent_status; -- expect: empty
```

### Live wireup verification (post-deploy)

- Restart launchd-managed orchestrator → next CC launch should populate its `agent_status` row → `SELECT * FROM agent_status` shows the launching agent.
- Send a fake `CLAIM: foo` message → `agent_messages_poll.py` log shows "poll: dropping banned-prefix" → no Telegram sent → message stays `read_at IS NULL` and `forwarded_to_telegram_at IS NULL`.
- Boot briefing for cc-ihsanos shows the new "World State" section with at least one row.

## Risks

1. **psycopg GUC propagation** (Step 4 subagent test) — if even a direct psycopg connection can't have `SET LOCAL` + INSERT seen by the trigger in the same transaction (unexpected; libpq-level semantics should work), we fail closed per CAI-RESP-043 B1: ship migration + adapters WITHOUT auto-UPSERT, post `blocker` agent_message on every boot, agent_status stays empty until BUG-024 Phase 1 ships proper identity. NO RPC wrapper (would structurally defeat the tripwire).
2. **Subprocess Python in launch script adds boot latency** (~200-500 ms). Acceptable; alternative is a separate post-launch Python hook, which loses atomicity.
3. **Banned-prefix rows accumulate until purge cron lands** — intentional for the short term (A1 — discipline visible). Nightly cron (filed as follow-up) will cap retention at 24h per CAI-RESP-043 C2.
4. **THIS-session bootstrap** — cc-ihsanos's current session was launched without the GUC set. Per CAI-RESP-042 Q2, we skip self-UPSERT this session; agent_status sits empty for cc-ihsanos until the next launch. Acceptable forensic gap of a few hours.
5. **`stale_agents` noisy until tool-call hook lands** — v0 only stamps `last_heartbeat` at session-launch and session-end, so long sessions (>15 min) flag as stale. Documented as known limitation; operator should read `agent_status` directly until the follow-up TASK lands.
6. **dblink availability** — resolved. CAI-RESP-044 Q1 confirmed `dblink 1.2` + `pg_cron 1.6.4` are both allowlisted on project `tscuymavysscrvoberrr`. `CREATE EXTENSION IF NOT EXISTS dblink` + `pg_cron` included in the migration directly.

## Follow-ups (deferred TASKs, not blocking ship)

- `TASK-NNN` (file post-ship): **90-day TTL cron for `agent_status_history`** (per CAI-RESP-036 B3, implementation named per CAI-RESP-043 C4). Use Supabase `pg_cron`: nightly at 03:00 UTC, `DELETE FROM agent_status_history WHERE recorded_at < now() - interval '90 days'`. If history grows hot, add partial index `WHERE recorded_at > now() - interval '30 days'` for common queries. Cron registration: `SELECT cron.schedule('agent_status_history_ttl', '0 3 * * *', $$DELETE FROM agent_status_history WHERE recorded_at < now() - interval '90 days'$$);`
- `TASK-NNN` (file post-ship): **Banned-prefix unread purge cron** (per CAI-RESP-043 C2, option a). `pg_cron` nightly at 03:15 UTC: `DELETE FROM agent_messages WHERE subject ~ '^(CLAIM|STATUS|HEARTBEAT|DIGEST|COMPLETE):' AND read_at IS NULL AND forwarded_to_telegram_at IS NULL AND created_at < now() - interval '24 hours'`. **Interim mechanism only** — CAI-RESP-044 C2 acknowledgment confirms option (b) counter-message is the preferred long-term approach once BUG-024 Phase 1 lands per-agent identity; purge is a bridge, not the end-state.
- `TASK-NNN` (file post-ship): Telegram digest of `stale_agents` view rows (15-min staleness alert) — currently silent, only visible if queried.
- `TASK-NNN` (file post-ship): Tool-call-boundary heartbeat hook — bump `last_heartbeat` once per minute during active work, so `stale_agents` becomes useful for long-session detection.
- `BUG-024 Phase 1`: replaces GUC tripwire with proper per-agent JWT identity; `agent_status` RLS becomes real then.
- `ARCH-036`: priority column on narrowed `agent_messages` — separate migration after ARCH-035 smoke-test passes (per CAI-RESP-036 A5).
