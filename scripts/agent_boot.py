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
import sys
from datetime import datetime, timezone
from pathlib import Path

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

    # 2. Unread inbox — requires_response first
    _print_section("INBOX (unread agent_messages)")
    inbox = (
        client.table("agent_messages")
        .select("*")
        .or_(f"to_agent.eq.{agent_id},to_agent.is.null")
        .is_("read_at", "null")
        .order("requires_response", desc=True)
        .order("created_at", desc=False)
        .execute()
        .data
    )
    if not inbox:
        print("  (empty)")
    else:
        print(f"  {len(inbox)} unread message(s):")
        for m in inbox:
            marker = "🔴" if m.get("requires_response") and not m.get("responded_at") else "  "
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
    parser.add_argument("--agent", required=True, help="agent_id (cai / cc-ihsanos / cc-web / cc-scholar)")
    parser.add_argument("--no-heartbeat", action="store_true", help="Skip the active+heartbeat update")
    parser.add_argument("--json", action="store_true", help="Output the briefing as JSON instead of human text")
    args = parser.parse_args(argv)

    if args.json:
        # JSON mode — used by automation to consume the boot output.
        client = _client()
        me = client.table("agents").select("*").eq("id", args.agent).execute().data
        ctx = (
            client.table("agent_context").select("*").eq("agent_id", args.agent).execute().data
        )
        inbox = (
            client.table("agent_messages")
            .select("*")
            .or_(f"to_agent.eq.{args.agent},to_agent.is.null")
            .is_("read_at", "null")
            .order("requires_response", desc=True)
            .order("created_at", desc=False)
            .execute()
            .data
        )
        out = {
            "agent": me[0] if me else None,
            "context": ctx[0] if ctx else None,
            "unread_inbox": inbox,
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
