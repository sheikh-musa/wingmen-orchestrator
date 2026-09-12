#!/bin/bash
# NOTE: canonical TRACKED copy. LIVE copy runs at /home/gazzai/tabung_supervisor.sh
# (gzb-local). Keep in sync; wingmen-irsyad-tabung-jumaat.service ExecStart uses the live copy.
# Adapted from coord_supervisor.sh (same recreate-proven pattern) for the standing
# irsyad-tabung-jumaat WORKER lane. Only lane-specific values differ (session/worktree/base).
# coord_supervisor.sh (gzb) — supervise the irsyad-tabung-jumaat tmux LANE session.
# systemd Restart=always brings us back if this exits. Adapted from
# /home/gazzai/orch_supervisor.sh, but coord is a WORKER LANE, not the hub:
#   - hub identity is NOT pinned here (no ORCH_BODY_ROLE/ORCH_AGENT_ID). The lane's
#     COORDINATOR identity IS pinned via CC_BASE_OVERRIDE=<fleet_lanes.base_agent_id>
#     (= cc-irsyad-coord; CAI-RESP-258, see the identity-pin block below) — the pwd
#     family-map alone would give the generic cc-irsyad. OAuth account is resolved by
#     lane_token_resolver from the tmux SESSION NAME → .group_default_token.irsyad
#     pointer → musa2 (irsyad lanes ride the musa2 pool, Phase-2 direction).
#   - it runs launch_dangerous_cc.sh (NOT raw claude), so it keeps the full lane
#     machinery: agent_boot (reads the inbox), the 5-min agent_status heartbeat,
#     the 25-min check-in reminder, and the EXIT trap (cc_work_sessions + bus row).
#   - it sources ONLY the SHARED tmpfs .env (RO DSNs + substrate). It must NOT
#     source /dev/shm/wingmen-secrets/private/write_dsn.env — a write-DSN in the
#     env trips the launcher's CAI-1225 Layer-2 guard (fail-closed). A lane that
#     needs a write goes through the sanctioned path, never ambient env.
#
# HARD headless-auth gate (docs/orch-move-runbook.md, 2026-09-05 incident): a real
# tmux size (-x/-y, never a zero-size detached pane) + conditional --continue (a
# fresh clone with no prior conversation must NOT get --continue, or claude exits on
# boot). Onboarding/trust for the coord worktree dir already seeded in ~/.claude.json.
set -uo pipefail

COORD_WT="$HOME/wingmen/projects/ihsanos-irsyad.wt-tabung-jumaat"
ORCH_DIR="$HOME/wingmen/orchestrator"
SESSION="irsyad-tabung-jumaat"
LAUNCHER="$ORCH_DIR/scripts/launch_dangerous_cc.sh"

cd "$COORD_WT" || { echo "[coord_supervisor] FATAL: coord worktree missing: $COORD_WT" >&2; exit 1; }
[ -x "$LAUNCHER" ] || { echo "[coord_supervisor] FATAL: launcher not executable: $LAUNCHER" >&2; exit 1; }

# SHARED tmpfs secrets only (RO DSNs + substrate). NEVER the private write_dsn.env
# (would trip the launcher's CAI-1225 L2 write-DSN-in-env guard). Repopulated by
# wingmen-fetch-secrets.service on boot; ephemeral across reboot.
set -a; . /dev/shm/wingmen-secrets/.env 2>/dev/null; set +a

# Never route a lane through metered API; Max OAuth only. (Launcher also scrubs this.)
unset ANTHROPIC_API_KEY

# Login-shell tool paths (pyenv shims, node, claude) + per-user installs.
export PATH="$HOME/.local/bin:$PATH"

# Resolve tmux robustly (systemd's minimal PATH can miss ~/.local/bin). claude is
# resolved inside the launcher.
TMUX_BIN="$(command -v tmux || true)"
if [ -z "$TMUX_BIN" ]; then
  for _t in /usr/bin/tmux /usr/local/bin/tmux "$HOME/.local/bin/tmux"; do
    [ -x "$_t" ] && TMUX_BIN="$_t" && break
  done
fi
[ -n "$TMUX_BIN" ] || { echo "[coord_supervisor] FATAL: tmux not found" >&2; exit 1; }

