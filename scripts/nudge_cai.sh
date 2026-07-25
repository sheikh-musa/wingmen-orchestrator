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
"$TMUX_BIN" has-session -t '=cai' 2>/dev/null || { echo "nudge_cai: no live cai session (log is durable; nothing lost)" >&2; exit 0; }

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
if [ -n "$COMPOSER" ] && [ "$COMPOSER" != "Pressuptoeditqueuedmessages" ]; then
    echo "nudge_cai: REFUSED — cai's composer holds unsent text; -l would concatenate onto it and submit the hybrid." >&2
    echo "           The bus row is already durable; cai reads it at its next turn. If this is urgent," >&2
    echo "           surface it another way rather than typing into a staged draft." >&2
    exit 3
fi

# '=cai:0.0': tmux 3.7a send-keys can't always resolve a bare '=cai' to a pane
"$TMUX_BIN" send-keys -t '=cai:0.0' -l "$LINE"
sleep 1
"$TMUX_BIN" send-keys -t '=cai:0.0' Enter
echo "nudged cai: $LINE"
