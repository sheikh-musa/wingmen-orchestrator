#!/usr/bin/env bash
# dev_group_edit.sh — EDIT an existing bot message IN PLACE in a dev-group channel
# via Telegram editMessageText. Companion to dev_group_send.sh (which only ever
# posts NEW messages). A bot can edit its OWN messages without admin/pin rights,
# so this keeps a pinned status dashboard live on ONE message_id instead of
# reposting (a repost leaves the pin pointing at a stale copy).
#
# Usage: scripts/dev_group_edit.sh <channel_key> <message_id> "<new_text>"
#   e.g. scripts/dev_group_edit.sh cosem-exams 214 "$(cat dashboard.txt)"
#
# Logs an outbound row (tagged <channel>-edit) so the edit is durable. Never
# echoes the token. Exits non-zero (and prints Telegram's reason) on failure —
# a wrong message_id / not-ours / identical-content edit fails loud, not silent.
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"
CHANNEL="${1:?usage: dev_group_edit.sh <channel_key> <message_id> \"<new_text>\"}"
MSG_ID="${2:?message_id required}"
TEXT="${3:?text required}"

ORCH_DIR="$ORCH_DIR" PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" - "$CHANNEL" "$MSG_ID" "$TEXT" <<'PY'
import os, re, sys, json, urllib.request, urllib.parse, psycopg
channel, msg_id, text = sys.argv[1], sys.argv[2], sys.argv[3]
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
    env = open(os.path.join(os.environ.get("ORCH_DIR", "."), ".env")).read()
    m = re.search(r'^%s=(.+)$' % re.escape(token_env_key), env, re.M)
    if not m:
        print("token %s not in .env" % token_env_key, file=sys.stderr); sys.exit(1)
    tok = m.group(1).strip()
    data = urllib.parse.urlencode({"chat_id": str(chat_id), "message_id": str(msg_id), "text": text}).encode()
    try:
        resp = json.load(urllib.request.urlopen("https://api.telegram.org/bot%s/editMessageText" % tok, data=data, timeout=20))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        print("edit failed (HTTP %s): %s" % (e.code, body), file=sys.stderr); sys.exit(1)
    except Exception as e:
        print("edit failed: %s" % e, file=sys.stderr); sys.exit(1)
    if not resp.get("ok"):
        print("telegram error: %s" % resp.get("description"), file=sys.stderr); sys.exit(1)
    try:
        cur.execute(
            "insert into operator_messages (direction, channel, chat_id, tag, text, delivered, from_name) "
            "values ('outbound', 'telegram', %s, %s, %s, true, 'nazim')",
            (str(chat_id), (tag or channel) + "-edit", text))
    except Exception as e:
        print("edited OK but log failed: %s" % e, file=sys.stderr)
    print("edited msg %s in %s (chat %s)" % (msg_id, channel, chat_id))
PY
