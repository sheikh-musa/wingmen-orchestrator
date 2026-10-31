"""Pending contacts — the state behind the one-tap Approve (TG2.0 section 6).

An inbound from a chat no channel row claims is logged under the group's
fallback tag and, by design, nothing else happens. This module turns that
silence into ONE alert to the reviewer with two buttons, and refuses to let the
buttons do more than they say:

  Approve  files a GATE REQUEST for the contact. It does not create, enable, or
           grant anything — `onboard_client_channel.py`, the INGEST_CHANNELS
           pre-flight, the `--store` requirement and the gate all still run.
  Ignore   records the decision so the same contact is not re-alerted for this
           contact's already-seen messages, but the NEXT genuine message from
           that chat creates a fresh pending row: an Ignore is not a blacklist.

State lives in `tg_pending_contacts` (a short-lived row per chat, with an
expiry) rather than in memory, because the ingest restarts and because a
replayed button must be able to find — and fail to find — its own row.

The opaque `pending_id` is what travels in `callback_data`; the raw chat_id
never does (design 6 build-note 3), which also keeps it inside Telegram's
64-byte callback_data limit.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

PENDING_TTL = timedelta(days=3)
ACTIONS = ("approve", "ignore")


def _as_aware(dt):
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def record_unmatched(conn, *, bot, chat_id, channel_tag, first_name=None,
                     username=None, now=None, pending_ttl=PENDING_TTL):
    """Called for every inbound from an unclaimed chat.

    Returns the notification dict exactly ONCE per pending row (and again after
    that row expires or was ignored), or None when the contact is already
    pending and the reviewer has already been told.

    ONE statement, no commit: the caller owns the transaction. ingest calls
    this inside `with conn.transaction():`, where psycopg3 forbids an explicit
    `conn.commit()` — doing it here raised ProgrammingError and every first
    alert was swallowed (PR#726 review, bus #86348 Finding 1).

    The upsert is also what makes expiry work. An expired row is STILL
    `state='pending'`, so the partial unique index
    `(bot_username, chat_id) WHERE state='pending'` refuses a second INSERT;
    a bare INSERT therefore lost the contact forever after the 3-day TTL
    (Finding 2). The ON CONFLICT target names that index, and its DO UPDATE's
    WHERE refreshes the stale row in place only when it has expired — so a
    live row still yields no RETURNING row and no second alert, atomically,
    even with two concurrent inbounds.
    """
    now = _as_aware(now or datetime.now(timezone.utc))
    chat_id = str(chat_id)
    pending_id = secrets.token_hex(6)

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tg_pending_contacts "
            "(bot_username, chat_id, channel_tag, first_name, username, pending_id, "
            " notified_at, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (bot_username, chat_id) WHERE state = 'pending' "
            "DO UPDATE SET pending_id = EXCLUDED.pending_id, "
            " channel_tag = EXCLUDED.channel_tag, "
            " first_name = EXCLUDED.first_name, username = EXCLUDED.username, "
            " notified_at = EXCLUDED.notified_at, expires_at = EXCLUDED.expires_at "
            "WHERE tg_pending_contacts.expires_at <= EXCLUDED.notified_at "
            "RETURNING pending_id",
            (bot, chat_id, channel_tag, first_name, username, pending_id,
             now, now + pending_ttl),
        )
        written = cur.fetchone()
    if not written:
        return None                      # already pending and already alerted

    return {
        "pending_id": written[0],
        "bot": bot,
        "chat_id": chat_id,
        "channel_tag": channel_tag,
        "first_name": first_name,
        "username": username,
    }


def alert(*, bot, chat_id, first_name=None, username=None, channel_tag=None,
          pending_id):
    """The reviewer's DM: who it is, where they landed, and two buttons.

    Deliberately carries no message body — the reviewer is being asked whether
    this contact exists, not what they said.
    """
    who = first_name or "Someone"
    handle = f" (@{username})" if username else ""
    text = (
        f"New contact on @{bot}: {who}{handle}\n"
        f"First message came in on the '{channel_tag}' group, and no channel "
        f"claims that chat.\n\n"
        f"Approve files a gate request (nothing is enabled by this tap). "
        f"Ignore leaves them unanswered — a later message will ask again."
    )
    keyboard = {"inline_keyboard": [[
        {"text": "✅ Approve", "callback_data": f"pc:approve:{pending_id}"},
        {"text": "✋ Ignore", "callback_data": f"pc:ignore:{pending_id}"},
    ]]}
    return text, keyboard


def gate_request_body(req):
    """The one-line body of the gate request an Approve tap files.

    Facts only: who the chat is, which group it landed on, and the least-
    privilege defaults the reviewer will be granting. No message content.
    """
    handle = f" (@{req['username']})" if req.get("username") else ""
    return (
        f"New contact gate request (filed by an Approve tap, TG2.0 s6): "
        f"chat_id={req['chat_id']} name={req.get('first_name') or 'unknown'}{handle} "
        f"landed on group '{req.get('channel_tag')}' with no channel claiming it. "
        f"Defaults: audience={req.get('audience', 'client')}, "
        f"powers={','.join(req.get('powers') or ['reply_text'])}, "
        f"phase={req.get('phase', 'supervised')}. "
        f"Outstanding for the reviewer: store + host + mode, and a channel key "
        f"(provisional pending-{req['chat_id']}). Nothing is enabled by the tap; "
        f"onboard_client_channel.py + INGEST_CHANNELS pre-flight + the gate all still run."
    )


def file_gate_request_via_bus(req, *, runner=None, bus_send_path=None):
    """File the gate request with orch-console, the same way a hand-run
    onboarding would: one bus row. Returns the CompletedProcess.

    bus_send.py is the ONLY way to write a bus row (a hand-written INSERT once
    left `priority` off and the row landed under the hub's wake floor), and the
    body goes inline — a one-line ruling is exactly the shape the bus guard asks
    for from a provider-routed body.
    """
    import subprocess
    import sys
    from pathlib import Path

    runner = runner or subprocess.run
    bus = Path(bus_send_path or (Path(__file__).resolve().parent.parent
                                 / "scripts" / "bus_send.py"))
    cmd = [sys.executable, str(bus), "--to", "orch-console", "--type", "decision",
           "--subject", f"New contact gate request: chat {req['chat_id']} on "
                        f"'{req.get('channel_tag')}'",
           "--priority", "P2", "--req", "--body", gate_request_body(req)]
    return runner(cmd, capture_output=True, text=True, timeout=120)


def _filing_failed(res):
    """A filing failed when it raised (handled by the caller) or reported a
    non-zero return code. `None` means the injected filer completed without
    reporting — the default runner always returns a CompletedProcess."""
    rc = getattr(res, "returncode", None)
    return rc is not None and rc != 0


def handle_button(conn, *, data, presser, reviewer_chat, file_gate_request, now=None):
    """Act on an inline-button press. Deny-by-default on every axis.

    Order matters: authorisation is checked BEFORE the database is touched, so a
    stranger's press cannot even probe which pending ids exist.
    """
    now = _as_aware(now or datetime.now(timezone.utc))
    parts = (data or "").split(":")
    if len(parts) != 3 or parts[0] != "pc" or parts[1] not in ACTIONS or not parts[2]:
        return {"ok": False, "reason": "bad_action"}
    action, pending_id = parts[1], parts[2]

    if not reviewer_chat or str(presser) != str(reviewer_chat):
        return {"ok": False, "reason": "not_reviewer"}

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, state, chat_id, channel_tag, first_name, username "
            "FROM tg_pending_contacts "
            "WHERE pending_id = %s AND expires_at > %s LIMIT 1",
            (pending_id, now),
        )
        row = cur.fetchone()
        if not row or row[1] != "pending":
            return {"ok": False, "reason": "expired_or_unknown"}
        row_id, _, chat_id, channel_tag, first_name, username = row

        new_state = "approved" if action == "approve" else "ignored"
        # The state literal is one of two fixed constants above — never caller
        # text — so this f-string cannot carry an injection.
        cur.execute(
            f"UPDATE tg_pending_contacts SET state = '{new_state}', resolved_at = %s, "
            "resolved_by = %s WHERE id = %s AND state = 'pending'",
            (now, str(presser), row_id),
        )
        if cur.rowcount != 1:
            conn.commit()
            return {"ok": False, "reason": "already_resolved"}
    conn.commit()

    if action == "ignore":
        return {"ok": True, "action": action}

    req = {
        "chat_id": str(chat_id),
        "channel_tag": channel_tag,
        "first_name": first_name,
        "username": username,
        "pending_id": pending_id,
        # Least privilege, and a phase promotion is still its own gate.
        "powers": ["reply_text"],
        "phase": "supervised",
        "audience": "client",
    }
    try:
        filed = file_gate_request(req)
    except Exception:                                            # noqa: BLE001
        filed = None
        failed = True
    else:
        failed = _filing_failed(filed)
    if failed:
        # Do not strand an approved-but-unfiled contact: the state was
        # committed before filing, so put the row back to pending and let the
        # reviewer press again (bus #86348, non-blocking (a)).
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tg_pending_contacts SET state = 'pending', resolved_at = NULL, "
                "resolved_by = NULL WHERE id = %s AND state = 'approved'",
                (row_id,),
            )
        conn.commit()
        return {"ok": False, "reason": "gate_not_filed"}
    return {"ok": True, "action": action}
