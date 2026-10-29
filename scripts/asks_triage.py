#!/usr/bin/env python3
"""asks_triage.py — set the TRIAGE axis on one operator_asks row.

Sibling of scripts/asks_open.py / scripts/asks_close.py, same vetted-script
shape (single statement, dsn fallback, exit 0/2).

Migration 082 (bus #45552 -> #45555 -> #45557 GO) adds triage_state to
operator_asks, orthogonal to the existing closed_at/closed_reason lifecycle:
"is this text even an ask" is a judgment call, not derivable from the
agent_messages thread, so it has to be recorded. See migration 082's header
for the full rationale.

Usage:
  asks_triage.py <id> ask  --summary "<one line>" [--delegated-to BODY]
                           [--committed-date ISO] [--outbound-msg-id ID]
                           [--needs-musa on|off] [--musa-summary "<plain line>"]
  asks_triage.py <id> not
  asks_triage.py <id> done [--evidence "<bus/op ids>"]
                           [--committed-date ISO] [--outbound-msg-id ID]
  (any action) [--delivered] — stamp delivered_at = now() (LIVE verified),
  first stamp wins, rides along with any action (op#34803, orch-console
  #78198: UI asks stay open after live until Musa confirms, so turnaround
  measures delivered_at, not closed_at). REFUSED when the evidence
  (--evidence, or the row's triage_evidence_ref) cites a bus#/op# row older
  than 30 min: that's a catch-up close, and now() would be the bookkeeping
  time, not the live time (orch-console #79947/#79949). Use --delivered-at.

  (any action) [--delivered-at bus#N | op#N | ISO-8601-with-offset] — stamp
  delivered_at at the moment the work actually went live: the created_at of
  agent_messages N / operator_messages N, or the given time. Same first-stamp-
  wins rule; records the ref in delivered_evidence_ref (source stays 'manual':
  'bus-report' is backfill-only, ruling #78610). Validated BEFORE any write, so
  a bad ref changes nothing.

  Either --delivered or --delivered-at REFUSES (raises ValueError, nothing
  written) unless a summary exists: triage_summary or operator_summary
  already on the row, or --musa-summary supplied this call (--summary only
  applies to action=ask). Console op#35502/35503 follow-up, orch-console bus
  #81244: operator_asks #5690 was stamped `done --evidence ... --delivered-at
  op#35467` with no summary ever written, so /builds fell back to showing the
  internal 'summary pending from X (topic)' placeholder next to a real
  elapsed-time pill -- reads like a leak on a card that looks delivered.

  (any action) [--answers ASK_ID] — the row being triaged IS Musa's answer to
  open ask ASK_ID: confirm+close it, linking the answering operator message
  (bus #78269 — he answers with fresh messages, not TG replies, so the
  reply-link auto-close misses those). Idempotent: an already-closed target
  is exit-0 ok (goal state holds); the close only fires when the triage
  itself applied (cc-quality 78363 finding 2).

Migration 118 (orch-console bus #75387) adds the needs-Musa axis, orthogonal
to the triage axis and usable with ANY action: --needs-musa on|off sets /
clears needs_operator_decision (genuinely needs Musa's own decision/action —
absent leaves it untouched, so a plain re-triage never silently drops the
mark); --musa-summary writes operator_summary, the plain-language
decision-first line the console /asks hero renders instead of the internal
triage_summary (no agent code-names/hc#/op#).

Actions:
  ask    a real, still-open request. Sets triage_state='ask',
         triage_summary (required), optionally delegated_to. ALWAYS clears
         closed_at (reopens if needed) — this is the heuristic pre-
         classifier's undo path: a misclassified 'not_an_ask' is one
         command away from correction. NEVER pushes chase_by out UNLESS
         --committed-date is given (migration 085, bus #47114 item 3a/b) — a
         plain "in progress" triage must not reschedule the chase net.
  not    not a real request (bare ack / approval / inline-answered
         question). Sets triage_state='not_an_ask', closed_at=now(),
         closed_reason='not_a_request'. Also clears chase_by (bus #47349) --
         a row judged not an ask must never carry a live chase deadline,
         belt-and-suspenders alongside the chase query's own triage_state
         filter. Restored with a fresh default window if later re-triaged
         back to 'ask' with no chase_by already set.
  done   a real request that has already been delivered. Sets
         triage_state='done', closed_at=now(), closed_reason='done',
         optionally triage_evidence_ref.

--committed-date / --outbound-msg-id (migration 085): a dated commitment on
an ask_surface='client-channel' row counts only if it was actually SENT to
the client — outbound_msg_id must name the operator_messages row that states
the date (DB CHECK enforces the pairing; a date agreed only on the bus never
satisfies this). For a client-channel row, `done` REFUSES (raises ValueError)
unless it has --evidence, OR both --committed-date and --outbound-msg-id
(this call or already on the row). ask_surface='operator' rows are NEVER
gated — this is a client-channel-only restriction.

Migration 132 (console bus #80707/#80718/#80719) closes a real gap: the
migration-085 CHECK only proved outbound_msg_id IS NOT NULL, never that it
named a REAL outbound send — lanes had set it to agent_messages (bus) row
ids in good faith. A freshly-passed --outbound-msg-id is now validated
client-side (validate_outbound_msg_id) before any write, mirroring the DB
trigger that is the real floor: it must be a real operator_messages row with
direction='outbound' that was sent AFTER this ask was created, else REFUSES
(raises ValueError). If --committed-date is also given, a date that doesn't
visibly appear in that message's text (in any common written form) only
WARNS to stderr — never refuses, since a date can be stated in a form this
doesn't recognize.

triaged_at/triaged_by are always stamped. triaged_by is resolved fail-closed
via scripts/lib/agent_identity.resolve_agent_id (CC_BASE_AGENT_ID, then
AGENT_ID, then ORCH_AGENT_ID only for the console body itself) — REFUSES
(exit 2) rather than default to 'orch-console' if none resolve (bus #47221:
the old $ORCH_AGENT_ID-or-'orch-console' fallback misattributed 19 rows
triaged by cc-oeh/cc-angullia to the console, since ORCH_AGENT_ID is
fleet-wide .env noise every process inherits regardless of who is actually
running the script). Pass --triaged-by to override explicitly.

Exit 0 + prints 'ok' on exactly one row updated; exit 2 + stderr
'error: ...' otherwise (bad args / no DSN / unknown id), so a shell caller
can surface a clean failure.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.agent_identity import resolve_agent_id  # noqa: E402


def _dsn() -> str | None:
    """Same fallback shape as asks_open.py's _dsn(): prefer an already-exported
    DATABASE_URL/SUPABASE_DB_URL, else load the orchestrator's own .env by an
    ABSOLUTE path (robust to whatever cwd the caller shell is in)."""
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if dsn:
        return dsn
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    load_dotenv(env_path)
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("id", type=int, help="operator_asks.id")
    ap.add_argument("action", choices=("ask", "not", "done"))
    ap.add_argument("--summary", default=None, help="triage_summary: required for action=ask; stored on done/not when given (an existing line is kept when omitted)")
    ap.add_argument("--delegated-to", default=None, help="action=ask only: agents.agent_id owner")
    ap.add_argument("--evidence", default=None, help="action=done only: triage_evidence_ref")
    ap.add_argument("--committed-date", default=None,
                     help="ISO datetime: a dated commitment (migration 085) — requires "
                          "--outbound-msg-id (this call or already on the row)")
    ap.add_argument("--outbound-msg-id", type=int, default=None,
                     help="operator_messages.id of the outbound send that STATED the "
                          "--committed-date (migration 085)")
    ap.add_argument("--triaged-by", default=None,
                     help="override triaged_by identity (default: auto-resolve fail-closed "
                          "via CC_BASE_AGENT_ID/AGENT_ID — see agent_identity.resolve_agent_id)")
    ap.add_argument("--needs-musa", choices=("on", "off"), default=None,
                     help="migration 118 flag, tri-state: 'on' sets "
                          "needs_operator_decision (genuinely needs Musa's own "
                          "decision/action), 'off' clears it, ABSENT leaves it "
                          "untouched (a plain re-triage never silently drops the mark)")
    ap.add_argument("--musa-summary", default=None,
                     help="operator_summary — the plain-language, decision-first line "
                          "the Needs-Musa hero renders instead of triage_summary "
                          "(no agent code-names/hc#/op#; orch-console bus #75387)")
    ap.add_argument("--delivered", action="store_true",
                    help="stamp delivered_at = now() (LIVE verified). Idempotent: the FIRST stamp "
                         "wins. Rides along with any action. UI asks stay open after live until "
                         "Musa confirms, so turnaround measures delivered_at, not closed_at "
                         "(op#34803, orch-console #78198)")
    ap.add_argument("--delivered-at", default=None, metavar="REF",
                    help="stamp delivered_at at the LIVE time: bus#N (agent_messages N's created_at), "
                         "op#N (operator_messages N's created_at) or an ISO-8601 time with an offset. "
                         "First stamp wins. Use this for any catch-up close (op#34803, #79949)")
    ap.add_argument("--answers", type=int, default=None, metavar="ASK_ID",
                     help="the operator message being triaged IS the answer to open "
                          "ask ASK_ID — confirm+close it (same close as the console "
                          "swipe: confirmed_at + closed_reason='operator_done'), "
                          "linking this row's source_msg_id as evidence. Root-cause "
                          "fix for the stale-pending drift: Musa answers with a "
                          "fresh message, not a TG reply, so the reply-link "
                          "auto-close never fires (orch-console bus #78269)")
    return ap


# A bare --delivered stamps now(); that is only the live time if the evidence is fresh.
BARE_DELIVERED_MAX_EVIDENCE_AGE = dt.timedelta(minutes=30)
_REF_RE = re.compile(r"\b(bus|op)\s*#\s*(\d+)", re.IGNORECASE)
_REF_TABLE = {"bus": "agent_messages", "op": "operator_messages"}


def parse_evidence_refs(text: str | None) -> list:
    """Pure: the bus#N / op#N refs in an evidence string, in order, as (kind, id).
    A bare '#N' is ignored: it could be a bus row, an op message or a PR."""
    return [(k.lower(), int(n)) for k, n in _REF_RE.findall(text or "")]


def _ref_time(cur, kind: str, ref_id: int):
    cur.execute(f"SELECT created_at FROM {_REF_TABLE[kind]} WHERE id = %s", (ref_id,))
    row = cur.fetchone()
    return row[0] if row else None


def resolve_delivered_at(cur, item_id: int, value: str, *, now=None):
    """Resolve a --delivered-at value to (timestamp, evidence_ref, source). Raises
    ValueError on anything unresolvable, naive, in the future, or before the ask."""
    now = now or dt.datetime.now(dt.timezone.utc)
    refs = parse_evidence_refs(value)
    if refs and _REF_RE.fullmatch(value.strip()):
        kind, ref_id = refs[0]
        ts = _ref_time(cur, kind, ref_id)
        if ts is None:
            raise ValueError(f"--delivered-at {value}: no {_REF_TABLE[kind]} row #{ref_id}")
        # always 'manual': bus-report is reserved for the backfill (ruling #78610,
        # enforced by operator_asks_bus_evidence_backfill_only_chk); the anchor lives in the ref
        ref, source = f"{kind}#{ref_id}", "manual"
    else:
        try:
            ts = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"--delivered-at {value!r}: not bus#N, op#N or an ISO-8601 time") from None
        if ts.tzinfo is None:
            raise ValueError(f"--delivered-at {value!r}: give an explicit offset (Z or +04:00), "
                             f"a naive time is ambiguous")
        ref, source = f"iso:{ts.isoformat()}", "manual"
    if ts > now + dt.timedelta(minutes=5):
        raise ValueError(f"--delivered-at {value}: {ts.isoformat()} is in the future")
    cur.execute("SELECT created_at FROM operator_asks WHERE id = %s", (item_id,))
    row = cur.fetchone()
    if row is not None and ts < row[0]:
        raise ValueError(f"--delivered-at {value}: {ts.isoformat()} is before the ask itself "
                         f"({row[0].isoformat()})")
    return ts, ref, source


def check_summary_before_delivery(cur, item_id: int, *, summary: str | None,
                                   musa_summary: str | None) -> None:
    """Refuse a --delivered/--delivered-at stamp when no summary exists on the
    row and none is supplied in this call (console op#35502/35503 follow-up,
    orch-console bus #81244). A delivered_at with no triage_summary/
    operator_summary falls back on the /builds board to the internal
    'summary pending from X (topic)' placeholder sitting next to a real
    elapsed-time pill -- reads like a leak on a card that looks complete.
    Root-caused live on operator_asks #5690: `done --evidence ... --delivered-
    at op#35467` with no --musa-summary and no prior `ask --summary` ever
    run, so triage_summary/operator_summary stayed NULL. --summary only
    applies to action=ask; --musa-summary rides along with any action and
    also counts (it's operator_summary, the other half of the fallback's
    OR)."""
    if (summary and summary.strip()) or (musa_summary and musa_summary.strip()):
        return
    cur.execute(
        "SELECT triage_summary, operator_summary FROM operator_asks WHERE id = %s",
        (item_id,),
    )
    row = cur.fetchone()
    if row and ((row[0] and row[0].strip()) or (row[1] and row[1].strip())):
        return
    raise ValueError(
        "a --delivered/--delivered-at stamp requires a written summary -- none "
        "exists on this row and none was supplied. Pass --musa-summary \"<line>\" "
        "(or triage it `ask --summary \"<line>\"` first) so /builds never shows "
        "the internal pending-placeholder next to a real elapsed time."
    )


def check_bare_delivered(cur, item_id: int, evidence: str | None, *, now=None) -> None:
    """Refuse a bare --delivered (stamp = now()) when the newest bus#/op# evidence row,
    from this call's --evidence or the row's triage_evidence_ref, is older than
    BARE_DELIVERED_MAX_EVIDENCE_AGE. No resolvable ref = allowed (verified live just now)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cur.execute("SELECT triage_evidence_ref FROM operator_asks WHERE id = %s", (item_id,))
    row = cur.fetchone()
    refs = parse_evidence_refs(evidence) + parse_evidence_refs(row[0] if row else None)
    timed = [(t, f"{k}#{n}") for k, n in refs for t in [_ref_time(cur, k, n)] if t is not None]
    if not timed:
        return
    newest, ref = max(timed)
    if now - newest > BARE_DELIVERED_MAX_EVIDENCE_AGE:
        age_min = int((now - newest).total_seconds() // 60)
        raise ValueError(f"--delivered would stamp now(), but the evidence {ref} is {age_min} min old: "
                         f"that records the bookkeeping time, not the live time. Use "
                         f"--delivered-at {ref} (or the row/time it actually went live).")


def _date_appears_in_text(committed_date: str, text: str) -> bool:
    """True if `committed_date` appears in `text` in a common written form.
    Every form is COMPUTED from the date itself via strftime -- never
    hard-coded (console bus #80719: a hard-coded weekday example was itself
    wrong, baking the slip into the suite would have been worse)."""
    try:
        d = dt.datetime.fromisoformat(committed_date.strip().replace("Z", "+00:00")).date()
    except ValueError:
        return True  # can't parse it ourselves -- don't block on our own parse failure
    forms = (
        d.strftime("%Y-%m-%d"),                                  # 2026-11-06
        d.strftime("%m-%d"),                                     # 11-06
        f"{d.day} {d.strftime('%b')}",                           # 6 Nov
        f"{d.strftime('%b')} {d.day}",                           # Nov 6
        f"{d.strftime('%a')} {d.day} {d.strftime('%b')}",        # Fri 6 Nov
    )
    return any(form in text for form in forms)


def validate_outbound_msg_id(cur, item_id: int, outbound_msg_id: int,
                              committed_date: str | None = None) -> None:
    """Client-side mirror of migration 132's DB trigger (console bus #80707),
    run BEFORE the write so a bad id never even attempts it, plus two more
    checks console asked for (bus #80718, corrected #80719): the message
    must postdate the ask, and (WARN, never refuse -- a date can be stated
    in a form this doesn't recognize) the committed date should visibly
    appear in it."""
    cur.execute(
        "SELECT direction, created_at, text FROM operator_messages WHERE id = %s",
        (outbound_msg_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError(
            f"--outbound-msg-id {outbound_msg_id}: no such operator_messages row -- "
            "it must be the operator_messages row that told Musa the date"
        )
    direction, msg_created_at, msg_text = row
    if direction != "outbound":
        raise ValueError(
            f"--outbound-msg-id {outbound_msg_id}: that operator_messages row is "
            f"{direction!r}, not outbound -- it must be the operator_messages row "
            "that told Musa the date"
        )
    cur.execute("SELECT created_at FROM operator_asks WHERE id = %s", (item_id,))
    ask_row = cur.fetchone()
    if ask_row is not None and msg_created_at < ask_row[0]:
        raise ValueError(
            f"--outbound-msg-id {outbound_msg_id}: that message was sent before "
            f"operator_asks #{item_id} even existed -- it cannot have committed this date"
        )
    if committed_date and not _date_appears_in_text(committed_date, msg_text):
        print(
            f"warning: --outbound-msg-id {outbound_msg_id}: the committed date "
            f"({committed_date}) does not appear in that message's text in any "
            "recognized form -- double check it actually states the date",
            file=sys.stderr,
        )


def _stamp_delivered_cur(cur, item_id: int, *, at=None, evidence_ref=None, source=None) -> int:
    if at is None:
        cur.execute("UPDATE operator_asks SET delivered_at = now() "
                    "WHERE id = %s AND delivered_at IS NULL", (item_id,))
    else:
        cur.execute("UPDATE operator_asks SET delivered_at = %s, delivered_source = %s, "
                    "  delivered_evidence_ref = COALESCE(delivered_evidence_ref, %s) "
                    "WHERE id = %s AND delivered_at IS NULL",
                    (at, source, evidence_ref, item_id))
    return cur.rowcount


def stamp_delivered(item_id: int, *, at=None, evidence_ref: str | None = None,
                    source: str | None = None, dsn: str | None = None) -> int:
    """Set operator_asks.delivered_at unless it's already set (first stamp wins): to `at`
    (with its evidence_ref/source) when given, else now(). Returns rows stamped: 1 on the
    first call, 0 on any repeat (not an error). The CLI stamps inside triage() instead."""
    dsn = dsn or _dsn()
    if not dsn:
        raise RuntimeError("no DATABASE_URL/SUPABASE_DB_URL")
    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as conn, conn.cursor() as cur:
        return _stamp_delivered_cur(cur, item_id, at=at, evidence_ref=evidence_ref, source=source)


def triage(item_id: int, action: str, *, summary: str | None = None,
           delegated_to: str | None = None, evidence: str | None = None,
           committed_date: str | None = None, outbound_msg_id: int | None = None,
           needs_musa: str | None = None, musa_summary: str | None = None,
           answers: int | None = None, dsn: str | None = None,
           triaged_by: str | None = None, deliver: dict | None = None) -> int:
    """Apply one triage action; returns the PRIMARY action's rowcount. `deliver`, when given
    ({"at": ts-or-None-for-now(), "ref": str, "source": str}), stamps delivered_at in the SAME
    transaction (first stamp wins) and records deliver["stamped"] = 0|1, so a refused stamp
    rolls the whole triage back instead of leaving a half-applied close (orch-console #79986)."""
    if action == "ask" and not (summary and summary.strip()):
        raise ValueError("--summary is required for action=ask")
    if answers is not None and answers == item_id:
        raise ValueError("--answers cannot name the row being triaged — the "
                         "answer and the ask are different rows")
    dsn = dsn or _dsn()
    if not dsn:
        raise RuntimeError("no DATABASE_URL/SUPABASE_DB_URL")
    triaged_by = triaged_by or resolve_agent_id(os.environ)

    # One transaction (commit on clean exit, rollback on any exception): the primary action,
    # the --answers close and the delivered stamp land together or not at all.
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            # --answers pre-check (bus #78269): validate the TARGET ask exists
            # (and whether it is ALREADY closed — the idempotency input for the
            # close below) and grab the answering message ref BEFORE any write
            # lands (autocommit — a post-UPDATE failure would leave the triage
            # applied but the ask open). Fail fast so a typo'd ASK_ID changes
            # nothing at all.
            ans_ref = None
            target_already_closed = False
            if answers is not None:
                cur.execute(
                    "SELECT closed_at FROM operator_asks WHERE id=%s", (answers,))
                tgt = cur.fetchone()
                if tgt is None:
                    raise ValueError(f"--answers: operator_asks #{answers} does not exist")
                target_already_closed = tgt[0] is not None
                cur.execute(
                    "SELECT source_msg_id FROM operator_asks WHERE id=%s", (item_id,))
                src = cur.fetchone()
                ans_ref = (f"answered by operator msg #{src[0]}" if src and src[0] is not None
                           else f"answered at triage of operator_asks #{item_id}")

            # migration 132 (console bus #80707/#80718/#80719): validate a
            # FRESHLY-PASSED --outbound-msg-id before any write — an id
            # already on the row was validated when it was first set.
            if outbound_msg_id is not None:
                validate_outbound_msg_id(cur, item_id, outbound_msg_id, committed_date)

            if action == "done":
                # Client-channel gate (migration 085, bus #47114 item 3a/3c): a dated
                # commitment only counts if it was actually SENT — evidence OR
                # committed_date+outbound_msg_id (this call or already on the row).
                # ask_surface='operator' rows are never gated (unchanged behavior).
                cur.execute(
                    "SELECT ask_surface, committed_date, outbound_msg_id "
                    "FROM operator_asks WHERE id=%s",
                    (item_id,),
                )
                row = cur.fetchone()
                if row is not None:
                    ask_surface, existing_committed, existing_outbound = row
                    has_commit = bool(committed_date or existing_committed)
                    has_outbound = bool(outbound_msg_id or existing_outbound)
                    if ask_surface == "client-channel" and not evidence and not (has_commit and has_outbound):
                        raise ValueError(
                            "client-channel 'done' requires --evidence, or --committed-date "
                            "together with --outbound-msg-id (this call or already on the "
                            "row) — bus #47114 item 3a/3c."
                        )
                sql = (
                    "UPDATE operator_asks SET "
                    "  triage_state = 'done', closed_at = now(), closed_reason = 'done', "
                    # console #81245: --summary was silently dropped on 'done', leaving
                    # a 'summary pending' row on the Builds page. Keep any existing line.
                    "  triage_summary = COALESCE(NULLIF(btrim(%s), ''), triage_summary), "
                    "  triage_evidence_ref = %s, triaged_at = now(), triaged_by = %s, "
                    "  committed_date = COALESCE(%s, committed_date), "
                    "  outbound_msg_id = COALESCE(%s, outbound_msg_id), "
                    # migration 118: needs-musa axis rides along on any action
                    "  needs_operator_decision = CASE WHEN %s = 'on' THEN true "
                    "WHEN %s = 'off' THEN false ELSE needs_operator_decision END, "
                    "  operator_summary = COALESCE(%s, operator_summary) "
                    "WHERE id = %s AND closed_at IS NULL"
                )
                params = (summary, evidence, triaged_by, committed_date, outbound_msg_id,
                          needs_musa, needs_musa, musa_summary, item_id)
            elif action == "ask":
                # committed_date/outbound_msg_id are optional here: a plain "in
                # progress" triage (neither given) leaves an EXISTING chase_by
                # untouched; only a real dated commitment reschedules the chase
                # net (extend, never shrink — GREATEST against any existing
                # chase_by). bus #47349: a row with chase_by NULL (a prior
                # not_an_ask insert/triage cleared it) gets a fresh default
                # 24h window restored here on re-triage back to 'ask' — same
                # default as operator_log.maybe_track_client_ask's chase_hours.
                sql = (
                    "UPDATE operator_asks SET "
                    "  triage_state = 'ask', triage_summary = %(summary)s, "
                    "  delegated_to = COALESCE(%(delegated_to)s, delegated_to), "
                    "  closed_at = NULL, closed_reason = NULL, "
                    "  triaged_at = now(), triaged_by = %(triaged_by)s, "
                    "  committed_date = COALESCE(%(cd)s::timestamptz, committed_date), "
                    "  outbound_msg_id = COALESCE(%(ob)s, outbound_msg_id), "
                    # migration 118: needs-musa axis rides along on any action
                    "  needs_operator_decision = CASE WHEN %(nm)s = 'on' THEN true "
                    "WHEN %(nm)s = 'off' THEN false ELSE needs_operator_decision END, "
                    "  operator_summary = COALESCE(%(ms)s, operator_summary), "
                    "  chase_by = CASE WHEN %(cd)s::timestamptz IS NOT NULL "
                    "                  THEN GREATEST(COALESCE(chase_by, %(cd)s::timestamptz), %(cd)s::timestamptz) "
                    "                  WHEN chase_by IS NULL THEN now() + interval '24 hours' "
                    "                  ELSE chase_by END "
                    "WHERE id = %(id)s"
                )
                params = {"summary": summary, "delegated_to": delegated_to, "triaged_by": triaged_by,
                          "cd": committed_date, "ob": outbound_msg_id, "id": item_id,
                          "nm": needs_musa, "ms": musa_summary}
                # no early return: the common tail below still needs to run the
                # --answers close (bus #78269) for action=ask too
            else:  # not
                sql = (
                    "UPDATE operator_asks SET "
                    "  triage_state = 'not_an_ask', closed_at = now(), closed_reason = 'not_a_request', "
                    "  chase_by = NULL, "
                    # console #81296: 'not' dropped --summary too. Keep any existing line.
                    "  triage_summary = COALESCE(NULLIF(btrim(%s), ''), triage_summary), "
                    "  triaged_at = now(), triaged_by = %s, "
                    # migration 118: needs-musa axis rides along on any action
                    "  needs_operator_decision = CASE WHEN %s = 'on' THEN true "
                    "WHEN %s = 'off' THEN false ELSE needs_operator_decision END, "
                    "  operator_summary = COALESCE(%s, operator_summary) "
                    "WHERE id = %s AND closed_at IS NULL"
                )
                params = (summary, triaged_by, needs_musa, needs_musa, musa_summary, item_id)

            cur.execute(sql, params)
            # The PRIMARY action's count is what the CLI exit code hangs on —
            # never the --answers close's (cc-quality 78363 finding 2: with
            # --answers, triage() used to return the close's rowcount, so an
            # already-closed target exited 2 "0 rows updated" and a primary
            # miss that still closed something exited 0 "ok").
            n = cur.rowcount

            # --answers ASK_ID (bus #78269): the row just triaged IS Musa's
            # answer to open ask ASK_ID (he replied with a fresh message, not a
            # TG reply, so the reply-link auto-close never fired). Confirm+close
            # the target with the SAME write the console swipe-to-confirm makes
            # (asks_close.py 'confirm': confirmed_at + closed_at +
            # closed_reason='operator_done'), evidence-linked to the answering
            # operator_messages row (ans_ref, resolved in the pre-check above).
            # Only when the primary triage APPLIED (n == 1) — a triage that
            # touched nothing cannot be the evidence an ask was answered.
            # Idempotent on an already-closed target (target_already_closed
            # from the pre-check): the goal state holds, skip the write, and
            # still report the primary's ok.
            if answers is not None and n == 1 and not target_already_closed:
                cur.execute(
                    "UPDATE operator_asks SET "
                    "  confirmed_at = now(), closed_at = now(), "
                    "  closed_reason = 'operator_done', triage_evidence_ref = %s, "
                    "  triaged_at = now(), triaged_by = %s "
                    "WHERE id = %s AND closed_at IS NULL",
                    (ans_ref, triaged_by, answers),
                )
            if deliver is not None:
                deliver["stamped"] = _stamp_delivered_cur(cur, item_id, at=deliver.get("at"),
                                                          evidence_ref=deliver.get("ref"),
                                                          source=deliver.get("source"))
            return n


def _precheck_delivery(args):
    """Validate --delivered/--delivered-at BEFORE triage() writes anything (it autocommits,
    so a refusal after it would leave a half-applied close). Returns (at, ref, source)."""
    if not (args.delivered or args.delivered_at):
        return None, None, None
    dsn = _dsn()
    if not dsn:
        raise RuntimeError("no DATABASE_URL/SUPABASE_DB_URL")
    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as conn, conn.cursor() as cur:
        check_summary_before_delivery(cur, args.id, summary=args.summary,
                                       musa_summary=args.musa_summary)
        if args.delivered_at:
            return resolve_delivered_at(cur, args.id, args.delivered_at)
        check_bare_delivered(cur, args.id, args.evidence)
        return None, None, None


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        d_at, d_ref, d_src = _precheck_delivery(args)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"error: {type(e).__name__}: {e}\n(nothing written)\n")
        return 2
    deliver = ({"at": d_at, "ref": d_ref, "source": d_src}
               if (args.delivered or args.delivered_at) else None)
    try:
        n = triage(args.id, args.action, summary=args.summary,
                   delegated_to=args.delegated_to, evidence=args.evidence,
                   committed_date=args.committed_date, outbound_msg_id=args.outbound_msg_id,
                   needs_musa=args.needs_musa, musa_summary=args.musa_summary,
                   answers=args.answers, triaged_by=args.triaged_by, deliver=deliver)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"error: {type(e).__name__}: {e}\n(nothing written: rolled back)\n")
        return 2
    if deliver is not None:
        print("delivered_at stamped" if deliver.get("stamped") else "delivered_at already set (first stamp kept)")
    if n == 1:
        if args.answers is not None:
            # True on both close paths — just-now confirmed or already closed
            # (idempotent re-run): the ask is closed and this row answers it.
            print(f"ok (ask #{args.answers} closed — answered by this message)")
        else:
            print("ok")
        return 0
    sys.stderr.write(f"error: {n} rows updated (id {args.id} not found or not eligible?)\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
