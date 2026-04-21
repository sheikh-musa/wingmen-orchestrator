# Deploy CC Family Runbook

**Owner:** cc-ihsanos (platform)
**Scope:** Bring a dark CC family (cc-scholar, cc-cosem, cc-web) online — dark → heartbeating in <5 minutes.
**Parent spec:** `strategic_decisions.decision_ref='TASK-045'`

---

## When to use this

- A CC family row exists in `agents` but `last_heartbeat IS NULL` (dark family).
- Jobs tagged for that family's repos are orphaning to another family (e.g., hifz-companion jobs landing on cc-ihsanos instead of cc-scholar).
- You want to add a new CC family (requires a new `agents` row first — out of scope for this runbook).

## When NOT to use this

- The family is already heartbeating. Run `scripts/launch_dangerous_cc.sh --repo <repo>` directly instead.
- You're restarting cc-ihsanos. Use `scripts/restart_orch.sh` or the standard launcher — this runbook is for **new** families.

---

## Pre-flight checklist

Run the preflight script before opening a new terminal. It catches misconfigurations without burning a session slot.

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
./scripts/deploy_cc_family.sh --dry-run cc-scholar
```

The script validates:

1. **Family ID** is one of: `cc-scholar`, `cc-cosem`, `cc-web`, `cc-ihsanos`.
2. **`agents` row** exists for the family with non-empty `repo_scope`.
3. **Repo clones** exist at `~/wingmen/projects/<repo>` for every repo in `repo_scope`.
4. **`.env`** at `/Users/sheikhmusa/wingmen/orchestrator/.env` exists and contains `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` + `DATABASE_URL`.
5. **`scripts/launch_dangerous_cc.sh`** exists and is executable.
6. **No active sibling** — no `agent_status` row for a family sibling with `last_heartbeat > now() - interval '5 min'`.

On success, the script prints the exact launcher invocation to paste into a new terminal. On failure, it exits non-zero with a specific error code (see Troubleshooting).

---

## Spin-up (two-line operator flow)

```bash
# Terminal A (orchestrator repo): preflight and get the command
./scripts/deploy_cc_family.sh --dry-run cc-scholar

# Terminal B (fresh terminal, any cwd): paste the command from Terminal A's output and run it
# Example output:
#   cd ~/wingmen/projects/ai-scholar && /Users/sheikhmusa/wingmen/orchestrator/scripts/launch_dangerous_cc.sh --repo ai-scholar
```

That's it. The launcher handles identity resolution, heartbeat registration, agent_status registration, EXIT trap, and the interactive `claude` invocation.

---

## Post-boot verification

Once the claude session is running in Terminal B and has completed its `SessionStart` hook (you'll see context injection scroll by), run these checks from Terminal A:

### 1. `agents.last_heartbeat` flipped to non-null

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
.venv/bin/python -c "
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
with psycopg.connect(os.environ['DATABASE_URL']) as c:
    with c.cursor() as cur:
        cur.execute(\"SELECT id, repo_scope, last_heartbeat, status FROM agents WHERE id = 'cc-scholar'\")
        print(cur.fetchone())
"
```

Expected: `('cc-scholar', ['ai-scholar', 'hifz-companion'], <recent timestamp>, 'active')` or similar.

**Fail mode:** `last_heartbeat` is still NULL → the launcher exited before the heartbeat loop started. Check `/tmp/cc_launch_ctx.txt` and the terminal B output for errors.

### 2. `agent_status` row appeared

```bash
.venv/bin/python -c "
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
with psycopg.connect(os.environ['DATABASE_URL']) as c:
    with c.cursor() as cur:
        cur.execute(\"SELECT agent_id, status, scope_repos, last_heartbeat FROM agent_status WHERE agent_id LIKE 'cc-scholar\\_%' ORDER BY last_heartbeat DESC LIMIT 3\")
        for r in cur.fetchall():
            print(r)
"
```

Expected: at least one row like `('cc-scholar_1', 'working', ['ai-scholar'], <recent>)`. The `_1` suffix is the sub-tag from the allocator (`scripts/lib/auto_agent_id.py::allocate_sub_tag_and_register`). Family identity is encoded in `agent_id` via the `<base>_<N>` convention — `agent_status` has no separate `base_agent_id` column.

**Fail mode:** No row → advisory-lock contention or misconfigured DSN. Check that `DATABASE_URL` in `.env` points at the same Postgres as `SUPABASE_URL` (same project).

### 3. Test job pickup (acceptance criterion 4)

Queue a dummy hifz-companion job and confirm cc-scholar claims it:

```bash
.venv/bin/python -c "
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
with psycopg.connect(os.environ['DATABASE_URL']) as c:
    with c.cursor() as cur:
        cur.execute(\"\"\"
            INSERT INTO jobs (repo, description, status, priority, spec)
            VALUES ('hifz-companion', 'TASK-045 pilot smoke test — no-op', 'queued', 3, '{}')
            RETURNING id
        \"\"\")
        job_id = cur.fetchone()[0]
        c.commit()
        print(f'queued job {job_id}')
"
```

