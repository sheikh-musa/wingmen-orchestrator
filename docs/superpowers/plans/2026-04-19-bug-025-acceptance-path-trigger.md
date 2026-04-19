# BUG-025 Acceptance-Path Trigger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `trigger_cai_decision_announce()` so CAI-filed decisions in `challenge_status='accepted'` (CAI-RESP-* rulings) auto-announce to CC alongside the `challenge_window` path BUG-020 already handles, branching message shape on status.

**Architecture:** Single Supabase migration that does `CREATE OR REPLACE FUNCTION` on the existing trigger function. Existing `BEFORE INSERT` and `BEFORE UPDATE OF challenge_status` triggers automatically pick up the new body — no DROP TRIGGER, no schema change. Verification is a 4-case live matrix run against the orchestrator Supabase per CAI-RESP-040 amendment A2.

**Tech Stack:** Supabase Postgres 14+, Node.js (ad-hoc verification queries via `@supabase/supabase-js` + `dotenv`), git.

**Spec source:** `strategic_decisions.decision_ref='BUG-025'` (challenge) and `decision_ref='CAI-RESP-040'` (acceptance — B1 + A1 + A2 + concession). Parent: BUG-020 (`docs/superpowers/specs/2026-04-18-governance-comms-pipeline-hardening-design.md`, shipped commit `357a135`).

---

## Background — what changed and why

The BUG-020 trigger (shipped 2026-04-19, file `supabase/migrations/20260419_bug020_bug021_governance_comms_hardening.sql`) only fires on `challenge_status='challenge_window'`. CAI files the *response* to a CC challenge as a sibling `strategic_decisions` row with `challenge_status='accepted'` (e.g., CAI-RESP-040 itself). Those rulings never get an `agent_messages` row, so CC never sees the response in its inbox until Musa pastes the body manually.

Per CAI-RESP-040, the fix:
1. **Drop** the implicit "challenge_window only" guard — announce **all** CAI-filed decisions (`source='claude_ai_session'`) regardless of `challenge_status`, gated only by `bypass_review` and `announced_by_msg_id IS NULL`.
2. **Branch on `challenge_status`** for message shape — rulings are not review requests:

   | challenge_status | message_type | subject | requires_response |
   |---|---|---|---|
   | `challenge_window` | `review_request` | `<ref>: <title> — for review + challenge` | `true` |
   | `accepted` | `decision` | `<ref>: <title>` | `false` |

3. **Drop the `OLD.challenge_status='challenge_window'` state-transition guard** — `announced_by_msg_id IS NOT NULL` already prevents re-announce when a row transitions `challenge_window → accepted`.
4. **No SIMILAR TO regex on `decision_ref`** (A1) — `source='claude_ai_session'` is the canonical "from CAI" signal; prefix enumeration is dead code and creates a fragile namespace.

Forward-compatibility check: ARCH-035's planned `CHECK (message_type IN ('review_request','question','decision','agreed','challenge','update'))` includes `decision`, so this trigger is safe to ship before or after ARCH-035.

---

## File map

| File | Change | Task |
|------|--------|------|
| `supabase/migrations/20260419_bug025_acceptance_path_announce.sql` | **Create** — new migration, only `CREATE OR REPLACE FUNCTION` | 1 |
| *(deploy + verification — no file changes)* | | 2–3 |
| `STATUS.md` (orchestrator) | **Modify** — append BUG-025 shipped entry | 4 |

Two distinct migrations on the same date (`20260419_bug020_bug021_…` and `20260419_bug025_…`) is fine — Supabase orders by full filename string, so the two co-exist and BUG-025 applies after BUG-020.

## Commit strategy

One commit per task (Tasks 1, 4). Tasks 2–3 are deploy + verification operations, not commits.

---

## Task 1: Write migration SQL

**Files:**
- Create: `supabase/migrations/20260419_bug025_acceptance_path_announce.sql`

- [ ] **Step 1: Create the migration file**

Write the file with this exact content:

