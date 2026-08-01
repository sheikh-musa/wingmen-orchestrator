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

import logging
import os
import queue
import threading
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger("wingmen.console.db")

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
        "  s.auth_account, "
        "  s.auth_fp, "
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
    Ordered P0-first, then oldest (a stuck item rotting for days must rise).

    AUDIENCE split (operator flag, 2026-07-12): the operator only reaches lanes
    THROUGH the hub, so a hub-directed decision (a '[D2 RULING NEEDED]' to
    cc-orchestrator, a blocked lane/task/deploy) is NOT his to answer — showing
    it in the same hero as a human-addressed ask makes him think he must reply
    when the HUB owns it. Each row now carries `audience`:
      • 'operator' — requires_response literally addressed to musa/operator; the
        only rows that belong in the "Needs you" hero.
      • 'fleet'    — hub-directed asks + all structural blocks; the client demotes
        these to a lower-emphasis "Fleet is handling" group (peek-only, not
        reply). To move a class across the line (e.g. make blocked deploys
        operator-facing), flip its literal below — nothing else changes.

    STALE filter (operator flag, 2026-07-12): a block resolves by flipping its
    row's status, with no resolved_at, so a row left at status='blocked' and
    never flipped (the 19d 'cosem-adcda TASK BLOCKED' zombie) rises forever. A
    genuinely live block gets touched; one untouched for >7d is resolved-but-
    not-flipped noise, so structural blocks are capped to updated_at within 7d."""
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
        "         COALESCE(priority,'P2') AS priority, id::text AS ref, "
        "         'operator' AS audience "
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
        "           COALESCE(priority,'P2') AS priority, id::text AS ref, "
        "           'fleet' AS audience "
        "    FROM agent_messages "
        "    WHERE requires_response AND responded_at IS NULL AND skipped_at IS NULL "
        "      AND to_agent = 'cc-orchestrator' AND priority IN ('P0','P1') "
        "      AND created_at > now() - interval '24 hours' "
        "    ORDER BY from_agent, created_at DESC"
        "  ) hub "
        "  UNION ALL "
        "  SELECT 'blocked_lane', agent_id, 'lane blocked', "
        "         COALESCE(blocked_on_description, current_task, 'blocked'), "
        "         round(extract(epoch FROM (now()-updated_at)))::int, 'P1', agent_id, 'fleet' "
        "  FROM agent_status WHERE status = 'blocked' "
        "    AND updated_at > now() - interval '7 days' "
        "  UNION ALL "
        "  SELECT 'blocked_task', lane, 'task blocked', title, "
        "         round(extract(epoch FROM (now()-updated_at)))::int, 'P1', id::text, 'fleet' "
        "  FROM lane_tasks WHERE status = 'blocked' "
        "    AND updated_at > now() - interval '7 days' "
        "  UNION ALL "
        "  SELECT 'blocked_deploy', workstream, 'deploy blocked', "
        "         COALESCE(NULLIF(detail,''), workstream), "
        "         round(extract(epoch FROM (now()-updated_at)))::int, 'P0', workstream, 'fleet' "
        "  FROM deploy_status WHERE stage = 'blocked' "
        "    AND updated_at > now() - interval '7 days' "
        # '%%' not '%': this query is executed with a (bound) params list, so
        # psycopg scans for placeholders — a literal % in the LIKE must be doubled.
        # operator-audience rows sort to the top so the hero is always his first.
        ") q ORDER BY (audience = 'operator') DESC, (kind LIKE 'blocked%%') DESC, "
        "             priority ASC, age_s ASC LIMIT 12"
    )
    return sql, []


def warm_pool(n: int = 3) -> None:
    """Open and stash up to *n* connections so the FIRST /api/fleet after a
    console restart is already warm (~150ms) instead of paying the ~650ms×N
    cold-connect on the operator's first tap. Best-effort: a DB blip at boot
    must not crash the console — the pool just fills lazily on demand instead.
    Called in a background thread from make_server()."""
    n = min(n, _POOL_MAX)
    conns = []
    try:
        for _ in range(n):
            conns.append(_get_conn())
    except Exception as e:
        logger.warning("pool pre-warm skipped (fills lazily): %s", e)
    finally:
        for c in conns:
            _return_conn(c)


def fetch_needs_you() -> List[dict]:
    sql, params = build_needs_you_query()
    return _query(sql, params)


def build_coordinators_query() -> Tuple[str, list]:
    """The three coordinator brains (orch hub + Nazim + cai) - not lanes, so
    absent from fleet_lanes. Liveness = freshest of their latest bus row or
    outbound operator message; activity = latest bus subject. tmux_session names
    the body's live pane so the console can peek it like a lane (operator #3672) —
    'orch' is the hub session on THIS (Studio) host; 'nazim' is Nazim's console
    session (on the MacBook, so only peekable when that pane is local — the
    caller marks it peekable only if the session is in local list-sessions);
    'cai' is the governance node.

    Each card also carries its OWN context + token attribution (operator op#9088):
      * `ctx_tokens`/`ctx_age_s` — the freshest current-context reading from
        cc_session_costs.latest_context_tokens for this body's cc_identity (the
        SAME source the context-bloat section uses); the caller turns it into a
        pct/level with the same window + thresholds.
      * `auth_fp`/`auth_account` — the freshest non-null OAuth fingerprint this
        body self-registered in agent_status, so the coordinator card shows the
        same 🔑 token badge the lane cards do (a body that never self-registers,
        like the cross-host hub, simply has no fp -> no badge)."""
    sql = (
        "SELECT c.agent_id, c.short, c.role_label, c.tmux_session, "
        "  (SELECT subject FROM agent_messages m "
        "     WHERE m.from_agent = c.agent_id ORDER BY m.id DESC LIMIT 1) AS activity, "
        "  (SELECT round(extract(epoch FROM (now()-created_at)))::int FROM agent_messages m "
        "     WHERE m.from_agent = c.agent_id ORDER BY m.id DESC LIMIT 1) AS activity_age_s, "
        "  (SELECT cs.latest_context_tokens FROM cc_session_costs cs "
        "     WHERE cs.cc_identity = c.agent_id AND cs.latest_context_tokens IS NOT NULL "
        "     ORDER BY COALESCE(cs.ended_at, cs.created_at) DESC LIMIT 1) AS ctx_tokens, "
        "  (SELECT round(extract(epoch FROM (now()-COALESCE(cs.ended_at, cs.created_at))))::int "
        "     FROM cc_session_costs cs "
        "     WHERE cs.cc_identity = c.agent_id AND cs.latest_context_tokens IS NOT NULL "
        "     ORDER BY COALESCE(cs.ended_at, cs.created_at) DESC LIMIT 1) AS ctx_age_s, "
        "  (SELECT a.auth_fp FROM agent_status a "
        "     WHERE a.base_agent_id = c.agent_id AND a.auth_fp IS NOT NULL "
        "     ORDER BY a.last_heartbeat DESC NULLS LAST LIMIT 1) AS auth_fp, "
        "  (SELECT a.auth_account FROM agent_status a "
        "     WHERE a.base_agent_id = c.agent_id AND a.auth_fp IS NOT NULL "
        "     ORDER BY a.last_heartbeat DESC NULLS LAST LIMIT 1) AS auth_account, "
        "  LEAST( "
        "    (SELECT round(extract(epoch FROM (now()-created_at)))::int FROM agent_messages m "
        "       WHERE m.from_agent = c.agent_id ORDER BY m.id DESC LIMIT 1), "
        "    (SELECT round(extract(epoch FROM (now()-created_at)))::int FROM operator_messages o "
        "       WHERE o.direction = 'outbound' AND o.tag = c.op_tag ORDER BY o.id DESC LIMIT 1) "
        "  ) AS last_seen_s "
        "FROM (VALUES "
        "  ('cc-orchestrator','Hub','Orchestrates the fleet','orch-channel','orch'), "
        "  ('orch-console','Nazim','CTO console / 2nd coordinator','nazim-console','nazim'), "
        "  ('cai','cai','governance / strategic node','cai','cai') "
        ") AS c(agent_id, short, role_label, op_tag, tmux_session) "
        "ORDER BY c.agent_id"
    )
    return sql, []


def fetch_coordinators() -> List[dict]:
    sql, params = build_coordinators_query()
    return _query(sql, params)


# How many recent bus rows the coordinator activity-feed fallback shows.
_COORD_PEEK_LIMIT = 16
# A published pane snapshot older than this is treated as stale (publisher hiccup)
# and the peek falls back to the bus-activity feed. Publisher writes every ~10s,
# so this tolerates ~9 missed writes before falling back.
_COORD_SNAPSHOT_MAX_AGE_S = 90


def _fetch_pane_snapshot(agent_id: str, max_age_s: int = _COORD_SNAPSHOT_MAX_AGE_S) -> Optional[str]:
    """The latest FRESH published tmux-pane snapshot for a cross-host coordinator
    (Mini-side publisher UPSERTs coordinator_panes; migration 024). None if there's
    no row or it's staler than max_age_s. No ssh — a plain substrate read."""
    rows = _query(
        "SELECT pane_text FROM coordinator_panes "
        "WHERE agent_id = %s AND captured_at > now() - make_interval(secs => %s)",
        [agent_id, max_age_s],
    )
    return rows[0]["pane_text"] if rows else None


