"""operator_log.py — append a row to operator_messages (durable bridge log).

Both directions of the operator<->orchestrator Telegram bridge land here so the
conversation is never lost and stays coherent across the live-tmux and headless
incarnations of cc-orchestrator. The tg_send.sh helper logs every outbound reply;
nervous_system/ingest.py logs every inbound message.
"""
from __future__ import annotations
import argparse
import json
import os
import sys

import psycopg
from dotenv import load_dotenv

from nervous_system import triage  # PASSIVE CoS triage annotation (read-only)
from nervous_system.vault_leak_guard import defensive_redact  # bus #44378

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


# --- ORCH-TOPOLOGY-001 body scoping -----------------------------------------
# Two orch bodies share this table (Studio hub + MacBook console/Nazim). Each
# body reconciles + stamps ONLY its own surface, enforced here (A3: in code
# that reads the env, not by promise): console sees channel='tmux-console'
# only; hub sees everything EXCEPT tmux-console (console messages are answered
# in-console by the console body). Unset role = legacy single-body behavior.

def _agent_id() -> str:
    return os.environ.get("ORCH_AGENT_ID", "cc-orchestrator")


def _body_role() -> str:
    return os.environ.get("ORCH_BODY_ROLE", "").strip().lower()


# --- Sender identity (BOT-INGEST-SENDER-001) --------------------------------
# ingest.py now records message.from into from_user_id / from_username /
# from_name. These helpers derive a human label + a source hint (DM vs group)
# so a reader can tell WHO sent a row — individuals within a group, not just
# the chat. Requires migration 020 applied (adds the three columns); the read
# functions below SELECT them, so run 020 before exercising this module.

def _musa_id() -> str:
    return os.environ.get("MUSA_TELEGRAM_ID", "").strip()


def _sender_label(from_user_id, from_name, from_username) -> str:
    """Human name for the sender: the operator himself → 'Musa'; else the
    Telegram first/last name; else @username; else 'unknown' (older rows / no
    message.from)."""
    musa = _musa_id()
    if from_user_id and musa and str(from_user_id) == musa:
        return "Musa"
    if from_name:
        return from_name
    if from_username:
        return "@" + str(from_username).lstrip("@")
    return "unknown"


# --- Passive CoS triage annotation (chief-of-staff spec, Step 1) -------------
# READ-ONLY: unprocessed()/recent() append a `triage` suggestion so Nazim can see
# the likely route when he reconciles. Prefer the stored cos_triage (computed by
# ingest at inbound time); fall back to recomputing from the text for legacy /
# NULL rows (deterministic — nothing is lost). This never routes or sends.

def _triage_for(stored, text, tag) -> dict:
    if isinstance(stored, dict) and stored:
        return stored
    try:
        return triage.classify(text, tag=tag).to_dict()
    except Exception:
        return {}


def _source_hint(chat_id, from_user_id) -> str:
    """'DM' when the message came from a private chat (positive chat_id, incl.
    the operator's own DM whose chat_id == MUSA_TELEGRAM_ID); 'group' for a
    negative group/supergroup chat_id. Unknown chat_id → 'group' (conservative:
    assume shared, don't mis-label as a private DM)."""
    musa = _musa_id()
    try:
        cid = int(chat_id) if chat_id is not None else None
    except (TypeError, ValueError):
        cid = None
    if cid is not None and cid > 0:
        return "DM"
    if from_user_id and musa and str(from_user_id) == musa and cid is not None and cid > 0:
        return "DM"
    return "group"


# SHARED-AWARENESS feeds — read deliberately by every body, NEVER auto-drained
# from any single body's PERSONAL DM inbox (#24, Nazim carve-out call 2026-07-10):
#   war-room     = fleet room; all 3 read, respond-by-protocol (CAI-RESP-339:
#                  hub=ops/status/fleet, cai=governance, Nazim=console/CTO).
#   hafiz-partner= external partner group (Nazim-owned, NDA-gated).
# They get their OWN tag and are carved out of BOTH the hub and console personal
# reconciliation scopes, so a fleet/partner message never pollutes a DM inbox.
_SHARED_FEED_TAGS = ("war-room", "hafiz-partner")

