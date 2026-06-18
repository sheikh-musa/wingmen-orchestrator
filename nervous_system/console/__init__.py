"""Fleet Console v1 — orchestrator-owned, read-only, server-mediated web console.

A separate process (NEVER inside the orch) that serves a live view of the fleet:
the agent_messages conversation bus + lane statuses. Read-only by construction
(SELECT-only DB role via CONSOLE_DB_URL; never the Supabase service key), token-
gated (Authorization: Bearer only, fail-closed, audited), and process-isolated
so a console fault cannot touch the orchestrator coordination path.

Spec:  docs/superpowers/specs/2026-06-17-fleet-console-v1-design.md
Plan:  docs/superpowers/plans/2026-06-17-fleet-console-v1.md
Binding hardenings: CAI-RESP-264.
"""

__all__ = ["app", "auth", "db", "feed", "pii"]
