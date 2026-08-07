#!/usr/bin/env bash
# dev_group_send.sh — send a message INTO a dev-group channel as its bot, and log
# the outbound to the substrate. Generic over any `bot_channels` row: resolves the
# channel's token_env_key (from .env) + its allowed_chat_ids[0] (the group), sends
# via that bot, and records an outbound row so the conversation stays durable for
# the future dedicated lane. Interim-manning tool until the per-group agent lanes
# are stood up. NEVER echoes the token.
#
# Usage: scripts/dev_group_send.sh <channel_key> "<text>"
#   e.g. scripts/dev_group_send.sh cosem-exams "Got it — looking into that now."
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"
CHANNEL="${1:?usage: dev_group_send.sh <channel_key> \"<text>\"}"
TEXT="${2:?text required}"

PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" - "$CHANNEL" "$TEXT" <<'PY'
import os, re, sys, json, urllib.request, urllib.parse, psycopg
channel, text = sys.argv[1], sys.argv[2]
dsn = os.environ["DATABASE_URL"]
with psycopg.connect(dsn) as c, c.cursor() as cur:
    cur.execute("select token_env_key, allowed_chat_ids, channel_tag from bot_channels where channel_key=%s", (channel,))
    row = cur.fetchone()
    if not row:
        print("no such channel: %s" % channel, file=sys.stderr); sys.exit(1)
    token_env_key, chat_ids, tag = row
    if not chat_ids:
        print("channel %s has no allowed_chat_ids (group chat_id) yet" % channel, file=sys.stderr); sys.exit(1)
    chat_id = chat_ids[0]
    # resolve token from .env (never DB, never printed)
    env = open(os.path.join(os.environ.get("ORCH_DIR", "."), ".env")).read()
    m = re.search(r'^%s=(.+)$' % re.escape(token_env_key), env, re.M)
    if not m:
        print("token %s not in .env" % token_env_key, file=sys.stderr); sys.exit(1)
    tok = m.group(1).strip()
    data = urllib.parse.urlencode({"chat_id": str(chat_id), "text": text}).encode()
    try:
        resp = json.load(urllib.request.urlopen("https://api.telegram.org/bot%s/sendMessage" % tok, data=data, timeout=20))
    except Exception as e:
        print("send failed: %s" % e, file=sys.stderr); sys.exit(1)
    if not resp.get("ok"):
        print("telegram error: %s" % resp.get("description"), file=sys.stderr); sys.exit(1)
    # durable outbound log (best-effort; tag identifies the dev channel)
    try:
        cur.execute(
            "insert into operator_messages (direction, channel, chat_id, tag, text, delivered, from_name) "
            "values ('outbound', 'telegram', %s, %s, %s, true, 'nazim')",
            (str(chat_id), tag, text))
    except Exception as e:
        print("sent OK but log failed: %s" % e, file=sys.stderr)
    print("sent to %s (chat %s)" % (channel, chat_id))
PY
