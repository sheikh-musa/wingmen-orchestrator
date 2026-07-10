"""Read-only DB access (CAI-RESP-264 condition 2).

Reads via env CONSOLE_DB_URL — the DSN of a SELECT-only Postgres role
(`console_readonly`, owned/created by the orchestrator, Task 1). For LOCAL DEV
ONLY it falls back to DATABASE_URL. This module:

  * NEVER reads SUPABASE_SERVICE_KEY (the service key never reaches the console).
  * Only ever issues SELECTs. There is no code path that writes.
  * Parameterizes all filters (no string interpolation of user input).

psycopg (v3) is already in the venv; no new dependency.
"""
from __future__ import annotations

import os
from typing import List, Optional, Tuple

import psycopg
from psycopg.rows import dict_row

MAX_LIMIT = 200
DEFAULT_LIMIT = 50
_CONNECT_TIMEOUT = 15
# Server-side ceiling on any single console query. Without it, a panel load
# only fails after the full _CONNECT_TIMEOUT (or a longer server default),
# hanging the UI on a spinner. 8s fails fast -> the client shows its retry
# state and the 10s periodic refresh repaints on the next good response.
#
# Applied via `SET statement_timeout` on the freshly-opened connection, NOT a
# libpq `-c` startup option: the console's DSN is the Supabase *session* pooler
# (Supavisor), which silently swallows startup options (verified: `-c
# statement_timeout=8000` left SHOW reporting the role default 10s) but honors
# a per-session SET. Session mode keeps one server connection for the whole
# client session, so the SET holds for the query that follows it.
_STATEMENT_TIMEOUT_MS = 8000

# Columns surfaced for the conversation panel. (No money/PII columns exist on
# the bus; bodies are additionally PII-redacted in-view by the API layer.)
_MESSAGE_COLS = (
    "id, thread_id, from_agent, to_agent, message_type, subject, body, "
    "priority, requires_response, sub_tag, created_at"
)


def resolve_dsn() -> Optional[str]:
    """Return the read-only DSN. CONSOLE_DB_URL wins; DATABASE_URL is dev-only
    fallback. Never the service key."""
    return os.environ.get("CONSOLE_DB_URL") or os.environ.get("DATABASE_URL")


def build_messages_query(
    limit: int, thread: Optional[str], agent: Optional[str]
) -> Tuple[str, list]:
    """Build a parameterized SELECT for recent bus rows + filters.

    *agent* matches either side of the exchange (from_agent OR to_agent).
    Returns (sql, params); the LIMIT is the last param.
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    limit = max(1, min(limit, MAX_LIMIT))

    where = []
    params: list = []
    if thread:
        where.append("thread_id = %s")
        params.append(thread)
    if agent:
        where.append("(from_agent = %s OR to_agent = %s)")
        params.append(agent)
        params.append(agent)

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    sql = (
        f"SELECT {_MESSAGE_COLS} FROM agent_messages"
        f"{clause} ORDER BY id DESC LIMIT %s"
    )
    params.append(limit)
    return sql, params


def build_lanes_query() -> Tuple[str, list]:
    """agents ⋈ agent_status ⋈ fleet_lanes — each instance's live status,
    heartbeat age, desired_state, and its CURRENT ACTIVITY.

    `current_task` only holds the boot string ("session-launch model=…"); lanes
    don't refresh it. The truthful "what is this lane working on now" signal is
    the subject of the latest bus message it authored, so we LATERAL-join that
    and expose it as `activity` (+ age). Falls back to current_task in the view
    if a lane has never posted.

    `tmux_session` (agent_status, migration 005) is the actual live tmux
    session name each INSTANCE self-registers at boot — the correct target
    for the live-pane peek. `fleet_lanes.lane` is a static label shared by an
    entire agent FAMILY (e.g. every cc-reviewer-N row has lane='reviewer'),
    which can never resolve to one specific on-demand session when several
    are live at once (proven live 2026-07-04: 3 simultaneous reviewer
    sessions, one static label). tmux_session is per-instance and always
    correct; the console prefers it and only falls back to `lane` if a lane
    hasn't self-registered."""
    sql = (
        "SELECT "
        "  s.agent_id, "
        "  s.base_agent_id, "
        "  a.display_name, "
        "  s.status, "
        "  s.current_task, "
        "  s.tmux_session, "
        "  round(extract(epoch FROM (now() - s.last_heartbeat)))::int "
        "    AS heartbeat_age_s, "
        "  l.desired_state, "
        "  l.lane, "
        "  act.subject AS activity, "
        "  round(extract(epoch FROM (now() - act.created_at)))::int "
        "    AS activity_age_s "
        "FROM agent_status s "
        "LEFT JOIN agents a ON a.id = s.base_agent_id "
        "LEFT JOIN fleet_lanes l ON l.base_agent_id = s.base_agent_id "
        "LEFT JOIN LATERAL ("
        "  SELECT subject, created_at FROM agent_messages m "
        "  WHERE m.from_agent = s.base_agent_id "
        "  ORDER BY m.id DESC LIMIT 1"
        ") act ON true "
        "ORDER BY s.base_agent_id, s.agent_id"
    )
    return sql, []


