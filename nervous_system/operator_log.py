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
import re
import sys

import psycopg
from dotenv import load_dotenv

from nervous_system import triage  # PASSIVE CoS triage annotation (read-only)
from nervous_system import personal_routing  # bus #47837: private-family content routing
from nervous_system.vault_leak_guard import defensive_redact  # bus #44378

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
# DATABASE_URL alone comes from the .env FILE even when the process inherited one:
# after a password rotation a long-running session's inherited value is stale and
# every call failed auth, keeping the pooler circuit breaker tripped (2026-09-28).
# Only this key: identity vars (AGENT_ID, ORCH_BODY_ROLE, ...) must stay per-process.
# NOT under pytest: forcing the .env prod DSN into os.environ at import would leak it
# across test modules (this module is imported at collection by several tests), un-skip
# DB tests against prod and defeat the root-conftest DSN seal (bus #46880). Under pytest
# the sealed / explicit (env-var) DSN wins; the rotation-staleness fix is prod-only.
_under_pytest = "pytest" in sys.modules or bool(os.environ.get("PYTEST_CURRENT_TEST"))
try:
    from dotenv import dotenv_values as _dotenv_values
    _file_db_url = _dotenv_values(os.path.join(os.path.dirname(__file__), "..", ".env")).get("DATABASE_URL")
    if _file_db_url and not _under_pytest:
        os.environ["DATABASE_URL"] = _file_db_url
except Exception:  # noqa: BLE001 — never block import on this
    pass


# --- ORCH-TOPOLOGY-001 body scoping -----------------------------------------
# Two orch bodies share this table (Studio hub + MacBook console/Nazim). Each
# body reconciles + stamps ONLY its own surface, enforced here (A3: in code
# that reads the env, not by promise): console sees channel='tmux-console'
# only; hub sees everything EXCEPT tmux-console (console messages are answered
# in-console by the console body). Unset role = legacy single-body behavior.

def _body_role() -> str:
    """The body role this process runs as: 'hub' | 'console' | 'lane'. UNSET (or
    whitespace) is 'lane' — NEVER an unscoped legacy body (Fable audit 2026-09-30,
    B-2: launch_dangerous_cc.sh unsets ORCH_BODY_ROLE for every lane, and '' used
    to be the SANCTIONED no-scope role, so one lane stamp could eat every channel)."""
    return os.environ.get("ORCH_BODY_ROLE", "").strip().lower() or "lane"


def _agent_id() -> str:
    """Audit identity for app.current_agent_id. Hub/console: ORCH_AGENT_ID (the
    singleton's own .env). Lane: its OWN lane identity (CC_BASE_AGENT_ID, then
    AGENT_ID) — never a silent 'cc-orchestrator' default, which mis-attributed
    every lane stamp to the hub (Fable audit B-2)."""
    role = _body_role()
    if role in ("hub", "console"):
        return os.environ.get("ORCH_AGENT_ID", "cc-orchestrator")
    return (os.environ.get("CC_BASE_AGENT_ID") or os.environ.get("AGENT_ID")
            or "unknown-lane")


# --- tag shape guard (bus #44971/#45020) -------------------------------------
# Mirrors the operator_messages.tag_shape_chk CHECK constraint (migration
# fix/operator-messages-tag-shape-44971) so a bad tag is refused at the write
# call, not just at the DB -- same belt-and-braces posture as the vault-leak
# guard above. Verified against the live tag corpus (routing tags like
# '@ihsanos', 'fleet/substrate', lane_reply.sh's '<tag>-drill'/'-draft'
# suffixes) before being set, not just code literals (op#16353's leaked-draft
# rows and the 2026-06-30 chat_id-shaped-tag rows are exactly what this
# excludes).
_TAG_SHAPE_RE = re.compile(r"^[a-zA-Z@][a-zA-Z0-9@_/+-]*$")


