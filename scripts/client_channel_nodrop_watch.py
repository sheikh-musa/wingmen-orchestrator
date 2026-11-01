#!/usr/bin/env python3
"""client_channel_nodrop_watch.py — no-drop safety net for CLIENT SUPPORT channels.

WHY (Nazim 43625/43631): a client operator (Fazli/TDU) posted in the cosem-tdu group at
2026-09-26 17:37Z and got NO real reply for ~12h (op#22517) — nothing paged. The Gazzabyte
no-drop watch only covers gazzabyte-irsyad. This generalizes the SAME pattern to every client
support channel, with tighter windows (a client is a paying customer).

SIGNAL (per channel): find the latest INBOUND (the client's last message). It is UNANSWERED
unless a NON-AUTOMATED outbound exists after it. The ingest auto-posts a "📨 Got your message"
RECEIPT into client groups (ingest.py reassure_if_unhandled, which itself dedups on
LIKE '%Got your message%'); a receipt is NOT a real reply and must NOT clear the drop — that
was the exact hole this watch exists to catch (Nazim 43631). is_receipt() matches that marker.

ACTION TIERS (evaluate(), per channel):
  - unanswered > nudge_s (default 30m): nudge the channel's COORD (with its send script).
  - unanswered > escalate_s (default 2h) AND coord was nudged on a PRIOR cycle: ALSO page
    orch-console (Nazim). The operator (Musa) is NEVER paged.

CHANNELS: DERIVED from bot_channels (mode=agent-session, audience='client'), so a new client
group is covered automatically once its bot_channel exists (Nazim 43631). gazzabyte-irsyad is
EXCLUDED (its dedicated watch owns it, different windows). Fail-safe: if bot_channels can't be
read, fall back to the known static cosem-tdu entry.

Fixed (fable-audit-20261006 finding 12, held_commitment #314): the original filter required
`group_routing->>'agent_reviewer' LIKE 'cc-%'` — a naming-convention heuristic, not the
schema's actual client marker. That both MISSED cosem-caai/cosem-exams (routed straight to
orch-console, no separate `cc-*` coord — 2,194 live operator_messages between them, the "2
missing channels" the audit found) and would have WRONGLY INCLUDED any future internal
channel that happens to route to a `cc-*` agent (e.g. `mamadah`, audience='internal', was
already matching the old filter by coincidence). `audience='client'` is the field the schema
defines for exactly this; `coord` can legitimately be `orch-console` itself for a channel
with no separate coordinator — the nudge→escalate ladder still works (console nudges, then
escalates to itself if still unanswered), it just collapses to one escalation tier instead
of two for those channels.

STAGED ARMING: DETECT-ONLY by default (logs what it WOULD page, writes NOTHING). --arm posts
the bus nudges. FAIL-CLOSED: a could-not-measure on a channel pages orch-console LOUD.
UNGATED (a safety signal is never lease-gated). Cadence: launchd; idempotent + deduped.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import bus_send  # noqa: E402

ORCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(ORCH, "state", "client_channel_nodrop_watch.json")

CONSOLE_AGENT = "orch-console"
SRE_AGENT = "cc-fleet-health"   # the fleet SRE — receives the declare-your-chat page
NUDGE_S = int(os.environ.get("CLIENT_NODROP_NUDGE_S", str(30 * 60)))         # coord nudge after 30m
ESCALATE_S = int(os.environ.get("CLIENT_NODROP_ESCALATE_S", str(2 * 3600)))  # console/Nazim after 2h
RE_NUDGE_S = int(os.environ.get("CLIENT_NODROP_RE_NUDGE_S", str(60 * 60)))   # re-nudge cadence
# Live-drop horizon (Nazim 43634): an inbound older than this is NOT a live drop (a nudge
# won't help — it's backlog / done-but-unacked). It is reported ONCE per message id as
# 'stale backlog' to console (no coord nudge, no repeats), so an ancient drop is still seen once.
MAX_LIVE_S = int(os.environ.get("CLIENT_NODROP_MAX_LIVE_S", str(7 * 86400)))  # 7 days
FETCH_LIMIT = 25  # recent rows PER CHAT (a per-chat window, so a busy sibling cannot starve a quiet one)

# Channels covered by OTHER mechanisms — never double-watch.
EXCLUDE_TAGS = {"gazzabyte-irsyad"}  # dedicated gazzabyte_request_nodrop_watch.py (3h/12h)
# Per-channel sanctioned send script (the coord's reply path), by tag. Unknown -> generic hint.
SEND_SCRIPTS = {
    "cosem-tdu": "scripts/cosem_tdu_support_send.sh",
    "gazzabyte-irsyad": "scripts/reviewer_send.sh",
    "cosem-caai": "scripts/caai_send.sh",
    "cosem-exams": "scripts/cosem_exams_support_send.sh",
}
# Fail-safe fallback if bot_channels can't be read: the known-live client channel.
FALLBACK_CHANNELS = [
    {"tag": "cosem-tdu", "coord": "cc-cosem-tdu-coord", "label": "cosem-tdu",
     "send_script": SEND_SCRIPTS["cosem-tdu"]},
]


class CouldNotMeasure(Exception):
    pass


def _dsn() -> str:
    env = os.environ.get("DATABASE_URL")
    if env:
        return env
    m = re.search(r"^DATABASE_URL=(.+)$", open(os.path.join(ORCH, ".env")).read(), re.M)
    if not m:
        raise SystemExit("DATABASE_URL not found")
    return m.group(1).strip()


def _musa_telegram_id() -> "str | None":
    """Operator Telegram id (migrations/020_operator_messages_sender_identity.sql) — an
    inbound row FROM the operator is never a client waiting for a reply, even on a client
    channel (orch-console #72925/#72951/#72953/#72963: Musa's own message, misfired into
    cosem-exams and deleted, kept re-paging because this watch had no sender check).

    SAME env-then-file fallback shape as _dsn() (orch-console #72969): the deployed
    launchd plist for this watch never sources .env (unlike other client-nodrop plists),
    so a bare os.environ.get() read None in the real cron's process environment and the
    PR#452 sender-check silently never fired in production. Fail-safe: None if absent from
    BOTH env and the .env file (never raises — is_operator_sender(musa_id=None) already
    fails toward NOT skipping, which is the right default when this id is unknown)."""
    env = os.environ.get("MUSA_TELEGRAM_ID")
    if env:
        return env
    try:
        text = open(os.path.join(ORCH, ".env")).read()
    except OSError:
        return None
    m = re.search(r"^MUSA_TELEGRAM_ID=(.+)$", text, re.M)
    return m.group(1).strip() if m else None


def _load_state() -> dict:
    try:
        return json.load(open(STATE_PATH))
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    json.dump(st, open(tmp, "w"))
    os.replace(tmp, STATE_PATH)


# ---- pure helpers (unit-tested; no I/O) ---------------------------------------
def is_receipt(text: "str | None") -> bool:
    """True for the ingest's auto-ack receipt ('📨 Got your message …'). Source of truth:
    ingest.py posts these and reassure_if_unhandled dedups on LIKE '%Got your message%'."""
    return "got your message" in (text or "").lower()


# The ingest's LAST-RESORT marker for an update it had no handler for and that carried no
# text/caption/media — `[non-text update 50118302 — keys: chat,date,from,message_id,pinned_message]`
# (nervous_system/ingest.py, _unknown_marker). Service events land here: a pin, a chat-member
# change, an edit with no new text. None of them is something a client is WAITING on.
# START-anchored and tolerant of truncation (fetch_recent takes LEFT(text,240), so a long key
# list can cut the closing bracket); a real message would have to BEGIN with this exact marker
# to be skipped, which is why the anchor matters more than the tail.
_NON_TEXT_UPDATE_RE = re.compile(r"^\[non-text update \d+")


def is_non_text_update(text: "str | None") -> bool:
    """True for the ingest's shape marker for a service/non-text update (NO text, caption or
    media — orch-console #89481). Such a row is a Telegram EVENT, not a message the client
    sent us: it must never be read as the client's latest request."""
    return bool(_NON_TEXT_UPDATE_RE.match((text or "").strip()))


def is_operator_sender(from_user_id, *, musa_id=None) -> bool:
    """True iff `from_user_id` is the operator's own Telegram id (orch-console
    #72925/#72951/#72953) — an inbound FROM the operator is never a client waiting for a
    reply. Caller resolves musa_id via _musa_telegram_id() (env-then-.env-file fallback,
    orch-console #72969); musa_id=None never matches anything (fail toward NOT skipping,
    since silently losing every channel's drop-watch is worse than one false page)."""
    return bool(musa_id) and str(from_user_id) == str(musa_id)


def is_same_chat(a, b) -> bool:
    """True when two rows are in the same Telegram chat, so that `a` can answer `b`.

    Fail-OPEN toward "same chat" when either side has no chat_id: on rows fetched before
    chat_id was selected, refusing to match would turn every answered message into a
    permanent false page. The chat guard exists to stop CROSS-chat answers, and it can only
    do that when both sides actually name a chat."""
    ca, cb = a.get("chat_id"), b.get("chat_id")
    if ca is None or cb is None:
        return True
    return str(ca) == str(cb)


def latest_unanswered_inbound(rows, *, musa_id=None):
    """rows = recent messages for ONE channel, NEWEST-FIRST, each
    {id, direction, created_epoch, from_name, text, from_user_id}. Return the latest INBOUND
    that has NOT been answered by a NON-RECEIPT outbound after it, or None (answered / empty
    / not-waiting). A '📨 Got your message' receipt does NOT count as an answer (Nazim
    43631). An inbound FROM THE OPERATOR is skipped entirely — not a client waiting, and
    does not count as "answered" either, so a genuine older unanswered client message
    underneath it still surfaces (#72953). A non-text/service update (see is_non_text_update)
    is skipped the same way — it is not a client message — so an older genuine message that
    IS still unanswered underneath a pin event is found by looking back (#89481)."""
    # newest-first: find the first (latest) inbound NOT from the operator and NOT a service event
    for i, r in enumerate(rows):
        if r["direction"] == "inbound":
            if is_operator_sender(r.get("from_user_id"), musa_id=musa_id):
                continue
            if is_non_text_update(r.get("text")):
                continue
            # any row newer than it (earlier in the list) that is a real (non-receipt) outbound
            # IN THE SAME CHAT? Chat-scoped on purpose: without it, an outbound in ANOTHER chat
            # (the operator's own DM is merged into a channel's view on real data) silences a
            # genuinely unanswered client — the one failure this watch must never have
            # (cc-quality 89559, reproduced on cosem-exams before this guard existed).
            for newer in rows[:i]:
                if (newer["direction"] == "outbound"
                        and not is_receipt(newer.get("text"))
                        and is_same_chat(newer, r)):
                    return None  # answered by a real reply in the client's chat
            return r  # latest inbound, only receipts (if any) after it -> UNANSWERED
    return None  # no inbound at all


def waiting_by_chat(rows, *, musa_id=None):
    """The unanswered inbound of EVERY chat in the window, newest-first. One entry per chat.

    A channel can own more than one client chat (cosem-caai and oeh each have two allowlisted).
    Judging the single newest inbound across the merged set lets one sibling hide the other:
    the newest inbound in chat A being answered returns None for the WHOLE channel while chat B
    still holds an unanswered client (cc-quality 89597, F1 — answer-shadow). Deciding per chat
    means both are judged on their own last turn, and a page is raised if ANY chat is waiting.
    """
    groups: dict = {}
    for r in rows:                      # rows arrive newest-first, so each group stays ordered
        groups.setdefault(str(r.get("chat_id")), []).append(r)
    out = []
    for chat_rows in groups.values():
        w = latest_unanswered_inbound(chat_rows, musa_id=musa_id)
        if w:
            out.append(w)
    out.sort(key=lambda r: r["created_epoch"], reverse=True)
    return out


UNDECLARED_KEY = "__undeclared__"


def undeclared_channel_action(tag, rows, state, now, re_page_s=86400):
    """Page cc-fleet-health ONCE to declare this channel's client chat, when a channel whose
    bot_channels.allowed_chat_ids is empty first shows real inbound traffic (orch-console 89596).

    With no declared chat the watcher can only match rows that happen to carry the channel tag.
    A client writing under a topic tag is invisible, and there is nothing to resolve to — so the
    channel is silently half-blind until someone declares which chat is the client's. Reporting
    the chats actually seen on INBOUND rows is what makes the declaration a one-minute job; it
    deliberately does NOT auto-declare, because only a human can say a chat is the client's and
    not the operator's own DM.
    """
    inbound = [r for r in rows if r.get("direction") == "inbound"]
    if not inbound:
        return None, {}
    st = dict(state.get(UNDECLARED_KEY) or {})
    if now - st.get("declared_paged_at", 0) < re_page_s:
        return None, {UNDECLARED_KEY: st}
    seen = sorted({str(r.get("chat_id")) for r in inbound})
    oldest = min(inbound, key=lambda r: r["created_epoch"])
    action = {
        "to": SRE_AGENT, "type": "update", "priority": "P3",
        "subject": f"DECLARE CHAT: {tag} has an empty allowed_chat_ids but inbound traffic",
        "body": (
            f"The {tag} client channel is running on the tag-only fallback because "
            f"bot_channels.allowed_chat_ids is empty for it, and it now has inbound traffic "
            f"(op#{oldest['id']}, chats seen: {', '.join(seen)}). Until its client chat is "
            f"declared, any message that arrives under a topic tag instead of the channel tag "
            f"is invisible to the no-drop watch.\n\n"
            f"CONFIRM which of those chat ids is the CLIENT's (not the operator's own DM — the "
            f"DM carries topic tags too and must never be declared), then set it in "
            f"bot_channels.allowed_chat_ids. This is a decision, not maintenance: do not infer "
            f"it from volume alone."
        ),
    }
    return action, {UNDECLARED_KEY: {"declared_paged_at": now}}


def evaluate(waiting, now, state, *, coord, label, tag, send_script,
             nudge_s=NUDGE_S, escalate_s=ESCALATE_S, re_nudge_s=RE_NUDGE_S, max_live_s=MAX_LIVE_S):
    """PURE decision core for ONE channel. `waiting` = the unanswered inbound (or None).
    Returns (actions, new_state). Operator is NEVER a target (coord + console only)."""
    actions: list = []
    if not waiting:
        return actions, {}                      # answered / empty — clear tracking
    age = now - waiting["created_epoch"]
    if age < nudge_s:
        return actions, dict(state)             # waiting but still within the reply window

    key = str(waiting["id"])
    st = dict(state.get(key, {}))
    mins = int(age // 60)
    who = waiting.get("from_name") or "the client"
    preview = (waiting.get("text") or "").strip().replace("\n", " ")[:160]

    # Stale backlog (Nazim 43634): older than the live horizon is NOT a live drop — report it
    # ONCE to console (no coord nudge, no repeats), so an ancient drop is seen but never spams.
    if age > max_live_s:
        if not st.get("stale_reported_at"):
            days = int(age // 86400)
            actions.append({
                "to": CONSOLE_AGENT, "type": "update", "priority": "P2",
                "subject": f"STALE BACKLOG: {label} op#{waiting['id']} unanswered ~{days}d (one-time, no page)",
                "body": (
                    f"The {tag} channel has an UNANSWERED client message ~{days} days old "
                    f"(op#{waiting['id']}, from {who}) — older than the {max_live_s // 86400}d live-drop "
                    f"horizon, so it is NOT nudged as a live drop (a nudge won't help). Reporting it ONCE "
                    f"so it is seen: likely done-but-unacked or a defunct thread — check + ack/close it. No "
                    f"coord nudge, no repeats for this message.\n\n  last message: “{preview}”"
                ),
            })
            st["stale_reported_at"] = now
        return actions, {key: st}

    prior_coord = st.get("coord_nudged_at", 0)
    prior_console = st.get("console_escalated_at", 0)

    if now - prior_coord >= re_nudge_s:
        actions.append({
            "to": coord, "type": "update", "priority": "P2",
            "subject": f"NO-DROP: {label} client waiting {mins}m, unanswered (op#{waiting['id']})",
            "body": (
                f"The {tag} client channel's last CLIENT message is from {who} and has had NO real "
                f"reply for {mins} minutes (operator_messages op#{waiting['id']}); an auto-receipt does "
                f"NOT count. You own this client loop — reply/act now via `{send_script} \"<reply>\"`, and "
                f"if it is blocked, tell the client what it is blocked on so the chat does not go silent. "
                f"Log/close it in your register.\n\n  last message: “{preview}”\n\n"
                f"This repeats every {re_nudge_s // 3600 or 1}h until answered."
            ),
        })
        st["coord_nudged_at"] = now
        st["coord_nudge_count"] = st.get("coord_nudge_count", 0) + 1

    if age >= escalate_s and prior_coord > 0 and (now - prior_console >= re_nudge_s):
        actions.append({
            "to": CONSOLE_AGENT, "type": "blocker", "priority": "P1",
            "subject": f"COORD DROPPING: {label} op#{waiting['id']} unanswered {mins}m",
            "body": (
                f"A {tag} client request has been unanswered for {mins} minutes and its coord ({coord}) "
                f"was already nudged on a prior cycle without clearing it (op#{waiting['id']}) — an "
                f"auto-receipt does not count as answered. Coord is dropping the client loop — step in: "
                f"confirm coord is alive/unwedged and get the client answered. Console backstop; the "
                f"operator is deliberately NOT paged by this watch.\n\n  last message from {who}: “{preview}”"
            ),
        })
        st["console_escalated_at"] = now

    return actions, {key: st}


# ---- channel discovery + fetch ------------------------------------------------
def load_channels(dsn) -> list:
    """Derive client support channels from bot_channels: mode=agent-session,
    audience='client' (the schema's actual client marker — NOT a `cc-*` naming
    convention on the coord, which misses any channel routed straight to orch-console),
    minus EXCLUDE_TAGS (gazzabyte has its own watch). Fail-safe: any read error ->
    FALLBACK_CHANNELS."""
    try:
        with psycopg2.connect(dsn) as c, c.cursor() as cur:
            cur.execute(
                "SELECT channel_tag, group_routing->>'agent_reviewer' "
                "FROM bot_channels WHERE mode='agent-session' AND audience='client' "
                "AND group_routing->>'agent_reviewer' IS NOT NULL"
            )
            rows = cur.fetchall()
    except Exception:  # noqa: BLE001 — fail-safe: still watch the known channel
        return list(FALLBACK_CHANNELS)
    out = []
    for tag, coord in rows:
        if tag in EXCLUDE_TAGS or not coord:
            continue
        out.append({"tag": tag, "coord": coord, "label": tag,
                    "send_script": SEND_SCRIPTS.get(tag, "your sanctioned client-send path")})
    return out or list(FALLBACK_CHANNELS)


def fetch_recent(dsn: str, tag: str, limit: int = FETCH_LIMIT, musa_id=None) -> list:
    """Recent messages for a channel's CONVERSATION, NEWEST-FIRST. Raises CouldNotMeasure on
    any DB failure.

    THE CONVERSATION IS THE CHAT, NOT THE TAG (orch-console #89533, after op#37148). An
    outbound reply is not always stamped with the channel tag: on cosem-exams the two
    replies to Hariz landed as `attempts-staff-20261101` and `role-assignment-20261101`,
    because the sender tags by topic. Selecting `WHERE tag=%s` therefore could not see
    them, and the watch read a well-answered client message as unanswered — 1851 outbound
    rows fleet-wide carry a tag that appears on no inbound row, so this is a class, not an
    incident. The channel's chat_id is stable across every row in the conversation, so
    resolve the chat ids from rows bearing the tag and read the whole chat.

    The chat set comes from bot_channels.allowed_chat_ids — the authoritative tag→chat map —
    NOT from "every chat that has carried this tag". That inference was the first version of
    this fix and cc-quality failed it (89559): the operator tags messages in their own DM by
    topic, so the DM carries a row for nearly every channel tag, 7 of 10 watched channels
    merged the DM in, and the DM's volume filled the window — hk-editor and oeh were 25-of-25
    operator-DM rows with no client row at all. The watcher went SILENT, which is the one
    failure this script must never have. bot_channels lists only real client chats
    (cosem-exams → -5390372474), and the DM appears in none of them.

    Fallback when that map is absent or empty (bariq has [] today): the chats that carried
    the tag, minus the operator's own DM. Never the DM, and never nothing.

    The last clause keeps tag-only rows whose chat_id is NULL (older data) readable, so this
    cannot lose a channel entirely — losing one silently is worse than a false page.
    """
    try:
        with psycopg2.connect(dsn) as c, c.cursor() as cur:
            chats = _channel_chats(cur, tag, musa_id)
            where, params = ((("tag=%s"), (tag,)) if chats is None
                             else (("chat_id = ANY(%s) OR (tag=%s AND chat_id IS NULL)"),
                                   (chats, tag)))
            # PER-CHAT limit, not one LIMIT over the merged window (cc-quality 89597, F1).
            # cosem-caai and oeh each own two client chats. With a single LIMIT, a busy
            # sibling fills the window and a quiet one's last client turn is never fetched,
            # so grouping in Python could not rescue it: the row would already be missing.
            # The window function gives every chat its own last `limit` rows.
            cur.execute(
                "SELECT id, direction, created_epoch, from_name, text, from_user_id, chat_id "
                "FROM (SELECT id, direction, EXTRACT(EPOCH FROM created_at) AS created_epoch, "
                "             from_name, LEFT(text, 240) AS text, from_user_id, chat_id, "
                "             row_number() OVER (PARTITION BY chat_id ORDER BY created_at DESC) AS rn "
                "      FROM operator_messages WHERE " + where + ") s "
                "WHERE rn <= %s ORDER BY created_epoch DESC",
                params + (limit,),
            )
            rs = cur.fetchall()
    except Exception as e:  # noqa: BLE001 — fail-closed
        raise CouldNotMeasure(str(e))
    return [{"id": int(r[0]), "direction": r[1], "created_epoch": float(r[2]),
             "from_name": r[3], "text": r[4] or "", "from_user_id": r[5],
             "chat_id": r[6]} for r in rs]


def channel_is_undeclared(dsn: str, tag: str) -> bool:
    """True when bot_channels declares no client chat for this tag. Raises CouldNotMeasure.

    Separate from _channel_chats on purpose: that one falls back to row-derived chats, so it
    returns a non-empty set for a channel that is undeclared but already has traffic — which is
    exactly the channel whose first client message should prompt a declaration."""
    try:
        with psycopg2.connect(dsn) as c, c.cursor() as cur:
            return not _allowlist(cur, tag)
    except Exception as e:  # noqa: BLE001 — fail-closed
        raise CouldNotMeasure(str(e))


def _allowlist(cur, tag: str) -> list:
    """bot_channels.allowed_chat_ids as text ids (the column is bigint[]; chat_id is TEXT)."""
    cur.execute(
        "SELECT allowed_chat_ids FROM bot_channels "
        "WHERE channel_tag=%s AND allowed_chat_ids IS NOT NULL",
        (tag,),
    )
    out = set()
    for (ids,) in cur.fetchall():
        for cid in (ids or []):
            if cid is not None:
                out.add(str(cid))
    return sorted(out)


def _channel_chats(cur, tag: str, musa_id=None):
    """The Telegram chats that make up this channel's conversation.

    bot_channels.allowed_chat_ids is the operator-declared allowlist and is the truth; the
    row-derived fallback exists only for a channel that has no allowlist yet, and it drops
    the operator's own DM unconditionally — a topic tag in that DM is not a client chat.

    Returns None when the channel resolves to NO chat at all. The caller must then read
    tag-only rather than read nothing: an empty chat set silently blinds the channel."""
    # NOTE ON TYPES: bot_channels.allowed_chat_ids is bigint[] but operator_messages.chat_id
    # is TEXT, so every id is normalised to str here and compared as text. Comparing the
    # bigint array directly raised "operator does not exist: text = bigint" on the live
    # column — a fake cursor cannot catch that, so this line is wet-proven, not unit-proven.
    chats = _allowlist(cur, tag)
    if chats:
        return chats
    cur.execute(
        "SELECT DISTINCT chat_id FROM operator_messages WHERE tag=%s AND chat_id IS NOT NULL",
        (tag,),
    )
    return sorted({str(c) for (c,) in cur.fetchall()
                   if musa_id is None or str(c) != str(musa_id)}) or None


def _bus(dsn: str, action: dict) -> None:
    bus_send.send(from_agent="client-nodrop-watch", to=action["to"], mtype=action["type"],
                   subject=action["subject"][:200], body=action["body"],
                   priority=action["priority"], req=True, dsn=dsn)


def _fail_closed_page(dsn, tag, err, armed):
    action = {
        "to": CONSOLE_AGENT, "type": "blocker", "priority": "P1",
        "subject": f"CLIENT NO-DROP watch could-not-measure ({tag}) fail-closed",
        "body": (f"client_channel_nodrop_watch could NOT read {tag}: {err}\n"
                 "Treat as a possible unanswered client request and check the channel manually."),
    }
    if armed:
        _bus(dsn, action)
    print(f"[{tag}] could-not-measure -> {'PAGED console' if armed else 'WOULD page (detect-only)'}: {err}",
          file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm", action="store_true",
                    help="actually post the bus nudges. Default: DETECT-ONLY (log only, write nothing).")
    args = ap.parse_args(argv)
    armed = args.arm
    mode = "ARMED" if armed else "DETECT-ONLY"

    dsn = _dsn()
    musa_id = _musa_telegram_id()
    now = time.time()
    state = _load_state()
    new_state = dict(state)
    total_actions = 0
    had_error = False
    channels = load_channels(dsn)

    for ch in channels:
        tag = ch["tag"]
        try:
            rows = fetch_recent(dsn, tag, musa_id=musa_id)
            undeclared = channel_is_undeclared(dsn, tag)
        except CouldNotMeasure as e:
            had_error = True
            _fail_closed_page(dsn, tag, str(e), armed)
            continue
        waitings = waiting_by_chat(rows, musa_id=musa_id)
        actions, ns = [], {}
        for waiting in waitings:            # ONE decision per chat, so no chat can shadow another
            a_i, ns_i = evaluate(waiting, now, state.get(tag, {}), coord=ch["coord"],
                                 label=ch["label"], tag=tag, send_script=ch["send_script"])
            actions += a_i
            ns.update(ns_i)
        if undeclared:
            a_u, ns_u = undeclared_channel_action(tag, rows, state.get(tag, {}), now)
            actions += [a_u] if a_u else []
            ns.update(ns_u)
        new_state[tag] = ns
        for a in actions:
            total_actions += 1
            verb = "PAGED" if armed else "WOULD page"
            if armed:
                _bus(dsn, a)
            print(f"[{tag}] {verb} {a['to']} ({a['priority']}): {a['subject']}")
        if not actions:
            shown = ", ".join("op#" + str(w["id"]) for w in waitings) or "none"
            print(f"[{tag}] coord={ch['coord']} waiting={shown} — no action")

    if armed:
        _save_state(new_state)
    print(f"[client-nodrop {mode}] channels={len(channels)} actions={total_actions} errors={int(had_error)}")
    return 2 if had_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