def build_queue_query() -> Tuple[str, list]:
    """lane_tasks — each lane's queued/active work in FIFO order (oldest first,
    by id) so nothing gets starved / 'stuck for days' — operator doctrine
    2026-07-08. Blocked rows sink to the bottom (they can't progress until the
    blocker clears); everything else is strict FIFO. Done tasks excluded."""
    sql = (
        "SELECT lane, title, detail, priority_rank, status, "
        "  round(extract(epoch FROM (now() - updated_at)))::int AS updated_age_s, "
        "  round(extract(epoch FROM (now() - started_at))/60)::int AS elapsed_min, "
        "  sla_minutes, "
        "  (sla_minutes IS NOT NULL AND started_at IS NOT NULL "
        "     AND now() > started_at + (sla_minutes || ' minutes')::interval) AS over_sla "
        "FROM lane_tasks WHERE status <> 'done' "
        "ORDER BY lane, (status = 'blocked'), id"
    )
    return sql, []


def build_deploys_query() -> Tuple[str, list]:
    """deploy_status — each workstream's push/live state across the fleet.
    `blocked` rows surface first (they need attention); the rest by recency so
    the latest movement is at the top."""
    sql = (
        "SELECT workstream, repo, stage, detail, url, "
        "  round(extract(epoch FROM (now() - updated_at)))::int AS updated_age_s, "
        "  updated_by "
        "FROM deploy_status "
        "ORDER BY (stage = 'blocked') DESC, updated_at DESC"
    )
    return sql, []


def _query(sql: str, params: list) -> List[dict]:
    dsn = resolve_dsn()
    if not dsn:
        raise RuntimeError("no read-only DSN configured (set CONSOLE_DB_URL)")
    # autocommit=True + read-only session: belt-and-suspenders on top of the
    # SELECT-only role. A console fault structurally cannot write.
    with psycopg.connect(
        dsn, connect_timeout=_CONNECT_TIMEOUT, autocommit=True,
        row_factory=dict_row,
    ) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            # Cap this session before the real query so a DB blip fails fast
            # instead of hanging the panel. Literal int (our own constant, not
            # user input) — SET doesn't bind a parameter for its value.
            cur.execute(f"SET statement_timeout = {int(_STATEMENT_TIMEOUT_MS)}")
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def fetch_messages(
    limit: int = DEFAULT_LIMIT,
    thread: Optional[str] = None,
    agent: Optional[str] = None,
) -> List[dict]:
    sql, params = build_messages_query(limit, thread, agent)
    return _query(sql, params)


def fetch_lanes() -> List[dict]:
    sql, params = build_lanes_query()
    return _query(sql, params)


def fetch_deploys() -> List[dict]:
    sql, params = build_deploys_query()
    return _query(sql, params)


def fetch_queue() -> List[dict]:
    sql, params = build_queue_query()
    return _query(sql, params)
