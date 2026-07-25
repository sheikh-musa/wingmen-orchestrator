#!/usr/bin/env bash
# irsyad_reply.sh — cc-irsyad's ONLY sanctioned reply path to the Gazzabyte /
# Irsyad-Support group, PHASE-GATED in code (not by promise).
#
# gazzabyte-irsyad is a LIVE CLIENT channel. The dedicated agent earns the live
# thread in stages; the stage lives in the DB, on the channel row itself:
#     bot_channels.group_routing->>'agent_phase' for channel_key='gazzabyte-irsyad'
#
#   drill      — nothing leaves the building. The reply is logged against the
#                'irsyad-drill' tag so the loop is exercised end-to-end offline.
#   supervised — the reply is filed as a DRAFT to Nazim (agent_messages ->
#                orch-console). Nazim reviews and sends. Client sees nothing
#                until a human has read it.
#   direct     — cc-irsyad answers the group itself (post-cutover steady state).
#
# Missing / unknown phase = fail CLOSED to drill. A live client is never exposed
# by a config gap.
#
# Usage: scripts/irsyad_reply.sh "<text>"
#        echo "<text>" | scripts/irsyad_reply.sh
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"
TEXT="${1:-$(cat)}"
[ -n "$TEXT" ] || { echo "no text to send" >&2; exit 1; }

ORCH_DIR="$ORCH_DIR" PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" - "$TEXT" <<'PY'
import os, re, sys, json, subprocess, urllib.request, urllib.parse, psycopg

TEXT = sys.argv[1]
ORCH = os.environ.get("ORCH_DIR", os.path.expanduser("~/wingmen/orchestrator"))
CHANNEL = "gazzabyte-irsyad"
AGENT = "cc-irsyad"
SUB_TAG = os.environ.get("CC_AGENT_ID") or None
if SUB_TAG and not SUB_TAG.startswith(AGENT + "-"):
    SUB_TAG = None            # sub_tag family-prefix CHECK constraint

def env_val(key):
    """Read a key out of the orchestrator .env. Never printed."""
    m = re.search(r'^%s=(.+)$' % re.escape(key),
                  open(os.path.join(ORCH, ".env")).read(), re.M)
    return m.group(1).strip() if m else None

dsn = os.environ.get("DATABASE_URL") or env_val("DATABASE_URL")
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT token_env_key, allowed_chat_ids, channel_tag, "
                "coalesce(group_routing->>'agent_phase','drill') "
                "FROM bot_channels WHERE channel_key=%s", (CHANNEL,))
    row = cur.fetchone()
    if not row:
        print("no such channel: %s" % CHANNEL, file=sys.stderr); sys.exit(1)
    token_env_key, chat_ids, tag, phase = row
    if phase not in ("drill", "supervised", "direct"):
        print("unknown agent_phase %r — failing closed to drill" % phase, file=sys.stderr)
        phase = "drill"

    if phase == "drill":
        cur.execute(
            "INSERT INTO operator_messages (direction, channel, chat_id, tag, text, "
            "delivered, from_name) VALUES ('outbound','telegram',NULL,'irsyad-drill',%s,false,%s)",
            (TEXT, AGENT))
        conn.commit()
        print("DRILL phase — NOT sent to the client. Logged under tag 'irsyad-drill'.")
        sys.exit(0)

    if phase == "supervised":
        cur.execute(
            "INSERT INTO agent_messages (from_agent, sub_tag, to_agent, message_type, "
            "subject, body, priority, requires_response) "
            "VALUES (%s,%s,'orch-console','review_request',%s,%s,'P1',true) RETURNING id",
            (AGENT, SUB_TAG,
             "irsyad reply DRAFT — review + send to Gazzabyte group",
             TEXT))
        msg_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO operator_messages (direction, channel, chat_id, tag, text, "
            "delivered, from_name) VALUES ('outbound','telegram',%s,'gazzabyte-irsyad-draft',%s,false,%s)",
            (str(chat_ids[0]) if chat_ids else None, TEXT, AGENT))
        conn.commit()
        print("SUPERVISED phase — draft filed for Nazim as agent_messages #%d. "
              "NOT sent to the client yet." % msg_id)
        sys.exit(0)

    # ── direct ────────────────────────────────────────────────────────────────
    tok = env_val(token_env_key)
    chat = str(chat_ids[0]) if chat_ids else env_val("IRSYAD_SUPPORT_GROUP_CHAT_ID")
    if not tok:
        print("token %s not in .env" % token_env_key, file=sys.stderr); sys.exit(1)
    if not chat:
        print("no chat id for %s" % CHANNEL, file=sys.stderr); sys.exit(1)
    # chunked sender keeps long replies from being truncated at Telegram's 4096;
    # token/chat/text via env, never argv (keeps the token out of `ps`).
    env = dict(os.environ, TG_TOK=tok, TG_CHAT=chat, TG_TEXT=TEXT)
    r = subprocess.run([os.path.join(ORCH, ".venv/bin/python3"),
                        os.path.join(ORCH, "scripts/_tg_chunked_send.py")], env=env)
    delivered = (r.returncode == 0)
    cur.execute(
        "INSERT INTO operator_messages (direction, channel, chat_id, tag, text, "
        "delivered, from_name) VALUES ('outbound','telegram',%s,%s,%s,%s,%s)",
        (chat, tag, TEXT, delivered, AGENT))
    conn.commit()
    if not delivered:
        print("send FAILED (logged, delivered=false)", file=sys.stderr); sys.exit(1)
    print("sent to the Gazzabyte group (chat %s)" % chat)
PY
