"""console_alive() -- fresh agent_status heartbeat check for orch-console (audit-20261006
item 4, bus#54648).

WHY: priority_sla_watchdog.py, sre_liveness_watchdog.py, and wake_backstop_sweep.py's
dead-foreign page each escalate TO orch-console (Nazim's operator-facing channel)
assuming it is listening. If orch-console itself is dead/wedged, that escalation lands
in a void -- the exact failure this check exists to close. Each caller checks this
BEFORE trusting orch-console as the escalation target; on False, re-address the
escalation to cc-orchestrator (the hub) as P1+requires_response instead.

FAIL-SAFE DIRECTION: any DB/lookup failure, missing DSN, or no matching row returns
False (treat as NOT alive) -- "fails toward paging," the same direction
priority_sla_watchdog's pre-existing _internal_path_alive already took ("cannot tell =>
page"). A wrongly-triggered hub redirect costs one extra hop; a wrongly-skipped one
repeats the void-page bug this closes.

Base-inclusive match (agent_id=CONSOLE_AGENT OR base_agent_id=CONSOLE_AGENT), same shape
as wake_backstop_sweep._matching_hbs -- orch-console is keyed by instance id with its
base in base_agent_id, while callers address the bare id.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg

CONSOLE_AGENT = "orch-console"
CONSOLE_ALIVE_FRESH_S = int(os.environ.get("CONSOLE_ALIVE_FRESH_S", "600"))  # 10min


def console_alive(dsn: str | None = None, fresh_s: int = CONSOLE_ALIVE_FRESH_S) -> bool:
    """True iff orch-console has an agent_status heartbeat (base-inclusive) fresher than
    fresh_s seconds. Fails to False on any lookup error (fail-toward-paging)."""
    dsn = dsn if dsn is not None else (os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL"))
    if not dsn:
        return False
    try:
        with psycopg.connect(dsn, connect_timeout=10) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT last_heartbeat FROM agent_status WHERE agent_id=%s OR base_agent_id=%s",
                (CONSOLE_AGENT, CONSOLE_AGENT))
            hbs = [r[0] for r in cur.fetchall() if r[0] is not None]
    except Exception:  # noqa: BLE001 — an unprovable check never claims "alive"
        return False
    if not hbs:
        return False
    now = datetime.now(timezone.utc)
    for hb in hbs:
        try:
            if (now - hb).total_seconds() < fresh_s:
                return True
        except Exception:  # noqa: BLE001 — an un-ageable heartbeat is not proof of life
            continue
    return False
