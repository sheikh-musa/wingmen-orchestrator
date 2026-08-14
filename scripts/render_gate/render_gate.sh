#!/usr/bin/env bash
# render_gate.sh — staged render-gate for client share pages.
# 3a: render the WRAPPED output (publish_share's actual emission, NOT the source
#     — the op#12996 trap) + responsiveness asserts. 3c: post-deploy live-URL
#     cache-bust check with VERCEL-pulled creds (never the deleted local file).
#
# STAGE behavior (RENDER_GATE_STAGE, default 0 — each stage separately console-signed):
#   0 OBSERVE : log PASS/WARN quietly, emit PNGs, NEVER block (exit 0).
#   1 LOUD    : on WARN, surface LOUDLY (banner + best-effort bus alert to console)
#               + human-eyeball ADVISORY, but STILL exit 0 (publish proceeds).
#               Hard-refuse is Stage 2 ONLY (per console 21279).
#   2 ENFORCE : (not built) hard-refuse on WARN, gated on a per-hash human approval.
# This script ALWAYS exits 0 through Stage 1 — non-blocking by construction. The
# `|| true` in the publish_share touchpoint is belt-and-suspenders on top of that.
#
# Usage: render_gate.sh <fragment.html> <slug> [--client|--internal] [--live]
set -uo pipefail   # NB: no -e — must never abort the caller through Stage 1

HERE="$(cd "$(dirname "$0")" && pwd)"
ORCH="$HOME/wingmen/orchestrator"
SHARE_DIR="$HOME/wingmen/wingmen-share"
FRAG="${1:?usage: render_gate.sh <fragment.html> <slug> [--client|--internal] [--live]}"
SLUG="${2:?slug required}"; shift 2 || true
CLASS="client"; LIVE=0
for a in "$@"; do case "$a" in --internal) CLASS=internal;; --client) CLASS=client;; --live) LIVE=1;; esac; done
STAGE="${RENDER_GATE_STAGE:-0}"

OUT="$ORCH/reports/share-render/$SLUG"
mkdir -p "$OUT"
log(){ printf '[render-gate:%s] %s\n' "$SLUG" "$*"; }

# LOUD, NON-BLOCKING warn (Stage 1+). Returns 0 always — never blocks a publish.
loud_warn(){
  local what="$1" detail="${2:-}"
  if [ "${STAGE}" -ge 1 ]; then
    printf '\n\033[1;33m'
    printf '======================================================================\n'
    printf ' RENDER-GATE WARN [%s] slug=%s  (Stage %s: LOUD but NON-BLOCKING)\n' "$what" "$SLUG" "$STAGE"
    printf ' publish CONTINUES. A HUMAN SHOULD EYEBALL before trusting it:\n'
    printf '   %s\n   %s\n' "$OUT/mobile-top.png" "$OUT/verdict.json"
    [ -n "$detail" ] && printf ' detail: %s\n' "$detail"
    printf '======================================================================\033[0m\n\n'
    # best-effort loud bus alert to console — never fails / never blocks the publish
    if [ -n "${DATABASE_URL:-}" ]; then
      psql "$DATABASE_URL" -v ON_ERROR_STOP=0 -c \
        "SELECT set_config('app.current_agent_id','cc-fleet-health',true);
         INSERT INTO agent_messages(from_agent,to_agent,message_type,subject,body,requires_response,priority,created_at,posted_by_identity)
         VALUES ('cc-fleet-health','orch-console','update',
           '⚠ RENDER-GATE WARN (non-blocking) — ${what} on client page ${SLUG}',
           'Stage-${STAGE} render-gate flagged ${what} on ${SLUG} at publish; publish PROCEEDED (non-blocking, hard-refuse is Stage-2). Eyeball ${OUT}/mobile-top.png; verdict ${OUT}/verdict.json. detail: ${detail}',
           true,'P2', now(),'cc-fleet-health');" >/dev/null 2>&1 || true
    fi
  else
    log "$what WARN (Stage 0 observe) ${detail}"
  fi
}

# ── 3a: render the WRAPPED output + asserts ──
log "3a render+assert ($CLASS, stage $STAGE) -> $OUT"
VERDICT_JSON="$(node "$HERE/render_gate_check.js" "$FRAG" "$SLUG" "$OUT" 2>>"$OUT/render.err")"
A_PASS="$(printf '%s' "$VERDICT_JSON" | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{try{console.log(JSON.parse(s).pass)}catch{console.log("ERR")}})' 2>/dev/null)"
if [ "$A_PASS" = "true" ]; then
  log "3a PASS — wrapped output responsive (390/standards/no-overflow); PNGs in $OUT"
else
  A_NOTES="$(printf '%s' "$VERDICT_JSON" | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{try{console.log((JSON.parse(s).notes||[]).join("; "))}catch{console.log("")}})' 2>/dev/null)"
  loud_warn "3a" "$A_NOTES"
fi

# ── 3c: live-URL cache-bust check (VERCEL-pulled creds only) ──
if [ "$LIVE" = "1" ]; then
  log "3c live-URL check"
  ENVFILE="$OUT/.vercel-env"
  if ( cd "$SHARE_DIR" && vercel env pull "$ENVFILE" --environment production --yes ) >>"$OUT/render.err" 2>&1; then
    SHARE_USER="$(grep -E '^SHARE_USER=' "$ENVFILE" | head -1 | cut -d= -f2- | tr -d '"')"
    SHARE_PASSWORD="$(grep -E '^SHARE_PASSWORD=' "$ENVFILE" | head -1 | cut -d= -f2- | tr -d '"')"
    rm -f "$ENVFILE"
    URL="https://share.wingmen.dev/r/$SLUG"
    CODE=000
    for i in 1 2 3 4 5; do
      CB="$(date +%s)$i"
      CODE="$(curl -s -o "$OUT/live.html" -w '%{http_code}' \
        -u "$SHARE_USER:$SHARE_PASSWORD" -H 'Cache-Control: no-cache' \
        "$URL?cb=$CB" 2>>"$OUT/render.err")"
      [ "$CODE" = "200" ] && break
      sleep 3
    done
    if [ "$CODE" = "200" ]; then
      HAS_VP=$(grep -qi 'name="viewport"' "$OUT/live.html" && echo yes || echo no)
      log "3c PASS — live 200, wrapper viewport=$HAS_VP (creds via vercel env pull)"
    else
      loud_warn "3c" "live URL returned $CODE after 5 retries (cache/alias not propagated, or cred issue)"
    fi
  else
    log "3c SKIP — 'vercel env pull' unavailable (needs vercel auth in $SHARE_DIR); NEVER falls back to the stale local .share-password"
  fi
fi

log "done (stage $STAGE: exits 0 — non-blocking through Stage 1)"
exit 0