# Tags owned by a DEDICATED LANE AGENT, not by an orch body. Neither the hub nor
# the console reconciles these — the lane agent does, on its own tag, with its own
# reply path. Excluded from both bodies' scopes so a drill/lane thread can never be
# mistaken for an operator DM and answered on the wrong voice (2026-07-25, cc-irsyad
# build). `gazzabyte-irsyad` JOINED this list at cc-irsyad-coord's direct cutover
# (#21399, 2026-08-14 Beat-3): coord now owns+reconciles the live client thread via
# its own tag-scoped loop (scripts/lane_operator_reconcile.py); the hub/console no
# longer reconcile it. Reverting this line hands the tag back to the hub scope.
_LANE_OWNED_TAGS = ("irsyad-drill", "gazzabyte-irsyad", "hk-editor")

# Client channels the Mini's nazim-ingest actually POLLS (scripts/boot_nazim_ingest.sh
# INGEST_CHANNELS) that the CONSOLE body (not a dedicated lane, not the hub) reconciles.
# ONE list feeding BOTH branches below, so a console-inclusion/hub-exclusion pair can never
# drift out of sync again -- exactly the bug that dropped Fazli's cosem-tdu messages for 12h
# (op#22517, 2026-09-26/27): INGEST_CHANNELS already polled cosem-tdu + angullia on the Mini,
# but this list (then duplicated ad hoc in each branch) was never updated, so the HUB's
# mark_handled_through stamped those rows handled without the console ever answering them.
# Keep this in lockstep with INGEST_CHANNELS (tests/test_operator_log_scope.py asserts it) --
# add a channel here THE SAME TIME you add it to boot_nazim_ingest.sh, not after.
_CONSOLE_POLLED_CLIENT_TAGS = frozenset({
    "cosem-caai", "cosem-exams", "alderei",  # 2026-08-03
    "cosem-tdu", "angullia",                  # 2026-09-27 (op#22517 fix)
    "oeh",                                     # 2026-09-27 (op#22521/bus#43713)
})

# Suffixes written by the lane phase-gate (scripts/lane_reply.sh): '<tag>-drill' is a reply
# that never left the building, '<tag>-draft' is one awaiting a reviewer's send. Neither is
# ever an operator surface, for either body — excluded by shape so a new lane can't reintroduce
# the 2026-07-25 leak just by existing.
_LANE_TAG_SUFFIXES = ("-drill", "-draft")


def _shared_feed_exclusion() -> str:
    tags = ",".join("'%s'" % t for t in _SHARED_FEED_TAGS + _LANE_OWNED_TAGS)
    # '%%' — this SQL is passed to psycopg alongside %s parameters, so a literal percent
    # must be doubled or it is parsed as a (malformed) placeholder.
    suffixes = " AND ".join("tag NOT LIKE '%%" + s + "'" for s in _LANE_TAG_SUFFIXES)
    return f" AND (tag IS NULL OR (tag NOT IN ({tags}) AND {suffixes}))"


# Recognized body roles. ANY other value MUST fail CLOSED, never silently fall
# through to "" (an unscoped query). The 2026-07-05 incident (commit 3691cea):
# a misconfigured/typo'd ORCH_BODY_ROLE fell through and made
# mark_handled_through() stamp EVERY channel's inbound handled in ONE call —
# cross-body message loss. '' (unset) is the SANCTIONED legacy single-body
# behavior and stays permitted; a non-empty UNRECOGNIZED role raises loudly.
_RECOGNIZED_BODY_ROLES = frozenset({"console", "hub", ""})


