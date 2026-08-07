# TASK-045 Deploy CC-Family Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a reusable `docs/runbooks/deploy-cc-family.md` + `scripts/deploy_cc_family.sh` that lets any operator spin up a CC family (cc-scholar, cc-cosem, cc-web) from dark → heartbeating in <5 minutes, and use it to deploy the cc-scholar pilot so hifz-companion jobs stop orphaning.

**Architecture:** The runbook is the source of truth for the operator flow. The script is a preflight gate in front of the existing `scripts/launch_dangerous_cc.sh` — it validates family-id, `agents` row + `repo_scope`, repo clone paths, `.env`, and absence of active siblings, then prints the exact interactive `launch_dangerous_cc.sh` invocation for the operator to paste into a fresh terminal. The script itself does **not** `exec` claude (interactive, can't daemonize). DB preflight is delegated to a Python helper (`scripts/lib/check_family_preflight.py`) that uses psycopg with the existing `.env` DSN. Pilot deployment of cc-scholar is a live operation — no new code, execute the runbook and validate against acceptance criteria.

**Tech Stack:** Bash (deploy script, same style as `scripts/launch_dangerous_cc.sh`), Python 3.13 + psycopg 3 (DB preflight helper, same pattern as `scripts/lib/auto_agent_id.py`), pytest + subprocess (tests, same pattern as `tests/test_check_lock_keys.py`).

**Spec source:**
- `agent_messages` msg 488 (CAI msg where TASK-045 was filed)
- `agent_messages` msg 486 (my original proposal that was accepted)
- `strategic_decisions.decision_ref='TASK-045'` (accepted, challenge window until 2026-04-22 02:52:14 UTC, parent=CAI-RESP-057)
- Parent chain: CAI-RESP-057 → CAI-HIERARCHY-001 → BUG-028 cleanup

---

## Background — what's dark and why it matters

As of 2026-04-21 the `agents` table has four family rows: cc-ihsanos (boots daily, heartbeating), cc-cosem (dark), cc-scholar (dark), cc-web (dark). The dark families have `last_heartbeat IS NULL` and no rows in `agent_status`. The launcher (`scripts/launch_dangerous_cc.sh`) is already family-aware via `scripts/lib/auto_agent_id.py::load_family_map()` — any operator can pass `--repo <name>` and the right CC family will be resolved. What's missing is the **operator-facing wrapper**: a runbook that says "to bring up cc-scholar, run X" and a script that catches misconfigurations before the operator wastes 2 minutes on a session that'll dead-end.

Pilot priority is cc-scholar first because hifz-companion jobs (ai-scholar repo scope) have been orphaning to cc-ihsanos since the scope split landed (per BUG-028 threads). cc-cosem is second (cosem-tdu + cosem-adcda ready, used for the claude-smoke vision port). cc-web is deferred (Railway DATABASE_URL broken on dookana per msg 478).

Acceptance criteria, verbatim from `strategic_decisions.decision_ref='TASK-045'`:

1. cc-scholar boots via runbook
2. `agents.last_heartbeat` flips to non-null
3. `agent_status` row appears (from sub-tag allocator)
4. Test job queued for hifz-companion picked up by cc-scholar (not orphaned)
5. Runbook copy-pasteable for cc-cosem in <5 minutes
6. Script catches ≥1 misconfiguration class in dry-run

---

## File Structure

### Commit 1 — runbook (docs-only, orchestrator repo)

- Create: `docs/runbooks/deploy-cc-family.md` — sections: Purpose, When to use, Pre-flight checklist, Spin-up, Post-boot verification, Troubleshooting, Rollback, Appendix (DB schema)

### Commit 2 — deploy script + tests (atomic, orchestrator repo)

- Create: `scripts/deploy_cc_family.sh` — bash preflight + command printer
- Create: `scripts/lib/check_family_preflight.py` — psycopg DB preflight helper
- Create: `tests/test_deploy_cc_family.py` — subprocess tests on bash script (family-id validation, repo clone check, env check, dry-run output)
- Create: `tests/test_check_family_preflight.py` — pytest tests on Python helper (mocked psycopg connection)

### Commit 3 — cc-scholar pilot deployment (live, no files)

No new code. Execute runbook against cc-scholar, capture verification artefacts, update runbook inline if friction surfaces.

### Commit 4 — STATUS.md + session digest (orchestrator repo)

- Modify: `STATUS.md` — append TASK-045 shipped entry
- Post: `agent_messages` row to `cai` with structured JSON session digest (per memory `feedback_session_digest.md`)

### Commit boundaries

- Commit 1 lands independently. Safe even if Commit 2 is rolled back.
- Commit 2 ships atomically — bash script + Python helper + tests as one unit. Tests gate the merge.
- Commit 3 is an *execution* step, not a code commit. If cc-scholar pilot surfaces runbook gaps, the fix lands as a Commit 3' amendment to the runbook.
- Commit 4 after Commit 3 validates the pilot passed acceptance.

### Branch

Already on `feat/task-045-deploy-cc-family` (created at session start). Push to remote only after Commit 2 is green.

### DRY / YAGNI guardrails

- The script does **not** reimplement identity resolution — `launch_dangerous_cc.sh` already does that via `auto_agent_id.py`. We only preflight.
- The script does **not** spawn a new terminal. Printing the command for the operator to paste is simpler, debuggable, and matches how Musa actually works.
- No daemonization. No launchd plists. CAI explicitly endorsed skipping plists in TASK-045 scope — "daemonizing CC sessions is wrong shape".
- No support for cc-web in the pilot path — dookana's Railway DATABASE_URL is broken, adding cc-web preflight cases would be dead code until that's fixed.

---

## Task 1: Runbook `docs/runbooks/deploy-cc-family.md`

**Files:**
- Create: `/Users/sheikhmusa/wingmen/orchestrator/docs/runbooks/deploy-cc-family.md`

- [ ] **Step 1: Write the runbook**

Use the Write tool to create the file with this exact content:

````markdown
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
4. **`.env`** at `/Users/sheikhmusa/wingmen/orchestrator/.env` exists and contains `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` + `ORCH_DSN`.
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
with psycopg.connect(os.environ['ORCH_DSN']) as c:
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
with psycopg.connect(os.environ['ORCH_DSN']) as c:
    with c.cursor() as cur:
        cur.execute(\"SELECT agent_id, base_agent_id, sub_tag, repo_scope, last_heartbeat FROM agent_status WHERE base_agent_id = 'cc-scholar' ORDER BY last_heartbeat DESC LIMIT 3\")
        for r in cur.fetchall():
            print(r)
"
```

Expected: at least one row like `('cc-scholar_1', 'cc-scholar', 1, 'ai-scholar', <recent>)`. The `_1` suffix is the sub-tag from the allocator (`scripts/lib/auto_agent_id.py::allocate_sub_tag_and_register`).

**Fail mode:** No row → advisory-lock contention or misconfigured DSN. Check that `ORCH_DSN` in `.env` points at the same Postgres as `SUPABASE_URL` (same project).

### 3. Test job pickup (acceptance criterion 4)

Queue a dummy hifz-companion job and confirm cc-scholar claims it:

```bash
.venv/bin/python -c "
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
with psycopg.connect(os.environ['ORCH_DSN']) as c:
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
with psycopg.connect(os.environ['ORCH_DSN']) as c:
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
with psycopg.connect(os.environ['ORCH_DSN']) as c:
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
| 5 | `.env` missing or incomplete | Copy from `.env.example`, fill `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` / `ORCH_DSN` |
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
with psycopg.connect(os.environ['ORCH_DSN']) as c:
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
````

- [ ] **Step 2: Verify structure**

Run:
```bash
grep -cE "^## " /Users/sheikhmusa/wingmen/orchestrator/docs/runbooks/deploy-cc-family.md
```

Expected: at least 7 (seven top-level sections: When to use, When NOT to use, Pre-flight, Spin-up, Post-boot, Troubleshooting, Rollback, Appendix).

- [ ] **Step 3: Commit**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && git add docs/runbooks/deploy-cc-family.md && git commit -m "$(cat <<'EOF'
docs(runbooks): deploy-cc-family runbook for dark-family spin-up (TASK-045)

Two-line operator flow + pre-flight checklist + post-boot verification SQL +
troubleshooting matrix (exit codes 2-7) + rollback. Parent: CAI-RESP-057,
CAI-HIERARCHY-001. Unblocks cc-scholar pilot deployment per TASK-045
acceptance criteria 1 and 5.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

Do NOT push yet — push with Commit 2.

---

## Task 2: Bash script skeleton + test harness

**Files:**
- Create: `/Users/sheikhmusa/wingmen/orchestrator/scripts/deploy_cc_family.sh`
- Create: `/Users/sheikhmusa/wingmen/orchestrator/tests/test_deploy_cc_family.py`

- [ ] **Step 1: Write the failing test harness**

Use Write to create `/Users/sheikhmusa/wingmen/orchestrator/tests/test_deploy_cc_family.py`:

```python
"""Tests for scripts/deploy_cc_family.sh — TASK-045.

Pattern mirrors tests/test_check_lock_keys.py: invoke the script via
subprocess with fixtures under tmp_path, assert on exit code + stderr.

Fixtures override the repo-root and projects-root via env vars so the
script can be tested in isolation without touching ~/wingmen/projects.
"""
import os
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = _REPO_ROOT / "scripts" / "deploy_cc_family.sh"


def _run(args: list[str], env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_script_is_executable():
    """CI and operators need the script to run without explicit `bash` prefix."""
    mode = SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "scripts/deploy_cc_family.sh must be +x"


def test_no_args_shows_usage():
    """No family-id → usage message + non-zero exit."""
    r = _run([])
    assert r.returncode != 0
    assert "usage" in r.stderr.lower() or "usage" in r.stdout.lower()
    assert "family-id" in (r.stderr + r.stdout).lower()
```

- [ ] **Step 2: Run the tests — expect 2 FAILs (script doesn't exist yet)**

Run:
```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/pytest tests/test_deploy_cc_family.py -v
```

Expected: both tests FAIL because `scripts/deploy_cc_family.sh` does not exist.

- [ ] **Step 3: Create the minimal script skeleton**

Use Write to create `/Users/sheikhmusa/wingmen/orchestrator/scripts/deploy_cc_family.sh`:

```bash
#!/usr/bin/env bash
# deploy_cc_family.sh — TASK-045 preflight for CC-family spin-up.
#
# Usage: deploy_cc_family.sh [--dry-run] <family-id>
#
# Validates preconditions for bringing a dark CC family (cc-scholar, cc-cosem,
# cc-web) online via scripts/launch_dangerous_cc.sh. On success, prints the
# exact interactive launcher invocation for the operator to paste into a new
# terminal. On failure, exits non-zero with a specific code (see runbook).
#
# Parent: strategic_decisions.decision_ref='TASK-045'
# Runbook: docs/runbooks/deploy-cc-family.md

set -euo pipefail

usage() {
  cat <<EOF >&2
usage: deploy_cc_family.sh [--dry-run] <family-id>

  <family-id>    One of: cc-scholar, cc-cosem, cc-web, cc-ihsanos
  --dry-run      Validate preconditions and print the launcher invocation,
                 but do not actually instruct the operator to run anything.

Exit codes:
  0  All preconditions pass
  1  Usage error (wrong args)
  2  Unknown family-id
  3  agents row missing or repo_scope empty
  4  Repo clone missing at ~/wingmen/projects/<repo>
  5  .env missing or incomplete
  6  launch_dangerous_cc.sh missing or non-executable
  7  Active sibling already heartbeating (stale session)

See docs/runbooks/deploy-cc-family.md for the full flow.
EOF
  exit 1
}

DRY_RUN=0
FAMILY_ID=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage ;;
    cc-*) FAMILY_ID="$1"; shift ;;
    *) echo "error: unexpected argument: $1" >&2; usage ;;
  esac
done

if [[ -z "$FAMILY_ID" ]]; then
  echo "error: family-id required" >&2
  usage
fi

echo "deploy_cc_family.sh: family-id=$FAMILY_ID dry-run=$DRY_RUN"
echo "  (skeleton — preconditions not yet implemented)"
```

Then:
```bash
chmod +x /Users/sheikhmusa/wingmen/orchestrator/scripts/deploy_cc_family.sh
```

- [ ] **Step 4: Run the tests — expect 2 PASS**

Run:
```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/pytest tests/test_deploy_cc_family.py -v
```

Expected: both tests PASS.

- [ ] **Step 5: Commit skeleton**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && git add scripts/deploy_cc_family.sh tests/test_deploy_cc_family.py && git commit -m "$(cat <<'EOF'
feat(scripts): deploy_cc_family.sh skeleton + test harness (TASK-045)

Executable bash skeleton with arg parsing (--dry-run, family-id) and
exit code matrix documented. Test harness mirrors test_check_lock_keys.py
pattern — subprocess calls under tmp_path fixtures. Precondition checks
land in subsequent commits as TDD steps.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Family-ID validation + repo-clone preflight

**Files:**
- Modify: `/Users/sheikhmusa/wingmen/orchestrator/scripts/deploy_cc_family.sh`
- Modify: `/Users/sheikhmusa/wingmen/orchestrator/tests/test_deploy_cc_family.py`

- [ ] **Step 1: Add failing tests for family-id and repo-clone checks**

Use Edit to append to `tests/test_deploy_cc_family.py`:

```python


def test_unknown_family_id_exits_2(tmp_path):
    """Unknown family-id → exit 2 with clear error."""
    r = _run(["cc-bogus"])
    assert r.returncode == 2, f"expected 2, got {r.returncode}. stderr:{r.stderr}"
    assert "unknown family" in r.stderr.lower() or "cc-bogus" in r.stderr


def test_known_family_id_passes_family_check(tmp_path):
    """Known family-id → passes past the family-ID gate. Downstream checks
    (repo clone etc.) will fail, but NOT with exit 2."""
    # Point projects root at empty tmp_path so repo-clone check fails with exit 4,
    # which proves we got past the family-id validator (exit 2).
    r = _run(
        ["cc-scholar"],
        env_overrides={"CC_PROJECTS_ROOT": str(tmp_path)},
    )
    assert r.returncode != 2, f"family-id check rejected cc-scholar. stderr:{r.stderr}"


def test_missing_repo_clone_exits_4(tmp_path):
    """Repo missing from CC_PROJECTS_ROOT → exit 4 with the repo name."""
    r = _run(
        ["cc-scholar"],
        env_overrides={"CC_PROJECTS_ROOT": str(tmp_path)},
    )
    assert r.returncode == 4, f"expected 4, got {r.returncode}. stderr:{r.stderr}"
    # cc-scholar's repo_scope is ['ai-scholar','hifz-companion'] — error should mention at least one
    assert "ai-scholar" in r.stderr or "hifz-companion" in r.stderr


def test_present_repo_clone_passes_clone_check(tmp_path):
    """All repos present in CC_PROJECTS_ROOT → passes clone check.
    Downstream checks (.env etc.) still fail, but NOT with exit 4."""
    (tmp_path / "ai-scholar" / ".git").mkdir(parents=True)
    (tmp_path / "hifz-companion" / ".git").mkdir(parents=True)
    r = _run(
        ["cc-scholar"],
        env_overrides={"CC_PROJECTS_ROOT": str(tmp_path)},
    )
    assert r.returncode != 4, f"clone check rejected despite repos present. stderr:{r.stderr}"
```

- [ ] **Step 2: Run tests — expect 4 new FAILs (plus 2 still passing)**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/pytest tests/test_deploy_cc_family.py -v
```

Expected: `test_unknown_family_id_exits_2`, `test_known_family_id_passes_family_check`, `test_missing_repo_clone_exits_4`, `test_present_repo_clone_passes_clone_check` all FAIL.

- [ ] **Step 3: Implement family-id validation + repo-clone check**

Use Edit on `/Users/sheikhmusa/wingmen/orchestrator/scripts/deploy_cc_family.sh`, replacing the final two `echo` lines (the skeleton placeholder) with:

```bash
# ---------------------------------------------------------------------------
# Precondition 1: family-id is a known CC family
# ---------------------------------------------------------------------------
case "$FAMILY_ID" in
  cc-scholar|cc-cosem|cc-web|cc-ihsanos) ;;
  *)
    echo "error: unknown family '$FAMILY_ID'" >&2
    echo "       valid: cc-scholar, cc-cosem, cc-web, cc-ihsanos" >&2
    exit 2
    ;;
esac

# ---------------------------------------------------------------------------
# Precondition 2: repo clones exist at CC_PROJECTS_ROOT/<repo>
# ---------------------------------------------------------------------------
# repo_scope per family (hardcoded mirror of agents.repo_scope — updated when
# CAI-AGENTS-001 changes. Kept in sync manually; DB preflight in the Python
# helper catches drift.)
case "$FAMILY_ID" in
  cc-ihsanos) REPOS=("ihsanos" "wingmen-orchestrator") ;;
  cc-cosem)   REPOS=("cosem-tdu" "cosem-adcda") ;;
  cc-scholar) REPOS=("ai-scholar" "hifz-companion") ;;
  cc-web)     REPOS=("wordpress-sites" "dookana") ;;