# systemd `stop`/`restart` sends SIGTERM to THIS supervisor, but the coord tmux session
# runs on the shared tmux SERVER daemon (outside the unit's cgroup), so it would SURVIVE a
# unit stop as an orphaned claude — the unit could not actually stop coord, and a "restart"
# would just re-adopt the stale session (invalid recreate). Trap TERM/INT to tear the
# session down, so systemctl stop/restart cleanly (re)creates coord. Required for a
# stoppable supervised lane (2026-09-12 cutover recreate-proof).
trap '"$TMUX_BIN" kill-session -t "$SESSION" 2>/dev/null; exit 0' TERM INT

# --continue is CONDITIONAL: a FRESH coord clone has no prior conversation for this
# project dir, and --continue would make claude EXIT on boot (crash-loop). The
# launcher takes claude args after a `--` boundary. Claude munges the cwd to the
# project-dir name by replacing both '/' and '.' with '-'.
CONT_ARGS=()
COORD_PROJ_DIR="$HOME/.claude/projects/-home-gazzai-wingmen-projects-ihsanos-irsyad-wt-tabung-jumaat"
if ls "$COORD_PROJ_DIR/"*.jsonl >/dev/null 2>&1; then CONT_ARGS=(-- --continue); fi

# CAI-RESP-258 IDENTITY PIN (Nazim 39259): the pwd->family map resolves this worktree to
# the GENERIC base cc-irsyad (agent cc-irsyad-coord has empty repo_scope), so WITHOUT a pin
# coord boots as cc-irsyad-N — not its distinct coordinator identity that the gazzabyte-irsyad
# channel's supervised review flow (group_routing.agent_reviewer=cc-irsyad-coord) references.
# The intended identity is data-driven in fleet_lanes.base_agent_id; auto_agent_id honors
# CC_BASE_OVERRIDE (cc-irsyad-coord is a registered cc-family, not an authority id, so allowed).
# Passed to the pane via `tmux new-session -e` — NOT a plain supervisor export: a pane inherits
# the tmux SERVER env, not this process's, the SAME lesson as the write-DSN scrub above.
VENV_PY="$ORCH_DIR/.venv/bin/python3"
COORD_BASE="$("$VENV_PY" - <<'PY' 2>/dev/null
import os,sys
try:
    import psycopg
    with psycopg.connect(os.environ['DATABASE_URL']) as c, c.cursor() as cur:
        cur.execute("SELECT base_agent_id FROM fleet_lanes WHERE lane='irsyad-tabung-jumaat'")
        r = cur.fetchone()
        sys.stdout.write((r[0] or '') if r else '')
except Exception:
    pass
PY
)"
[ -n "$COORD_BASE" ] || COORD_BASE="cc-irsyad"  # resilient fallback to the known coordinator id

# ADOPT an existing live session; create only if missing. tmux runs the launcher
# INSIDE the session named "$SESSION", so the launcher's `tmux display-message -p
# '#S'` returns irsyad-coord and lane_token_resolver maps it to the musa2 pointer.
#
# CAI-1225 DEFENSE-IN-DEPTH (2026-09-12): a tmux PANE inherits the tmux SERVER's
# global environment, NOT this supervisor's clean env. If that server was started
# before the CAI-1225 split (or otherwise captured write DSNs into its global env),
# every new pane inherits GOUMLYNE_DATABASE_URL / IHSANOS_PROD_DATABASE_URL /
# IHSANOS_SUPABASE_SERVICE_KEY and the launcher's L2 guard REFUSES to boot (crash-loop,
# 2026-09-12 cutover). The root fix is scrubbing the server global env
# (`tmux set-environment -g -r <var>`), but we ALSO unset here so coord is robust to
# any future server-env regression — a lane must NEVER carry the write DSN.
if ! "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; then
  "$TMUX_BIN" new-session -d -s "$SESSION" -x 220 -y 50 -c "$COORD_WT" \
    -e "CC_BASE_OVERRIDE=$COORD_BASE" \
    -- bash -lc 'unset GOUMLYNE_DATABASE_URL IHSANOS_PROD_DATABASE_URL IHSANOS_SUPABASE_SERVICE_KEY; exec "$@"' _ \
       "$LAUNCHER" "${CONT_ARGS[@]+"${CONT_ARGS[@]}"}"
fi

# Hold the unit alive while the lane session lives; exit (→ Restart=always) when it dies.
while "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; do sleep 5; done