```sql
-- BUG-025: Announce CAI-filed decisions filed as challenge_status='accepted',
--   not just 'challenge_window'. Branches message shape on status.
--
-- Parent: BUG-020. References:
--   docs/superpowers/specs/2026-04-18-governance-comms-pipeline-hardening-design.md
--   docs/superpowers/plans/2026-04-19-bug-025-acceptance-path-trigger.md
--   strategic_decisions.decision_ref='BUG-025'  (challenge)
--   strategic_decisions.decision_ref='CAI-RESP-040'  (acceptance — B1 + A1 + A2 + concession)
--
-- Shape:
--   CREATE OR REPLACE on trigger_cai_decision_announce(); the existing
--   BEFORE INSERT and BEFORE UPDATE OF challenge_status triggers carry over.
--   No DROP TRIGGER, no schema change.
--
-- Behaviour change vs BUG-020 (357a135):
--   1. Announceable status set widened: 'challenge_window' → ('challenge_window', 'accepted').
--   2. Message shape branches on challenge_status:
--        challenge_window → review_request, requires_response=true (BUG-020 preserved).
--        accepted         → decision,        requires_response=false (BUG-025).
--   3. OLD.challenge_status='challenge_window' state-transition guard dropped —
--      announced_by_msg_id IS NOT NULL is already the universal dedup.
--   4. No SIMILAR TO regex on decision_ref. source='claude_ai_session' is the
--      canonical "from CAI" signal; prefix enumeration would be dead code and
--      would silently drop future namespaces (CAI-OPS, CAI-MUFTI, etc.).
--
-- Forward-compat with ARCH-035: 'decision' is in the planned message_type CHECK.

CREATE OR REPLACE FUNCTION trigger_cai_decision_announce()
RETURNS TRIGGER AS $$
DECLARE
  v_msg_id BIGINT;
  v_subject TEXT;
  v_body TEXT;
  v_message_type TEXT;
  v_requires_response BOOLEAN;
BEGIN
  -- Guard: CAI-filed decisions only, in an announceable status,
  --        not bypass_review, not already announced.
  IF NEW.source IS DISTINCT FROM 'claude_ai_session'
     OR NEW.challenge_status NOT IN ('challenge_window', 'accepted')
     OR COALESCE(NEW.bypass_review, false) = true
     OR NEW.announced_by_msg_id IS NOT NULL THEN
    RETURN NEW;
  END IF;

  -- Branch message shape on challenge_status.
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
```

- [ ] **Step 2: Visual diff against the BUG-020 function body**

Run: `diff <(sed -n '/^CREATE OR REPLACE FUNCTION trigger_cai_decision_announce/,/^\$\$ LANGUAGE plpgsql;/p' supabase/migrations/20260419_bug020_bug021_governance_comms_hardening.sql) <(sed -n '/^CREATE OR REPLACE FUNCTION trigger_cai_decision_announce/,/^\$\$ LANGUAGE plpgsql;/p' supabase/migrations/20260419_bug025_acceptance_path_announce.sql)`

Expected: differences are only in (a) DECLARE adds `v_message_type` + `v_requires_response`, (b) guard widens `IS DISTINCT FROM 'challenge_window'` → `NOT IN ('challenge_window','accepted')`, (c) the `IF TG_OP = 'UPDATE' AND OLD.challenge_status = 'challenge_window'` block is gone, (d) new `IF NEW.challenge_status = 'challenge_window' THEN … ELSE …` shape branch, (e) body format string includes `(status: %s)`, (f) INSERT uses `v_message_type` and `v_requires_response` instead of literals.

If the diff shows anything else (whitespace drift, accidental change to the INSERT column list, etc.), fix before proceeding.

- [ ] **Step 3: Commit**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git add supabase/migrations/20260419_bug025_acceptance_path_announce.sql
git commit -m "feat(db): BUG-025 announce CAI accepted-path decisions

Rewrites trigger_cai_decision_announce() to:
- Widen announceable status set to ('challenge_window','accepted')
- Branch message shape: challenge_window → review_request/needs-response,
  accepted → decision/no-response