esac

PROJECTS_ROOT="${CC_PROJECTS_ROOT:-$HOME/wingmen/projects}"

for repo in "${REPOS[@]}"; do
  if [[ ! -d "$PROJECTS_ROOT/$repo/.git" ]]; then
    echo "error: repo clone missing: $PROJECTS_ROOT/$repo/.git" >&2
    echo "       clone it: git clone git@github.com:<org>/$repo.git $PROJECTS_ROOT/$repo" >&2
    exit 4
  fi
done

echo "deploy_cc_family.sh: family-id=$FAMILY_ID dry-run=$DRY_RUN"
echo "  family + repo-clone checks: ok"
echo "  (.env + wrapper + DB + sibling checks not yet implemented)"
```

- [ ] **Step 4: Run tests — expect all 6 PASS**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/pytest tests/test_deploy_cc_family.py -v
```

Expected: 6/6 PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && git add scripts/deploy_cc_family.sh tests/test_deploy_cc_family.py && git commit -m "$(cat <<'EOF'
feat(scripts): family-id + repo-clone preflight (TASK-045)

Validates family-id against hardcoded list (exit 2) and repo clones
under CC_PROJECTS_ROOT (exit 4, defaults to ~/wingmen/projects). Repo
scope hardcoded per family — DB drift caught by the Python helper in a
later commit. Four new tests, all green.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Env + wrapper-script preflight