def _channel_scope_sql() -> str:
    role = _body_role()
    if role not in _RECOGNIZED_BODY_ROLES:
        raise ValueError(
            "ORCH_BODY_ROLE=%r is not a recognized orch body role "
            "(expected 'console', 'hub', or empty). Refusing to build an "
            "UNSCOPED channel query: an unrecognized role must never silently "
            "let unprocessed()/mark_handled_through() span EVERY channel "
            "(cross-body message loss — see commit 3691cea, 2026-07-05)." % role
        )
    if role == "console":
        # Nazim reconciles his OWN surfaces: in-console typing (channel
        # 'tmux-console') AND his private Telegram DM channel — @nazim_cto_bot,
        # which ingest logs as channel='telegram', tag='nazim-console'. Shared
        # feeds are excluded (a war-room/Hafiz msg is not a personal-DM nudge).
        # PLUS the client channels his Mini console-ingest already POLLS
        # (boot_nazim_ingest.sh INGEST_CHANNELS, see _CONSOLE_POLLED_CLIENT_TAGS):
        # cosem-caai (Ray), cosem-exams (Hariz), alderei (Nahar). Align-reconcile-
        # to-ingest-ownership — whoever polls a channel reconciles it, else the
        # poll-here/reconcile-there split forces the hub to relay every cosem
        # inbound (2026-08-03, hub+console co-signed; operator 'cosem = Nazim').
        # cosem-tdu + angullia joined 2026-09-27 (op#22517: same split dropped
        # Fazli's cosem-tdu messages for 12h). cosem-adcda is NOT here — the hub
        # ingest polls that one, so the hub keeps reconciling it.
        tag_list = ",".join("'%s'" % t for t in sorted(_CONSOLE_POLLED_CLIENT_TAGS))
        return (" AND (channel='tmux-console' OR tag='nazim-console'"
                " OR tag IN (%s))" % tag_list
                + _shared_feed_exclusion())
    if role == "hub":
        # Hub owns every operator surface EXCEPT the OTHER bodies' DMs (Nazim's
        # console @nazim_cto_bot, cai's @cai bot) and the shared feeds — so it
        # never answers another body's DM on the wrong bot/voice/pen, nor drains
        # a shared-awareness feed as a personal DM. (cai-channel was already
        # carved from mark_handled; carving it from the read scope too closes the
        # leak the operator's 2026-07-10 pipeline test exposed.)
        # _CONSOLE_POLLED_CLIENT_TAGS carved out too (2026-08-03, +cosem-tdu/angullia
        # 2026-09-27 op#22517): the Mini console-ingest polls them, so the console
        # reconciles them — the hub must NOT also discover+relay them. IS DISTINCT
        # FROM (not NOT IN) keeps NULL-tag rows in hub scope.
        exclusions = " AND ".join(
            "tag IS DISTINCT FROM '%s'" % t for t in sorted(_CONSOLE_POLLED_CLIENT_TAGS)
        )
        return (" AND channel<>'tmux-console'"
                " AND tag IS DISTINCT FROM 'nazim-console'"
                " AND tag IS DISTINCT FROM 'cai-channel'"
                " AND " + exclusions +
                # finance-console (2026-09-05, Nazim 37730): the cc-finance lane
                # reconciles its OWN revenue channel (bot_channels finance-console
                # inject_target=finance, pinned to the Mini's nazim-ingest). The hub
                # must not also surface it, or the operator gets a double answer
                # post-gzb-flip. Same carve-out shape as the 08-03 cosem/alderei one.
                " AND tag IS DISTINCT FROM 'finance-console'"
                + _shared_feed_exclusion())
    return ""


# --- Operator asks ledger (op#22669) ----------------------------------------
# "If I ask 1000 things I expect you to track 1001." Every genuine operator
# message on a MONITORED surface becomes a row in operator_asks, UNLESS it is
# itself a reply that closes one out — never both for the same message.

