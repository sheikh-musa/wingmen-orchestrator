#!/usr/bin/env bash
# lanes.sh — tmux fleet manager for Wingmen CC build lanes.
#
# Each lane is a tmux session that runs launch_dangerous_cc.sh inside a worktree.
# IDEMPOTENT: a lane is skipped if a `claude` process already has its dir as cwd,
# so re-running never double-launches a dangerous (auto-pushing) CC onto a tree
# that already has one — the only failure mode that can actually lose work.
#
# The lane roster is READ from the `fleet_lanes` table (desired_state='up',
# launcher='launch_dangerous_cc.sh') — never a hardcoded list. Adding a lane
# is a DB row, not an edit to this file (op#19103 item 5a). A lane with
# desired_state != 'up' is invisible to this script by design: it is not
# lanes.sh's roster until someone flips it.
#
# Usage:
#   lanes.sh ls          # show fleet status (running / down) for every UP-desired lane
#   lanes.sh up <lane>   # boot one named lane (must be desired_state='up' in fleet_lanes)
#   lanes.sh attach <lane>   # attach to a lane's tmux session
#
# NOTE: there is deliberately no bare `lanes.sh up` (audit finding: it used to
# boot every lane in a hardcoded list regardless of whether that lane was
# meant to be running, which is how stray lanes got booted). Boot lanes one
# at a time, by name.
set -euo pipefail

LAUNCHER="$HOME/wingmen/orchestrator/scripts/launch_dangerous_cc.sh"

_require_dsn() {
  if [ -z "${DATABASE_URL:-}" ]; then
    echo "DATABASE_URL not set — lanes.sh reads its roster from fleet_lanes, no fallback." >&2
    exit 2
  fi
}

# lane <TAB> worktree_path, for every fleet_lanes row this script owns
# (launcher=launch_dangerous_cc.sh). desired_state is a separate column so ls
# can show BOTH the registry's intent and the observed tmux/process state.
lane_rows() {
  _require_dsn
  psql "$DATABASE_URL" -t -A -F $'\t' -c \
    "SELECT lane, desired_state, worktree_path FROM fleet_lanes
     WHERE launcher = 'launch_dangerous_cc.sh' AND worktree_path IS NOT NULL
     ORDER BY lane;"
}

up_lane_row() {
  local want="$1"
  _require_dsn
  psql "$DATABASE_URL" -t -A -F $'\t' -c \
    "SELECT worktree_path, base_agent_id FROM fleet_lanes
     WHERE lane = '${want}' AND launcher = 'launch_dangerous_cc.sh'
       AND desired_state = 'up' AND worktree_path IS NOT NULL;"
}

lane_exists_any_state() {
  local want="$1"
  _require_dsn
  psql "$DATABASE_URL" -t -A -c \
    "SELECT desired_state FROM fleet_lanes WHERE lane = '${want}' AND launcher = 'launch_dangerous_cc.sh';"
}

# Resolve a required binary to an ABSOLUTE path. PATH is tried first so a test/stub bin dir
# can override it, then the caller's system fallbacks. Both tools this probe needs live
# outside the PATH a launchd job, a cron entry, or a reduced tool shell may hand down
# (`lsof` is /usr/sbin/lsof; /usr/sbin is often absent), and "command not found" used to
# produce EMPTY output that every caller read as "no lane is running".
resolve_tool() {
  local name="$1"; shift
  local c
  if c="$(command -v "$name" 2>/dev/null)" && [ -n "$c" ]; then printf '%s\n' "$c"; return 0; fi
  for c in "$@"; do
    [ -x "$c" ] && { printf '%s\n' "$c"; return 0; }
  done
  return 1
}

resolve_lsof()  { resolve_tool lsof  /usr/sbin/lsof /usr/bin/lsof; }
resolve_pgrep() { resolve_tool pgrep /usr/bin/pgrep /usr/sbin/pgrep /bin/pgrep; }