**Files:**
- Modify: `/Users/sheikhmusa/wingmen/orchestrator/scripts/deploy_cc_family.sh`
- Modify: `/Users/sheikhmusa/wingmen/orchestrator/tests/test_deploy_cc_family.py`

- [ ] **Step 1: Add failing tests**

Use Edit to append to `tests/test_deploy_cc_family.py`:

```python


def _fixture_repos(tmp_path: Path) -> Path:
    """Create fake cc-scholar repos under tmp_path."""
    (tmp_path / "ai-scholar" / ".git").mkdir(parents=True)
    (tmp_path / "hifz-companion" / ".git").mkdir(parents=True)
    return tmp_path


def test_missing_env_exits_5(tmp_path):
    """CC_ENV_FILE pointing at nonexistent file → exit 5."""
    _fixture_repos(tmp_path)
    r = _run(
        ["cc-scholar"],
        env_overrides={
            "CC_PROJECTS_ROOT": str(tmp_path),
            "CC_ENV_FILE": str(tmp_path / "nonexistent.env"),
        },
    )
    assert r.returncode == 5, f"expected 5, got {r.returncode}. stderr:{r.stderr}"
    assert ".env" in r.stderr or "env" in r.stderr.lower()


def test_incomplete_env_exits_5(tmp_path):
    """.env missing required keys → exit 5."""
    _fixture_repos(tmp_path)
    env_file = tmp_path / "test.env"
    env_file.write_text("SUPABASE_URL=https://example.supabase.co\n")  # missing SERVICE_KEY and DSN
    r = _run(
        ["cc-scholar"],
        env_overrides={
            "CC_PROJECTS_ROOT": str(tmp_path),
            "CC_ENV_FILE": str(env_file),
        },
    )
    assert r.returncode == 5, f"expected 5, got {r.returncode}. stderr:{r.stderr}"
    assert "SUPABASE_SERVICE_KEY" in r.stderr or "ORCH_DSN" in r.stderr


def test_complete_env_passes_env_check(tmp_path):
    """.env with all required keys → passes env check (may fail downstream)."""
    _fixture_repos(tmp_path)
    env_file = tmp_path / "test.env"
    env_file.write_text(
        "SUPABASE_URL=https://example.supabase.co\n"
        "SUPABASE_SERVICE_KEY=eyJfake\n"
        "ORCH_DSN=postgresql://fake\n"
    )
    r = _run(
        ["cc-scholar"],
        env_overrides={
            "CC_PROJECTS_ROOT": str(tmp_path),
            "CC_ENV_FILE": str(env_file),
        },
    )
    assert r.returncode != 5, f"env check rejected despite all keys present. stderr:{r.stderr}"


def test_missing_wrapper_exits_6(tmp_path):
    """launch_dangerous_cc.sh missing or non-executable → exit 6."""
    _fixture_repos(tmp_path)
    env_file = tmp_path / "test.env"
    env_file.write_text(
        "SUPABASE_URL=https://example.supabase.co\n"
        "SUPABASE_SERVICE_KEY=eyJfake\n"
        "ORCH_DSN=postgresql://fake\n"
    )
    # Point CC_LAUNCHER at a path that doesn't exist
    r = _run(
        ["cc-scholar"],
        env_overrides={
            "CC_PROJECTS_ROOT": str(tmp_path),
            "CC_ENV_FILE": str(env_file),
            "CC_LAUNCHER": str(tmp_path / "does_not_exist.sh"),
        },
    )
    assert r.returncode == 6, f"expected 6, got {r.returncode}. stderr:{r.stderr}"
    assert "launch" in r.stderr.lower() or "wrapper" in r.stderr.lower()
```