Wait 60 seconds (next orchestrator tick), then:

```bash
.venv/bin/python -c "
import os, psycopg, sys
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
job_id = int(sys.argv[1])
with psycopg.connect(os.environ['DATABASE_URL']) as c:
    with c.cursor() as cur:
        cur.execute(\"SELECT id, repo, status, claimed_by FROM jobs WHERE id = %s\", (job_id,))
        print(cur.fetchone())
" <JOB_ID_FROM_PREVIOUS_STEP>
```

Expected: `claimed_by` is a cc-scholar sub-tag (e.g., `cc-scholar_1`), not `cc-ihsanos` or any variant.

**Fail mode:** `claimed_by = 'cc-ihsanos_N'` → scope split not honoured; check the `agents.repo_scope` for both families and the claim logic in `wingmen_orch.py`. This would be a **scope-split regression**, escalate to CAI via `agent_messages`.

### 4. Cancel the test job

```bash
.venv/bin/python -c "
import os, psycopg, sys
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
job_id = int(sys.argv[1])
with psycopg.connect(os.environ['DATABASE_URL']) as c:
    with c.cursor() as cur:
        cur.execute(\"UPDATE jobs SET status='cancelled', result_summary='TASK-045 smoke test — verified pickup' WHERE id = %s\", (job_id,))
        c.commit()
        print('cancelled')
" <JOB_ID>
```

---

## Troubleshooting

| Exit code | Meaning | Fix |
|-----------|---------|-----|
| 2 | Unknown family-id | Use one of `cc-scholar`, `cc-cosem`, `cc-web`, `cc-ihsanos` |
| 3 | `agents` row missing or `repo_scope` empty | Insert row with scope via CAI (`INSERT INTO agents (id, repo_scope, status) VALUES ('cc-<name>', ARRAY['repo1','repo2'], 'idle')`) |
| 4 | Repo clone missing | `git clone git@github.com:<org>/<repo>.git ~/wingmen/projects/<repo>` |
| 5 | `.env` missing or incomplete | Copy from `.env.example`, fill `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` / `DATABASE_URL` |
| 6 | `launch_dangerous_cc.sh` missing or non-executable | `chmod +x scripts/launch_dangerous_cc.sh` |
| 7 | Active sibling already heartbeating | Either the family is already deployed (run `scripts/launch_dangerous_cc.sh --repo <repo>` directly) or a stale session is holding the slot — check `agent_status` for rows with stale `last_heartbeat` and coordinate cleanup with CAI |

### Dark family symptoms (diagnostic)

If jobs for a repo are orphaning to the wrong family:

```sql
-- Confirm which family "owns" the repo
SELECT id, repo_scope, last_heartbeat FROM agents WHERE 'hifz-companion' = ANY(repo_scope);

-- If the row is there but last_heartbeat is NULL → family is dark, use this runbook
-- If the row is missing → scope split hasn't been applied for this repo, escalate to CAI
```

---

## Rollback

The runbook is additive — there's nothing to roll back in the database. To stop a running session, Ctrl-C in Terminal B. The EXIT trap in `launch_dangerous_cc.sh` flips `agent_status` to `offline` and `agents.status` to `idle` automatically. If the session dies without running the trap (kill -9), manual cleanup:

```bash
.venv/bin/python -c "
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
with psycopg.connect(os.environ['DATABASE_URL']) as c:
    with c.cursor() as cur:
        cur.execute(\"UPDATE agent_status SET status='offline' WHERE base_agent_id = 'cc-scholar' AND last_heartbeat < now() - interval '5 min'\")
        cur.execute(\"UPDATE agents SET status='idle' WHERE id = 'cc-scholar' AND last_heartbeat < now() - interval '5 min'\")
        c.commit()
        print('cleanup committed')
"
```

---

## Appendix: DB schema quick reference

```sql
-- agents: one row per CC family (identity + ownership)
CREATE TABLE agents (
  id              TEXT PRIMARY KEY,           -- 'cc-ihsanos', 'cc-scholar', ...
  repo_scope      TEXT[] NOT NULL,            -- repos this family owns
  status          TEXT,                       -- 'idle', 'active', 'offline'
  last_heartbeat  TIMESTAMPTZ,                -- flipped to non-null on first heartbeat
  ...
);

-- agent_status: one row per live session (sub-tag granularity)
CREATE TABLE agent_status (
  agent_id        TEXT PRIMARY KEY,           -- 'cc-scholar_1', 'cc-ihsanos_3', ...
  base_agent_id   TEXT REFERENCES agents(id), -- 'cc-scholar'
  sub_tag         INT NOT NULL,               -- 1..20, allocated by auto_agent_id.py
  repo_scope      TEXT,                       -- single repo for this session (worktree)
  status          TEXT,                       -- 'online', 'offline'
  last_heartbeat  TIMESTAMPTZ,
  ...
);
```

Full schema: `supabase/migrations/` (grep for `CREATE TABLE agents` / `CREATE TABLE agent_status`).