# Absolute cwd of every running `claude --dangerously-skip-permissions` process.
# Returns NON-ZERO when liveness cannot be determined, so callers can fail CLOSED. An
# empty-but-successful list still means "no claude is running" — the two must not be
# confused: the first is "I cannot tell", the second is "nobody is there", and only the
# second may ever authorise starting a second claude in a worktree.
#
# BOTH tools are resolved and checked. lsof was the reported hole; pgrep is the same class
# (an unresolvable pgrep yields an empty pid list, which is indistinguishable from "none
# running") and is resolved here too so the guarantee is whole rather than half-applied.
running_cwds() {
  local lsof_bin pgrep_bin pids rc
  if ! lsof_bin="$(resolve_lsof)"; then
    echo "lanes.sh: REFUSING — cannot locate lsof (checked PATH, /usr/sbin/lsof, /usr/bin/lsof)." >&2
    echo "lanes.sh: lane liveness is UNKNOWN; a 'down' reading here would be a guess." >&2
    return 3
  fi
  if ! pgrep_bin="$(resolve_pgrep)"; then
    echo "lanes.sh: REFUSING — cannot locate pgrep (checked PATH, /usr/bin, /usr/sbin, /bin)." >&2
    echo "lanes.sh: lane liveness is UNKNOWN; a 'down' reading here would be a guess." >&2
    return 3
  fi
  # pgrep's OWN failure must not read as "no processes": exit 1 is its documented
  # "nothing matched", anything else (2 = usage/regex error) is an unusable probe.
  # `|| rc=$?` — NOT a bare assignment followed by `rc=$?`, which would let set -e kill
  # the script on pgrep's own exit 1 (the "no processes" case) before it could be read.
  rc=0
  pids="$("$pgrep_bin" -f 'claude --dangerously-skip-permissions' 2>/dev/null)" || rc=$?
  if [ "$rc" -ne 0 ] && [ "$rc" -ne 1 ]; then
    echo "lanes.sh: REFUSING — pgrep failed (rc=$rc); lane liveness is UNKNOWN." >&2
    return 3
  fi
  for pid in $pids; do
    "$lsof_bin" -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p'
  done
}

# Does any cwd in the newline-list on STDIN equal $1, or sit nested under "$1/"
# (a subdir or a nested git worktree of the lane)? Reads a MATERIALISED list from
# stdin — never a live producer pipe. This is the fix for bus #44251/#44258: the
# old `running_cwds | grep -Fxq` let grep's match short-circuit SIGPIPE the still-
# writing running_cwds, and under `set -o pipefail` that non-zero exit propagated
# so a LIVE lane reported "down" — which made `up`'s guard (boot_one) miss the
# running claude and risk a second one in the lane's worktree (double-boot).
_cwd_matches_any() {
  local target="${1%/}" cwd
  while IFS= read -r cwd; do
    [ -z "$cwd" ] && continue
    cwd="${cwd%/}"
    [ "$cwd" = "$target" ] && return 0
    [ "${cwd#"$target"/}" != "$cwd" ] && return 0   # cwd is nested under target/
  done
  return 1
}

# True if a live dangerous-CC has $1 (or a dir nested under it) as its cwd.
# Snapshots running_cwds ONCE into a variable before matching (pipefail-safe).
#
# Exit codes are THREE-VALUED and callers must respect that:
#   0 = a claude is running in (or under) $1        → occupied, do not boot
#   1 = liveness WAS determined and nothing matches → free, booting is safe
#   2 = liveness could NOT be determined            → UNKNOWN, never treat as free
# Returning 1 (or, via `|| true`, nothing at all) for the third case is precisely how a
# missing `lsof` turns this double-boot guard into a silent pass.
dir_has_claude() {
  local snap
  if ! snap="$(running_cwds)"; then
    echo "lanes.sh: liveness UNKNOWN (probe failed) — must not be read as 'no claude running'." >&2
    return 2
  fi
  _cwd_matches_any "$1" <<< "$snap"
}

cmd_ls() {
  local snap
  # one snapshot for the whole listing (correct + O(pids), not O(rows*pids)).
  # If the probe cannot run, print NOTHING and exit NON-ZERO: a table of "down" rows built
  # from a failed probe is worse than no table — it reads as fact.
  if ! snap="$(running_cwds)"; then
    echo "lanes.sh ls: REFUSING to report — lane liveness is unknown (see above)." >&2
    exit 4
  fi
  printf '%-24s %-8s %-8s %s\n' LANE DESIRED STATUS DIR
  while IFS=$'\t' read -r name desired dir; do
    [ -z "$name" ] && continue
    if [ ! -d "$dir" ]; then status="MISSING"
    elif _cwd_matches_any "$dir" <<< "$snap"; then status="running"
    else status="down"; fi
    printf '%-24s %-8s %-8s %s\n' "$name" "$desired" "$status" "$dir"
  done < <(lane_rows)
}