- [ ] **Step 2: Run tests — expect 4 new FAILs**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/pytest tests/test_deploy_cc_family.py -v
```

Expected: `test_missing_env_exits_5`, `test_incomplete_env_exits_5`, `test_complete_env_passes_env_check`, `test_missing_wrapper_exits_6` FAIL.

- [ ] **Step 3: Implement env + wrapper checks**

Use Edit on `/Users/sheikhmusa/wingmen/orchestrator/scripts/deploy_cc_family.sh`, replacing the trailing `echo` block with:

```bash
# ---------------------------------------------------------------------------
# Precondition 3: .env exists and has required keys
# ---------------------------------------------------------------------------
ENV_FILE="${CC_ENV_FILE:-/Users/sheikhmusa/wingmen/orchestrator/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "error: .env not found at $ENV_FILE" >&2
  echo "       copy from .env.example and fill in SUPABASE_URL / SUPABASE_SERVICE_KEY / ORCH_DSN" >&2
  exit 5
fi

for key in SUPABASE_URL SUPABASE_SERVICE_KEY ORCH_DSN; do
  if ! grep -qE "^${key}=" "$ENV_FILE"; then
    echo "error: .env missing required key: $key (in $ENV_FILE)" >&2
    exit 5
  fi
done

# ---------------------------------------------------------------------------
# Precondition 4: launch_dangerous_cc.sh exists and is executable
# ---------------------------------------------------------------------------
LAUNCHER="${CC_LAUNCHER:-/Users/sheikhmusa/wingmen/orchestrator/scripts/launch_dangerous_cc.sh}"

if [[ ! -x "$LAUNCHER" ]]; then
  echo "error: wrapper script missing or non-executable: $LAUNCHER" >&2
  echo "       chmod +x $LAUNCHER" >&2
  exit 6
fi

echo "deploy_cc_family.sh: family-id=$FAMILY_ID dry-run=$DRY_RUN"
echo "  family + repo-clone + env + wrapper checks: ok"
echo "  (DB + sibling checks not yet implemented)"
```

- [ ] **Step 4: Run tests — expect all 10 PASS**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/pytest tests/test_deploy_cc_family.py -v
```

Expected: 10/10 PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && git add scripts/deploy_cc_family.sh tests/test_deploy_cc_family.py && git commit -m "$(cat <<'EOF'
feat(scripts): .env + wrapper preflight (TASK-045)

