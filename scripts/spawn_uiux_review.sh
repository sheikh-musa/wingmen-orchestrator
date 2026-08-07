#!/usr/bin/env bash
# spawn_uiux_review.sh — capture screenshots of a live app at mobile+desktop, then
# boot a fresh cc-uiux reviewer that READS the screenshots and reviews the render.
# Solves the fleet's terminal-blindness to UI (eyeball-ui-before-deploy): agents
# can't see a page, but they CAN read a PNG they captured.
#
# Usage:
#   spawn_uiux_review.sh <base_url> <route1,route2,...> "<scope note>" [capture_repo_dir]
#     base_url        : running app (e.g. https://cosem-platform-demo.vercel.app)
#     routes          : comma-separated (e.g. /login,/dashboard,/exams)
#     scope           : what to focus the review on
#     capture_repo_dir: a repo dir with playwright installed to run the capture
#                       (default: ~/wingmen/projects/cosem-platform)
set -euo pipefail
ORCH_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BASE_URL="${1:?usage: spawn_uiux_review.sh <base_url> <routes> \"<scope>\" [capture_repo_dir]}"
ROUTES="${2:?routes required (comma-separated)}"
SCOPE="${3:-General mobile+desktop responsiveness & UX review.}"
CAP_DIR="${4:-$HOME/wingmen/projects/cosem-platform}"

STAMP="$(date -u +%Y%m%d-%H%M%S)"
SESSION="uiux-${STAMP}"
SHOT_DIR="$ORCH_DIR/reports/uiux/${SESSION}"
mkdir -p "$SHOT_DIR"

echo "[uiux] capturing $BASE_URL routes=$ROUTES -> $SHOT_DIR"
# capture via the repo's playwright (needs chromium; install once if missing)
( cd "$CAP_DIR" && node "$ORCH_DIR/scripts/lib/uiux_capture.mjs" "$BASE_URL" "$ROUTES" "$SHOT_DIR" ) \
  > "$SHOT_DIR/manifest.json" 2> "$SHOT_DIR/capture.log" || {
    echo "[uiux] capture FAILED — see $SHOT_DIR/capture.log (likely need: cd $CAP_DIR && npx playwright install chromium)" >&2
  }
SHOT_COUNT=$(ls "$SHOT_DIR"/*.png 2>/dev/null | wc -l | tr -d ' ')
echo "[uiux] $SHOT_COUNT screenshots in $SHOT_DIR"

BRIEF_FILE="$ORCH_DIR/logs/reviewer-briefs/${SESSION}.md"
mkdir -p "$ORCH_DIR/logs/reviewer-briefs"
cat > "$BRIEF_FILE" <<EOF
# UI/UX REVIEW — ${BASE_URL}

You are **cc-uiux**, an INDEPENDENT UI/UX reviewer. You review the RENDER, not the code.
Screenshots were captured at MOBILE (390x844) and DESKTOP (1440x900) and are in:
  ${SHOT_DIR}
READ each PNG (mobile__*.png and desktop__*.png) and review it. Capture manifest: ${SHOT_DIR}/manifest.json

## Scope
${SCOPE}

## Review checklist (report concrete, per-screenshot findings with the filename)
- MOBILE FIRST: does every screen actually work at 390px wide? horizontal overflow / cut-off content / tiny tap targets / overlapping elements / unreadable text / off-screen buttons = FINDINGS.
- Layout: alignment, spacing, hierarchy, whitespace; nothing clipped or colliding.
- Nav: is primary navigation usable on mobile (hamburger/bottom-nav vs a desktop bar squished)?
- Forms/inputs: labels, sizing, keyboard/tap ergonomics on mobile.
- Contrast/readability/a11y basics.
- Desktop: not just a stretched mobile view; uses the space sensibly.
- Flag anything that would embarrass us in front of a director at a live showcase.

## Output
Post an agent_messages update to to_agent='cc-orchestrator' with a VERDICT: SHIP-READY or CHANGES-REQUIRED, and a prioritized list of concrete fixes (each tied to a screenshot filename + viewport). Read-only — do not edit code; your job is to SEE and report.
EOF

echo "$(date -u +%FT%TZ) | uiux spawn base=$BASE_URL routes=$ROUTES session=$SESSION shots=$SHOT_COUNT brief=$BRIEF_FILE scope=$(printf %q "$SCOPE")" >> "$ORCH_DIR/logs/reviewer_spawns.log"

# boot the cc-uiux reviewer in the screenshot dir so it can read the PNGs directly
tmux new-session -d -s "$SESSION" -c "$SHOT_DIR" \
  "CC_BASE_OVERRIDE=cc-uiux exec $ORCH_DIR/scripts/launch_dangerous_cc.sh"
sleep 20
if tmux has-session -t "$SESSION" 2>/dev/null; then
  MSG="[uiux-start] Read your brief at ${BRIEF_FILE} and review the screenshots in ${SHOT_DIR}. Read-only; post your SHIP-READY / CHANGES-REQUIRED verdict to cc-orchestrator on the bus."
  tmux send-keys -t "$SESSION" -l "$MSG"; sleep 1; tmux send-keys -t "$SESSION" Enter
  echo "[uiux] cc-uiux reviewing in tmux '$SESSION' ($SHOT_COUNT shots). watch: tmux attach -t $SESSION"
else
  echo "[uiux] ERROR: cc-uiux session failed to boot" >&2; exit 1
fi