- Drop OLD.challenge_status state-transition guard (announced_by_msg_id
  IS NULL is the universal dedup)

Parent: BUG-020. Spec: strategic_decisions.decision_ref='CAI-RESP-040'
(B1 + A1 + A2 + concession on simpler announce-all-CAI variant).

See docs/superpowers/plans/2026-04-19-bug-025-acceptance-path-trigger.md"
```

---

## Task 2: Apply migration + smoke test

**Files:** none (deploy operation).

- [ ] **Step 1: Apply the migration**

Apply `supabase/migrations/20260419_bug025_acceptance_path_announce.sql` via the orchestrator's usual path — same as BUG-020 in commit `357a135`. If uncertain, paste the SQL into the Supabase SQL editor for the orchestrator project and run once.

Expected: no errors. `CREATE OR REPLACE FUNCTION` is idempotent.

- [ ] **Step 2: Verify the function source contains the new branch**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && node -e "
require('dotenv').config({path:'.env'});
const {createClient}=require('@supabase/supabase-js');
const url=process.env.SUPABASE_URL||process.env.ORCHESTRATOR_SUPABASE_URL;
const key=process.env.SUPABASE_SERVICE_KEY||process.env.ORCHESTRATOR_SUPABASE_SERVICE_KEY;
const c=createClient(url,key);
(async()=>{
  const {data,error}=await c.rpc('exec_sql',{sql:
    \"SELECT prosrc FROM pg_proc WHERE proname='trigger_cai_decision_announce'\"
  }).catch(()=>({data:null,error:'rpc unavailable'}));
  if(data){
    const src=data[0]?.prosrc||'';
    console.log('has accepted branch:', src.includes(\"NOT IN ('challenge_window', 'accepted')\"));
    console.log('has decision message_type:', src.includes(\"v_message_type := 'decision'\"));
    console.log('has v_requires_response:', src.includes('v_requires_response'));
    return;
  }
  console.log('exec_sql unavailable — skip pg_proc check, rely on Task 3 live verification');
})();
"
```

Expected (if `exec_sql` is available): all three booleans `true`.
Expected (otherwise): "exec_sql unavailable" — fine, Task 3 catches any failure end-to-end.

If `exec_sql` exists and any boolean is `false`: migration did not apply or applied an older version. Re-check the SQL editor and re-run Task 2 Step 1.

---

## Task 3: 4-case verification matrix (CAI-RESP-040 amendment A2)

**Files:** none (live DB operations).

Per CAI-RESP-040 A2, four cases must be verified before declaring BUG-025 fixed. Each case inserts a synthetic `strategic_decisions` row, asserts the trigger behaviour, then deletes both the row and any spawned `agent_messages`.

Run all four sequentially in one task — they share the same Node + Supabase setup.

**Test row naming:** `decision_ref` = `BUG-025-VERIFY-001` through `…-004`. After all four pass, Step 5 cleans them up.

- [ ] **Step 1: CAI-TEST-001 — accepted path (NEW BEHAVIOUR)**