# The tag stamped on operator_messages for each monitored Telegram surface:
#   'orch-channel'  — the hub's operator-orch bridge (migration 014 seed).
#   'nazim-console' — the console body's own @nazim_cto_bot DM (ORCH-TOPOLOGY-001).
# tmux-console is matched by `channel`, not `tag` (log_console_msg.sh never sets one).
_ASK_TRACKED_TAGS = frozenset({"orch-channel", "nazim-console"})


def _is_operator_ask_surface(channel: str, tag, from_user_id) -> bool:
    """True only for a message that is genuinely the OPERATOR, on a surface this
    ledger watches. tmux-console is operator-only by construction (only
    scripts/log_console_msg.sh writes it, direct from a human at the keyboard —
    no from_user_id to check). A Telegram surface additionally requires the
    sender to BE Musa (MUSA_TELEGRAM_ID) — a bot channel's tag alone doesn't
    prove who sent a given update."""
    if channel == "tmux-console":
        return True
    if channel == "telegram" and tag in _ASK_TRACKED_TAGS:
        musa = _musa_id()
        return bool(musa) and from_user_id is not None and str(from_user_id) == musa
    return False


def maybe_track_ask(op_msg_id: int, direction: str, channel: str, tag,
                     text: str, from_user_id=None,
                     reply_to_tg_message_id: int | None = None) -> int | None:
    """Called for every INBOUND message on a monitored operator surface.

    A reply-match ALSO opens a new row for the reply itself (orch-console bus
    #43972/#43985, PR #180 change 1) — closing without opening drops the
    reply's own content. The operator's reply IS the answer to act on, and
    often carries a new ask of its own ("yes, and also do X"); silently
    swallowing that is the op#22669 failure mode itself.
      - if the inbound IS a genuine Telegram reply to one of OUR outbound
        messages that is itself linked to an OPEN ask
        (operator_asks.outbound_msg_id -> operator_messages.tg_message_id):
        that ask auto-closes (closed_reason='operator_replied op#<new id>')
        AND a NEW row opens for the reply (source_msg_id = this message).
      - otherwise -> just the NEW operator_asks row opens for this message,
        so it joins the ledger like everything else the operator asks.

    Returns the id of the NEW row (opened either way), or None if this
    message isn't on a monitored surface (nothing tracked). Not called for
    direction='outbound' — that direction's rows are captured via
    outbound_msg_id at open-ask time (scripts/asks_open.py), never as a NEW
    ask themselves.
    """
    if direction != "inbound":
        return None
    if not _is_operator_ask_surface(channel, tag, from_user_id):
        return None

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id',%s,true)", (_agent_id(),))
        closed_id = None
        if reply_to_tg_message_id is not None:
            cur.execute(
                "SELECT id FROM operator_asks WHERE closed_at IS NULL AND outbound_msg_id IN "
                "(SELECT id FROM operator_messages WHERE tg_message_id=%s)",
                (reply_to_tg_message_id,),
            )
            row = cur.fetchone()
            if row is not None:
                closed_id = row[0]

        cur.execute(
            "INSERT INTO operator_asks (ask, source_msg_id) VALUES (%s,%s) RETURNING id",
            (text, op_msg_id),
        )
        rid = cur.fetchone()[0]

        if closed_id is not None:
            cur.execute(
                "UPDATE operator_asks SET closed_at=now(), closed_reason=%s WHERE id=%s",
                (f"operator_replied op#{rid}", closed_id),
            )

        conn.commit()
        return rid


def open_asks_for(body: str) -> list:
    """Open (closed_at IS NULL) operator_asks rows delegated to `body`,
    oldest-first — the read a boot-hook / reconstitution step surfaces so a
    fresh (re)launch doesn't lose sight of standing asks. `body` is an
    agents.agent_id-shaped value ('orch-console', 'cc-orchestrator', ...), matched
    against operator_asks.delegated_to. Returns (id, ask, delegated_to, created_at,
    waiting_on_operator, chase_by)."""
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, ask, delegated_to, created_at, waiting_on_operator, chase_by "
            "FROM operator_asks WHERE closed_at IS NULL AND delegated_to=%s "
            "ORDER BY created_at ASC",
            (body,),
        )
        return cur.fetchall()