def _validate_tag_shape(tag: str | None) -> None:
    if tag is None:
        return
    if len(tag) > 64 or not _TAG_SHAPE_RE.match(tag):
        raise ValueError(
            f"operator_log.log(): tag {tag!r} fails the tag-shape guard "
            r"(<=64 chars, must match ^[a-zA-Z@][a-zA-Z0-9@_/+-]*$) -- "
            "looks like prose/an id landed in the tag slot instead of a "
            "channel tag (op#16353's mechanism). Not writing this row."
        )


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
# `mamadah` JOINED 2026-10-01 (bus #47808, op#24172/op#24173): cc-mamadah ("Mama
# Dah's Assistant") owns+reconciles its own private-family Telegram channel via
# its own tag-scoped loop (scripts/lane_operator_reconcile.py --tag mamadah),
# same shape as hk-editor/gazzabyte-irsyad -- this is PRIVATE FAMILY data (Musa
# + wife Zahidah), not a client channel the console should read or answer on
# Zahidah's behalf.
# `coffeemedia` JOINED 2026-10-01 (op#24578/24592): cc-coffeemedia, the Coffee Media GLM lane, talks to
# Musa directly over @wingmendevbot and reconciles its own tag, like mamadah.
_LANE_OWNED_TAGS = ("irsyad-drill", "gazzabyte-irsyad", "hk-editor", "mamadah", "coffeemedia")

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
# through to an unscoped query. The 2026-07-05 incident (commit 3691cea): a
# misconfigured/typo'd ORCH_BODY_ROLE fell through and made
# mark_handled_through() stamp EVERY channel's inbound handled in ONE call —
# cross-body message loss. '' used to be the SANCTIONED legacy single-body
# (unscoped) role; since the Fable audit (2026-09-30, B-2) UNSET == 'lane', and a
# lane must name its tag= — there is no unscoped role any more.
_RECOGNIZED_BODY_ROLES = frozenset({"console", "hub", "lane"})


def _channel_scope(tag: str | None = None) -> "tuple[str, tuple]":
    """The (sql_fragment, params) channel scope for THIS body. The fragment starts
    with ' AND ' and is appended to a WHERE on operator_messages; params are the
    psycopg placeholders it needs (a lane's tag is PARAMETERIZED, never
    interpolated). Raises on an unrecognized role, and on role 'lane' without a
    tag — a lane may never read/stamp across tags."""
    role = _body_role()
    if role not in _RECOGNIZED_BODY_ROLES:
        raise ValueError(
            "ORCH_BODY_ROLE=%r is not a recognized orch body role "
            "(expected 'console', 'hub', or 'lane'/unset). Refusing to build an "
            "UNSCOPED channel query: an unrecognized role must never silently "
            "let unprocessed()/mark_handled() span EVERY channel "
            "(cross-body message loss — see commit 3691cea, 2026-07-05)." % role
        )
    if role == "lane":
        t = (tag or "").strip()
        if not t:
            raise ValueError(
                "operator_log: this process is a LANE (ORCH_BODY_ROLE unset) — "
                "unprocessed()/mark_handled() require tag=<your channel tag>. A lane "
                "never reads or stamps across channels (Fable audit 2026-09-30 B-2: "
                "an unscoped lane stamp marked every channel handled). Use "
                "scripts/lane_operator_reconcile.py --tag <tag> or pass tag=."
            )
        _validate_tag_shape(t)
        return (" AND tag=%s", (t,))
    if tag is not None and str(tag).strip():
        # a hub/console caller narrowing to one tag INSIDE its own scope is fine
        _validate_tag_shape(str(tag).strip())
        return (_channel_scope_sql() + " AND tag=%s", (str(tag).strip(),))
    return (_channel_scope_sql(), ())