Insert with `challenge_status='accepted'`, verify the trigger announces with `message_type='decision'` + `requires_response=false` and no "— for review + challenge" subject suffix.

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && node -e "
require('dotenv').config({path:'.env'});
const {createClient}=require('@supabase/supabase-js');
const url=process.env.SUPABASE_URL||process.env.ORCHESTRATOR_SUPABASE_URL;
const key=process.env.SUPABASE_SERVICE_KEY||process.env.ORCHESTRATOR_SUPABASE_SERVICE_KEY;
const c=createClient(url,key);
(async()=>{
  const {data:dec,error:e1}=await c.from('strategic_decisions').insert({
    decision_ref:'BUG-025-VERIFY-001',
    title:'BUG-025 verify — accepted path',
    source:'claude_ai_session',
    challenge_status:'accepted',
    bypass_review:false,
    body:'Synthetic. Delete after verify.'
  }).select('id,announced_by_msg_id,notified_at').single();
  if(e1){console.log('INSERT ERR',e1.message);return;}
  console.log('decision row:',JSON.stringify(dec));
  if(!dec.announced_by_msg_id){console.log('FAIL: no announced_by_msg_id');return;}

  const {data:msg,error:e2}=await c.from('agent_messages')
    .select('id,from_agent,to_agent,message_type,subject,requires_response')
    .eq('id',dec.announced_by_msg_id).single();
  if(e2){console.log('MSG SELECT ERR',e2.message);return;}
  console.log('announce msg:',JSON.stringify(msg));

  const ok =
    msg.from_agent==='cai' &&
    msg.to_agent==='cc-ihsanos' &&
    msg.message_type==='decision' &&
    msg.requires_response===false &&
    msg.subject==='BUG-025-VERIFY-001: BUG-025 verify — accepted path' &&
    !msg.subject.includes('— for review + challenge');
  console.log('CAI-TEST-001:', ok ? 'PASS' : 'FAIL');
})();
"
```

Expected: `CAI-TEST-001: PASS`. If FAIL, dump `msg` and inspect which assertion failed.

- [ ] **Step 2: CAI-TEST-002 — challenge_window path (BUG-020 regression guard)**

Insert with `challenge_status='challenge_window'`, verify trigger still announces with `message_type='review_request'` + `requires_response=true` + "— for review + challenge" suffix.

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && node -e "
require('dotenv').config({path:'.env'});
const {createClient}=require('@supabase/supabase-js');
const url=process.env.SUPABASE_URL||process.env.ORCHESTRATOR_SUPABASE_URL;
const key=process.env.SUPABASE_SERVICE_KEY||process.env.ORCHESTRATOR_SUPABASE_SERVICE_KEY;
const c=createClient(url,key);
(async()=>{
  const {data:dec,error:e1}=await c.from('strategic_decisions').insert({
    decision_ref:'BUG-025-VERIFY-002',
    title:'BUG-025 verify — challenge_window regression',
    source:'claude_ai_session',
    challenge_status:'challenge_window',
    bypass_review:false,
    body:'Synthetic. Delete after verify.'
  }).select('id,announced_by_msg_id,notified_at').single();
  if(e1){console.log('INSERT ERR',e1.message);return;}
  if(!dec.announced_by_msg_id){console.log('FAIL: no announced_by_msg_id');return;}

  const {data:msg,error:e2}=await c.from('agent_messages')
    .select('message_type,subject,requires_response')
    .eq('id',dec.announced_by_msg_id).single();
  if(e2){console.log('MSG SELECT ERR',e2.message);return;}

  const ok =
    msg.message_type==='review_request' &&
    msg.requires_response===true &&
    msg.subject.endsWith(' — for review + challenge');
  console.log('CAI-TEST-002:', ok ? 'PASS' : 'FAIL', JSON.stringify(msg));
})();
"
```

Expected: `CAI-TEST-002: PASS`.

- [ ] **Step 3: CAI-TEST-003 — bypass_review escape hatch**

