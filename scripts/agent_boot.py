#!/usr/bin/env python3
"""Read the agent's working memory, inbox, and pending governance.

Run this at the start of every CC session — it prints a compact briefing
of everything the agent needs to know:

  1. agent_context (working memory: active decisions, blockers, repo health)
  2. agent_messages (inbox: unread, requires_response first)
  3. strategic_decisions (governance: rows in challenge_window scoped to my repos)

After printing, the script sets agents.status='active' and bumps
last_heartbeat. Pass --no-heartbeat to skip that side-effect.

Usage:
  python -m scripts.agent_boot --agent cc-ihsanos
  python -m scripts.agent_boot --agent cai

Environment:
  SUPABASE_URL, SUPABASE_SERVICE_KEY (loaded from .env)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Agent ids are a controlled vocabulary (e.g. cc-orchestrator, cc-reviewer-13).
# _inbox_or_filter interpolates the id into a PostgREST `or=` string whose
# structure uses commas/parens/dots — so reject anything outside the known-safe
# charset rather than let a stray comma/paren/dot corrupt the filter (CAI-RESP-296
# A2). Dot is excluded deliberately: it's PostgREST's operator separator (eq./is.).
_SAFE_AGENT_ID = re.compile(r"^[A-Za-z0-9_-]+$")

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


def _client():
    from supabase import create_client

    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _print_section(title: str, char: str = "─") -> None:
    print()
    print(char * 70)
    print(f" {title}")
    print(char * 70)


def _inbox_or_filter(agent_id: str) -> str:
    """PostgREST `or=` filter for the open-inbox view (CAI-RESP-296).

    Surfaces, for this agent OR broadcast (to_agent IS NULL), messages that are
    EITHER unread (read_at IS NULL) OR read-but-unresponded requires_response
    (requires_response=true AND responded_at IS NULL). Written in DNF (a flat
    OR of and()-groups) so it's a single unambiguous `or=` param — postgrest-py
    does not reliably AND two separate .or_() calls.

    agent_id is interpolated raw into the filter, so validate it against the
    known-safe charset (CAI-RESP-296 A2): a comma/paren/dot in the id would
    corrupt the filter structure. Fail closed rather than build a broken filter.
    """
    if not _SAFE_AGENT_ID.match(agent_id):
        raise ValueError(f"unsafe agent_id for PostgREST filter: {agent_id!r}")
    return (
        f"and(to_agent.eq.{agent_id},read_at.is.null),"
        f"and(to_agent.eq.{agent_id},requires_response.eq.true,responded_at.is.null),"
        f"and(to_agent.is.null,read_at.is.null),"
        f"and(to_agent.is.null,requires_response.eq.true,responded_at.is.null)"
    )


def mark_responded(message_id: int, dsn: str | None = None) -> bool:
    """Close the loop on a requires_response item (CAI-RESP-296 part b).

    Sets agent_messages.responded_at=now() for `message_id`, but ONLY if it is
    still unresponded — idempotent, never overwrites an existing responded_at.
    A genuine in-session reply MUST call this (or set responded_at directly);
    the sweep/wrapper MUST NOT (no fabricated responses — Section D). Returns
    True if this call set responded_at, False if it was already set / no-op.

    Mirrors the read_at discipline: closure is an explicit in-session action,
    not an automatic wrapper side-effect. Uses psycopg (the nervous-system
    write path) so it is exercised by the same connection the sweep uses.
    """
    import psycopg

    dsn = dsn or os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    with psycopg.connect(dsn, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "UPDATE agent_messages SET responded_at=now() "
            "WHERE id=%s AND responded_at IS NULL",
            (message_id,),
        )
        return cur.rowcount > 0


def boot(agent_id: str, mark_active: bool = True) -> int:
    client = _client()

    # 0. Verify the agent exists
    me = client.table("agents").select("*").eq("id", agent_id).execute().data
    if not me:
        print(f"ERROR: no agent with id={agent_id} — seed it first")
        return 1
    me = me[0]

    print(f"agent_boot for {agent_id} ({me['display_name']})")
    print(f"  status={me['status']}  scope={me['repo_scope']}  last_heartbeat={me.get('last_heartbeat')}")

    # 1. Working memory
    _print_section("AGENT CONTEXT (working memory)")
    ctx = (
        client.table("agent_context").select("*").eq("agent_id", agent_id).execute().data
    )
    if ctx:
        c = ctx[0]
        print(f"  active_decision_refs : {c.get('active_decision_refs')}")
        print(f"  pending_review_refs  : {c.get('pending_review_refs')}")
        print(f"  current_blockers     : {c.get('current_blockers')}")
        print(f"  session_notes        : {c.get('session_notes')}")
        repo_health = c.get("repo_health") or {}
        if repo_health:
            print("  repo_health          :")
            for k, v in repo_health.items():
                print(f"      {k}: {v}")
    else:
        print("  (no context row — first boot)")

    # 2. Inbox — UNREAD or read-but-UNRESPONDED(requires_response); requires_response first.
    # CAI-RESP-296: the prior query keyed on read_at IS NULL alone, so a
    # requires_response item that was READ but never ANSWERED dropped out of
    # the boot view permanently (the CAI-RESP-274 bug). Now read-but-unanswered
    # asks resurface until genuinely answered (responded_at set on a real reply).
    inbox = (
        client.table("agent_messages")
        .select("*")
        .or_(_inbox_or_filter(agent_id))
        .order("requires_response", desc=True)
        .order("created_at", desc=False)
        .execute()
        .data
    )
    _print_section("INBOX (unread + unresponded agent_messages)")
    if not inbox:
        print("  (empty)")
    else:
        n_unresp = sum(
            1 for m in inbox if m.get("requires_response") and not m.get("responded_at")
        )
        print(f"  {len(inbox)} open message(s) ({n_unresp} unresponded requires_response):")
        for m in inbox:
            # 🔴 = owed a reply (requires_response + not yet responded); 📭 = unread-only
            if m.get("requires_response") and not m.get("responded_at"):
                marker = "🔴"
            elif not m.get("read_at"):
                marker = "📭"
            else:
                marker = "  "
            to = m.get("to_agent") or "broadcast"
            subject = m.get("subject", "")
            print(
                f"  {marker} #{m['id']} [{m['message_type']}] {m['from_agent']}→{to}: {subject}"
            )

    # 3. Governance items in challenge_window scoped to my repos
    _print_section("GOVERNANCE (strategic_decisions challenge_window)")
    gov = (
        client.table("strategic_decisions")
        .select("decision_ref,title,domain,repos_affected,decided_at")
        .eq("challenge_status", "challenge_window")
        .order("decided_at", desc=True)
        .limit(20)
        .execute()
        .data
    )
    in_scope = [
        g
        for g in gov
        if not g.get("repos_affected")
        or "*" in (me.get("repo_scope") or [])
        or any(r in (me.get("repo_scope") or []) for r in g.get("repos_affected") or [])
    ]
    if not in_scope:
        print("  (no in-scope challenge_window items)")
    else:
        for g in in_scope[:10]:
            print(f"  • {g['decision_ref']} ({g.get('domain')}): {g['title']}")

    # 4. Heartbeat
    if mark_active:
        ts = datetime.now(timezone.utc).isoformat()
        client.table("agents").update(
            {"status": "active", "last_heartbeat": ts}
        ).eq("id", agent_id).execute()
        print()
        print(f"agents.{agent_id}: status=active, last_heartbeat={ts}")

    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", help="agent_id (cai / cc-ihsanos / cc-web / cc-scholar)")
    parser.add_argument("--no-heartbeat", action="store_true", help="Skip the active+heartbeat update")
    parser.add_argument("--json", action="store_true", help="Output the briefing as JSON instead of human text")
    parser.add_argument(
        "--mark-responded",
        type=int,
        metavar="MSG_ID",
        help="CAI-RESP-296: close the loop on a requires_response item after a "
        "genuine in-session reply (sets responded_at; idempotent). Use only "
        "when you actually answered it.",
    )
    args = parser.parse_args(argv)

    # CAI-RESP-296 loop-closure subcommand — independent of --agent.
    if args.mark_responded is not None:
        did = mark_responded(args.mark_responded)
        print(
            f"agent_messages #{args.mark_responded}: "
            + ("responded_at set (loop closed)" if did else "already responded / not found (no-op)")
        )
        return 0

    if not args.agent:
        parser.error("--agent is required (unless --mark-responded is given)")

    if args.json:
        # JSON mode — used by automation to consume the boot output.
        client = _client()
        me = client.table("agents").select("*").eq("id", args.agent).execute().data
        ctx = (
            client.table("agent_context").select("*").eq("agent_id", args.agent).execute().data
        )
        # CAI-RESP-296: unread OR read-but-unresponded requires_response.
        inbox = (
            client.table("agent_messages")
            .select("*")
            .or_(_inbox_or_filter(args.agent))
            .order("requires_response", desc=True)
            .order("created_at", desc=False)
            .execute()
            .data
        )
        out = {
            "agent": me[0] if me else None,
            "context": ctx[0] if ctx else None,
            # Key kept as `unread_inbox` for backward-compat with consumers;
            # now also includes read-but-unresponded requires_response items.
            "unread_inbox": inbox,
            "open_inbox": inbox,
        }
        if not args.no_heartbeat and me:
            ts = datetime.now(timezone.utc).isoformat()
            client.table("agents").update(
                {"status": "active", "last_heartbeat": ts}
            ).eq("id", args.agent).execute()
            out["heartbeat"] = ts
        print(json.dumps(out, indent=2, default=str))
        return 0

    return boot(args.agent, mark_active=not args.no_heartbeat)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