def _channel_scope_sql() -> str:
    """Param-free scope fragment for the two SINGLETON bodies (hub/console). A
    lane has no param-free form (its scope IS its tag) — raises, use
    _channel_scope(tag=...)."""
    role = _body_role()
    if role not in _RECOGNIZED_BODY_ROLES:
        raise ValueError(
            "ORCH_BODY_ROLE=%r is not a recognized orch body role "
            "(expected 'console', 'hub', or 'lane'/unset). Refusing to build an "
            "UNSCOPED channel query: an unrecognized role must never silently "
            "let unprocessed()/mark_handled() span EVERY channel "
            "(cross-body message loss — see commit 3691cea, 2026-07-05)." % role
        )
    if role == "lane":
        raise ValueError(
            "operator_log: role 'lane' (ORCH_BODY_ROLE unset) has no unscoped "
            "channel clause — pass tag= (see _channel_scope). A lane never spans "
            "every channel (Fable audit 2026-09-30 B-2)."
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
    raise AssertionError("unreachable: role %r" % role)  # every role handled above


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


# --- Triage heuristic (migration 082, bus #45557 condition 3) --------------
# Cheap, reversible pre-classifier for bare acks ("ok", "thanks", "👍") so the
# digest doesn't need a human pass for the overwhelming common case. Kept
# deliberately conservative: a false 'not_an_ask' silently hides a real ask
# from the digest, which is worse than leaving it in the 'captured' bucket
# for a human/agent to clear via scripts/asks_triage.py. NEVER fires if the
# text contains '?' or any token that reads as a request.
_BARE_ACK_TOKENS = frozenset({
    "ok", "okay", "k", "kk", "thanks", "thank", "thankyou", "ty", "thx",
    "got", "it", "sounds", "good", "great", "nice", "cool", "yes", "yep",
    "yeah", "sure", "noted", "understood", "perfect", "awesome", "np",
})
_REQUEST_TOKENS = frozenset({
    "please", "can", "could", "would", "should", "need", "want", "check",
    "fix", "build", "send", "show", "give", "find", "make", "add", "remove",
    "update", "deploy", "run", "look", "investigate", "explain", "tell",
    "why", "when", "where", "who", "how", "what",
})


def _heuristic_triage(text: str) -> str | None:
    """Returns 'not_an_ask' only for an unambiguous bare acknowledgement;
    None (defer — stays 'captured') for everything else, including anything
    ambiguous. Reversible either way: a wrong call is undone with
    `asks_triage.py <id> ask --summary ...`."""
    if not text:
        return None
    stripped = text.strip()
    if not stripped or "?" in stripped:
        return None
    letters = re.sub(r"[^A-Za-z]", " ", stripped).split()
    if not letters:
        # no ASCII letters at all -> emoji/punctuation-only, reads as a bare ack
        return "not_an_ask"
    tokens = [t.lower().strip(".,!¡") for t in stripped.split()]
    tokens = [t for t in tokens if t]
    if len(tokens) > 3:
        return None
    if any(t in _REQUEST_TOKENS for t in tokens):
        return None
    if all(t in _BARE_ACK_TOKENS for t in tokens):
        return "not_an_ask"
    return None


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

        heuristic = _heuristic_triage(text)
        if heuristic == "not_an_ask":
            # stays OPEN (closed_at IS NULL) — heuristic hits are reversible via
            # asks_triage.py, unlike a human/agent 'not' triage which closes it.
            cur.execute(
                "INSERT INTO operator_asks "
                "  (ask, source_msg_id, triage_state, triaged_at, triaged_by) "
                "VALUES (%s,%s,'not_an_ask',now(),'heuristic') RETURNING id",
                (text, op_msg_id),
            )
        else:
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


_WH_WORD = r"(?:where|what|which|who|why|when|how)"

_CLIENT_ASK_PATTERNS = [re.compile(p, re.I) for p in (
    r"\?",                                    # any question -- addressed to us
    r"\bplease\b",
    r"\bcan (you|we|i)\b",
    r"\bcould (you|we)\b",
    r"\bwould (you|it be)\b",
    # bus #47267 item 3a: a WH-stem question with the '?' dropped ("where is
    # the invoice", "what's the status") -- contracted 's forms need no
    # trailing aux since the aux IS the contraction.
    rf"\b{_WH_WORD}'s\b",
    rf"\b{_WH_WORD}\b[^.?!]{{0,40}}?\b(?:is|are|do|does|did)\b",
    # need/want: bare substrings flood on personal-desire chatter ("I need a
    # coffee", "want a break") -- negative-lookahead excludes that short,
    # common class without narrowing the directed-request recall the 359-row
    # backfill was already validated against (bus #47267 item 3: tighten
    # without re-litigating the proven irsyad-coord sample).
    r"\bneed(?:s|ed)?\b(?!\s+(?:a\s+)?(?:break|rest|coffee|nap|vacation|sleep|minute|moment)\b)",
    r"\bmust\b",
    r"\bhave to\b",
    r"\bwhen (will|is|can|does)\b",
    r"\bhow (do|can|long|much|many)\b",
    r"\bstatus (on|of)\b",
    r"\bany update\b",
    r"\bfollow(?:ing)? up\b",
    r"\bstill waiting\b",
    r"\basap\b",
    r"\burgent(?:ly)?\b",
    # reminder: only as a directed nudge, not a plain FYI ("just a reminder
    # we're closed tomorrow" is chatter, not a request of us).
    r"\breminder\b.{0,25}\b(?:to|that you|please|kindly)\b",
    r"\bnot (?:done|fixed|working)\b",
    r"\b(?:is |seems |appears )?missing\b",
    r"\bbroken\b",
    r"\bdoesn'?t work\b",
    r"\bisn'?t work(?:ing)?\b",
    r"\bstopped working\b",
    r"\bwant(?:s|ed)?\b(?!\s+(?:a\s+)?(?:break|rest|coffee|nap|vacation|sleep)\b)",
    r"\bit(?:'s| is| has been)\b.*\b(?:day|days|hour|hours|week|weeks)\b",
    # bus #47267 item 3d: indirect/declarative asks with no '?' and no
    # imperative -- a status complaint phrased as a statement of fact.
    r"\bhaven'?t (?:received|got(?:ten)?|heard)\b",
    r"\bno\b[^.?!]{0,25}\byet\b",
    r"\bis it (?:ready|done|fixed|working|live|up)\b",
    r"\bwrong\b",
    r"\berror\b",
    r"\b(?:is|looks|seems|appears|feels) off\b",
)]

# A reply-thread quote wrapper ("↩️ re "<quoted earlier message>": <the actual
# reply>") sits in front of the real content on every threaded reply -- the
# leading-imperative check below is anchored to the START of a sentence, and
# with the wrapper unstripped that start is always the quoted OLD message, not
# the new one. GREEDY matching up to the FINAL literal '":' in the string
# (bus #47267 item 3c, fixing the prior lazy `.*?`) -- a quoted excerpt that
# itself contains an internal '":' sequence made the lazy match stop at that
# internal occurrence, leaving the tail of the OLD quoted text spliced onto
# the front of what the imperative check then treated as the NEW reply. The
# real reply always follows the wrapper's own closing '":', which is the
# LAST one in the string (the reply itself is not expected to contain a
# '":' sequence of its own).
_REPLY_QUOTE_PREFIX_RE = re.compile(r'^\s*(?:↩️\s*)?re\s+".*":\s*', re.I | re.S)

_IMPERATIVE_LEAD_RE = re.compile(
    r"^(?:add|build|fix|remove|change|send|check|review|confirm|provide|"
    r"give|share|schedule|arrange|update|deploy|create|make|set ?up|keep|guide)\b", re.I)
_SENTENCE_SPLIT_RE = re.compile(r"[\n]+|(?<=[.!?])\s+")
# bus #47267 item 3b: strip a leading politeness token before the imperative
# check -- "please X" is already caught by _CLIENT_ASK_PATTERNS' bare
# \bplease\b, but "pls/plz/kindly X" reached neither that pattern nor the
# imperative check (the politeness token, not the verb, sat at sentence-start).
_POLITENESS_LEAD_RE = re.compile(r"^(?:pls|plz|please|kindly)[,:]?\s+", re.I)


def _has_leading_imperative(text: str) -> bool:
    """An imperative ("send me X", "please make Y") only reads as a command
    at the START of ITS OWN sentence -- checking only the start of the WHOLE
    message (as a single ^-anchored pattern would) misses every imperative
    that isn't the message's very first word, which is most of them in a
    multi-sentence client message (bus #47184 precision-sample finding:
    "...DP and SPR has different type of BC number. make it flexible when
    entering..." -- the real ask is the second sentence). A leading
    politeness token ("pls send X") is stripped first so the imperative verb
    itself lands at the segment start (bus #47267 item 3b)."""
    return any(_IMPERATIVE_LEAD_RE.match(_POLITENESS_LEAD_RE.sub("", seg.strip()))
               for seg in _SENTENCE_SPLIT_RE.split(text))


def classify_client_ask(text: str) -> str:
    """First-pass classifier for client-channel inbound (Musa op#23944 follow-up,
    orch-console bus #47184): default is 'not_an_ask' -- 'ask' ONLY for a
    request/question addressed to us (an imperative, "please/can you/how do
    I/when will", a question mark, or urgency/follow-up phrasing). INVERTED
    bias from _heuristic_triage()'s operator-surface default (which defers
    ambiguous text to 'captured', because under-capturing an operator ask is
    the worse failure there): the 085 backfill classified 199/359 rows as
    chased asks when most (cosem-caai) were plain client-channel chatter/acks
    -- a ledger where 1 real ask hides among 20 chatter rows recreates
    exactly the Shuq failure this whole ledger exists to prevent, and paging
    on it floods every lane. A wrong call either way is one command away from
    correction: scripts/asks_triage.py <id> ask --summary "..." (or `not`).

    Strips a leading reply-thread quote wrapper before matching (bus #47184
    precision-sample finding #2: a reply-quote-prefixed imperative like
    '↩️ re "...": send me the names' was invisible to a whole-string-anchored
    check).

    Examples: "It's more than a day. It needs to be done" -> ask (needs +
    urgency phrase). "Lolol" / "roger" / "yup" -> not_an_ask."""
    stripped = (text or "").strip()
    if not stripped:
        return "not_an_ask"
    stripped = _REPLY_QUOTE_PREFIX_RE.sub("", stripped)
    if not stripped:
        return "not_an_ask"
    for pat in _CLIENT_ASK_PATTERNS:
        if pat.search(stripped):
            return "ask"
    if _has_leading_imperative(stripped):
        return "ask"
    return "not_an_ask"


def maybe_track_client_ask(op_msg_id: int, text: str, owner_lane: str | None,
                            chase_hours: float = 24, opened_at=None) -> int:
    """Called for every INBOUND message on a channel whose bot_channels.audience
    = 'client' (migration 085, Musa op#23944, bus #47105 -> #47114). Opens ONE
    operator_asks row per client inbound with ask_surface='client-channel'
    (supersedes cc-fleet-health's never-built op#23531 component-4, same
    value -- orch-console #47110 decision 1), delegated_to=owner_lane so the
    SLA watchdog knows who to page.

    chase_by is REQUIRED here (unlike maybe_track_ask()'s optional chase_by
    for the operator path) -- every GENUINE client ask gets a deadline at
    open time. EXCEPTION (bus #47349, orch-console): a row the classifier
    labels 'not_an_ask' gets chase_by left NULL instead -- it still opens a
    row (see below, no inbound is ever silently dropped) and stays OPEN for
    the same one-command reversibility, but a bare ack/chatter row must never
    carry a live chase deadline (the SLA chase net's own query also filters
    triage_state as a second layer, but a not_an_ask row shouldn't have a
    chase_by at all even in principle -- 193 such rows were found live with
    one anyway). scripts/asks_triage.py restores chase_by with a fresh
    default window on re-triage back to 'ask'.
    Unlike maybe_track_ask(), this NEVER auto-closes on a reply: an "in
    progress" reply must not close the ask or push chase_by out (bus #47110
    item 3) -- only an explicit dated commitment (scripts/asks_triage.py
    committed_date+outbound_msg_id) or a done-confirmation closes it.

    Every call opens a row (unlike maybe_track_ask(), there is no surface-gate
    or direction check here — the caller, ingest.py's 3c block, already knows
    this is an inbound client-channel message). triage_state is set directly
    from classify_client_ask() at insert (bus #47184 follow-up) -- NOT left at
    the 'captured' default -- so the owning lane's queue is already filtered
    to genuine asks instead of every inbound needing a human look. A bare ack
    still opens a row, just pre-triaged 'not_an_ask', so no client inbound is
    ever silently dropped. Returns the new row's id.

    `opened_at` (scripts/backfill_client_asks_ledger.py, bus #47110 item 4):
    when given, backdates created_at/chase_by to the ORIGINAL message time
    instead of now() -- a 3-day-old backfilled ask must show as already
    overdue, not get a fresh 24h grace period it never actually had."""
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    # cc-quality PR#231 review (bus #47265, LOW): this connect had no timeout,
    # unlike every other fresh-connect call site in this file -- a stalled
    # pooler would hang ingest.py's inbound path indefinitely instead of
    # failing the capture loud and fast.
    with psycopg.connect(dsn, connect_timeout=10) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id',%s,true)", (_agent_id(),))
        label = classify_client_ask(text)
        # stays OPEN (closed_at IS NULL) either way — a heuristic hit is
        # reversible via asks_triage.py, same convention as the operator path.
        # chase_by is NULL for a not_an_ask label (bus #47349) — a bare
        # ack/chatter row never gets a live chase deadline; asks_triage.py
        # restores it with a fresh window on re-triage back to 'ask'.
        cur.execute(
            "INSERT INTO operator_asks "
            "  (ask, source_msg_id, ask_surface, delegated_to, created_at, chase_by, "
            "   triage_state, triaged_at, triaged_by) "
            "VALUES (%s,%s,'client-channel',%s, COALESCE(%s,now()), "
            "        CASE WHEN %s = 'not_an_ask' THEN NULL "
            "             ELSE COALESCE(%s,now()) + (%s || ' hours')::interval END, "
            "        %s, now(), 'heuristic') RETURNING id",
            (text, op_msg_id, owner_lane, opened_at, label, opened_at, chase_hours, label),
        )
        rid = cur.fetchone()[0]
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
    except BaseException:
        # Broad on purpose (bus #44412 CI catch): bus_send.py raises SystemExit,
        # not a subclass of Exception, when DATABASE_URL is unset — an
        # `except Exception` here left that specific failure unswallowed,
        # contradicting this function's own "never raises" docstring claim
        # (same failure shape as defensive_redact's VaultError-only catch,
        # bus #44388 round 2). A best-effort bus-post helper must not let ANY
        # exception type escape past the already-committed row it's a fallback
        # for.
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
    _validate_tag_shape(tag)
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
    # PRIVATE-FAMILY routing (bus #47837 C1/C2): this `log()` is the ONE shared
    # INSERT point every *_send.sh sibling's `operator_log outbound` call goes
    # through (plus any inbound caller other than ingest.py's own raw insert),
    # so a personal-routed tag's real text must never land here either — a
    # reply quoting her draft/coursework is content too, not just her own
    # inbound words. Every other tag is byte-identical (personal_routed=False
    # takes the untouched original path below).
    personal_routed = personal_routing.is_personal_routed(tag)
    stored_text = personal_routing.SENTINEL_TEXT if personal_routed else text
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id',%s,true)", (_agent_id(),))
        cur.execute(
            "INSERT INTO operator_messages (direction, channel, chat_id, tag, text, delivered, cos_triage, tg_message_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s) RETURNING id",
            (direction, channel, chat_id, tag, stored_text, delivered, cos, tg_message_id),
        )
        rid = cur.fetchone()[0]
        # C1 (bus #47837): envelope NOT committed yet. The real-content write
        # to wingmen-personal must land first and be committed — if it raises,
        # roll back the envelope and re-raise (caller sees the failure; no
        # orphaned content-free row, no false "logged" result). Not
        # best-effort, unlike every other side-effect in this function.
        if personal_routed:
            try:
                personal_routing.write_personal_content(
                    rid, direction=direction, channel=channel, tag=tag,
                    text=text, chat_id=chat_id, tg_message_id=tg_message_id,
                )
            except Exception:
                conn.rollback()
                raise
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
    # primary durable log row above, which has already committed. Skipped for
    # a personal-routed tag's own sentinel text: _is_operator_ask_surface
    # already excludes 'mamadah' (not in _ASK_TRACKED_TAGS) so this was always
    # a no-op for it, but routing the SENTINEL through here instead of real
    # text is correct regardless if that set ever changes.
    if direction == "inbound":
        try:
            maybe_track_ask(rid, direction, channel, tag, stored_text)
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
# / on the autonomous wakeup, answers, then stamps the ids it answered via
# mark_handled([ids]) (mark_handled_through() is deprecated + bounded to the read).
# At-least-once: a rare re-surfacing beats a silent loss (cai's ruling).
# Same convention applies to open_asks_for(<body>) (op#22669): read it at the
# same reconciliation points (turn start / autonomous wakeup), not just at boot —
# the SessionStart hook (scripts/session_start_reconstitute.py) only fires on a
# FRESH context (startup/clear), so a long-lived resumed session must re-check
# open_asks_for() itself the same way it already re-checks unprocessed().

# The ids the LAST unprocessed() call in this process returned. mark_handled_through()
# (deprecated) is bounded to this set: it may stamp only rows the caller was actually
# SHOWN. Before (Fable audit 2026-09-30 B-2): unprocessed(limit=20) showed 20 rows but
# mark_handled_through(max_id) stamped EVERY row <= max_id — the 21st+ row was marked
# handled without ever being read (silent loss). Process-local on purpose: the stamp
# must come from the same body that read.
_LAST_READ_IDS: list = []


def _reset_read_cursor() -> None:
    _LAST_READ_IDS.clear()


def unprocessed(limit: int = 20, tag: str | None = None) -> list:
    """Inbound operator messages not yet marked handled, oldest-first. The
    reconciliation read that makes delivery independent of keystrokes landing.
    A LANE body (ORCH_BODY_ROLE unset) MUST pass tag=; hub/console may narrow
    with tag= inside their own scope. Records the returned ids for
    mark_handled_through()'s bound.

    Return shape (BACKWARD-COMPATIBLE — first 4 positions unchanged, sender
    fields then the passive triage suggestion APPENDED at the end):
        (id, tag, text, created_at,                 # original 4
         from_user_id, from_username, from_name,    # raw message.from
         sender_label, source,                      # derived: 'Musa'/name, 'DM'/'group'
         triage)                                    # passive CoS route suggestion (dict), read-only
    """
    scope_sql, scope_params = _channel_scope(tag)
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, tag, text, created_at, chat_id, "
            "from_user_id, from_username, from_name, cos_triage FROM operator_messages "
            "WHERE direction='inbound' AND handled_at IS NULL"
            + scope_sql +
            " ORDER BY id ASC LIMIT %s", (*scope_params, limit))
        rows = cur.fetchall()
    _LAST_READ_IDS[:] = [r[0] for r in rows]
    return [(rid, tag_, text, created_at, fuid, funame, fname,
             _sender_label(fuid, fname, funame),
             _source_hint(chat_id, fuid),
             _triage_for(cos_triage, text, tag_))
            for (rid, tag_, text, created_at, chat_id,
                 fuid, funame, fname, cos_triage) in rows]


