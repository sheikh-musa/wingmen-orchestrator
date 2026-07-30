#!/usr/bin/env bash
# nudge_cai.sh — R1 (CAI-RESP-377): the ONLY sanctioned programmatic injection
# into the cc-cai tmux session. NUDGE-ONLY with a provenance header — never
# content, never operator words, never authorization claims. Anything cai needs
# to read arrives as an attributable substrate row (agent_messages), judged as
# agent testimony; operator words reach cai only via bridge-logged rows cited by
# operator_messages id, cockpit-verified rows, or his own typing at cai's console.
#
# Usage: scripts/nudge_cai.sh            # auto: count cai's unread bus messages
#        scripts/nudge_cai.sh <N>        # explicit count (no free text accepted)
set -euo pipefail
ORCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMUX_BIN="$(command -v tmux || echo /opt/homebrew/bin/tmux)"

# Shared composer extraction — the fleet's ONE definition of "dim ghost vs real
# staged text" (reset_lane.sh/reset_cai.sh source the same lib; lane_wedge_watchdog.py
# mirrors its dim test). Needed by the composer guard below so all three tools agree
# that a fully-DIM composer line is a ghost/placeholder over an EMPTY buffer, not text.
. "$ORCH_DIR/scripts/lib/composer_capture.sh" || { echo "nudge_cai: composer_capture.sh missing" >&2; exit 9; }

N="${1:-}"
if [ -z "$N" ]; then
  set -a; source "$ORCH_DIR/.env"; set +a
  N="$(PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" - <<'PY'
import os, psycopg
with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
    cur.execute("SELECT count(*) FROM agent_messages WHERE to_agent='cai' AND read_at IS NULL")
    print(cur.fetchone()[0])
PY
)"
fi
case "$N" in (*[!0-9]*|'') echo "nudge_cai: count must be numeric — free text is forbidden (R1)" >&2; exit 2;; esac

LINE="[cc-orchestrator relay] ${N} unread on the bus — drain agent_messages"

# CROSS-HOST (2026-07-26, Nazim). cai's tmux lives on the STUDIO; this script was
# checking the LOCAL tmux and, from the Mini, printing "no live cai session" — a
# claim about a host it cannot see. cai was alive and idle with 4 unread at the
# time. That is tonight's defect class exactly: a measurement whose instrument
# cannot reach what it certifies must report THAT, never a finding.
#
# So: no local '=cai' session is NOT evidence of absence. Re-exec over SSH on
# cai's host (reset_cai.sh's documented convention), where the composer guard
# below runs against the REAL pane. Non-interactive ssh gets a bare PATH, so
# tmux must be reachable — hence the explicit PATH (a bare 'tmux' there fails
# with 'command not found', which would look identical to a dead session).
CAI_SSH_HOST="${CAI_SSH_HOST:-Musa@mac-studio}"
if ! "$TMUX_BIN" has-session -t '=cai' 2>/dev/null; then
  if [ -n "${NUDGE_CAI_REMOTE:-}" ]; then
    # We ARE the remote leg and still see no session — now absence is observed.
    echo "nudge_cai: no live cai session on $(hostname -s) (log is durable; nothing lost)" >&2
    exit 0
  fi
  # rc must be captured from ssh ITSELF: after `fi`, $? is the if-statement's
  # status (0 when the condition merely tested false), which silently turned an
  # unreachable host back into a success. `|| rc=$?` also shields it from set -e.
  rc=0
  ssh -o ConnectTimeout=10 -o BatchMode=yes "$CAI_SSH_HOST" \
      "cd ~/wingmen/orchestrator && NUDGE_CAI_REMOTE=1 PATH=/opt/homebrew/bin:\$PATH bash scripts/nudge_cai.sh $N" \
      || rc=$?
  [ "$rc" = 0 ] && exit 0
  # Distinguish "cai refused/absent" (the remote leg spoke) from "we never got
  # to ask" — ssh's own failure is 255. Never convert unreachability into a verdict.
  if [ "$rc" = 255 ]; then
    echo "nudge_cai: COULD NOT CHECK — cai is not local and $CAI_SSH_HOST is unreachable." >&2
    echo "           This is NOT 'cai is down'; the bus row is durable either way." >&2
  fi
  exit "$rc"
fi

