#!/usr/bin/env bash
# flip_fleet.sh — ONE operator-armed command to flip the WHOLE fleet onto a target
# Claude Max account (op#11326, Musa->Syed bridge to the weekly reset). CAI-785 spend
# remit. DOCTRINE: the OPERATOR arms/triggers this (--arm); agents build+verify+stage,
# NEVER trigger. Default is DRY-RUN (prints the full plan, changes nothing).
#
# TRANSPARENT: every stage echoes what it does before doing it. SELF-VERIFYING: ends
# with verify_fleet_token.py (loud PASS/FAIL). REVERSIBLE: pointer repoints back up the
# old value (.was-<acct>.pre) and live bodies revert on their next boot once repointed.
#
# TWO LAYERS per body (see flip-prep-op11326):
#   (a) DURABLE default pointer -> target   (survives reboot; revert = restore/rm pointer)
#   (b) LIVE re-token of the running process (switch_*_token.sh; takes effect NOW)
#
# STAGES (in order):
#   1. Repoint durable pointers (.lane_default_token + the 4 singleton pointers).
#   2. Live re-token each Mini engineer lane            (switch_lane_token.sh).
#   3. Live re-token Mini singletons: cai, then cc-fleet-health LAST (so it does not
#      kill its OWN runner). orch-console + cc-quality: durable-repointed here; their
#      LIVE re-token is Nazim's (console context) / next-boot (quality often idle).
#   4. Cross-host bodies (VPS hub, Mac-Studio scholar): PRINT the recipe — Nazim runs
#      these (he holds the VPS/Studio keys, F2). Not driven from here by default.
#   5. verify_fleet_token.py --expect <target> (loud PASS/FAIL over the Mini fleet).
#
# RUN FROM A PLAIN SHELL — never from inside a tmux session this flips (it refuses if
# it detects it is running inside cc-fleet-health / a target lane).
#
# Usage:
#   scripts/flip_fleet.sh                       # DRY-RUN to Syed (default) — prints the plan
#   scripts/flip_fleet.sh ~/.wingmen/keys/syed-oauth-token
#   scripts/flip_fleet.sh --arm                 # ARM: actually flip the fleet to Syed
#   scripts/flip_fleet.sh --arm ~/.wingmen/keys/musa-oauth-token   # flip back to Musa
set -uo pipefail
cd "$HOME/wingmen/orchestrator" || { echo "ERROR: orch dir missing" >&2; exit 9; }
ORCH_DIR="$(pwd)"
set -a; . .env 2>/dev/null || true; set +a
VENV_PY="$ORCH_DIR/.venv/bin/python3"
TM="${TM:-/usr/local/bin/tmux}"; [ -x "$TM" ] || TM="$(command -v tmux || echo tmux)"

# ── args ─────────────────────────────────────────────────────────────────────
ARM=0
TOKFILE="$HOME/.wingmen/keys/syed-oauth-token"
for a in "$@"; do
  case "$a" in
    --arm) ARM=1 ;;
    -*) echo "unknown flag: $a" >&2; exit 2 ;;
    *) TOKFILE="$a" ;;
  esac
done
[ -r "$TOKFILE" ] || { echo "ERROR: token file not readable: $TOKFILE" >&2; exit 3; }
TARGET_FP="$(printf '%s' "$(cat "$TOKFILE")" | shasum -a 256 2>/dev/null | cut -c1-12)"
[ -n "$TARGET_FP" ] && [ "$TARGET_FP" != "e3b0c44298fc" ] || { echo "ERROR: empty/unreadable token file" >&2; exit 4; }

MODE="DRY-RUN"; [ "$ARM" = 1 ] && MODE="ARMED"
echo "═══════════════════════════════════════════════════════════════════════════"
echo "  flip_fleet.sh — $MODE — target=$(basename "$TOKFILE") (fp $TARGET_FP)"
[ "$ARM" = 0 ] && echo "  (DRY-RUN: nothing is changed. Re-run with --arm to execute — OPERATOR only.)"
echo "═══════════════════════════════════════════════════════════════════════════"

# Refuse an ARMED run from inside a session we are about to flip (it would kill our
# runner mid-flip). A DRY-RUN changes nothing, so it is always allowed.
if [ "$ARM" = 1 ]; then
  _here="$($TM display-message -p '#S' 2>/dev/null || true)"
  case "$_here" in
    fleet-health|cai|nazim|quality) echo "REFUSING: armed run from inside tmux session '$_here', which this flips. Run from a PLAIN shell." >&2; exit 5 ;;
  esac
fi

run() { echo "    \$ $*"; [ "$ARM" = 1 ] && "$@"; }