def _stamp_scope(tag: str | None) -> "tuple[str, tuple]":
    scope_sql, scope_params = _channel_scope(tag)
    # Hub stamps must never eat another agent's channel: cai-channel rows are
    # cai's to handle (2026-07-05 — a blanket stamp nearly marked the operator's
    # message to cai as handled while cai was still booting). unprocessed()
    # intentionally still SHOWS them to the hub as a visibility backstop; only
    # the stamp is scoped away.
    if _body_role() == "hub":
        scope_sql += " AND channel IS DISTINCT FROM 'cai-channel'"
    return scope_sql, scope_params


def mark_handled(ids, tag: str | None = None) -> int:
    """Stamp EXACTLY the given inbound ids as handled (within this body's scope —
    an id outside it is silently not stamped, so a lane can never stamp another
    tag's row even by id). The ONE sanctioned stamp: a reply names the ids it
    answered (or deferred elsewhere). No high-water form. Returns rows stamped;
    [] -> 0 without touching the DB."""
    id_list = sorted({int(i) for i in (ids or []) if i is not None})
    if not id_list:
        return 0
    scope_sql, scope_params = _stamp_scope(tag)
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id',%s,true)", (_agent_id(),))
        cur.execute(
            "UPDATE operator_messages SET handled_at=now() "
            "WHERE direction='inbound' AND handled_at IS NULL AND id = ANY(%s)"
            + scope_sql,
            (id_list, *scope_params))
        n = cur.rowcount
        conn.commit()
        return n


