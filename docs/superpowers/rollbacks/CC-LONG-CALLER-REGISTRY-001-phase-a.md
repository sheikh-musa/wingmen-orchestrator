# CC-LONG-CALLER-REGISTRY-001 Phase A Rollback

Per CAI-RESP-161 constraint: "All Phase A + Phase B PRs must include rollback procedure in case of defect discovery post-ship."

## Triggers for rollback
- Phase A migration causes boot_briefing view corruption
- Helper module breaks orchestrator boot
- Manifest sweep blocks orchestrator main loop
- Discovered data-integrity issue (e.g., FK violation on substrate seed)
- Unexpected interaction with substrate-native callers (ralphy / paused-job-retry)

## Rollback steps

### 1. Code revert
```bash
# From a checkout authenticated with workflow scope:
git revert <PR-merge-commit-SHA>
git push origin main
./scripts/restart_orch.sh
```

The code revert pulls back the helper module imports in `wingmen_orch.py` + the helper module itself + the manifests directory.

### 2. Migration rollback (additive — soft-revert via DROP + view restore)

The Task 1 migration is additive (CREATE TABLE IF NOT EXISTS + CREATE OR REPLACE VIEW + INSERT ON CONFLICT). Soft revert:

```sql
BEGIN;

-- Drop the table (cascades to lose all caller registrations + substrate seed)
DROP TABLE IF EXISTS long_running_claude_callers CASCADE;

-- Restore boot_briefing view to its pre-Phase-A definition.
-- BEFORE running the soft-revert, capture the current view def MINUS the
-- long_running_caller UNION arm. The pre-Phase-A view body is the same as
-- the Task 1 migration's view body but with the long_running_caller arm
-- removed (the last UNION ALL block ending in `lrcc.revoked_at > now() - interval '30 days'`).
CREATE OR REPLACE VIEW boot_briefing AS
<paste pre-Phase-A view body — i.e., the post-PR-#36 view from main HEAD
 before this PR landed; capture via `git show <pre-merge-SHA>:supabase/migrations/...`
 or via pg_get_viewdef on a parallel non-Phase-A DB>;

COMMIT;
```

If no clean pre-Phase-A snapshot is available, the conservative approach is to manually edit `pg_get_viewdef('boot_briefing')` output, strip the trailing `UNION ALL SELECT 'long_running_caller'...` block, wrap in `CREATE OR REPLACE VIEW boot_briefing AS ...;`, and apply.

### 3. Verify rollback

```bash
python3 -c "
import os, psycopg
from dotenv import load_dotenv
load_dotenv()
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
with psycopg.connect(dsn, autocommit=True) as c:
    with c.cursor() as cur:
        # Table dropped
        cur.execute(\"SELECT count(*) FROM information_schema.tables WHERE table_name='long_running_claude_callers'\")
        assert cur.fetchone()[0] == 0, 'table still exists'
        # View no longer references the new arm
        cur.execute(\"SELECT pg_get_viewdef('boot_briefing'::regclass, true)\")
        defn = cur.fetchone()[0]
        assert 'long_running_caller' not in defn, 'view still references long_running_caller'
        # All prior UNION arms still present (sanity)
        for ref in ['repo_context', 'ralph_state', 'cc_session_costs', 'active_autonomous_loops', 'synthetic_filter']:
            assert ref in defn, f'view missing prior arm {ref}'
print('rollback verified — long_running_caller artifacts removed; prior arms intact')
"
```

### 4. Orchestrator restart

```bash
./scripts/restart_orch.sh
launchctl list | grep wingmen.orchestrator
```

Confirm orch starts cleanly without the `long_running_claude_callers` import path (revert handles this) and without the manifest-sweep block (revert handles this too).

### 5. Audit notification_log for caller-related rows

```sql
SELECT count(*), source FROM notification_log
 WHERE source IN ('caller_registered', 'caller_revoked', 'watchdog_hard_kill', 'watchdog_soft_alert', 'caller_self_kill')
 GROUP BY source;
```

These rows remain after rollback (notification_log is the cross-cutting audit surface and IS NOT dropped). They're informational; preserved for forensic trail. No cleanup needed unless explicitly desired.

## Data loss on rollback

- **Lost on table DROP:** any caller registrations (Phase A: 2 substrate-native seeds + any operator-filed registrations). substrate seeds are re-applied by re-running Task 1's migration if the rollback is later reversed.
- **Preserved:** notification_log audit rows for caller_registered/caller_revoked events (informational; no foreign-key reference).
- **Preserved:** manifests under `manifests/long_running_callers/*.yaml` (filesystem unchanged by SQL rollback; code revert removes the sweep but leaves the manifest files for forensic record).

## Filing requirement

Any rollback must be filed as a decision_ref `CC-LONG-CALLER-REGISTRY-ROLLBACK-NNN` (next sequential NNN) in strategic_decisions with:
- **Trigger:** which defect (link the bug report / observation)
- **Decision:** revert + soft-drop scope (what's dropped, what's preserved)
- **Audit log:** list of all caller registrations lost (queryable from notification_log pre-rollback via source='caller_registered')
- **Recovery plan:** what's needed to forward-fix vs re-ship Phase A clean

challenge_window 24h per usual; rollback is operational-emergency-shaped so cai may pre-ratify if filed mid-incident.