def _fetch_bus_activity_feed(agent_id: str, limit: int = _COORD_PEEK_LIMIT) -> str:
    """A readable activity feed from a coordinator's recent bus posts, newest LAST
    so it reads top-to-bottom like a pane scrollback. Each line:
    'Hm ago → to  [type] subject'. The fallback when no fresh pane snapshot exists."""
    rows = _query(
        "SELECT created_at, message_type, to_agent, subject "
        "FROM agent_messages WHERE from_agent = %s ORDER BY id DESC LIMIT %s",
        [agent_id, limit],
    )
    lines = []
    for r in reversed(rows):
        ts = r.get("created_at")
        age = ""
        if ts is not None:
            secs = max(0, int((datetime.now(timezone.utc) - ts).total_seconds()))
            age = ("%dm" % (secs // 60)) if secs < 3600 else ("%dh" % (secs // 3600))
        to = r.get("to_agent") or ""
        mtype = r.get("message_type") or ""
        subject = (r.get("subject") or "").strip()
        prefix = " ".join(p for p in ((age and age + " ago"), (to and "→ " + to), (mtype and "[" + mtype + "]")) if p)
        lines.append((prefix + "  " + subject).strip() if prefix else subject)
    return "\n".join(lines)


def fetch_coordinator_peek(agent_id: str, limit: int = _COORD_PEEK_LIMIT) -> str:
    """The peek text for a cross-host coordinator body (Nazim on the Mini). ONE
    source switch (operator #3729): PREFER a fresh published tmux-pane snapshot
    (reflowed exactly like a local pane) and FALL BACK to the recent bus-activity
    feed when the publisher hasn't landed / is stale. No ssh either way."""
    snap = _fetch_pane_snapshot(agent_id)
    if snap:
        from nervous_system.console import panes  # lazy import: reflow the raw pane
        return panes._clean_pane_text(snap)
    return _fetch_bus_activity_feed(agent_id, limit)


# --- persistent connection pool ----------------------------------------------
#
# WHY: the DSN is the Supabase pooler in ap-southeast-2. A *fresh* connection
# costs ~650ms (TCP+TLS+auth handshake over that RTT) plus ~100ms for the
# per-session `SET statement_timeout` round-trip — paid on EVERY _query() call.
# /api/fleet issues three reads, so a cold-connect-per-query design spent
# ~2.6s/request almost entirely in handshakes (query bodies are ~100ms each).
# That slowness is what tripped the phone's client-side timeout -> the hard
# "Could not connect" screen (2026-07-11 regression). A warm, reused connection
# pays connect + SET ONCE (at pool fill), so a fleet request drops to ~3×100ms
# of actual query time — comfortably under the <500ms budget.
#
# The pool is a tiny stdlib affair (no psycopg_pool dep, which isn't in the
# venv): a LIFO of idle connections, bounded by CONSOLE_DB_POOL. Borrow-time
# health checks + a single transparent reconnect-and-retry make a server-side
# idle disconnect (the pooler culls idle sessions) invisible to callers: the
# first request after an idle gap pays one reconnect, the rest stay warm.
_POOL_MAX = max(1, int(os.environ.get("CONSOLE_DB_POOL", "6")))
_idle: "queue.LifoQueue[psycopg.Connection]" = queue.LifoQueue()
_pool_lock = threading.Lock()
_pool_size = 0  # live connections that exist (idle + checked-out), guarded by lock


def _new_conn() -> psycopg.Connection:
    """Open one warm read-only connection with the statement-timeout already
    set, so the per-request hot path never pays the connect or the SET."""
    dsn = resolve_dsn()
    if not dsn:
        raise RuntimeError("no read-only DSN configured (set CONSOLE_DB_URL)")
    # autocommit=True + read-only session: belt-and-suspenders on top of the
    # SELECT-only role. A console fault structurally cannot write.
    conn = psycopg.connect(
        dsn, connect_timeout=_CONNECT_TIMEOUT, autocommit=True,
        row_factory=dict_row,
    )
    conn.read_only = True
    with conn.cursor() as cur:
        # Cap every query on this session so a DB blip fails fast instead of
        # hanging a panel. Literal int (our own constant, not user input) — SET
        # doesn't bind a parameter for its value. Set once per connection; it
        # persists for the life of the pooled session (Supavisor session mode
        # keeps one server backend per client connection).
        cur.execute(f"SET statement_timeout = {int(_STATEMENT_TIMEOUT_MS)}")
    return conn


def _get_conn() -> psycopg.Connection:
    """Borrow a warm connection: reuse an idle one, else open a new one up to
    _POOL_MAX, else block for one to be returned."""
    global _pool_size
    try:
        conn = _idle.get_nowait()
        if not (conn.closed or conn.broken):
            return conn
        # a dead idle connection: drop it, fall through to make a replacement
        with _pool_lock:
            _pool_size -= 1
    except queue.Empty:
        pass

    with _pool_lock:
        may_create = _pool_size < _POOL_MAX
        if may_create:
            _pool_size += 1
    if may_create:
        try:
            return _new_conn()
        except Exception:
            with _pool_lock:
                _pool_size -= 1
            raise
    # at capacity: wait for a peer to return one.
    return _idle.get()


def _return_conn(conn: psycopg.Connection) -> None:
    """Return a healthy connection to the idle pool (drop it if it went bad)."""
    global _pool_size
    if conn.closed or conn.broken:
        with _pool_lock:
            _pool_size -= 1
        return
    _idle.put(conn)


def _discard_conn(conn: psycopg.Connection) -> None:
    global _pool_size
    try:
        conn.close()
    except Exception:
        pass
    with _pool_lock:
        _pool_size -= 1


def _query(sql: str, params: list) -> List[dict]:
    # Up to two attempts: a pooled connection the server culled while idle only
    # reveals itself as broken on first use, so on a connection-level failure we
    # drop it and retry ONCE with a fresh one. A query-level error (bad SQL,
    # statement timeout) leaves the connection healthy — return it and re-raise.
    last_err: Optional[Exception] = None
    for attempt in range(2):
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = [dict(r) for r in cur.fetchall()]
            _return_conn(conn)
            return rows
        except psycopg.Error as e:
            if (conn.closed or conn.broken) and attempt == 0:
                _discard_conn(conn)
                last_err = e
                logger.debug("pooled connection died mid-query, reconnecting: %s", e)
                continue
            _return_conn(conn)
            raise
    raise last_err  # pragma: no cover - loop always returns or raises above


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


def build_backlog_query() -> Tuple[str, list]:
    """The operator's REALTIME "Your asks" tracker (operator op#9088).

    Reads live from the `operator_backlog` substrate table (Nazim maintains the
    rows; the console just renders them), so the running backlog is one place the
    operator can always look and it refreshes on the normal /api/fleet poll — no
    static HTML page to regenerate. Ordered by STATUS priority (needs_you first,
    then in_progress, done, parked) so what wants the operator floats to the top,
    then by the curator's explicit `sort_order`, then id as a stable tiebreak."""
    sql = (
        "SELECT id, ask, status, op_ref, note, sort_order, "
        "  round(extract(epoch FROM (now()-updated_at)))::int AS updated_age_s "
        "FROM operator_backlog "
        "ORDER BY CASE status "
        "    WHEN 'needs_you' THEN 0 WHEN 'in_progress' THEN 1 "
        "    WHEN 'done' THEN 2 WHEN 'parked' THEN 3 ELSE 4 END, "
        "  sort_order, id"
    )
    return sql, []


def fetch_backlog() -> List[dict]:
    sql, params = build_backlog_query()
    return _query(sql, params)


def build_context_bloat_query() -> Tuple[str, list]:
    """Latest current-context size per always-on agent (window fill).

    `latest_context_tokens` on the FRESHEST cc_session_costs row per identity is
    the LAST assistant turn's actual input context (fresh input + cache-read +
    cache-creation) = how full that agent's context window is RIGHT NOW. NOTE:
    `cache_read_input_tokens` is a LIFETIME SUM across every turn (e.g. 98M vs a
    1M window) and is NOT a current-context signal — using it was the pre-
    2026-07-16 gauge bug that showed impossible 100% readings. Freshness is by
    activity time (ended_at = the session jsonl mtime), not DB-insert time.
    One-off `operator-*` capture sessions are excluded; a 45-day window drops
    long-dead identities; rows without a current-context value are skipped.
    `age_s` surfaces a stale reading (writer paused), never shown as live."""
    sql = (
        "SELECT DISTINCT ON (cc_identity) "
        "  cc_identity, "
        "  latest_context_tokens AS ctx_tokens, "
        "  input_tokens, "
        "  round(extract(epoch FROM (now() - COALESCE(ended_at, created_at))))::int AS age_s, "
        "  COALESCE(ended_at, created_at) AS latest_at, "
        # Freshest OAuth fingerprint this identity self-registered in agent_status,
        # so each context-bloat row shows the same 🔑 token badge as the lane cards
        # (operator op#9088). cc_identity == agent_status.base_agent_id for lanes.
        "  (SELECT a.auth_fp FROM agent_status a "
        "     WHERE a.base_agent_id = cc_session_costs.cc_identity AND a.auth_fp IS NOT NULL "
        "     ORDER BY a.last_heartbeat DESC NULLS LAST LIMIT 1) AS auth_fp "
        "FROM cc_session_costs "
        "WHERE cc_identity NOT LIKE 'operator-%%' "
        "  AND latest_context_tokens IS NOT NULL "
        "  AND COALESCE(ended_at, created_at) > now() - interval '45 days' "
        "ORDER BY cc_identity, COALESCE(ended_at, created_at) DESC"
    )
    return sql, []


def fetch_context_bloat() -> List[dict]:
    sql, params = build_context_bloat_query()
    return _query(sql, params)
