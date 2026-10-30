# console_deploy_probe.sh — SSOT for the deploy_console.sh post-kickstart health probe.
#
# backlog#135 (orch-console 79249 item 1, 2026-10-28): fc-v88's deploy curled the bound
# tailnet address ONCE and echoed whatever came back -- blank or not -- and always exited
# 0, so a good deploy's log line can read as empty with no signal anything was wrong.
# This seam hits the SAME bound address (never loopback) with a short retry, and on
# exhausting retries prints "COULD NOT MEASURE /api/version" to stderr and returns
# non-zero -- never a blank line that looks like a passing health check.
#
# Sourced by scripts/deploy_console.sh and by tests/test_console_deploy_probe.py so the
# real gate and the test call the exact same function.

console_deploy_probe() {
  local url="$1" token="$2" attempts="${3:-5}" delay="${4:-1}"
  local i served
  for ((i = 1; i <= attempts; i++)); do
    served=$(curl -s -m 3 -H "Authorization: Bearer ${token}" "$url" 2>/dev/null)
    if [ -n "$served" ]; then
      printf '%s' "$served"
      return 0
    fi
    if [ "$i" -lt "$attempts" ]; then
      sleep "$delay"
    fi
  done
  echo "COULD NOT MEASURE /api/version" >&2
  return 1
}