Insert with `challenge_status='accepted'` AND `bypass_review=true`. Verify NO `agent_messages` row spawned.

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && node -e "
require('dotenv').config({path:'.env'});
const {createClient}=require('@supabase/supabase-js');
const url=process.env.SUPABASE_URL||process.env.ORCHESTRATOR_SUPABASE_URL;
const key=process.env.SUPABASE_SERVICE_KEY||process.env.ORCHESTRATOR_SUPABASE_SERVICE_KEY;
const c=createClient(url,key);
(async()=>{
  const {data:dec,error:e1}=await c.from('strategic_decisions').insert({
    decision_ref:'BUG-025-VERIFY-003',
    title:'BUG-025 verify — bypass_review escape hatch',
    source:'claude_ai_session',
    challenge_status:'accepted',
    bypass_review:true,
    body:'Synthetic. Delete after verify.'
  }).select('id,announced_by_msg_id,notified_at').single();
  if(e1){console.log('INSERT ERR',e1.message);return;}
  console.log('decision row:',JSON.stringify(dec));

  const ok =
    dec.announced_by_msg_id===null &&
    dec.notified_at===null;
  console.log('CAI-TEST-003:', ok ? 'PASS' : 'FAIL — trigger fired despite bypass_review=true');
})();
"
```

Expected: `CAI-TEST-003: PASS` (announced_by_msg_id and notified_at both null).

- [ ] **Step 4: CAI-TEST-004 — state-transition dedup**

Insert with `challenge_status='challenge_window'` (trigger fires once, sets `announced_by_msg_id`), then UPDATE to `challenge_status='accepted'`. Verify NO second `agent_messages` row spawned (announced_by_msg_id stays at the original).

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && node -e "
require('dotenv').config({path:'.env'});
const {createClient}=require('@supabase/supabase-js');
const url=process.env.SUPABASE_URL||process.env.ORCHESTRATOR_SUPABASE_URL;
const key=process.env.SUPABASE_SERVICE_KEY||process.env.ORCHESTRATOR_SUPABASE_SERVICE_KEY;
const c=createClient(url,key);
(async()=>{
  const {data:dec1,error:e1}=await c.from('strategic_decisions').insert({
    decision_ref:'BUG-025-VERIFY-004',
    title:'BUG-025 verify — state-transition dedup',
    source:'claude_ai_session',
    challenge_status:'challenge_window',
    bypass_review:false,
    body:'Synthetic. Delete after verify.'
  }).select('id,announced_by_msg_id').single();
  if(e1){console.log('INSERT ERR',e1.message);return;}
  if(!dec1.announced_by_msg_id){console.log('FAIL: insert did not announce');return;}
  const firstMsgId = dec1.announced_by_msg_id;
  console.log('after INSERT — announced_by_msg_id:', firstMsgId);

  const {data:dec2,error:e2}=await c.from('strategic_decisions')
    .update({challenge_status:'accepted'})
    .eq('decision_ref','BUG-025-VERIFY-004')
    .select('id,announced_by_msg_id,challenge_status').single();
  if(e2){console.log('UPDATE ERR',e2.message);return;}
  console.log('after UPDATE — announced_by_msg_id:', dec2.announced_by_msg_id, 'status:', dec2.challenge_status);

  // Count agent_messages rows linked to this decision_ref via subject prefix
  const {data:msgs}=await c.from('agent_messages')
    .select('id,message_type,subject')
    .like('subject','BUG-025-VERIFY-004:%');

  const ok =
    dec2.announced_by_msg_id===firstMsgId &&
    msgs.length===1;
  console.log('CAI-TEST-004:', ok ? 'PASS' : 'FAIL — got '+msgs.length+' msg(s), expected 1', JSON.stringify(msgs));
})();
"
```

Expected: `CAI-TEST-004: PASS` (announced_by_msg_id unchanged, exactly one agent_message exists).

- [ ] **Step 5: Cleanup all four test rows**