def mark_handled_through(max_id: int, tag: str | None = None) -> int:
    """DEPRECATED high-water stamp — kept only for the two singleton bodies' existing
    call sites, and BOUNDED: stamps only ids <= max_id that THIS process's last
    unprocessed() call actually returned (never a row the caller was not shown —
    the 21st-row loss). Raises for a LANE (use mark_handled(ids, tag=)) and when
    no unprocessed() read preceded it in this process."""
    if _body_role() == "lane":
        raise ValueError(
            "operator_log.mark_handled_through() is not available to a LANE: a "
            "high-water stamp across a channel silently marks unanswered rows handled "
            "(Fable audit 2026-09-30 B-2). Use mark_handled([ids], tag=<tag>) with the "
            "ids you actually answered, or scripts/lane_operator_reconcile.py handle "
            "--tag <tag> --ids <id,id>."
        )
    if not _LAST_READ_IDS:
        raise ValueError(
            "operator_log.mark_handled_through(): no unprocessed() read in this "
            "process — refusing a blind high-water stamp. Read first, answer, then "
            "stamp the ids you read (mark_handled([ids]))."
        )
    if int(max_id) not in _LAST_READ_IDS:
        raise ValueError(
            f"operator_log.mark_handled_through({max_id}): that id was NOT among the rows "
            f"your last unprocessed() returned ({_LAST_READ_IDS}) — you are stamping past a "
            f"row you never read (the 21st-row loss, Fable audit 2026-09-30 B-2). Stamp only "
            f"ids you read+answered: mark_handled([ids])."
        )
    ids = [i for i in _LAST_READ_IDS if i <= int(max_id)]
    return mark_handled(ids, tag=tag)


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
