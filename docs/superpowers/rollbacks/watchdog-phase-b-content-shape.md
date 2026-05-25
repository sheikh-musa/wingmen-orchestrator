# Watchdog Phase B Content-Shape Rollback (CAI-RESP-164 / CAI-RESP-167)

Per CAI-RESP-161 + CAI-RESP-163 + CAI-RESP-167 constraint: PR #42 includes rollback procedure.

## Triggers for rollback

- Content-shape signals produce false-positive SIGTERM on legit operator work
- jsonl read paths get stuck or block watchdog daemon (R3 violated)
- watchdog_monitored_callers table fills with spurious entries / churns
- R5 pre-SIGTERM audit barrier silently bypassed (any SIGTERM without preceding `watchdog_pre_kill_audit` row)
- boot_briefing view loses an arm during view-replace (CC-SUBSTRATE-VIEW-INTEGRITY-001 incident shape repeats)
- Calibration window (CAI-RESP-164 R2) reveals thresholds materially wrong

## Rollback procedure

### 1. Immediate operator action — panic button

Same panic button as PR #41 (still primary stop, no code revert required):

```bash
echo "WINGMEN_LONG_CALLER_WATCHDOG_DISABLED=true" >> .env
launchctl kickstart -k "gui/$(id -u)/dev.wingmen.watchdog"
```

This stops the daemon from issuing any SIGTERM regardless of content-shape result. Buys time to plan proper rollback.

### 2. Code revert

```bash
git revert <PR-42-merge-commit-SHA>
git push origin main
launchctl kickstart -k "gui/$(id -u)/dev.wingmen.watchdog"
```

After this, the watchdog daemon is back to PR #41 behavior — but per CAI-RESP-167 it should remain dormant (no kickstart unless cai re-ratifies).

### 3. Migration rollback

```sql
BEGIN;

-- Drop the new arm by re-creating the view with PR #41 body (one fewer arm).
-- Capture the current view, strip the watchdog_monitored_callers UNION arm, re-apply.
-- NOTE: this is the safest path — manually re-paste the view body without the new arm
-- and run CREATE OR REPLACE VIEW boot_briefing AS ... ;

CREATE OR REPLACE VIEW boot_briefing AS <paste PR #41 view body — 19 arms, no watchdog_monitored_callers>;

DROP TABLE IF EXISTS watchdog_monitored_callers;

COMMIT;
```

### 4. Verify rollback

```bash
python3 - <<'PY'
import os, psycopg
from dotenv import load_dotenv
load_dotenv()
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
with psycopg.connect(dsn, autocommit=True) as c, c.cursor() as cur:
    cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='watchdog_monitored_callers'")
    assert cur.fetchone()[0] == 0, 'watchdog_monitored_callers still exists'
    cur.execute("SELECT pg_get_viewdef('boot_briefing'::regclass, true)")
    defn = cur.fetchone()[0]
    assert "'watchdog_monitored_callers'" not in defn, 'arm still in view'
    assert "'active_autonomous_loops'" in defn, 'PR #41 arm missing'
    assert "'long_running_caller'" in defn, 'PR #41 arm missing'
print('rollback verified')
PY
```

### 5. Audit notification_log

```sql
SELECT count(*), source FROM notification_log
 WHERE source IN (
   'watchdog_hard_kill',
   'watchdog_pre_kill_audit',
   'watchdog_pre_kill_audit_failed',
   'watchdog_aborted_kill'
 )
 GROUP BY source;
```

Preserve these rows for forensic trail. The pre_kill_audit rows are the most valuable — they show what content-shape signals were observed at the moment of each kill decision. Do NOT delete them; they're the calibration data for CAI-RESP-164 R2.

### 6. Audit watchdog_monitored_callers (before table drop)

```sql
-- Snapshot the monitored-callers state into a JSONB blob in notification_log for posterity:
INSERT INTO notification_log (source, decision_ref, channel, recipient, message_text)
SELECT 'watchdog_monitored_callers_snapshot_pre_rollback',
       'CAI-RESP-167-ROLLBACK',
       'long_running_callers',
       'rollback-snapshot',
       to_jsonb(t)::text
  FROM watchdog_monitored_callers t;
```

Then proceed with `DROP TABLE` in step 3.

## Filing requirement

Rollback must be filed as decision_ref `CC-WATCHDOG-CONTENT-SHAPE-ROLLBACK-NNN` with:
- Trigger (which defect — false positive identifier, R3 violation, view rollback, audit barrier bypass, etc.)
- Decision (revert PR #42 + drop scope)
- Audit log of all `watchdog_hard_kill` rows in notification_log within the incident window
- Audit log of all `watchdog_pre_kill_audit` rows for forensic reconstruction
- Forward-fix plan (e.g., threshold adjustment + re-ship, structural redesign if R5 audit failed)

## Calibration window note

Per CAI-RESP-164 R2: 30-day post-ship calibration window. If thresholds prove materially wrong during this window:
- Do NOT rollback the entire PR
- Adjust the threshold constants in `nervous_system/content_shape_signals.py` (`SIGNAL_A_MAX_BYTES`, `SIGNAL_B_BAND_LO`, `SIGNAL_B_BAND_HI`, `SIGNAL_B_MIN_SPAN_SECONDS`)
- File CC-WATCHDOG-CALIBRATION-001 with TP/FP data and proposed new values
- Cai ratifies, then ship a one-line PR adjusting the constants

Threshold drift is a calibration concern, not a rollback trigger.
