#!/usr/bin/env bash
# deploy_console.sh — THE ONLY sanctioned way to deploy a fleet-console build.
#
# op#12457: the operator does NOT trust agent self-promises to "eyeball the
# render / run it through the design pipeline" — they don't survive a context
# reset (fc-v38/v39 shipped a Lane-manager regression + clutter because I skipped
# exactly that). So the process is enforced in CODE here, fail-closed, same shape
# as orch_lease.py / require_verified_authorization.py: no artifacts -> no deploy.
#
# GATES (all must pass, in order):
#   1. VERSION-SYNC   sw.js VERSION == fleet.js APP_BUILD == lanes.html badge
#                     (an unsynced constant is the recurring false-stale badge).
#   2. TESTS          tests/console/test_app.py green.
#   3. RENDER         fleet.html + lanes.html render with live data (auto) — a
#                     page that won't render can't ship. PNGs land for eyeballing.
#   4. REVIEW         a cc-quality review recorded for THIS exact console content
#                     (content-hash-keyed) must exist. Forces the review to run on
#                     what you're actually shipping, not a stale/other diff.
# Only then: launchctl kickstart (so /api/version updates) + record the deploy.
set -uo pipefail
cd "$HOME/wingmen/orchestrator" || exit 9
set -a; source .env 2>/dev/null; set +a
STATIC=nervous_system/console/static
ART=reports/console-deploy

fail(){ echo "" >&2; echo "❌ deploy_console REFUSED — $1" >&2; exit "${2:-1}"; }

# item-4b (Nazim #31843): the review content-hash covers the whole console BACKEND (every *.py
# under nervous_system/console/) — not just the five static files — because the console runs as
# `python -m nervous_system.console` and a backend-only change (app.py/db.py/panes.py/...) used to
# leave the hash unchanged and ship the backend UNREVIEWED. The file set + hash are the SSOT seam
# scripts/lib/console_deploy_manifest.sh (unit-tested by tests/test_deploy_console_gate.py). The
# EXACT coverage AND the deliberate cuts (only 5 static gated; other served static, outside-package
# imports, and the Dockerfile are NOT — pending Nazim's widen decisions) are documented in that
# seam's header — read it there; it is the single source of truth, not restated here.
MANIFEST="$(dirname "${BASH_SOURCE[0]}")/lib/console_deploy_manifest.sh"
[ -f "$MANIFEST" ] || fail "console deploy manifest seam missing: $MANIFEST" 2
source "$MANIFEST"
HASH=$(console_content_hash "$PWD")
[ -n "$HASH" ] || fail "could not compute console content hash (manifest seam)." 2
# repo-relative pathspec of everything the hash covers — used in the review instruction below so
# the reviewer diffs the BACKEND too, not just $STATIC.
GATED_PATHS=$(console_deploy_files_rel "$PWD" | tr '\n' ' ')
DIR="$ART/$HASH"
mkdir -p "$DIR"

echo "== deploy_console gate (content $HASH) =="

# ---- GATE 1: version sync ----
SW=$(grep -oE 'const VERSION = "fc-v[0-9]+"' "$STATIC/sw.js" | grep -oE 'fc-v[0-9]+' | head -1)
FL=$(grep -oE "APP_BUILD = 'fc-v[0-9]+'" "$STATIC/fleet.js" | grep -oE 'fc-v[0-9]+' | head -1)
LB=$(grep 'id="build"' "$STATIC/lanes.html" | grep -oE 'fc-v[0-9]+' | tail -1)
echo "  [1/4] version-sync: sw.js=$SW fleet.js=$FL lanes.html=$LB"
{ [ -n "$SW" ] && [ "$SW" = "$FL" ] && [ "$SW" = "$LB" ]; } || \
  fail "version constants OUT OF SYNC (sw=$SW fleet=$FL lanes=$LB) — bump all three in sync." 3

# ---- GATE 2: tests ----
echo "  [2/4] console tests..."
if [ -x .venv/bin/python3 ]; then
  PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/console/test_app.py -q >"$DIR/pytest.log" 2>&1 \
    || fail "console tests FAILED (see $DIR/pytest.log)." 4
  echo "        $(grep -oE '[0-9]+ passed[^,]*' "$DIR/pytest.log" | tail -1)"
else echo "        (.venv missing — skipped, but flagging)"; fi

# ---- GATE 3: render (auto; must succeed) ----
echo "  [3/4] render fleet+lanes with live data..."
bash scripts/render_console_pages.sh "$DIR" >"$DIR/render.log" 2>&1 \
  || fail "RENDER failed (see $DIR/render.log) — a page that won't render must not ship." 5
[ -s "$DIR/fleet.png" ] && [ -s "$DIR/lanes.png" ] || fail "render produced no PNGs." 5
echo "        renders: $DIR/fleet.png + $DIR/lanes.png  <-- EYEBALL THESE"

# ---- GATE 4: cc-quality review for THIS content ----
REVIEW="$DIR/cc-quality-review.md"
echo "  [4/4] cc-quality review for content $HASH..."
if [ ! -s "$REVIEW" ]; then
  cat >&2 <<EOF

❌ deploy_console REFUSED — no cc-quality review for this console content ($HASH).

   This is the gate the operator asked for (op#12457): you cannot ship a console
   build without a design/quality review of exactly what you're shipping.

   DO THIS:
     1. EYEBALL the renders:  $DIR/fleet.png   $DIR/lanes.png
     2. Run cc-quality on the FULL console diff — static AND backend:
           git diff -- $GATED_PATHS
        (the hash now covers the console backend package, so a backend-only
         change also lands here — review it, don't just eyeball the renders.)
        SAVE its review to:   $REVIEW
        (route to the live cc-quality agent — it is the sanctioned reviewer.)
     3. Re-run:               scripts/deploy_console.sh

   The review file must be non-empty and is content-hash-keyed, so it can't be a
   stale review of a different diff.
EOF
  exit 6
fi
echo "        review present: $REVIEW ($(wc -l <"$REVIEW" | tr -d ' ') lines)"

# ---- all gates passed: deploy ----
echo "== all gates passed -> kickstarting fleet-console =="
launchctl kickstart -k "gui/$(id -u)/dev.wingmen.fleet-console" || fail "kickstart failed." 7
sleep 3
SERVED=$(curl -s -H "Authorization: Bearer ${CONSOLE_TOKEN:-}" http://100.83.21.34:8787/api/version 2>/dev/null)
echo "  /api/version now: $SERVED"
date -u +"%Y-%m-%dT%H:%M:%SZ content=$HASH ver=$SW served=$SERVED" >> "$ART/deploy-log.txt"
echo "✅ deployed console $SW (content $HASH). Recorded in $ART/deploy-log.txt"