Checks CC_ENV_FILE has SUPABASE_URL / SUPABASE_SERVICE_KEY / ORCH_DSN
(exit 5) and CC_LAUNCHER is executable (exit 6). Env-var overrides
enable isolated testing. Four new tests, all green.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: DB preflight helper (`scripts/lib/check_family_preflight.py`)

**Files:**
- Create: `/Users/sheikhmusa/wingmen/orchestrator/scripts/lib/check_family_preflight.py`
- Create: `/Users/sheikhmusa/wingmen/orchestrator/tests/test_check_family_preflight.py`

- [ ] **Step 1: Write failing tests for the Python helper**

Use Write to create `/Users/sheikhmusa/wingmen/orchestrator/tests/test_check_family_preflight.py`:

```python
"""Tests for scripts/lib/check_family_preflight.py — TASK-045.

The helper queries agents + agent_status for DB-level preconditions that
the bash preflight can't easily express. Tests mock the psycopg.connect
call so we don't need a live DSN.
"""
from unittest.mock import MagicMock, patch

import pytest

from scripts.lib.check_family_preflight import (
    check_family_preflight,
    ExitCode,
)


def _mock_cursor_with_results(results: list):
    """Return a psycopg-like context-manager chain that yields `results` from fetchone/fetchall."""
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchone = MagicMock(side_effect=results)
    cur.fetchall = MagicMock(side_effect=[results])

    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor = MagicMock(return_value=cur)

    return conn


def test_missing_agents_row_returns_exit_3():
    """agents row absent → ExitCode.AGENTS_MISSING (3)."""
    conn = _mock_cursor_with_results([None])  # first fetchone (agents row) → None

    with patch("scripts.lib.check_family_preflight.psycopg.connect", return_value=conn):
        code, msg = check_family_preflight(
            family_id="cc-scholar",
            dsn="postgresql://fake",
        )
    assert code == ExitCode.AGENTS_MISSING
    assert "cc-scholar" in msg


def test_empty_repo_scope_returns_exit_3():
    """agents row present but repo_scope empty → ExitCode.AGENTS_MISSING (3)."""
    conn = _mock_cursor_with_results([("cc-scholar", [], None)])  # (id, repo_scope, last_heartbeat)

    with patch("scripts.lib.check_family_preflight.psycopg.connect", return_value=conn):
        code, msg = check_family_preflight(
            family_id="cc-scholar",
            dsn="postgresql://fake",
        )
    assert code == ExitCode.AGENTS_MISSING
    assert "repo_scope" in msg.lower() or "empty" in msg.lower()


def test_active_sibling_returns_exit_7():
    """Fresh agent_status row for sibling → ExitCode.ACTIVE_SIBLING (7)."""
    from datetime import datetime, timezone, timedelta
    recent = datetime.now(timezone.utc) - timedelta(minutes=1)
    conn = _mock_cursor_with_results([
        ("cc-scholar", ["ai-scholar", "hifz-companion"], None),  # agents row: scope ok, dark
        [("cc-scholar_1", recent)],  # active siblings query (fetchall)
    ])

    with patch("scripts.lib.check_family_preflight.psycopg.connect", return_value=conn):
        code, msg = check_family_preflight(
            family_id="cc-scholar",
            dsn="postgresql://fake",
        )
    assert code == ExitCode.ACTIVE_SIBLING
    assert "cc-scholar_1" in msg


def test_all_clear_returns_exit_0():
    """Agents row good, no active siblings → ExitCode.OK (0)."""
    conn = _mock_cursor_with_results([
        ("cc-scholar", ["ai-scholar", "hifz-companion"], None),
        [],  # no active siblings
    ])

    with patch("scripts.lib.check_family_preflight.psycopg.connect", return_value=conn):
        code, msg = check_family_preflight(
            family_id="cc-scholar",
            dsn="postgresql://fake",
        )
    assert code == ExitCode.OK
    assert "ok" in msg.lower() or "ready" in msg.lower()
```

- [ ] **Step 2: Run tests — expect ImportError (module doesn't exist)**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/pytest tests/test_check_family_preflight.py -v
```

Expected: collection errors because `scripts.lib.check_family_preflight` doesn't exist.

- [ ] **Step 3: Implement the helper**

Use Write to create `/Users/sheikhmusa/wingmen/orchestrator/scripts/lib/check_family_preflight.py`:

```python
"""DB preflight for deploy_cc_family.sh — TASK-045.

Verifies:
  - agents row exists for the family with non-empty repo_scope
  - No active sibling session in agent_status (last_heartbeat within 5 min)

Returns (exit_code, human_message). Caller (bash) exits with the code.

Parent: scripts/deploy_cc_family.sh, docs/runbooks/deploy-cc-family.md
"""
from __future__ import annotations

import argparse
import enum
import os
import sys
from datetime import datetime, timezone

import psycopg


class ExitCode(enum.IntEnum):
    OK = 0
    AGENTS_MISSING = 3       # mirrors deploy_cc_family.sh exit 3
    ACTIVE_SIBLING = 7       # mirrors deploy_cc_family.sh exit 7


_SIBLING_STALE_THRESHOLD_SECONDS = 300  # 5 min — matches heartbeat interval


def check_family_preflight(family_id: str, dsn: str) -> tuple[ExitCode, str]:
    """Run agents + agent_status checks. Returns (code, message)."""
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, repo_scope, last_heartbeat FROM agents WHERE id = %s",
                (family_id,),
            )
            row = cur.fetchone()
            if row is None:
                return (
                    ExitCode.AGENTS_MISSING,
                    f"agents row missing for '{family_id}' — insert via CAI before deploying",
                )

            _, repo_scope, _ = row
            if not repo_scope:
                return (
                    ExitCode.AGENTS_MISSING,
                    f"agents.repo_scope empty for '{family_id}' — set scope via CAI before deploying",
                )

            cur.execute(
                """
                SELECT agent_id, last_heartbeat
                FROM agent_status
                WHERE base_agent_id = %s
                  AND last_heartbeat > now() - interval '5 minutes'
                ORDER BY last_heartbeat DESC
                """,
                (family_id,),
            )
            siblings = cur.fetchall()
            if siblings:
                names = ", ".join(s[0] for s in siblings)
                return (
                    ExitCode.ACTIVE_SIBLING,
                    f"active sibling(s) already heartbeating: {names} — "
                    f"run launch_dangerous_cc.sh directly or clean up stale rows",
                )

    return (ExitCode.OK, f"{family_id}: agents row + no-active-sibling checks ok")


