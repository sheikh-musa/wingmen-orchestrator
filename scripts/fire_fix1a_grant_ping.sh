#!/usr/bin/env bash
# fire_fix1a_grant_ping.sh — ONE-SHOT: at/after the CAI-774 window close (22:34Z =
# 06:34 Mini-local), post a directed bus row waking cc-storefront to fire the Fix1a
# §6.6 grant-ping to cai (cc-ihsanos request #17059; cai cleared Fix1a #17057).
# Then boots out its OWN launchd job so it fires exactly once.
# Launched by launchd dev.wingmen.fix1a-grant-ping (StartCalendarInterval 06:34 local).
set -uo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"
set -a; . ./.env 2>/dev/null || true; set +a

"$ORCH_DIR/.venv/bin/python3" - <<'PY' 2>>"$ORCH_DIR/logs/fix1a_grant_ping.log" || true
import os, datetime, psycopg
now = datetime.datetime.now(datetime.timezone.utc)
# Guard: only fire at/after 22:34Z (the CAI-774 window close). If launchd fires
# early for any reason, do nothing (don't ping before the window closes).
if now < now.replace(hour=22, minute=34, second=0, microsecond=0):
    print(f"{now.isoformat()} too early (<22:34Z), skipping"); raise SystemExit(0)
conn = psycopg.connect(os.environ["DATABASE_URL"]); cur = conn.cursor()
cur.execute("SELECT set_config('app.current_agent_id','orch-console',true)")
cur.execute("""INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,requires_response,priority)
  VALUES (%s,%s,%s,%s,%s,%s,%s)""",
  ("orch-console","cc-storefront","update",
   "TIMED WAKE (Nazim scheduled): CAI-774 window CLOSED (22:34Z) — fire your Fix1a §6.6 grant-ping to cai NOW",
   "The CAI-774 challenge window has closed. Per cai #17057 you're cleared to fire the §6.6 grant-ping to cai for Fix1a (PR #252, mig150+151, both tweaks verified) — no re-review. Send it now. Then the gates are: cai §6.6 grant -> hub apply mig150+151 -> operator arms STOREFRONT_CONFIRM_PAID_ARMED. This is the scheduled wake cc-ihsanos requested (#17059); Nazim fired it via launchd one-shot.",
   False, "P2"))
conn.commit()
print(f"{now.isoformat()} posted grant-ping wake to cc-storefront")
PY

# One-shot: remove the launchd job so it never fires again.
UID_NUM="$(id -u)"
/bin/launchctl bootout "gui/${UID_NUM}/dev.wingmen.fix1a-grant-ping" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/dev.wingmen.fix1a-grant-ping.plist" 2>/dev/null || true
echo "$(date -u +%FT%TZ) fired + self-removed" >> "$ORCH_DIR/logs/fix1a_grant_ping.log"
