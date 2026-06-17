#!/usr/bin/env bash
# spawn_reviewer.sh — on-demand independent reviewer (CAI-RESP-257/258).
#
# Spawns a FRESH cc-reviewer-N lane (never inherits builder/orchestrator context)
# to read-only review a diff, apply the mandatory review_dimensions, and post an
# artifact-cited verdict to the bus. Identity comes from the STANDARD launcher via
# CC_BASE_OVERRIDE=cc-reviewer; auto_agent_id hard-refuses forging an authority
# identity (the guardrail). Every spawn is audited to logs/reviewer_spawns.log.
#
# Usage:
#   spawn_reviewer.sh <target_dir> <diff_ref> "<scope note>"
#     target_dir : repo/worktree to review in (e.g. ~/wingmen/projects/ihsanos)
#     diff_ref   : git range/branch/PR to review (e.g. main..HEAD, feat/x)
#     scope note : what to focus on (free text)
set -euo pipefail

ORCH_DIR="$HOME/wingmen/orchestrator"
LAUNCHER="$ORCH_DIR/scripts/launch_dangerous_cc.sh"
AUDIT_LOG="$ORCH_DIR/logs/reviewer_spawns.log"

TARGET_DIR="${1:?usage: spawn_reviewer.sh <target_dir> <diff_ref> \"<scope>\"}"
DIFF_REF="${2:?missing diff_ref}"
SCOPE="${3:-general review}"
[ -d "$TARGET_DIR" ] || { echo "ERROR: target_dir not found: $TARGET_DIR" >&2; exit 1; }

# Unique tmux session per review (reviewer-<epoch-ish>); pass timestamp from caller
# env if determinism is needed, else derive from the diff ref + a pid for uniqueness.
SESSION="reviewer-$(printf '%s' "$DIFF_REF" | tr -c 'a-zA-Z0-9' '-' | cut -c1-20)-$$"

REVIEW_BRIEF="You are cc-reviewer-N — an INDEPENDENT, READ-ONLY reviewer (CAI-RESP-257/258). \
You did NOT write this code and you are NOT this repo's engineer. HARD RULES: do not edit, \
commit, push, or run mutating commands; you review and report only. Review the diff: \
${DIFF_REF} in $(basename "$TARGET_DIR"). Scope: ${SCOPE}. Apply the mandatory review_dimensions \
from the substrate (SELECT name, description, applies_when FROM review_dimensions) — finance \
(money flows/totals/reconciliation) and security (PII/auth/secrets/RLS) where the diff touches \
them. Then POST an artifact-cited verdict to agent_messages, from_agent='cc-reviewer' (set \
app.current_agent_id to your cc-reviewer-N sub_tag), to_agent='cai' (always) and 'cc-orchestrator', \
on its own thread, routed: message_type='update' if clean/advisory (no rr); 'blocker' if a real \
defect (also notify the builder, requires_response=true); 'challenge' ONLY to dispute a ruling/spec. \
You review, you never rule — verdicts are advisory to cai. Cite file:line for every finding. When \
done, exit."

mkdir -p "$ORCH_DIR/logs"
# Audit (CAI-RESP-258): record the spawn provenance BEFORE launch.
printf '%s | spawn target=%s diff=%s session=%s scope=%q\n' \
    "$(date -u +%FT%TZ)" "$TARGET_DIR" "$DIFF_REF" "$SESSION" "$SCOPE" >> "$AUDIT_LOG"

# Fresh lane via STANDARD launcher; identity forced to cc-reviewer-N by the override.
CC_BASE_OVERRIDE="cc-reviewer" \
    tmux new-session -d -s "$SESSION" -c "$TARGET_DIR" "$LAUNCHER"

# Deliver the review brief as the lane's task (send-keys = signal/initial-task only).
sleep 6
tmux send-keys -t "$SESSION" -l "$REVIEW_BRIEF"
sleep 1
tmux send-keys -t "$SESSION" Enter

echo "spawned cc-reviewer in tmux '$SESSION' (target=$TARGET_DIR diff=$DIFF_REF)"
echo "audit: $AUDIT_LOG"
echo "watch: tmux attach -t $SESSION   (detach: Ctrl-b d)"