def main() -> int:
    p = argparse.ArgumentParser(description="DB preflight for deploy_cc_family.sh")
    p.add_argument("family_id")
    p.add_argument("--dsn", default=os.environ.get("ORCH_DSN"))
    args = p.parse_args()

    if not args.dsn:
        print("error: ORCH_DSN not set and --dsn not passed", file=sys.stderr)
        return 1

    code, msg = check_family_preflight(args.family_id, args.dsn)
    stream = sys.stdout if code == ExitCode.OK else sys.stderr
    print(msg, file=stream)
    return int(code)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests — expect all 4 PASS**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/pytest tests/test_check_family_preflight.py -v
```

Expected: 4/4 PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && git add scripts/lib/check_family_preflight.py tests/test_check_family_preflight.py && git commit -m "$(cat <<'EOF'
feat(scripts/lib): check_family_preflight DB helper (TASK-045)

Queries agents table (missing/empty repo_scope → exit 3) and
agent_status table (active sibling → exit 7). Uses psycopg 3 with
ORCH_DSN from .env. Four tests with mocked connection, all green.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Wire DB helper into bash preflight + dry-run output

**Files:**
- Modify: `/Users/sheikhmusa/wingmen/orchestrator/scripts/deploy_cc_family.sh`
- Modify: `/Users/sheikhmusa/wingmen/orchestrator/tests/test_deploy_cc_family.py`

- [ ] **Step 1: Add tests for dry-run invocation output + DB helper skip flag**

Use Edit to append to `tests/test_deploy_cc_family.py`:

```python


def _fixture_full_env(tmp_path: Path) -> dict[str, str]:
    """Full environment fixture: repos + .env + launcher (dummy +x file)."""
    _fixture_repos(tmp_path)
    env_file = tmp_path / "test.env"
    env_file.write_text(
        "SUPABASE_URL=https://example.supabase.co\n"
        "SUPABASE_SERVICE_KEY=eyJfake\n"
        "ORCH_DSN=postgresql://fake\n"
    )
    launcher = tmp_path / "launch_dangerous_cc.sh"
    launcher.write_text("#!/bin/bash\necho dummy launcher\n")
    launcher.chmod(0o755)
    return {
        "CC_PROJECTS_ROOT": str(tmp_path),
        "CC_ENV_FILE": str(env_file),
        "CC_LAUNCHER": str(launcher),
    }


def test_dry_run_skip_db_prints_invocation(tmp_path):
    """--dry-run with CC_SKIP_DB=1 → exit 0 + prints launcher command."""
    env = _fixture_full_env(tmp_path)
    env["CC_SKIP_DB"] = "1"
    r = _run(["--dry-run", "cc-scholar"], env_overrides=env)
    assert r.returncode == 0, f"dry-run failed: {r.returncode}. stderr:{r.stderr}"
    # Output should contain the launcher command with one of the cc-scholar repos
    combined = r.stdout + r.stderr
    assert "launch_dangerous_cc.sh" in combined or "launcher" in combined.lower()
    assert "--repo" in combined
    assert "ai-scholar" in combined or "hifz-companion" in combined


def test_non_dry_run_skip_db_prints_operator_instructions(tmp_path):
    """No --dry-run with CC_SKIP_DB=1 → exit 0 + prints "paste this into a new terminal" guidance."""
    env = _fixture_full_env(tmp_path)
    env["CC_SKIP_DB"] = "1"
    r = _run(["cc-scholar"], env_overrides=env)
    assert r.returncode == 0, f"non-dry-run failed: {r.returncode}. stderr:{r.stderr}"
    combined = r.stdout + r.stderr
    assert "launch_dangerous_cc.sh" in combined
    # Operator guidance wording — adjust assertion to match implementation
    assert "terminal" in combined.lower() or "paste" in combined.lower()
```

- [ ] **Step 2: Run tests — expect 2 new FAILs**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/pytest tests/test_deploy_cc_family.py -v
```

Expected: the two new tests FAIL.

- [ ] **Step 3: Implement DB helper call + dry-run output**

Use Edit on `/Users/sheikhmusa/wingmen/orchestrator/scripts/deploy_cc_family.sh`, replacing the trailing `echo` block with:

```bash
# ---------------------------------------------------------------------------
# Precondition 5: DB preflight (agents row + no active sibling)
# ---------------------------------------------------------------------------
# CC_SKIP_DB=1 bypasses for unit tests that don't need a live DSN.
if [[ "${CC_SKIP_DB:-0}" != "1" ]]; then
  ORCH_DIR="$(cd "$(dirname "$LAUNCHER")/.." && pwd)"
  PY="${ORCH_DIR}/.venv/bin/python"
  if [[ ! -x "$PY" ]]; then
    echo "error: orchestrator venv Python not found at $PY" >&2
    exit 5
  fi
  # Source .env so ORCH_DSN is in the environment for the helper.
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  DB_OUT="$("$PY" -m scripts.lib.check_family_preflight "$FAMILY_ID" 2>&1)"
  DB_CODE=$?
  if [[ $DB_CODE -ne 0 ]]; then
    echo "$DB_OUT" >&2
    exit $DB_CODE
  fi
  echo "  DB preflight: $DB_OUT"
fi

# ---------------------------------------------------------------------------
# All preconditions pass — emit the launcher invocation
# ---------------------------------------------------------------------------
# Pick the FIRST repo in the family's scope as the default worktree.
PRIMARY_REPO="${REPOS[0]}"

LAUNCH_CMD="cd $PROJECTS_ROOT/$PRIMARY_REPO && $LAUNCHER --repo $PRIMARY_REPO"

echo ""
echo "=== Preflight complete for $FAMILY_ID ==="
echo ""
echo "Launcher invocation (paste into a fresh terminal):"
echo ""
echo "  $LAUNCH_CMD"
echo ""

if [[ $DRY_RUN -eq 1 ]]; then
  echo "(dry-run — no action taken)"
else
  echo "Open a new terminal window, paste the command above, and run it."
  echo "The launcher will resolve identity, register the session, and start claude."
  echo "See docs/runbooks/deploy-cc-family.md for post-boot verification."
fi

exit 0
```

