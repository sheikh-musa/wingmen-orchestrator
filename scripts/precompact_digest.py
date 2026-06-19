#!/usr/bin/env python3
"""precompact_digest.py — auto-capture a state snapshot right before the context
window compacts. Wired to the Claude Code PreCompact hook so 'lose nothing on
compaction' is automatic and never depends on cc-orchestrator estimating its own
context % (which it cannot read directly).

This writes a DETERMINISTIC snapshot (recent operator bridge messages, active
lane tasks + SLA, recent bus traffic, pointer to the last synthesis digest) —
NOT a model synthesis. cc-orchestrator still writes richer digests at
checkpoints; this is the safety net the harness triggers on its own.
"""
import os
from datetime import datetime, timezone

import psycopg
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with psycopg.connect(DSN) as c:
        cur = c.cursor()
        cur.execute("SELECT set_config('app.current_agent_id','cc-orchestrator',true)")

        def rows(sql, *a):
            cur.execute(sql, a if a else None)  # None => no %-interpolation (literal % in LIKE)
            return cur.fetchall()

        ops = rows("SELECT direction, tag, left(text,90) FROM operator_messages ORDER BY id DESC LIMIT 12")
        tasks = rows("""SELECT lane, title, status,
                        round(extract(epoch FROM (now()-started_at))/60) AS m, sla_minutes
                        FROM lane_tasks WHERE status <> 'done' ORDER BY lane, priority_rank""")
        bus = rows("""SELECT from_agent, to_agent, left(subject,70)
                      FROM agent_messages WHERE created_at > now() - interval '90 min'
                      ORDER BY id DESC LIMIT 15""")
        lastdig = rows("SELECT id, title FROM session_digests WHERE source LIKE 'cc_orchestrator%' AND source <> 'cc_orchestrator_precompact' ORDER BY id DESC LIMIT 1")

        parts = [f"[AUTO PreCompact snapshot @ {stamp}] Deterministic state captured by the hook (not a synthesis — see the latest synthesis digest below).", ""]
        if lastdig:
            parts.append(f"LATEST SYNTHESIS DIGEST: #{lastdig[0][0]} — {lastdig[0][1]}")
        parts.append("\nLANE QUEUE (open tasks, elapsed/SLA):")
        for lane, title, status, m, sla in tasks:
            over = " ⚠OVER-SLA" if (sla and m and m > sla) else ""
            parts.append(f"  @{lane} [{status}] {title} — {m}m/{sla}m{over}")
        parts.append("\nRECENT OPERATOR BRIDGE (newest first):")
        for d, tag, t in ops:
            parts.append(f"  {d}{('/'+tag) if tag else ''}: {t}")
        parts.append("\nRECENT BUS (90m):")
        for fr, to, subj in bus:
            parts.append(f"  {fr}->{to}: {subj}")
        body = "\n".join(parts)

        cur.execute("""INSERT INTO session_digests
            (source, session_date, title, topics_covered, key_reasoning, decisions_made, open_questions, action_items, repos_discussed, created_at)
            VALUES ('cc_orchestrator_precompact', current_date,
              %s, ARRAY['precompact-snapshot'], %s, ARRAY['(auto-captured deterministic state)'],
              ARRAY['(see latest synthesis digest for open questions)'],
              ARRAY['post-compaction: read latest synthesis digest + this snapshot to re-orient'],
              ARRAY['orchestrator'], now())""",
            (f"[auto] PreCompact state snapshot {stamp}", body))
        c.commit()
    print(f"precompact snapshot written @ {stamp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
