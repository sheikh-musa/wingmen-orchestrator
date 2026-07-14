#!/usr/bin/env bash
# boot_coord_pane_publisher.sh — one-command installer for the coordinator-pane
# publisher (op #3729, task #13). HOST-AGNOSTIC: derives every path from the local
# checkout + $HOME, generates the launchd plist, (re)loads it, and verifies it is
# writing fresh coordinator_panes rows. Idempotent — safe to re-run.
#
# Each host runs ONE instance. It captures the live fleet tmux panes on THIS host
# and UPSERTs them into substrate coordinator_panes so the fleet console can peek
# them cross-host with zero SSH. On the fleet hub it publishes the hub + local
# lanes; on Nazim's Mac Mini it publishes agent_id='orch-console' (Nazim's pane).
#
# Usage:  scripts/boot_coord_pane_publisher.sh
set -euo pipefail

LABEL="dev.wingmen.coord-pane-publisher"

# Repo root = parent of this script's dir (works regardless of cwd / host user).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PY="$REPO_ROOT/.venv/bin/python3"
LOG="$REPO_ROOT/logs/coord-pane-publisher.log"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

[ -x "$PY" ] || { echo "FATAL: venv python not found/executable at $PY" >&2; exit 1; }
mkdir -p "$REPO_ROOT/logs" "$HOME/Library/LaunchAgents"

echo "Installing $LABEL"
echo "  repo   : $REPO_ROOT"
echo "  python : $PY"
echo "  plist  : $PLIST"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$PY</string>
    <string>-m</string><string>nervous_system.coordinator_pane_publisher</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO_ROOT</string>
  <key>KeepAlive</key><true/>
  <key>RunAtLoad</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
  <key>EnvironmentVariables</key><dict>
    <key>HOME</key><string>$HOME</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict></plist>
PLIST_EOF

# Reload idempotently: bootout any existing instance, then bootstrap fresh
# (RunAtLoad starts it — no separate kickstart, which would just SIGTERM the
# freshly-started process and leave a confusing non-zero last-exit status).
# bootout is ASYNC: wait for the label to actually disappear before bootstrap,
# else bootstrap races the teardown and fails with "5: Input/output error".
DOMAIN="gui/$(id -u)"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
for _ in $(seq 1 20); do
  launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 || break
  sleep 0.5
done
# Retry bootstrap a few times to ride out any residual teardown lag.
for attempt in 1 2 3 4 5; do
  if launchctl bootstrap "$DOMAIN" "$PLIST" 2>/tmp/coord_pane_bootstrap.err; then
    break
  fi
  if [ "$attempt" = "5" ]; then
    echo "FATAL: bootstrap failed after retries:" >&2; cat /tmp/coord_pane_bootstrap.err >&2; exit 1
  fi
  sleep 1
done

echo "Loaded. Waiting ~12s to verify it is writing fresh rows..."
sleep 12

if launchctl list | grep -q "$LABEL"; then
  echo "OK: service is listed:"
  launchctl list | grep "$LABEL"
else
  echo "WARN: service not listed — check $LOG" >&2
fi

echo "--- last log lines ---"
tail -n 5 "$LOG" 2>/dev/null || true

# Verify it published at least one FRESH row (captured_at within 30s) for a
# session live on this host. Read-only DB check via the same venv.
echo "--- fresh coordinator_panes rows (this host) ---"
REPO_ROOT="$REPO_ROOT" "$PY" - <<'PYEOF' || echo "WARN: DB verification could not run (check DATABASE_URL / connectivity)" >&2
import os, subprocess
from dotenv import load_dotenv
# Explicit path: find_dotenv() walks stack frames and fails from a stdin heredoc.
load_dotenv(os.path.join(os.environ["REPO_ROOT"], ".env"))
import psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
tmux = next((c for c in ("/opt/homebrew/bin/tmux","/usr/local/bin/tmux","/usr/bin/tmux") if os.path.isfile(c)), "tmux")
try:
    live = subprocess.run([tmux,"list-sessions","-F","#{session_name}"],capture_output=True,text=True,timeout=5).stdout.split()
except Exception:
    live = []
with psycopg.connect(dsn, autocommit=True) as c:
    rows = c.execute(
        "SELECT agent_id, round(extract(epoch FROM (now()-captured_at)))::int AS age_s "
        "FROM coordinator_panes WHERE captured_at > now() - interval '30 seconds' ORDER BY agent_id"
    ).fetchall()
    if rows:
        for aid, age in rows:
            print(f"  {aid:24s} age={age}s")
    else:
        print("  (no fresh rows yet — check the log above)")
PYEOF

echo "Done. Label: $LABEL"