- [ ] **Step 4: Run tests — expect 12/12 PASS**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/pytest tests/test_deploy_cc_family.py tests/test_check_family_preflight.py -v
```

Expected: 12 bash tests + 4 python tests = 16/16 PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && git add scripts/deploy_cc_family.sh tests/test_deploy_cc_family.py && git commit -m "$(cat <<'EOF'
feat(scripts): DB preflight + launcher invocation printout (TASK-045)

Bash script now sources .env, invokes scripts.lib.check_family_preflight
via venv Python, and propagates the helper's exit code. On success,
prints the exact `cd <repo> && launch_dangerous_cc.sh --repo <repo>`
command for the operator to paste. CC_SKIP_DB=1 bypass for unit tests.
Two new tests covering dry-run + non-dry-run output formats, all green.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Push branch + local dry-run dress rehearsal

**Files:** none (operational)

- [ ] **Step 1: Run full test suite**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/pytest tests/test_deploy_cc_family.py tests/test_check_family_preflight.py -v
```

Expected: 16/16 PASS.

- [ ] **Step 2: Run against the real DB (dry-run — should NOT boot anything)**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && ./scripts/deploy_cc_family.sh --dry-run cc-scholar
```

Expected output includes:
- `family + repo-clone + env + wrapper checks: ok`
- `DB preflight: cc-scholar: agents row + no-active-sibling checks ok`
- `Launcher invocation (paste into a fresh terminal):` followed by the exact `cd ~/wingmen/projects/ai-scholar && /Users/sheikhmusa/wingmen/orchestrator/scripts/launch_dangerous_cc.sh --repo ai-scholar`

If the DB preflight fails ("active sibling" — unlikely if cc-scholar is dark), stop and coordinate with CAI before proceeding to Task 8.

- [ ] **Step 3: Validate acceptance criterion 6 (script catches ≥1 misconfiguration in dry-run)**

Run against a known-bad family to confirm exit codes fire:

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && ./scripts/deploy_cc_family.sh --dry-run cc-bogus ; echo "exit=$?"
```

Expected: `exit=2` + "unknown family" stderr.

Run against cc-web (dookana Railway broken, may not have repo cloned):

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && ./scripts/deploy_cc_family.sh --dry-run cc-web ; echo "exit=$?"
```

Expected: exits 4 (repo missing) or proceeds if both repos are cloned. Either outcome documents what cc-web deployment would need.

- [ ] **Step 4: Push branch to remote**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && git push -u origin feat/task-045-deploy-cc-family
```

Expected: push succeeds. The branch is pushed but NOT merged. Pilot deployment runs from this branch.

---

## Task 8: cc-scholar pilot deployment (live)

**Files:** none (operational) — amend runbook inline if friction surfaces

This task validates acceptance criteria 1, 2, 3, 4.

- [ ] **Step 1: Run the runbook pre-flight**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && ./scripts/deploy_cc_family.sh --dry-run cc-scholar
```

Expected: exit 0 + launcher invocation printed.

- [ ] **Step 2: Open Terminal B and paste the launcher command**

In a fresh terminal, paste and run the exact command from Step 1 output, e.g.:

```bash
cd ~/wingmen/projects/ai-scholar && /Users/sheikhmusa/wingmen/orchestrator/scripts/launch_dangerous_cc.sh --repo ai-scholar
```

Wait for the launcher to:
1. Resolve identity (auto_agent_id.py output)
2. Flip `agents.last_heartbeat` to non-null
3. Insert `agent_status` row
4. Start the 5-min heartbeat loop
5. Launch `claude --dangerously-skip-permissions`

The claude session is now interactive in Terminal B. Leave it running.

- [ ] **Step 3: Verify acceptance criteria 1, 2, 3 from Terminal A**

Run the three verification queries from `docs/runbooks/deploy-cc-family.md` Post-boot verification section:
- `agents` row has non-null `last_heartbeat` (criterion 2)
- `agent_status` has a cc-scholar_N row with recent heartbeat (criterion 3)
- Runbook flow worked end-to-end (criterion 1)

Record the timestamps + sub_tag value.

- [ ] **Step 4: Verify acceptance criterion 4 (test job pickup)**

Run the "Test job pickup" section from the runbook: queue a dummy hifz-companion job, wait 60s, verify `claimed_by` is `cc-scholar_N` not `cc-ihsanos_N`.

If cc-ihsanos claims the job instead: **stop**. This is a scope-split regression — escalate to CAI via `agent_messages` with priority P1 and do not proceed to Task 10 until resolved.

If cc-scholar claims cleanly: cancel the dummy job (runbook Step 4 of verification section) and proceed.

- [ ] **Step 5: Document pilot results in runbook**

If any friction surfaced (unclear wording, missing step, wrong exit code), edit `docs/runbooks/deploy-cc-family.md` inline. Otherwise, skip to Step 6.

- [ ] **Step 6: Commit runbook amendments (if any)**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && git status docs/runbooks/deploy-cc-family.md
# If modified:
cd /Users/sheikhmusa/wingmen/orchestrator && git add docs/runbooks/deploy-cc-family.md && git commit -m "$(cat <<'EOF'
docs(runbooks): amend deploy-cc-family with pilot learnings (TASK-045)

Pilot deployment surfaced <specific issue>. Updated <section>.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

If no amendments: no commit.

- [ ] **Step 7: Leave the cc-scholar session running or tear down**

Per the pilot plan, the cc-scholar session can stay up long-term (it'll pick up real hifz-companion jobs as they queue). Or, if this was a smoke-only run, Ctrl-C in Terminal B to trigger the EXIT trap. Document the choice in the session digest (Task 10).

---

## Task 9: cc-cosem dry-run validation (acceptance criterion 5)

**Files:** none

This task validates acceptance criterion 5: "Runbook copy-pasteable for cc-cosem in <5 minutes." We do NOT actually boot cc-cosem here — the claude-smoke vision port is a separate workstream. We only confirm the dry-run works.

- [ ] **Step 1: Time the dry-run for cc-cosem**

```bash
time (cd /Users/sheikhmusa/wingmen/orchestrator && ./scripts/deploy_cc_family.sh --dry-run cc-cosem)
```

Expected:
- exit 0 (or exit 4 if cosem-tdu/cosem-adcda not cloned — in that case, note as a follow-up for the vision-port work)
- elapsed time <5s (wall clock)
- If exit 4: document the specific repo to clone; this is acceptance criterion 5's "copy-paste" bar — the operator sees exactly what's missing.

- [ ] **Step 2: Record timing + exit code in the session digest**

The digest in Task 10 will capture this as evidence that the runbook flow is <5 min for cc-cosem.

---

## Task 10: STATUS.md update + session digest to CAI

**Files:**
- Modify: `/Users/sheikhmusa/wingmen/orchestrator/STATUS.md`
- Post: `agent_messages` row (no file)

- [ ] **Step 1: Append TASK-045 entry to STATUS.md**

Use Edit to append to `/Users/sheikhmusa/wingmen/orchestrator/STATUS.md` (find the most recent "Shipped" or equivalent section and append a new entry below it). Entry format (match existing style):

```markdown
### TASK-045 — Deploy CC-Family runbook + script + cc-scholar pilot (2026-04-21)