boot_one() {
  local name="$1" dir="$2" base_agent_id="${3:-}" rc=0
  if [ ! -d "$dir" ]; then echo "SKIP $name — dir missing: $dir"; return; fi
  # FAIL CLOSED: 0 = occupied (skip), 1 = free (proceed), anything else = UNKNOWN (refuse).
  dir_has_claude "$dir" || rc=$?
  case "$rc" in
    0) echo "SKIP $name — claude already running in $dir"; return ;;
    1) : ;;
    *) echo "REFUSE $name — cannot determine whether claude is already running in $dir (rc=$rc)." >&2
       echo "REFUSE $name — NOT booting: a second dangerous-CC on a live worktree is the one failure that loses work." >&2
       return 4 ;;
  esac
  # '=' forces tmux's EXACT target match (bus #56370): a bare -t "$name" does
  # PREFIX matching, so e.g. 'cosem-tdu' matched the running 'cosem-tdu-coord'
  # session and this guard false-positived SKIP, permanently blocking the
  # builder lane from ever booting while the coord lane was up.
  if tmux has-session -t "=$name" 2>/dev/null; then
    echo "SKIP $name — tmux session already exists (attach with: lanes.sh attach $name)"; return
  fi
  # Pin identity to the row's own base_agent_id (op#22448/bus#43342): launch_dangerous_cc.sh
  # falls back to pwd-based auto-resolution against agents.repo_scope when no override is
  # given, which picks the WRONG agent whenever two rows' repo_scope/worktree naming overlap
  # (e.g. cc-cosem-tdu the builder vs cc-cosem-tdu-coord the coord both plausibly match a
  # cosem-tdu-ish path — `lanes.sh up cosem-tdu-coord` booted as cc-cosem-tdu until caught and
  # killed/relaunched by hand). tmux's own `-e` sets a session-local env var applied when the
  # pane's process starts, so the launcher sees CC_BASE_OVERRIDE regardless of pwd guessing
  # (same mechanism already used by deploy/tabung_supervisor.sh, deploy/coord_supervisor.sh,
  # deploy/irsyad_worker_supervisor.sh for the identical purpose).
  if [ -n "$base_agent_id" ]; then
    tmux new-session -d -s "$name" -c "$dir" -e "CC_BASE_OVERRIDE=$base_agent_id" "$LAUNCHER"
  else
    tmux new-session -d -s "$name" -c "$dir" "$LAUNCHER"
  fi
  echo "BOOTED $name → tmux session '$name' ($dir)${base_agent_id:+, base=$base_agent_id}"
}

cmd_up() {
  local want="${1:?usage: lanes.sh up <lane> — bare 'up' was removed, see the header comment}"
  local row dir base_agent_id
  row="$(up_lane_row "$want")"
  if [ -z "$row" ]; then
    local state
    state="$(lane_exists_any_state "$want")"
    if [ -n "$state" ]; then
      echo "REFUSE $want — fleet_lanes.desired_state='$state', not 'up'. Flip the row first." >&2
    else
      echo "REFUSE $want — no launch_dangerous_cc.sh row named '$want' in fleet_lanes." >&2
    fi
    exit 1
  fi
  IFS=$'\t' read -r dir base_agent_id <<< "$row"
  boot_one "$want" "$dir" "$base_agent_id"
}

# WIND A LANE DOWN — the counterpart `up` never had. Until 2026-08-16 this substrate was
# one-directional: lanes could be spun up and nothing anywhere ended one, which is how ten
# irsyad-family lanes came to sit parked holding context nobody was reading. The doctrine
# line calling the manual pen "interim until the autoscaler subsumes it" described code that
# did not exist.
#
# The GATES ARE NOT HERE ON PURPOSE. They live in scripts/lib/lane_winddown.py, unit-tested
# without a live tmux or DB, and the SRE's idle detector will call the SAME predicate — so a
# lane can never be ended by one path under rules the other path would have refused. Ending a
# session is harsher than a recycle (there is no boot afterwards; context that was only in
# that session is gone), so every gate fails CLOSED: busy, unread bus rows, a stale/missing
# handoff, real staged text, or an unreadable composer all refuse.
cmd_down() {
  local lane="${1:?usage: lanes.sh down <lane> [--kill]}"; shift || true
  "$HOME/wingmen/orchestrator/.venv/bin/python3" "$HOME/wingmen/orchestrator/scripts/lib/lane_winddown.py" "$lane" "$@"
}

# Dispatch only when EXECUTED, not when SOURCED — so the liveness helpers
# (running_cwds / _cwd_matches_any / dir_has_claude) can be unit-tested with
# stubbed pgrep/lsof without the CLI running (tests/test_lanes_liveness.py).
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
case "${1:-ls}" in
  ls) cmd_ls ;;
  up) cmd_up "${2:-}" ;;
  down) shift; cmd_down "$@" ;;
  attach) tmux attach -t "${2:?usage: lanes.sh attach <lane>}" ;;
  *) echo "usage: lanes.sh {ls|up <lane>|down <lane> [--kill]|attach <lane>}" >&2; exit 2 ;;
esac
fi