# COMPOSER GUARD (2026-07-25, cc-orchestrator's observation). `send-keys -l` APPENDS: if cai has
# half-typed text staged, this would concatenate the nudge onto it and then submit the hybrid —
# putting words in cai's mouth that neither we nor cai composed, on a governance body whose entire
# value is attributable authorship. cai's composer has held genuinely-changing unsent text for
# whole sessions (an idle body stages its OWN next step there), and the hub carried this property
# by hand all night by simply never nudging. A safety property that depends on every future body
# remembering is not a safety property, so it lives here now.
#
# We do NOT clear the composer: that text is cai's own draft and destroying it is not a nudge's
# business. We REFUSE instead, loudly, and tell the caller what to do — the durable log means a
# skipped nudge loses nothing (Option B: delivery is guaranteed by reconciliation, not keystrokes).
# NOTE the byte-level trap, found by testing this against cai's real pane rather than trusting it:
# the TUI renders the prompt as '❯' + U+00A0 NON-BREAKING SPACE (\302\240), not '❯' + ' '. A
# pattern written as '^❯ ' matches NOTHING, so the guard would silently never fire — failing OPEN
# while looking like protection, which is worse than no guard. Strip the marker, then delete NBSP
# bytes explicitly before the ordinary whitespace squeeze.
COMPOSER="$("$TMUX_BIN" capture-pane -t '=cai:0.0' -p 2>/dev/null | grep '^❯' | tail -1 | sed 's/^❯//' | LC_ALL=C tr -d '\302\240' | tr -d '[:space:]')"
# QUEUE GUARD (2026-07-26, Nazim). The 'Press up to edit queued messages' placeholder was
# treated as "composer empty, safe to nudge" — correct about STAGED TEXT, and wrong about the
# thing that matters. That placeholder means the body ALREADY HAS MESSAGES QUEUED AND
# UNPROCESSED. Nudging then does not deliver a signal; it appends one more item to a queue the
# body is demonstrably not draining. Observed tonight: cai sat at 100% context with my
# count-only nudges stacked unconsumed in exactly this state, and I sent ~6 of them.
# A nudge is signal-only and BEST-EFFORT (Option B: delivery is guaranteed by the durable log
# and reconciliation, never by the keystroke). So when the body is visibly backed up, the
# correct action is to send NOTHING and let the log do its job — refusing costs us nothing and
# stops us adding noise to a saturated body.
if [ "$COMPOSER" = "Pressuptoeditqueuedmessages" ]; then
    echo "nudge_cai: SKIPPED — cai already has QUEUED, unprocessed messages ('Press up to edit" >&2
    echo "           queued messages'). It has been signalled and is not draining; another nudge" >&2
    echo "           only lengthens the queue. The bus rows are durable — cai reads them at its" >&2
    echo "           next turn. Chase it with a reset or leave it, but do not keep typing at it." >&2
    exit 0
fi
if [ -n "$COMPOSER" ]; then
    # GHOST GUARD (2026-07-29 root cause). The plain `-p` COMPOSER above cannot see
    # colour: an IDLE Claude-Code lane paints its most-recent history entry as a DIM
    # (SGR-2) autosuggestion GHOST into an EMPTY input buffer. That ghost read as
    # "unsent text" and this guard REFUSED to nudge a genuinely-idle cai — leaving it
    # un-nudgeable with pending bus work (the exact stall this fix addresses). So
    # before refusing, re-read the SGR-preserving pane and, if the composer content
    # is ENTIRELY dim, treat it as EMPTY and PROCEED (the buffer really is empty; a
    # nudge appends to nothing). Only REAL, non-dim typed text still blocks — that is
    # cai's own draft and -l would concatenate onto it, so we never clobber it.
    # _cc_dim_only lives in composer_capture.sh (sourced above): 0/true == all-dim.
    RAW_E="$("$TMUX_BIN" capture-pane -t '=cai:0.0' -p -e 2>/dev/null)"
    if [ -n "$RAW_E" ] && _cc_dim_only "$RAW_E"; then
        echo "nudge_cai: composer shows only a DIM ghost/placeholder over an empty buffer — proceeding with the nudge." >&2
    else
        # Real non-dim text (or no SGR data to prove otherwise — fail closed, preserve).
        echo "nudge_cai: REFUSED — cai's composer holds real unsent text; -l would concatenate onto it and submit the hybrid." >&2
        echo "           The bus row is already durable; cai reads it at its next turn. If this is urgent," >&2
        echo "           surface it another way rather than typing into a staged draft." >&2
        exit 3
    fi
fi

# '=cai:0.0': tmux 3.7a send-keys can't always resolve a bare '=cai' to a pane
"$TMUX_BIN" send-keys -t '=cai:0.0' -l "$LINE"
sleep 1
"$TMUX_BIN" send-keys -t '=cai:0.0' Enter
echo "nudged cai: $LINE"