def _alert_vault_redaction(leaked_keys: list[str], direction: str, channel: str, tag) -> None:
    """Loud, durable copy of a fired defensive redaction (orch-console review,
    bus #44388, fix 1a): the row-level cos_triage.vault_redacted flag written
    by log() below is the primary durable evidence, but every send script
    pipes operator_log's own stderr to /dev/null (oeh_send.sh included — the
    one that leaked), so a WARNING print alone is never seen by anyone. This
    posts a P1 bus row to orch-console instead. Called by log() only AFTER
    its own INSERT has committed (bus #44388 round 2 nit — never page about a
    row that doesn't exist because the write itself failed). Best-effort: a
    bus-post hiccup (no identity resolvable, DB down) must never cost that
    already-committed write — the row flag is the fallback if this fails."""
    try:
        from scripts import bus_send
        from_agent = bus_send.resolve_from_agent(os.environ)
        subject = f"vault_leak_guard fired: {leaked_keys} redacted from a {direction}/{tag} log row"
        body = (
            f"vault_leak_guard fired on a {direction} operator_messages row "
            f"(channel={channel!r}, tag={tag!r}): vault key(s) {leaked_keys} were present "
            "in the raw text and were redacted before the durable INSERT (bus #44378). "
            "This means a send script emitted a raw secret value that almost reached the "
            "durable log — check the originating send path for a missing "
            "--secret-vault-key wiring (or a hand-pasted value bypassing it entirely)."
        )
        bus_send.send(from_agent, "orch-console", "blocker", subject, body, "P1", req=True)
    except Exception:
        pass


def log(direction: str, text: str, chat_id: str | None = None,
        tag: str | None = None, delivered: bool = True,
        channel: str = "telegram", failure_reason=None,
        tg_message_id: int | None = None) -> int:
    # failure_reason (Nazim #40837): why a send failed, recorded on the row so a swallowed
    # 'nazim_send failed' becomes durable evidence. Stored under cos_triage.send_failure.
    # tg_message_id (op#22669 asks-tracking): the Telegram Bot API result.message_id for
    # an OUTBOUND send only — captured so a later inbound reply_to_message can be matched
    # back to this exact row (reply-linked operator_asks auto-close).
    cos_payload: dict = {}
    if failure_reason:
        payload = failure_reason if isinstance(failure_reason, dict) else {"description": str(failure_reason)}
        cos_payload["send_failure"] = payload
    # Defensive vault-value scan (bus #44378, after op#22696's repeat; tightened
    # per orch-console's PR #197 review, bus #44388): the LAST line of defense
    # before a secret reaches the durable log, independent of whether the
    # caller used --secret-vault-key's {{SECRET}} mechanism or
    # nervous_system.secret_redact's pattern pass. Best-effort — a
    # defensive_redact() hiccup must never cost the primary log write.
    #
    # "Could not check" must never look the same as "checked, clean" (44388
    # fix 1b): a fired redaction is recorded on the row (vault_redacted); a
    # scan that could not RUN for an in-scope key is recorded distinctly
    # (vault_scan_skipped) instead of being silently indistinguishable from
    # "ran, found nothing". defensive_redact() itself now catches broadly
    # (bus #44388 round 2, fix 1c) so it practically never reaches the
    # `except Exception` below — but if it somehow still does (a bug in the
    # guard itself, not in vault.get()), that must ALSO be a recorded skip,
    # not a silent "clean": the key name "*" marks "the whole scan didn't run"
    # rather than one specific key.
    try:
        text, leaked_keys, skipped = defensive_redact(text, tag=tag)
    except Exception as exc:
        leaked_keys, skipped = [], [("*", f"guard_error:{type(exc).__name__}")]
    if leaked_keys:
        cos_payload["vault_redacted"] = leaked_keys
        print(
            f"operator_log: WARNING — defensive redaction fired for vault key(s) "
            f"{leaked_keys} on a {direction} message (channel={channel!r}, tag={tag!r}) "
            "— a raw secret value almost reached the durable log (bus #44378)",
            file=sys.stderr,
        )
    if skipped:
        cos_payload["vault_scan_skipped"] = [{"key": k, "reason": r} for k, r in skipped]
    cos = json.dumps(cos_payload) if cos_payload else None
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id',%s,true)", (_agent_id(),))
        cur.execute(
            "INSERT INTO operator_messages (direction, channel, chat_id, tag, text, delivered, cos_triage, tg_message_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s) RETURNING id",
            (direction, channel, chat_id, tag, text, delivered, cos, tg_message_id),
        )
        rid = cur.fetchone()[0]
        conn.commit()
    # Loud bus copy AFTER the commit (orch-console review, bus #44388 round 2
    # nit): the row must actually exist before we page anyone about it — a
    # failing INSERT (which would raise out of the `with` block above, before
    # reaching here) must never trigger a page about a row that was never
    # written.
    if leaked_keys:
        _alert_vault_redaction(leaked_keys, direction, channel, tag)
    # Ledger every genuine operator ask (op#22669: "if I ask 1000 things I expect
    # you to track 1001"). Best-effort — a tracking hiccup must never cost the
    # primary durable log row above, which has already committed.
    if direction == "inbound":
        try:
            maybe_track_ask(rid, direction, channel, tag, text)
        except Exception:
            pass
    return rid


