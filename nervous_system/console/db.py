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


def build_needs_you_query() -> Tuple[str, list]:
    """The NEEDS-YOU hero feed (redesign #7576): everything genuinely waiting on
    the operator, high-signal only. NOT every requires_response row — there are
    ~70 unanswered agent-to-hub asks at any time and drowning the operator in
    those is the opposite of "surface what's HIS". Scope:
      1. unanswered requires_response addressed to the HUMAN (musa/operator);
      2. unanswered requires_response to the hub (cc-orchestrator) that are P0/P1
         and recent — real decisions the fleet is stuck on, not routine chatter;
      3. blocked lanes / blocked lane_tasks / blocked deploys.
    Ordered P0-first, then oldest (a stuck item rotting for days must rise)."""
    # Precision matters more than recall here: agents over-use requires_response
    # for progress FYIs and under-set responded_at, so a naive "unanswered
    # requires_response" list is a flood (one agent posting 8 progress updates
    # would show 8 times, drowning the 1 genuinely-blocked item). So: (a)
    # human-directed asks show individually; (b) hub-directed asks collapse to
    # the FRESHEST one per agent, fresh window only; (c) structural blocks
    # (lane/task/deploy) are always exact. Better to under-surface a redundant
    # FYI than to cry wolf and make the operator ignore the hero section.
    sql = (
        "SELECT * FROM ("
        "  SELECT 'response' AS kind, from_agent AS who, 'needs response' AS tag, "
        "         COALESCE(NULLIF(subject,''), left(body,140)) AS what, "
        "         round(extract(epoch FROM (now()-created_at)))::int AS age_s, "
        "         COALESCE(priority,'P2') AS priority, id::text AS ref "
        "  FROM agent_messages "
        "  WHERE requires_response AND responded_at IS NULL AND skipped_at IS NULL "
        "    AND lower(to_agent) IN ('musa','operator') "
        "    AND created_at > now() - interval '14 days' "
        "  UNION ALL "
        "  SELECT * FROM ("
        "    SELECT DISTINCT ON (from_agent) 'response' AS kind, from_agent AS who, "
        "           'needs response' AS tag, "
        "           COALESCE(NULLIF(subject,''), left(body,140)) AS what, "
        "           round(extract(epoch FROM (now()-created_at)))::int AS age_s, "
        "           COALESCE(priority,'P2') AS priority, id::text AS ref "
        "    FROM agent_messages "
        "    WHERE requires_response AND responded_at IS NULL AND skipped_at IS NULL "
        "      AND to_agent = 'cc-orchestrator' AND priority IN ('P0','P1') "
        "      AND created_at > now() - interval '24 hours' "
        "    ORDER BY from_agent, created_at DESC"
        "  ) hub "
        "  UNION ALL "
        "  SELECT 'blocked_lane', agent_id, 'lane blocked', "
        "         COALESCE(blocked_on_description, current_task, 'blocked'), "
        "         round(extract(epoch FROM (now()-updated_at)))::int, 'P1', agent_id "
        "  FROM agent_status WHERE status = 'blocked' "
        "  UNION ALL "
        "  SELECT 'blocked_task', lane, 'task blocked', title, "
        "         round(extract(epoch FROM (now()-updated_at)))::int, 'P1', id::text "
        "  FROM lane_tasks WHERE status = 'blocked' "
        "  UNION ALL "
        "  SELECT 'blocked_deploy', workstream, 'deploy blocked', "
        "         COALESCE(NULLIF(detail,''), workstream), "
        "         round(extract(epoch FROM (now()-updated_at)))::int, 'P0', workstream "
        "  FROM deploy_status WHERE stage = 'blocked' "
        # '%%' not '%': this query is executed with a (bound) params list, so
        # psycopg scans for placeholders — a literal % in the LIKE must be doubled.
        ") q ORDER BY (kind LIKE 'blocked%%') DESC, priority ASC, age_s ASC LIMIT 12"
    )
    return sql, []


def fetch_needs_you() -> List[dict]:
    sql, params = build_needs_you_query()
    return _query(sql, params)


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


def build_coordinators_query() -> Tuple[str, list]:
    """The two ORCHESTRATOR bodies — not lanes, so absent from fleet_lanes and
    invisible on the console until now (operator #3617, 2026-07-12). They don't
    self-register in agent_status either, so liveness comes from the freshest
    thing each one did: the latest bus row it authored OR its latest outbound
    operator message (coordinators talk to the operator as much as to the bus,
    so bus-age alone understates them). `activity` = latest bus subject = what
    it is currently coordinating."""
    sql = (
        "SELECT c.agent_id, c.short, c.role_label, "
        "  (SELECT subject FROM agent_messages m "
        "     WHERE m.from_agent = c.agent_id ORDER BY m.id DESC LIMIT 1) AS activity, "
        "  (SELECT round(extract(epoch FROM (now()-created_at)))::int FROM agent_messages m "
        "     WHERE m.from_agent = c.agent_id ORDER BY m.id DESC LIMIT 1) AS activity_age_s, "
        "  LEAST( "
        "    (SELECT round(extract(epoch FROM (now()-created_at)))::int FROM agent_messages m "
        "       WHERE m.from_agent = c.agent_id ORDER BY m.id DESC LIMIT 1), "
        "    (SELECT round(extract(epoch FROM (now()-created_at)))::int FROM operator_messages o "
        "       WHERE o.direction = 'outbound' AND o.tag = c.op_tag ORDER BY o.id DESC LIMIT 1) "
        "  ) AS last_seen_s "
        "FROM (VALUES "
        "  ('cc-orchestrator','Hub','Orchestrates the fleet','orch-channel'), "
        "  ('orch-console','Nazim','CTO console · 2nd coordinator','nazim-console') "
        ") AS c(agent_id, short, role_label, op_tag) "
        "ORDER BY c.agent_id"
    )
    return sql, []


def fetch_coordinators() -> List[dict]:
    sql, params = build_coordinators_query()
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
