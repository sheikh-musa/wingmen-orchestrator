# Long-Caller Watchdog Phase B Rollback

Per CAI-RESP-161 + CAI-RESP-163 constraint: all Phase B PRs include rollback procedure.

## Triggers for rollback
- Phase B watchdog SIGTERMs a legitimate caller (false-positive in production)
- Cadence tracker memory leak / accumulates state without bound
- PID re-verification race fails to catch a PID recycle, kills wrong process
- Panic button env flag doesn't propagate / doesn't disable kills
- watchdog.py integration breaks bot or orchestrator monitoring

## Rollback procedure

### 1. Immediate operator action — set panic button

If false-positive kill is observed, immediately set the env flag:

```bash
# In .env
echo "WINGMEN_LONG_CALLER_WATCHDOG_DISABLED=true" >> .env

# Restart watchdog
launchctl kickstart -k "gui/$(id -u)/dev.wingmen.watchdog"
```

This stops all new SIGTERMs without requiring code revert. Buys time to plan a proper rollback.

### 2. Code revert

```bash
git revert <PR-merge-commit-SHA>
git push origin main
launchctl kickstart -k "gui/$(id -u)/dev.wingmen.watchdog"
```

### 3. Migration rollback (Task 1 schema)

```sql
BEGIN;
-- Drop the parent_pid column (loses recent data; OK because non-load-bearing)
ALTER TABLE active_autonomous_loops DROP COLUMN IF EXISTS parent_pid;
-- Restore boot_briefing view to pre-Phase-B definition (active_autonomous_loops
-- arm without parent_pid in json_build_object).
CREATE OR REPLACE VIEW boot_briefing AS <paste pre-Phase-B view body>;
COMMIT;
```

### 4. Verify rollback

```bash
python3 -c "
import os, psycopg
from dotenv import load_dotenv
load_dotenv()
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
with psycopg.connect(dsn, autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute(\"SELECT count(*) FROM information_schema.columns WHERE table_name='active_autonomous_loops' AND column_name='parent_pid'\")
        assert cur.fetchone()[0] == 0, 'parent_pid still exists'
print('rollback verified')
"
```

### 5. Audit notification_log

```sql
SELECT count(*), source FROM notification_log
 WHERE source IN ('watchdog_hard_kill', 'watchdog_soft_alert', 'watchdog_aborted_kill')
 GROUP BY source;
```

Preserve these rows for forensic trail. They're informational; no cleanup needed.

## Filing requirement

Rollback must be filed as decision_ref `CC-LONG-CALLER-WATCHDOG-PHASE-B-ROLLBACK-NNN` with:
- Trigger (which defect)
- Decision (revert + soft-drop scope)
- Audit log of all SIGTERM events from notification_log
- Forward-fix plan
