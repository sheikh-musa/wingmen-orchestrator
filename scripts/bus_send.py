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

Follow-up (op#22669): send() ALSO writes the operator_asks link row (migration
044) when the call is a fresh console-to-body ask (from_agent='orch-console',
type='decision', --req, no --thread/--reply-to) — the same link
console_assign.py's HTTP path writes. This closes a 41-day gap
(2026-08-17 -> 2026-09-27) where this script became the dominant console-assign
path but never wrote that half, so operator_asks silently stopped growing.

Usage:
    scripts/bus_send.py --to cc-orchestrator --type update \\
        --subject "short subject" --priority P1 [--req] \\
        [--thread <uuid-or-prefix>] [--reply-to <id>] [--from <agent_id>] \\
        [--dry-run] <<'EOF'
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
import sys
import uuid

_VALID_PRIORITIES = ("P0", "P1", "P2", "P3")
_VALID_TYPES = (
    "review_request", "question", "decision", "agreed",
    "challenge", "update", "blocker", "counter",
)
_MIN_BODY_BYTES = 40
_HUB_AGENT = "cc-orchestrator"


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


class IdentityError(RuntimeError):
    pass


def resolve_from_agent(env: dict) -> str:
    """Resolve "who am I" for bus attribution. Fail closed, never guess."""
    v = env.get("CC_BASE_AGENT_ID")
    if v:
        return v
    v = env.get("AGENT_ID")
    if v:
        return v
    if env.get("ORCH_BODY_ROLE") == "console":
        v = env.get("ORCH_AGENT_ID")
        if v:
            return v
    raise IdentityError(
        "cannot resolve bus from_agent identity: set CC_BASE_AGENT_ID or "
        "AGENT_ID in the environment, or pass --from explicitly. Refusing "
        "to guess (see reference_agent_id_not_orch_agent_id_for_identity)."
    )


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
    p.add_argument("--from", dest="from_agent", default=None, help="override identity (default: auto-resolve)")
    p.add_argument("--dry-run", action="store_true", help="resolve+validate, print the row, do not touch the DB")
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
    dsn: str | None = None,
) -> tuple[int, str]:
    """Do the actual INSERT. The one place the SQL lives — CLI (`main`) and
    every shim (`_bus_tmp.py`, `scratchpad/bus_send.py`) call this so there is
    exactly one INSERT INTO agent_messages in the whole fleet's ad-hoc-send
    path. `priority` has no default here either — callers must pass it."""
    if priority not in _VALID_PRIORITIES:
        raise ValueError(f"send(): priority must be one of {_VALID_PRIORITIES}, got {priority!r}")

    # op#22669 root-cause fix (operator-asks-tracking build, bus thread
    # d0533248-2d25-484f-84ad-3cdd08fe1fce): scripts/console_assign.py used to be
    # the ONLY writer of both an agent_messages bus row AND its operator_asks
    # link row, in one transaction (see console_assign.assign()). This script
    # (bus #43651/#43673) has since become the fleet's actual day-to-day
    # console-assign path — orch-console posts its directives via bus_send.py,
    # not console_assign.py's HTTP endpoint — but never wrote the operator_asks
    # half. Confirmed live (2026-09-27): agent_messages kept filling with
    # from_agent='orch-console', message_type='decision', thread_id NOT NULL rows
    # at 20-250/day every single day from July through today, while
    # operator_asks received ZERO writes for 41 days (2026-08-17 -> 2026-09-27,
    # until a manual backfill). Not a console_assign.py defect — a gap in the
    # newer, now-dominant write path.
    #
    # is_new_ask mirrors console_assign.assign()'s exact shape: a FRESH top-level
    # ask from the console to a body (a brand-new thread, not a reply riding an
    # existing one). Computed from the ORIGINAL args — `thread` is reassigned
    # below when resolving/generating the real thread_id, so this must be
    # evaluated before that happens.
    is_new_ask = (
        from_agent == "orch-console" and mtype == "decision" and req
        and not reply_to and not thread
    )

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

    if is_new_ask:
        # SAME-TRANSACTION link row (migration 044), same shape console_assign.py
        # writes: status is NEVER stored here — the "Your asks" board derives it
        # live from the agent_messages thread just inserted above.
        cur.execute(
            "INSERT INTO operator_asks (ask, thread_id, delegated_to) VALUES (%s, %s, %s)",
            (subject, thread_id, to),
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

    if args.dry_run:
        print(f"DRY RUN — would insert: from={from_agent} to={args.to} type={args.type} "
              f"priority={args.priority} req={args.req} subject={args.subject!r} "
              f"body_bytes={len(body)}")
        return 0

    row_id, thread_id = send(
        from_agent, args.to, args.type, args.subject, body, args.priority,
        req=args.req, thread=args.thread, reply_to=args.reply_to,
    )
    print(f"SENT id={row_id} thread={thread_id} from={from_agent} to={args.to} "
          f"priority={args.priority} req={args.req}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
