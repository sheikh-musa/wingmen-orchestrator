#!/usr/bin/env python3
"""bus_send.py — the ONE sanctioned way to post an agent_messages bus row.

Why this exists (orch-console bus #43651, op#22517 aftermath): every prior
"bus helper" was a per-agent scratch file recreated ad hoc (e.g.
~/wingmen/orchestrator/_bus_tmp.py, scratchpad/bus_send.py) — untracked,
uncovered by tests, and each one lets --priority default or be omitted. On
2026-09-27 (cc-substrate) a hand-written INSERT for a hub action-now ask
omitted the priority column; it silently landed at the table default 'P2',
which is BELOW the hub's P0/P1+requires_response wake floor (CAI-451/786,
memory reference_hub_wake_floor_p1_rr), so the hub never woke on it. A
promise to "remember next time" doesn't survive a context reset — this
script makes --priority a REQUIRED argparse argument so the mistake is no
longer possible to make silently.

Follow-up (bus #43673): the untracked scratch helpers (_bus_tmp.py,
scratchpad/bus_send.py) are now thin shims over send() below, so there is
exactly one INSERT INTO agent_messages in the fleet's ad-hoc-send path.

Follow-up (op#22669): send() used to ALSO write an operator_asks link row
(migration 044) for every fresh console-to-body ask (from_agent='orch-console',
type='decision', --req, no --thread/--reply-to). REVERTED (Musa op#23554, bus
#46353, 2026-09-30): that heuristic couldn't tell a genuine Musa ask from a
pure fleet delegation, and a delegation is NOT an ask OF Musa — it phantom-
appeared on his "Your asks" board (ids 378/381: "OEH: cc-quality review...",
"Musa picked V2 option B..."), and broadcasting the same decision to several
bodies duplicated identically (ids 210/211/212). A delegation is now NEVER a
new ask; --link-ask lets a caller LINK this bus row to an operator_asks row
that already exists because it traces back to something Musa actually said
(migration 084's ask_surface + source_msg_id/waiting_on_operator scope).

Usage:
    scripts/bus_send.py --to cc-orchestrator --type update \\
        --subject "short subject" --priority P1 [--req] \\
        [--thread <uuid-or-prefix>] [--reply-to <id>] [--link-ask <id>] \\
        [--from <agent_id>] [--dry-run] <<'EOF'
    body text goes on stdin (>=40 bytes — the empty-body guard; Nazim shipped
    three blank bus rows on 2026-09-05 by forgetting the heredoc)
    EOF

Identity: --from is resolved automatically if omitted, in this order:
    1. CC_BASE_AGENT_ID   (interactive fleet lane / this Claude Code session)
    2. AGENT_ID           (daemon/watchdog launchers)
    3. ORCH_AGENT_ID, but ONLY when ORCH_BODY_ROLE=console (the one
       documented case where the console's own identity is the right
       fallback — see reference_agent_id_not_orch_agent_id_for_identity;
       ORCH_AGENT_ID is otherwise fleet-wide-exported .env noise and must
       NEVER be trusted blindly)
    Refuses (does not guess) if none of these resolve.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.agent_identity import IdentityError, resolve_agent_id  # noqa: E402

_VALID_PRIORITIES = ("P0", "P1", "P2", "P3")
_VALID_TYPES = (
    "review_request", "question", "decision", "agreed",
    "challenge", "update", "blocker", "counter",
)
_MIN_BODY_BYTES = 40
_HUB_AGENT = "cc-orchestrator"

# Real, intentional send targets that are legitimately NEVER wake-eligible by
# is_wake_eligible_recipient's definition (operator / human-opened addresses) --
# never "undeliverable" below, despite having no live wake owner. Mirrors the
# human/musa half of scripts/fleet_health.py's _HUMAN_OPENED / _NEVER_ARCHIVE_ADDRS
# (that set also has "operator" and "substrate", omitted here: "operator" has zero
# rows ever sent to it, and "substrate" is already rejected at the DB layer by
# agent_messages_reject_pseudo_targets regardless of this check -- cc-quality
# #50975 verified both empirically). Small, stable sets kept in sync by hand;
# worth a shared module if either grows.
_HUMAN_OPENED = frozenset({"cto-desktop"})
_UNDELIVERABLE_EXEMPT = frozenset({"musa"}) | _HUMAN_OPENED


_LIVE_INSTANCE_WINDOW = "30 minutes"  # matches scripts/irsyad_autoscaler.py LIVE_HEARTBEAT_WINDOW


def live_instance_ids(base_to: str, dsn: str | None = None) -> list[str]:
    """bus #49220: agent_status rows whose base_agent_id matches `base_to` but
    whose own agent_id differs (a real NN-suffixed instance, e.g. cc-irsyad-1
    for base cc-irsyad -- not the base address reporting as its own row,
    which singletons/solo coords do), seen live in the last 30 minutes.
    Returns [] for a base id with no fanned-out instances (singletons, solo
    coords) -- that case is a no-op by construction, not a special case."""
    import psycopg2

    conn = psycopg2.connect(dsn or dburl(os.environ))
    cur = conn.cursor()
    cur.execute(
        "SELECT agent_id FROM agent_status "
        "WHERE base_agent_id=%s AND agent_id<>%s "
        "AND status IN ('idle','working') "
        f"AND last_heartbeat > now() - interval '{_LIVE_INSTANCE_WINDOW}' "
        "ORDER BY agent_id",
        (base_to, base_to),
    )
    instances = [r[0] for r in cur.fetchall()]
    conn.close()
    return instances


def _heartbeat_age_str(agent_id: str, dsn: str | None = None) -> str:
    """bus #49675: a suggested instance can go stale between query and read --
    stamp the suggestion with the heartbeat age the DB measured at refusal time
    (server-side now()-last_heartbeat, not a client clock) so a future "it
    suggested a dead instance" report carries its own evidence instead of
    requiring a live-re-query postmortem days later."""
    import psycopg2

    try:
        conn = psycopg2.connect(dsn or dburl(os.environ))
        cur = conn.cursor()
        cur.execute(
            "SELECT status, now() - last_heartbeat FROM agent_status WHERE agent_id=%s",
            (agent_id,),
        )
        row = cur.fetchone()
        conn.close()
        if row is None:
            return "no agent_status row"
        status, age = row
        return f"status={status}, heartbeat {age} ago"
    except (Exception, SystemExit) as exc:  # noqa: BLE001 -- diagnostic text only, never block the refusal
        return f"heartbeat age unavailable ({exc})"


def refuse_if_base_has_live_instances(to: str, to_base: bool, dsn: str | None = None) -> None:
    """Refuse (not just warn) a send to a BASE id when live instance(s) exist,
    unless the caller explicitly opts in via --to-base. Builder lanes poll
    their own instance address, not the base -- orch-console lost 5 rows to
    this on 2026-10-01 even with a memory rule in place (a promise doesn't
    survive a context reset, same lesson as the --priority fix above)."""
    if to_base:
        return
    instances = live_instance_ids(to, dsn=dsn)
    if not instances:
        return
    age = _heartbeat_age_str(instances[0], dsn=dsn)
    raise SystemExit(
        f"bus_send: REFUSED — '{to}' is a BASE id with live instance(s) "
        f"({', '.join(instances)}); they poll their own instance address, not "
        f"the base, so this row would sit unread. Did you mean --to {instances[0]} "
        f"[{age}]? Pass --to-base to send to the base address anyway."
    )


def _agent_status_row(agent_id: str, dsn: str | None = None) -> tuple | None:
    """(base_agent_id, is_live) for one agent_status row, or None if it has none.
    `is_live` uses the same floor as live_instance_ids: status IN ('idle','working')
    and a heartbeat inside _LIVE_INSTANCE_WINDOW."""
    import psycopg2

    conn = psycopg2.connect(dsn or dburl(os.environ))
    cur = conn.cursor()
    cur.execute(
        "SELECT base_agent_id, "
        f"(status IN ('idle','working') AND last_heartbeat > now() - interval '{_LIVE_INSTANCE_WINDOW}') "
        "FROM agent_status WHERE agent_id=%s",
        (agent_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def refuse_if_instance_stale_and_base_live(to: str, dsn: str | None = None) -> None:
    """Mirror of refuse_if_base_has_live_instances, inverted (bus #58614 item 1).
    Motivating case: bus row #58568 (cc-irsyad-3 -> cc-quality-1, an urgent review
    request) was stranded because cc-quality-1 had gone stale while its base
    cc-quality was the live singleton actually polling -- a numbered instance id
    can retire (per the ORCH-TOPOLOGY-001/op#27235 relaunch pattern) without the
    sender knowing. Refuses and points at the live base instead of silently
    forwarding -- same shape as the base-has-live-instances guard, so a sender
    always gets an explicit, correctable address rather than a guess.

    Cheap pre-check: only a to_agent shaped like a numbered instance (ends in
    -<digits>) can possibly be this case -- skips the DB round-trip for every
    other address, including write-only producers (commitment-sweeper, ...)
    that refuse_if_undeliverable already handles with zero DB calls."""
    if not re.search(r"-\d+$", to or ""):
        return
    row = _agent_status_row(to, dsn=dsn)
    if row is None:
        return  # no status row at all -- not this guard's concern
    base_agent_id, to_is_live = row
    if not base_agent_id or base_agent_id == to or to_is_live:
        return  # `to` IS the base, or the instance itself is live -- nothing stranded
    base_row = _agent_status_row(base_agent_id, dsn=dsn)
    if base_row is None or not base_row[1]:
        return  # the base isn't live either -- no live address to redirect to
    age = _heartbeat_age_str(to, dsn=dsn)
    raise SystemExit(
        f"bus_send: REFUSED — '{to}' is a retired/stale instance [{age}]; its base "
        f"'{base_agent_id}' is the live singleton now. Use --to {base_agent_id} instead."
    )


def _undeliverable(to_agent: str | None) -> bool:
    """A to_agent with NO possible live wake owner -- same SSOT definition as
    scripts/fleet_health.py's _undeliverable: not eligible on agent_wake's most
    permissive floor (P0 + requires_response), so a narrow-floor recipient (the
    hub) is never misread as dead. Exempt addresses (operator / human-opened)
    are real, intentional targets that are just never wake-eligible by design,
    not a misroute -- never flagged here."""
    if not to_agent or to_agent in _UNDELIVERABLE_EXEMPT:
        return False
    from nervous_system.agent_wake import is_wake_eligible_recipient

    return not is_wake_eligible_recipient(to_agent, "P0", True)


def refuse_if_undeliverable(
    to: str, reply_to: int | None = None, allow_undeliverable: bool = False, stream=None,
) -> None:
    """Refuse a send to a structurally-undeliverable to_agent -- a write-only
    producer with no live wake owner (commitment-sweeper, programme-stall-guard,
    sla-watchdog, ...). Four real dead-letters (bus #50648/#50649/#50689/#50697)
    were a previous console body "replying" to exactly this class of address --
    a standing rule against it doesn't survive a context reset (orch-console
    #50969), so this makes it structural.

    This refuses regardless of --reply-to -- replying to a row FROM a write-only
    producer must not create a new row TO it either; the correct action is to
    STAMP the original row (a plain UPDATE, which --reply-to already does as a
    side effect of a normal send), never to send anything. When reply_to is
    known, the refusal message points at that exact row to close instead of a
    generic one.

    --allow-undeliverable is the explicit, logged escape hatch for a genuine
    deliberate one-off: downgrades this to a warning and the send proceeds."""
    if not _undeliverable(to):
        return
    close_id = str(reply_to) if reply_to else "<its row id>"
    message = (
        f"'{to}' has no wake owner (write-only producer?). To close one of its "
        f"fire rows: UPDATE agent_messages SET read_at=now(), responded_at=now(), "
        f"response_ref='...' WHERE id={close_id}."
    )
    if allow_undeliverable:
        print(f"bus_send: WARNING — {message} (--allow-undeliverable set, sending anyway)",
              file=stream or sys.stderr)
        return
    raise SystemExit(f"bus_send: REFUSED — {message}")


def warn_if_below_hub_wake_floor(to: str, req: bool, priority: str, stream=None) -> None:
    """bus #44527: --to cc-orchestrator --req at a priority below P1 will NOT wake the
    hub (floor = P0/P1 AND requires_response — reference_hub_wake_floor_p1_rr). This is
    a WARNING, not a refusal: P2-rr is a legitimate way to post a non-urgent item that
    still wants a response eventually."""
    if to == _HUB_AGENT and req and priority not in ("P0", "P1"):
        print(
            f"bus_send: WARNING — --to {_HUB_AGENT} --req at priority {priority} will NOT "
            "wake the hub (floor = P0/P1 + requires_response); use --priority P1 if it must act.",
            file=stream or sys.stderr,
        )


_DATA_SECURITY_KEYWORDS = (
    "pii", "real data", "real client data", "real trainee", "gov-pii",
    "data leak", "data breach", "data exposure", "exposed data",
    "data security", "sensitive data",
)
_PROVENANCE_CITATION_TOKENS = ("data_truth", "data_provenance", "classification:")


def warn_if_missing_provenance_citation(priority: str, subject: str, body: str, stream=None) -> None:
    """op#25626 / bus #51657/#51717 (orch-console gate condition #4, v1 = warn):
    a P0/P1 message that raises a data-security claim must cite a data_truth.py
    classification — the fleet already raised a false data-security P0 over
    generated test data once. WARNING, not a refusal (mirrors
    warn_if_below_hub_wake_floor's shape): a heuristic keyword match can
    false-positive, and a blocked urgent safety message is worse than a missed
    reminder. After a week in production, report firing/false-positive rate to
    orch-console, who will decide whether to harden this to a refusal."""
    if priority not in ("P0", "P1"):
        return
    text = f"{subject}\n{body}".lower()
    if not any(k in text for k in _DATA_SECURITY_KEYWORDS):
        return
    if any(t in text for t in _PROVENANCE_CITATION_TOKENS):
        return
    print(
        "bus_send: WARNING — this message raises a data-security concern at "
        f"priority {priority} without citing a classification. Run "
        "`scripts/data_truth.py classify <project_ref> [org_id]` first and cite "
        "the result (see docs/DATA-PROVENANCE.md) — a slug/name is NOT evidence.",
        file=stream or sys.stderr,
    )


def warn_if_weekday_date_mismatch(subject: str, body: str, stream=None) -> None:
    """2026-10-07 (orch-console bus #57970): flag a weekday paired with the wrong date
    ("Thursday 9 October" when 9 Oct 2026 is a Friday). The send scripts REFUSE on this;
    the bus only WARNS — never blocks, never raises (a guard bug must not stop a bus post)."""
    out = stream or sys.stderr
    try:
        from scripts.lib import date_weekday_guard as _dwg
        for f in _dwg.find_mismatches(f"{subject}\n{body}"):
            print(f"bus_send: WARNING — weekday/date mismatch: {f.message()}", file=out)
    except Exception as e:  # noqa: BLE001 — warn-only path, must never block a bus send
        print(f"bus_send: WARNING — weekday/date guard could not run ({type(e).__name__}); "
              "message not checked.", file=out)


# Re-exported for callers/tests that reach for bus_send.resolve_from_agent /
# bus_send.IdentityError directly — the real logic now lives in
# scripts/lib/agent_identity.py (bus #47221) so asks_triage.py, asks_open.py,
# etc. can reuse the same fail-closed resolver instead of re-deriving it.
resolve_from_agent = resolve_agent_id


def dburl(env: dict) -> str:
    # The .env FILE wins over the inherited environment: a long-running session
    # keeps the DATABASE_URL it was launched with, so after a password rotation
    # every bus post from it failed auth and kept the pooler circuit breaker
    # tripped fleet-wide (2026-09-28 rotation incident). The file is the
    # rotation's single push-point (Mini .env; gzb .env -> /dev/shm secrets).
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip().strip('"')
    except FileNotFoundError:
        pass
    v = env.get("DATABASE_URL")
    if v:
        return v
    raise SystemExit("no DATABASE_URL: set it in the environment or .env")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--to", required=True, help="to_agent")
    p.add_argument("--type", required=True, choices=_VALID_TYPES, help="message_type")
    p.add_argument("--subject", required=True)
    p.add_argument(
        "--priority", required=True, choices=_VALID_PRIORITIES,
        help="REQUIRED, no default. Hub action-now asks MUST use P1 (or P0) "
             "with --req — see reference_hub_wake_floor_p1_rr.",
    )
    p.add_argument("--req", action="store_true", help="requires_response=true")
    p.add_argument("--thread", default=None, help="uuid or prefix of an existing thread")
    p.add_argument("--reply-to", type=int, default=None, help="id of the row being answered")
    p.add_argument(
        "--link-ask", type=int, default=None, dest="link_ask",
        help="operator_asks.id to LINK this row to — never creates a new ask "
             "(Musa op#23554, bus #46353)",
    )
    p.add_argument("--from", dest="from_agent", default=None, help="override identity (default: auto-resolve)")
    p.add_argument("--dry-run", action="store_true", help="resolve+validate, print the row, do not touch the DB")
    p.add_argument(
        "--to-base", action="store_true",
        help="send to --to literally even if it's a BASE id with live instance(s) "
             "(bus #49220) -- normally refused with the live instance address instead",
    )
    p.add_argument(
        "--allow-undeliverable", action="store_true", dest="allow_undeliverable",
        help="send anyway to a structurally-undeliverable --to (write-only producer, "
             "no live wake owner) -- logs a warning instead of refusing; for a genuine "
             "deliberate one-off, never the default",
    )
    return p


def read_body(stream) -> str:
    body = stream.read()
    if len(body) < _MIN_BODY_BYTES:
        raise SystemExit(
            f"bus_send: REFUSED — body is {len(body)} bytes (<{_MIN_BODY_BYTES}). "
            "Did you forget the heredoc?"
        )
    return body


def send(
    from_agent: str, to: str, mtype: str, subject: str, body: str, priority: str,
    req: bool = False, thread: str | None = None, reply_to: int | None = None,
    link_ask: int | None = None, dsn: str | None = None, allow_undeliverable: bool = False,
) -> tuple[int, str]:
    """Do the actual INSERT. The one place the SQL lives — CLI (`main`) and
    every shim (`_bus_tmp.py`, `scratchpad/bus_send.py`) call this so there is
    exactly one INSERT INTO agent_messages in the whole fleet's ad-hoc-send
    path. `priority` has no default here either — callers must pass it."""
    if priority not in _VALID_PRIORITIES:
        raise ValueError(f"send(): priority must be one of {_VALID_PRIORITIES}, got {priority!r}")
    refuse_if_undeliverable(to, reply_to=reply_to, allow_undeliverable=allow_undeliverable)

    import psycopg2

    conn = psycopg2.connect(dsn or dburl(os.environ))
    cur = conn.cursor()
    cur.execute("SELECT set_config('app.current_agent_id', %s, true)", (from_agent,))

    if reply_to and not thread:
        cur.execute("SELECT thread_id FROM agent_messages WHERE id=%s", (reply_to,))
        row = cur.fetchone()
        thread = str(row[0]) if row else None
    if thread and len(thread) < 36:
        cur.execute(
            "SELECT thread_id FROM agent_messages WHERE thread_id::text LIKE %s "
            "ORDER BY created_at DESC LIMIT 1",
            (thread + "%",),
        )
        row = cur.fetchone()
        if not row:
            raise SystemExit(f"no thread matches prefix {thread}")
        thread = str(row[0])
    if not thread:
        thread = str(uuid.uuid4())

    cur.execute(
        """INSERT INTO agent_messages
             (from_agent, to_agent, message_type, subject, body,
              priority, requires_response, thread_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING id, thread_id""",
        (from_agent, to, mtype, subject, body, priority, req, thread),
    )
    row_id, thread_id = cur.fetchone()

    if link_ask is not None:
        # Musa op#23554 (bus #46353): a delegation is NEVER a new ask — at most
        # it LINKS an existing operator_asks row (one already traceable to a
        # real Musa inbound) to this bus thread, so the "Your asks" board
        # tracks the delegate's live progress. Fails LOUD on a bad/closed id —
        # a caller typo should never silently no-op.
        cur.execute(
            "UPDATE operator_asks SET thread_id=%s, delegated_to=%s "
            "WHERE id=%s AND closed_at IS NULL",
            (thread_id, to, link_ask),
        )
        if cur.rowcount == 0:
            conn.rollback()
            conn.close()
            raise SystemExit(
                f"bus_send: --link-ask {link_ask} does not exist or is already closed"
            )

    if reply_to:
        cur.execute(
            "UPDATE agent_messages SET read_at=coalesce(read_at,now()), "
            "responded_at=coalesce(responded_at,now()), "
            "response_ref=coalesce(response_ref,%s) WHERE id=%s",
            (str(row_id), reply_to),
        )

    conn.commit()
    conn.close()
    return row_id, str(thread_id)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        from_agent = args.from_agent or resolve_from_agent(os.environ)
    except IdentityError as e:
        print(f"bus_send: {e}", file=sys.stderr)
        return 2

    body = read_body(sys.stdin)

    warn_if_below_hub_wake_floor(args.to, args.req, args.priority)
    warn_if_missing_provenance_citation(args.priority, args.subject, body)
    warn_if_weekday_date_mismatch(args.subject, body)

    if args.dry_run:
        print(f"DRY RUN — would insert: from={from_agent} to={args.to} type={args.type} "
              f"priority={args.priority} req={args.req} subject={args.subject!r} "
              f"body_bytes={len(body)}")
        return 0

    refuse_if_base_has_live_instances(args.to, args.to_base)
    refuse_if_instance_stale_and_base_live(args.to)

    row_id, thread_id = send(
        from_agent, args.to, args.type, args.subject, body, args.priority,
        req=args.req, thread=args.thread, reply_to=args.reply_to,
        link_ask=args.link_ask, allow_undeliverable=args.allow_undeliverable,
    )
    print(f"SENT id={row_id} thread={thread_id} from={from_agent} to={args.to} "
          f"priority={args.priority} req={args.req}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
