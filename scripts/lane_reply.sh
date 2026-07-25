#!/usr/bin/env bash
# lane_reply.sh — THE reply path for any lane agent that talks to an outside group
# (a client, or an SME dev group), PHASE-GATED in code rather than by prompt.
#
# A lane agent earns its group in stages. The stage lives in the DB, on the channel row:
#     bot_channels.group_routing->>'agent_phase'   for that channel_key
#
#   drill      — nothing leaves the building. Logged under '<tag>-drill'.
#   supervised — filed as a DRAFT to the lane's reviewer (agent_messages), who sends it.
#                Logged under '<tag>-draft'. The group sees nothing.
#   direct     — the lane answers the group itself. Steady state.
#
# Missing / unknown / absent phase = fail CLOSED to drill. A config gap must never be the
# thing that exposes an outside party.
#
# The reviewer defaults to 'orch-console' (Nazim) and can be set per channel with
#     group_routing->>'agent_reviewer'
#
# Usage:  scripts/lane_reply.sh <channel_key> "<text>"
#         echo "<text>" | scripts/lane_reply.sh <channel_key>
#
# History: generalized 2026-07-25 from scripts/irsyad_reply.sh (which now delegates here),
# so the second and third lane inherit the proven staircase instead of near-copies drifting.
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"
CHANNEL="${1:?usage: lane_reply.sh <channel_key> \"<text>\"}"
TEXT="${2:-$(cat)}"
[ -n "$TEXT" ] || { echo "no text to send" >&2; exit 1; }

ORCH_DIR="$ORCH_DIR" PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" - "$CHANNEL" "$TEXT" <<'PY'
import os, re, sys, shutil, subprocess, psycopg

CHANNEL, TEXT = sys.argv[1], sys.argv[2]
ORCH = os.environ.get("ORCH_DIR", os.path.expanduser("~/wingmen/orchestrator"))
SUB_TAG = os.environ.get("CC_AGENT_ID") or None


def _agent_from_tmux(dsn):
    """Resolve the speaker from the tmux session via fleet_lanes.

    Why: the launcher exports CC_BASE_AGENT_ID into the `claude` process, but a lane's
    shell tool does NOT reliably inherit it (found 2026-07-25 by cc-cosem-exams, which hit
    this script cold and would have concluded it had no reply path at all). The tmux session
    name is a fact about where we are running, so derive from it and treat env as the hint,
    not the requirement.
    """
    tmux = shutil.which("tmux") or "/opt/homebrew/bin/tmux"
    try:
        sess = subprocess.run([tmux, "display-message", "-p", "#S"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:                                   # noqa: BLE001
        return None
    if not sess:
        return None
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        # agent_status.tmux_session is stamped by launch_dangerous_cc.sh from inside the
        # pane, so it is the authoritative session->agent mapping. fleet_lanes is the
        # fallback for lanes whose row name matches the session (they need not: the exams
        # lane is `cosem-exams` in the registry but runs in tmux `exams`).
        cur.execute("SELECT base_agent_id FROM agent_status WHERE tmux_session=%s "
                    "AND base_agent_id IS NOT NULL ORDER BY last_heartbeat DESC NULLS LAST "
                    "LIMIT 1", (sess,))
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute("SELECT base_agent_id FROM fleet_lanes WHERE lane=%s", (sess,))
        row = cur.fetchone()
    return row[0] if row else None


def env_val(key):
    """Read a key from the orchestrator .env. Never printed."""
    m = re.search(r'^%s=(.+)$' % re.escape(key), open(os.path.join(ORCH, ".env")).read(), re.M)
    return m.group(1).strip() if m else None

dsn = os.environ.get("DATABASE_URL") or env_val("DATABASE_URL")

AGENT = (os.environ.get("CC_BASE_AGENT_ID") or os.environ.get("LANE_AGENT_ID")
         or _agent_from_tmux(dsn) or "")
if not AGENT:
    sys.exit("cannot attribute this reply: no CC_BASE_AGENT_ID / LANE_AGENT_ID, and this tmux "
             "session does not match a fleet_lanes.lane row.\nSet LANE_AGENT_ID=<your agent id> "
             "so the log names who spoke.")
if SUB_TAG and not SUB_TAG.startswith(AGENT + "-"):
    SUB_TAG = None                      # agent_messages sub_tag family-prefix CHECK

with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT token_env_key, allowed_chat_ids, channel_tag, "
                "coalesce(group_routing->>'agent_phase','drill'), "
                "coalesce(group_routing->>'agent_reviewer','orch-console') "
                "FROM bot_channels WHERE channel_key=%s", (CHANNEL,))
    row = cur.fetchone()
    if not row:
        sys.exit("no such channel: %s" % CHANNEL)
    token_env_key, chat_ids, tag, phase, reviewer = row
    if phase not in ("drill", "supervised", "direct"):
        print("unknown agent_phase %r — failing closed to drill" % phase, file=sys.stderr)
        phase = "drill"

    def log_out(log_tag, chat, delivered):
        cur.execute(
            "INSERT INTO operator_messages (direction, channel, chat_id, tag, text, delivered, "
            "from_name) VALUES ('outbound','telegram',%s,%s,%s,%s,%s)",
            (chat, log_tag, TEXT, delivered, AGENT))

    if phase == "drill":
        log_out(f"{tag}-drill", None, False)
        conn.commit()
        print(f"DRILL phase — NOT sent. Logged under tag '{tag}-drill'.")
        sys.exit(0)

    if phase == "supervised":
        cur.execute(
            "INSERT INTO agent_messages (from_agent, sub_tag, to_agent, message_type, subject, "
            "body, priority, requires_response) VALUES (%s,%s,%s,'review_request',%s,%s,'P1',true) "
            "RETURNING id",
            (AGENT, SUB_TAG, reviewer,
             f"{CHANNEL} reply DRAFT — review + send", TEXT))
        msg_id = cur.fetchone()[0]
        log_out(f"{tag}-draft", str(chat_ids[0]) if chat_ids else None, False)
        conn.commit()
        print(f"SUPERVISED phase — draft filed for {reviewer} as agent_messages #{msg_id}. "
              f"NOT sent to the group yet.")
        sys.exit(0)

    # ── direct ────────────────────────────────────────────────────────────────────────
    tok = env_val(token_env_key)
    if not tok:
        sys.exit("token %s not in .env" % token_env_key)
    if not chat_ids:
        sys.exit("channel %s has no allowed_chat_ids (group chat_id)" % CHANNEL)
    chat = str(chat_ids[0])
    # chunked sender: long replies must not be truncated at Telegram's 4096.
    # token/chat/text via env, never argv — keeps the token out of `ps`.
    env = dict(os.environ, TG_TOK=tok, TG_CHAT=chat, TG_TEXT=TEXT)
    rc = subprocess.run([os.path.join(ORCH, ".venv/bin/python3"),
                         os.path.join(ORCH, "scripts/_tg_chunked_send.py")], env=env).returncode
    log_out(tag, chat, rc == 0)
    conn.commit()
    if rc != 0:
        sys.exit("send FAILED (logged, delivered=false)")
    print(f"sent to {CHANNEL} (chat {chat})")
PY