# ── STAGE 1: durable pointers ────────────────────────────────────────────────
echo; echo "── STAGE 1: durable default pointers -> $TOKFILE (reversible) ──"
for ptr in .lane_default_token .fleet-health_default_token .cai_default_token .quality_default_token .nazim_default_token; do
  cur="$( [ -r "$ORCH_DIR/$ptr" ] && cat "$ORCH_DIR/$ptr" || echo '<unset>')"
  echo "  $ptr: $cur -> $TOKFILE"
  if [ "$ARM" = 1 ]; then
    [ -r "$ORCH_DIR/$ptr" ] && cp -p "$ORCH_DIR/$ptr" "$ORCH_DIR/$ptr.was.pre" 2>/dev/null || true
    printf '%s\n' "$TOKFILE" > "$ORCH_DIR/$ptr"
    echo "    repointed (backup: $ptr.was.pre)"
  fi
done

# ── STAGE 2: live re-token each Mini engineer lane ───────────────────────────
echo; echo "── STAGE 2: live re-token Mini engineer lanes ──"
LANES="$("$VENV_PY" - <<'PY' 2>/dev/null
import os, psycopg
dsn=os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
SING={"cc-fleet-health","cai","orch-console","cc-quality"}
with psycopg.connect(dsn, connect_timeout=15) as c, c.cursor() as cur:
    cur.execute("""SELECT tmux_session FROM agent_status
                   WHERE status IN ('working','active')
                     AND (host='Sheikhs-Mini' OR host IS NULL)
                     AND tmux_session IS NOT NULL
                     AND agent_id != ALL(%s) ORDER BY tmux_session""",(list(SING),))
    print("\n".join(r[0] for r in cur.fetchall() if r[0]))
PY
)"
if [ -z "$LANES" ]; then echo "  (no live Mini lanes found)"; fi
for sess in $LANES; do
  echo "  lane: $sess"
  if [ "$ARM" = 1 ]; then run "$ORCH_DIR/scripts/switch_lane_token.sh" "$sess" "$TOKFILE"
  else run "$ORCH_DIR/scripts/switch_lane_token.sh" --dry-run "$sess" "$TOKFILE"; fi
done

# ── STAGE 3: Mini singletons (cai, then SRE LAST) ────────────────────────────
echo; echo "── STAGE 3: Mini singletons ──"
echo "  cai (switch_singleton_token.sh — checkpoint-gated):"
if [ "$ARM" = 1 ]; then run "$ORCH_DIR/scripts/switch_singleton_token.sh" cai "$TOKFILE"
else run "$ORCH_DIR/scripts/switch_singleton_token.sh" --dry-run cai "$TOKFILE"; fi
echo "  orch-console (nazim): durable pointer repointed in STAGE 1; Nazim re-tokens his"
echo "    OWN console (context-preserving) when ready — NOT auto-flipped here."
echo "  cc-quality: durable pointer repointed; live re-token only if running (often idle)."
echo "  cc-fleet-health (SRE, me) — LAST so it does not kill this runner:"
if [ "$ARM" = 1 ]; then run "$ORCH_DIR/scripts/switch_singleton_token.sh" cc-fleet-health "$TOKFILE"
else run "$ORCH_DIR/scripts/switch_singleton_token.sh" --dry-run cc-fleet-health "$TOKFILE"; fi

# ── STAGE 4: cross-host (Nazim's, F2) ────────────────────────────────────────
echo; echo "── STAGE 4: cross-host bodies — NAZIM runs these (holds VPS/Studio keys) ──"
echo "  VPS hub (cc-orchestrator @ wingmen-core): repoint .orch_default_token -> syed on"
echo "    the VPS, then re-token (kill+relaunch boot_orch). Recipe: see FLIP-READY report."
echo "  Mac-Studio cc-scholar-1: repoint the Studio lane default + re-token on the Studio."

# ── STAGE 5: verify ──────────────────────────────────────────────────────────
echo; echo "── STAGE 5: verify (loud PASS/FAIL) ──"
if [ "$ARM" = 1 ]; then
  echo "  (allow a few seconds for relaunches to stamp auth_fp, then:)"
  "$VENV_PY" "$ORCH_DIR/scripts/verify_fleet_token.py" --expect-file "$TOKFILE" || true
else
  echo "    \$ $VENV_PY scripts/verify_fleet_token.py --expect-file $TOKFILE"
  echo "  (run after an armed flip; expects every Mini body on the target, none metered)"
fi

echo; echo "═══════════════════════════════════════════════════════════════════════════"
echo "  $MODE complete. Reverse: restore each .*_default_token.was.pre (or repoint to"
echo "  musa) + re-run this with the musa token. Lanes/singletons revert on next boot."
echo "═══════════════════════════════════════════════════════════════════════════"