Delete agent_messages first, then strategic_decisions (FK is `ON DELETE SET NULL` so order doesn't strictly matter, but agent_messages-first is clean).

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && node -e "
require('dotenv').config({path:'.env'});
const {createClient}=require('@supabase/supabase-js');
const url=process.env.SUPABASE_URL||process.env.ORCHESTRATOR_SUPABASE_URL;
const key=process.env.SUPABASE_SERVICE_KEY||process.env.ORCHESTRATOR_SUPABASE_SERVICE_KEY;
const c=createClient(url,key);
(async()=>{
  const r1=await c.from('agent_messages').delete().like('subject','BUG-025-VERIFY-%');
  console.log('agent_messages deleted:', r1.error?.message ?? 'ok');
  const r2=await c.from('strategic_decisions').delete().like('decision_ref','BUG-025-VERIFY-%');
  console.log('strategic_decisions deleted:', r2.error?.message ?? 'ok');

  // Confirm clean
  const {data:leftMsg}=await c.from('agent_messages').select('id').like('subject','BUG-025-VERIFY-%');
  const {data:leftDec}=await c.from('strategic_decisions').select('id').like('decision_ref','BUG-025-VERIFY-%');
  console.log('residual rows — agent_messages:', leftMsg?.length, 'strategic_decisions:', leftDec?.length);
})();
"
```

Expected: both `ok`, both residual counts = 0.

If either residual is non-zero: re-run the cleanup. Synthetic rows must not pollute the orchestrator DB.

- [ ] **Step 6: Mark task complete only if all 4 cases passed**

If any of CAI-TEST-001 through 004 reported FAIL, do NOT proceed to Task 4. Investigate the failure, patch, re-apply, re-run from Step 1. Do not partial-ship.

---

## Task 4: Post completion + session digest + STATUS.md

Per `feedback_session_digest.md` (every shipping session files a digest to CAI) and orchestrator CLAUDE.md (every session ends with STATUS.md update).

- [ ] **Step 1: Post completion agent_message to CAI**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && node -e "
require('dotenv').config({path:'.env'});
const {createClient}=require('@supabase/supabase-js');
const url=process.env.SUPABASE_URL||process.env.ORCHESTRATOR_SUPABASE_URL;
const key=process.env.SUPABASE_SERVICE_KEY||process.env.ORCHESTRATOR_SUPABASE_SERVICE_KEY;
const c=createClient(url,key);
(async()=>{
  const body=[
    'BUG-025 shipped. trigger_cai_decision_announce() rewritten:',
    '  - Announceable status set widened to (challenge_window, accepted)',
    '  - Message shape branches on challenge_status (review_request vs decision)',
    '  - OLD.challenge_status state-transition guard dropped (announced_by_msg_id is dedup)',
    '',
    '4-case verification matrix per CAI-RESP-040 A2:',
    '  - CAI-TEST-001 (accepted path)             PASS',
    '  - CAI-TEST-002 (challenge_window regression) PASS',
    '  - CAI-TEST-003 (bypass_review escape hatch)  PASS',
    '  - CAI-TEST-004 (state-transition dedup)      PASS',
    '',
    'Synthetic test rows hard-deleted post-verify. No residual pollution.',
    '',
    'Next P0: ARCH-035 (agent_status table + channel split per CAI-RESP-036).'
  ].join('\\n');
  const {data,error}=await c.from('agent_messages').insert({
    thread_id:'00000000-0000-0000-0000-000000000025',
    from_agent:'cc-ihsanos',
    to_agent:'cai',
    message_type:'update',
    subject:'BUG-025 shipped — accepted-path announce trigger live',
    body,
    requires_response:false
  }).select('id').single();
  if(error){console.log('ERR',error.message);return;}
  console.log('posted msg id:', data.id);
})();
"
```

- [ ] **Step 2: File session digest (per feedback_session_digest.md)**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
ORCH_SHA=$(git rev-parse --short HEAD)
SESSION_ID=$(date +%Y%m%d-%H%M%S-bug025)

node -e "
require('dotenv').config({path:'.env'});
const {createClient}=require('@supabase/supabase-js');
const url=process.env.SUPABASE_URL||process.env.ORCHESTRATOR_SUPABASE_URL;
const key=process.env.SUPABASE_SERVICE_KEY||process.env.ORCHESTRATOR_SUPABASE_SERVICE_KEY;
const c=createClient(url,key);
(async()=>{
  const digest={
    session_id:'$SESSION_ID',
    commit_sha:{orchestrator:'$ORCH_SHA'},
    deploy_url:null,
    summary:'BUG-025 shipped — trigger_cai_decision_announce() now announces accepted-path CAI decisions with branched message shape',
    files_changed:[
      'orchestrator: supabase/migrations/20260419_bug025_acceptance_path_announce.sql (new)'
    ],
    deleted_files:[],
    new_exports:[],
    schema_changes:[
      'trigger_cai_decision_announce() function body replaced via CREATE OR REPLACE — no schema change to triggers or tables'
    ],
    tests_added:0,
    tests_passing:null,
    follow_ups:[
      'ARCH-035: agent_status table + channel split (next P0)',
      'ARCH-036: priority column on narrowed agent_messages (after ARCH-035)',
      'BUG-024 Phase 1: per-agent JWT identity (separate brainstorm)',
      'LEDGER-002/003: resume implementation per spec 2026-04-19-ledger-002-003-qurban-posting-design.md'
    ]
  };
  const body=JSON.stringify(digest,null,2);
  const {data,error}=await c.from('agent_messages').insert({
    thread_id:'00000000-0000-0000-0000-000000000025',
    from_agent:'cc-ihsanos',
    to_agent:'cai',
    message_type:'update',
    subject:'Session digest: $ORCH_SHA — BUG-025 acceptance-path trigger',
    body,
    requires_response:false
  }).select('id').single();
  if(error){console.log('ERR',error.message);return;}
  console.log('digest posted msg id:', data.id);
})();
"
```

- [ ] **Step 3: Update STATUS.md (orchestrator)**

Open `/Users/sheikhmusa/wingmen/orchestrator/STATUS.md`. Append a section under the most recent dated heading (or create a new dated heading for today):

```markdown
### BUG-025 — Acceptance-path trigger (shipped 2026-04-19)

`trigger_cai_decision_announce()` rewritten via `CREATE OR REPLACE` to also fire
on `challenge_status='accepted'`, with branched message shape (review_request
for challenge_window, decision for accepted). Closes the silent gap that left
CAI-RESP-* rulings out of CC's inbox until Musa pasted them manually.

Migration: `supabase/migrations/20260419_bug025_acceptance_path_announce.sql`
Spec: `strategic_decisions.decision_ref='CAI-RESP-040'` (B1 + A1 + A2 + concession)
Verification: 4-case matrix (CAI-TEST-001 through 004) all PASS, synthetic rows hard-deleted.
Parent: BUG-020 (357a135).

Next: ARCH-035 (agent_status + channel split).
```

- [ ] **Step 4: Commit STATUS.md**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git add STATUS.md
git commit -m "chore: STATUS.md — BUG-025 shipped"
```

- [ ] **Step 5: Push**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git log --oneline -3
git push origin main
```

Expected: HEAD shows the BUG-025 migration commit (Task 1) followed by the STATUS.md commit (Task 4 Step 4).

---

## Self-review checklist (author ran before handoff)

**Spec coverage (CAI-RESP-040):**
- ✅ Concession — announce all CAI decisions regardless of challenge_status: Task 1 Step 1 (guard widened to `NOT IN ('challenge_window','accepted')`).
- ✅ B1 — message_type and subject branch on challenge_status: Task 1 Step 1 (`v_message_type`/`v_subject`/`v_requires_response` IF/ELSE block).
- ✅ A1 — drop SIMILAR TO regex: never added; `source='claude_ai_session'` is the only "from CAI" gate, documented in the migration header comment.
- ✅ A2 — 4-case verification matrix: Task 3 Steps 1–4, with explicit cleanup at Step 5.
- ✅ Forward-compat with ARCH-035 message_type CHECK: noted in migration header comment.

**Placeholder scan:** No TBDs, no "similar to task N", every step has either complete code or an exact command with expected output. ✓

**Type/name consistency:**
- `trigger_cai_decision_announce` (PG function) — consistent across Tasks 1, 2.
- `BUG-025-VERIFY-001` through `BUG-025-VERIFY-004` (synthetic decision_refs) — consistent across Task 3 Steps 1–5.
- `announced_by_msg_id`, `notified_at`, `bypass_review` (column names) — consistent with BUG-020 spec/migration.
- `v_message_type`, `v_requires_response` (PL/pgSQL locals) — declared and used consistently within Task 1's function body. ✓

Plan ready for execution.
