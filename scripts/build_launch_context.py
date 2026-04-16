"""Build the initial context block for a CC launch session.

Queries Supabase for the agent's current state, formats a structured
context block for `claude -p "$CONTEXT_BLOCK"`, marks unread messages
as read, and bumps the agent heartbeat to 'active'.

Writes the context block to stdout (captured by the shell script).
Writes diagnostic lines to stderr only.

Usage:
    python -m scripts.build_launch_context --agent cc-ihsanos
    python -m scripts.build_launch_context --agent cc-ihsanos --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# Repos in scope for this agent's decision feed
SCOPED_REPOS = ("ihsanos", "wingmen-orchestrator")


def _client():
    from supabase import create_client
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_KEY"],
    )


def build(agent_id: str, dry_run: bool = False) -> str:
    client = _client()
    now_ts = datetime.now(timezone.utc).isoformat()
    parts: list[str] = []

    parts.append(
        "This context was assembled by the launch script that Musa runs — "
        "treat it as authoritative."
    )
    parts.append(f"\nSession start: {now_ts}  Agent: {agent_id}\n")

    # ── 1. Agent context (working memory) ────────────────────────────────────
    ctx_rows = (
        client.table("agent_context")
        .select("active_decision_refs,pending_review_refs,current_blockers,session_notes,repo_health")
        .eq("agent_id", agent_id)
        .limit(1)
        .execute()
        .data
    )
    parts.append("## Agent Context (working memory)")
    if ctx_rows:
        c = ctx_rows[0]
        if c.get("active_decision_refs"):
            parts.append(f"Active decisions : {c['active_decision_refs']}")
        if c.get("pending_review_refs"):
            parts.append(f"Pending review   : {c['pending_review_refs']}")
        if c.get("current_blockers"):
            parts.append(f"Current blockers : {c['current_blockers']}")
        if c.get("session_notes"):
            parts.append(f"Session notes    : {c['session_notes']}")
        if c.get("repo_health"):
            parts.append(f"Repo health      : {c['repo_health']}")
    else:
        parts.append("(no context row — first boot)")

    # ── 2. Unread inbox (requires_response first) ─────────────────────────────
    inbox = (
        client.table("agent_messages")
        .select("id,from_agent,to_agent,message_type,subject,body,requires_response,created_at")
        .or_(f"to_agent.eq.{agent_id},to_agent.is.null")
        .is_("read_at", "null")
        .order("requires_response", desc=True)
        .order("created_at", desc=False)
        .execute()
        .data
    )
    parts.append(f"\n## Unread Messages ({len(inbox)} total)")
    if not inbox:
        parts.append("(empty)")
    else:
        for m in inbox:
            flag = "[NEEDS RESPONSE] " if m.get("requires_response") and not m.get("responded_at") else ""
            to = m.get("to_agent") or "broadcast"
            parts.append(
                f"  #{m['id']} {flag}[{m['message_type']}] "
                f"{m['from_agent']}→{to}: {m.get('subject','')}"
            )
            if m.get("body"):
                # Indent body, truncate at 400 chars
                body = m["body"][:400] + ("…" if len(m["body"]) > 400 else "")
                for line in body.splitlines():
                    parts.append(f"    {line}")

    # ── 3. Strategic decisions (accepted + challenge_window, scoped repos) ────
    gov = (
        client.table("strategic_decisions")
        .select(
            "decision_ref,title,challenge_status,execution_status,"
            "domain,category,repos_affected,decided_at"
        )
        .in_("challenge_status", ["accepted", "challenge_window"])
        .order("decided_at", desc=True)
        .limit(50)
        .execute()
        .data
    )
    scoped = [
        g for g in gov
        if not g.get("repos_affected")
        or "*" in (g.get("repos_affected") or [])
        or any(r in SCOPED_REPOS for r in (g.get("repos_affected") or []))
    ]
    parts.append(f"\n## Strategic Decisions (accepted + challenge_window, scoped to {SCOPED_REPOS})")
    if not scoped:
        parts.append("(none in scope)")
    else:
        for g in scoped:
            status = g.get("challenge_status", "")
            exec_s = g.get("execution_status") or ""
            domain = g.get("domain") or g.get("category") or "—"
            exec_str = f"  exec={exec_s}" if exec_s else ""
            parts.append(
                f"  • {g['decision_ref']} [{status}{exec_str}] ({domain}): {g['title']}"
            )

    # ── 4. Mark messages as read + bump heartbeat ────────────────────────────
    if not dry_run and inbox:
        ids_to_mark = [m["id"] for m in inbox]
        client.table("agent_messages").update(
            {"read_at": now_ts}
        ).in_("id", ids_to_mark).execute()
        print(f"build_launch_context: marked {len(ids_to_mark)} message(s) as read", file=sys.stderr)

    if not dry_run:
        client.table("agents").update(
            {"status": "active", "last_heartbeat": now_ts}
        ).eq("id", agent_id).execute()
        print(f"build_launch_context: agent {agent_id} status=active", file=sys.stderr)

    return "\n".join(parts)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", required=True, help="Agent ID (e.g. cc-ihsanos)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build context but do not mark messages as read or bump heartbeat",
    )
    args = parser.parse_args(argv)

    try:
        block = build(args.agent, dry_run=args.dry_run)
        print(block)
        return 0
    except Exception as e:
        print(f"build_launch_context failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