def attach_transcript(msg_id: int, transcript: str) -> bool:
    """Enrich a logged voice-note row with its transcript, so the operator's
    actual WORDS are in the durable audit log — not just an audio-file pointer.
    Voice is a delivery layer on top of the text log, never a replacement."""
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id',%s,true)", (_agent_id(),))
        cur.execute(
            "UPDATE operator_messages SET text = text || ' | transcript: ' || %s "
            "WHERE id=%s AND text NOT LIKE '%% | transcript: %%'",
            (transcript, msg_id),
        )
        n = cur.rowcount
        conn.commit()
        return n > 0


def recent(limit: int = 20) -> list:
    """Latest exchanges, oldest-first — the continuity context a fresh
    (headless or rebooted) cc-orchestrator reads to catch up.

    Return shape (BACKWARD-COMPATIBLE — first 4 positions unchanged, sender
    fields then the passive triage suggestion APPENDED at the end):
        (direction, tag, text, created_at,          # original 4
         from_user_id, from_username, from_name,    # raw message.from
         sender_label, source,                      # derived: 'Musa'/name, 'DM'/'group'
         triage)                                    # passive CoS route suggestion (dict), read-only
    """
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT direction, tag, text, created_at, chat_id, "
            "from_user_id, from_username, from_name, cos_triage FROM operator_messages "
            "ORDER BY id DESC LIMIT %s", (limit,))
        rows = cur.fetchall()
    out = []
    for (direction, tag, text, created_at, chat_id,
         fuid, funame, fname, cos_triage) in reversed(rows):
        out.append((direction, tag, text, created_at, fuid, funame, fname,
                    _sender_label(fuid, fname, funame),
                    _source_hint(chat_id, fuid),
                    _triage_for(cos_triage, text, tag)))
    return out