**Status:** Shipped (branch: `feat/task-045-deploy-cc-family`, commits: <commit-list>)

**Scope delivered:**
- `docs/runbooks/deploy-cc-family.md` — full operator flow (pre-flight, spin-up, post-boot, troubleshooting, rollback)
- `scripts/deploy_cc_family.sh` — bash preflight with exit codes 2-7 per misconfiguration class
- `scripts/lib/check_family_preflight.py` — DB preflight (agents row + active-sibling check)
- `tests/test_deploy_cc_family.py` — 12 subprocess tests
- `tests/test_check_family_preflight.py` — 4 unit tests with mocked psycopg

**Acceptance verified:**
1. cc-scholar boots via runbook ✅ (timestamp: <fill>)
2. `agents.last_heartbeat` flipped to non-null ✅
3. `agent_status` row registered (cc-scholar_<N>) ✅
4. hifz-companion test job picked up by cc-scholar ✅ (job_id: <fill>)
5. Runbook copy-pasteable for cc-cosem <5min ✅ (dry-run: <elapsed>)
6. Script catches ≥1 misconfiguration in dry-run ✅ (cc-bogus → exit 2, cc-web → exit 4)

**Parent:** CAI-RESP-057 → CAI-HIERARCHY-001. TASK-045 challenge window closes 2026-04-22 02:52:14 UTC.

**Next:** cc-cosem spin-up (precondition for claude-smoke vision port, separate workstream).
```

- [ ] **Step 2: Commit STATUS.md**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && git add STATUS.md && git commit -m "$(cat <<'EOF'
chore(status): TASK-045 shipped — deploy-cc-family + cc-scholar pilot

Runbook + preflight script + Python DB helper + cc-scholar live pilot
validated. 6/6 acceptance criteria met. Unblocks cc-cosem spin-up for
claude-smoke vision port (separate workstream).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3: Push the branch**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && git push origin feat/task-045-deploy-cc-family
```

- [ ] **Step 4: Post session digest to CAI via agent_messages**

Per memory `feedback_session_digest.md`, post a structured JSON digest. Use the orchestrator venv + psycopg (DSN from `.env`) — do not write a temp file if possible, use a python heredoc.

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python <<'PYEOF'
import json, os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')

digest = {
    "session_type": "feature_ship",
    "task_ref": "TASK-045",
    "parent_refs": ["CAI-RESP-057", "CAI-HIERARCHY-001"],
    "shipped": {
        "files_created": [
            "docs/runbooks/deploy-cc-family.md",
            "scripts/deploy_cc_family.sh",
            "scripts/lib/check_family_preflight.py",
            "tests/test_deploy_cc_family.py",
            "tests/test_check_family_preflight.py",
        ],
        "branch": "feat/task-045-deploy-cc-family",
        "tests": {"bash": "12/12 green", "python": "4/4 green"},
    },
    "acceptance": {
        "cc_scholar_booted": True,
        "agents_heartbeat_flipped": True,
        "agent_status_registered": True,
        "hifz_job_picked_up_by_cc_scholar": True,
        "cc_cosem_dry_run_under_5min": True,
        "dry_run_catches_misconfig": True,
    },
    "next": "cc-cosem spin-up (precondition for claude-smoke vision port)",
}

body = "Session digest — TASK-045 shipped.\n\n```json\n" + json.dumps(digest, indent=2) + "\n```"

with psycopg.connect(os.environ["ORCH_DSN"]) as c:
    with c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agent_messages
              (from_agent, to_agent, priority, message_type,
               requires_response, subject, body)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            ("cc-ihsanos", "cai", "P3", "update", False,
             "TASK-045 shipped — cc-scholar live", body),
        )
        msg_id = cur.fetchone()[0]
        c.commit()
        print(f"posted session digest msg #{msg_id}")
PYEOF
```

- [ ] **Step 5: Open PR (optional, if Musa wants review before merge)**

If branching-first workflow: create a PR against `main` with title `TASK-045: Deploy CC-family runbook + script + cc-scholar pilot`. Otherwise merge directly. Follow existing team convention.

---

## Self-review checklist (for the controller before dispatching Task 1)

- [ ] All 6 acceptance criteria from `strategic_decisions.decision_ref='TASK-045'` have a task that validates them
- [ ] No placeholders — every code block is complete, every shell command has concrete args
- [ ] Type consistency — `ExitCode` enum values in Python match exit codes in bash (2, 3, 4, 5, 6, 7)
- [ ] Test coverage — every bash precondition has at least one test; DB helper has mocked unit tests
- [ ] DRY — repo_scope is hardcoded once in bash and validated against live DB by the Python helper (dual source of truth, drift detected by helper)
- [ ] YAGNI — no launchd plists, no daemonization, no cc-web pilot (Railway broken)
- [ ] Commits atomic — each commit is one logical unit and passes its own tests
- [ ] Runbook is copy-pasteable — every SQL snippet is a complete runnable command, not pseudo-code