# --- Option B: durable-log-as-source-of-truth (CAI-RESP-277) ---------------
# The keystroke injection is a best-effort NUDGE; the guarantee that an operator
# message is seen + answered lives HERE. Every inbound is logged with
# handled_at=NULL; cc-orchestrator reconciles by reading unprocessed() each turn
# / on the autonomous wakeup, answers, then stamps via mark_handled_through().
# At-least-once: a rare re-surfacing beats a silent loss (cai's ruling).
# Same convention applies to open_asks_for(<body>) (op#22669): read it at the
# same reconciliation points (turn start / autonomous wakeup), not just at boot —
# the SessionStart hook (scripts/session_start_reconstitute.py) only fires on a
# FRESH context (startup/clear), so a long-lived resumed session must re-check
# open_asks_for() itself the same way it already re-checks unprocessed().

def unprocessed(limit: int = 20) -> list:
    """Inbound operator messages not yet marked handled, oldest-first. The
    reconciliation read that makes delivery independent of keystrokes landing.

    Return shape (BACKWARD-COMPATIBLE — first 4 positions unchanged, sender
    fields then the passive triage suggestion APPENDED at the end):
        (id, tag, text, created_at,                 # original 4
         from_user_id, from_username, from_name,    # raw message.from
         sender_label, source,                      # derived: 'Musa'/name, 'DM'/'group'
         triage)                                    # passive CoS route suggestion (dict), read-only
    """
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, tag, text, created_at, chat_id, "
            "from_user_id, from_username, from_name, cos_triage FROM operator_messages "
            "WHERE direction='inbound' AND handled_at IS NULL"
            + _channel_scope_sql() +
            " ORDER BY id ASC LIMIT %s", (limit,))
        rows = cur.fetchall()
    return [(rid, tag, text, created_at, fuid, funame, fname,
             _sender_label(fuid, fname, funame),
             _source_hint(chat_id, fuid),
             _triage_for(cos_triage, text, tag))
            for (rid, tag, text, created_at, chat_id,
                 fuid, funame, fname, cos_triage) in rows]


def mark_handled_through(max_id: int) -> int:
    """Stamp every inbound up to and including max_id as handled. Called after
    cc-orchestrator has read + answered the operator's current messages. Returns
    the number of rows stamped."""
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id',%s,true)", (_agent_id(),))
        # Hub stamps must never eat another agent's channel: cai-channel rows
        # are cai's to handle (2026-07-05 — a blanket stamp nearly marked the
        # operator's message to cai as handled while cai was still booting).
        # unprocessed() intentionally still SHOWS them to the hub as a
        # visibility backstop; only the stamp is scoped away.
        scope = _channel_scope_sql()
        if _body_role() == "hub":
            scope += " AND channel IS DISTINCT FROM 'cai-channel'"
        cur.execute(
            "UPDATE operator_messages SET handled_at=now() "
            "WHERE direction='inbound' AND handled_at IS NULL AND id <= %s"
            + scope,
            (max_id,))
        n = cur.rowcount
        conn.commit()
        return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("direction", choices=["inbound", "outbound"])
    ap.add_argument("text")
    ap.add_argument("--chat")
    ap.add_argument("--tag")
    ap.add_argument("--undelivered", action="store_true")
    ap.add_argument("--channel", default="telegram",
                    help="entry surface: telegram (default) | tmux-console | cockpit")
    ap.add_argument("--reason", default=None,
                    help="send-failure reason (JSON from _tg_chunked_send $TG_FAIL_OUT, or "
                         "plain text); stored under cos_triage.send_failure (Nazim #40837)")
    ap.add_argument("--tg-message-id", type=int, default=None,
                    help="Telegram Bot API result.message_id for an OUTBOUND send "
                         "(op#22669 asks-tracking reply-match; ignored for inbound)")
    a = ap.parse_args()
    reason = a.reason
    if reason:
        try:
            reason = json.loads(reason)          # structured {status,description,retry_after,...}
        except (ValueError, TypeError):
            pass                                  # fall back to plain text -> {description: ...}
    print(log(a.direction, a.text, a.chat, a.tag, not a.undelivered, a.channel,
              failure_reason=reason, tg_message_id=a.tg_message_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
