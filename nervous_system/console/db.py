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

from nervous_system.console import coordinators

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
    hasn't self-registered.

    STALE-INSTANCE DEDUP (op#9770): a lane that restarts leaves its OLD
    agent_status row behind (a new instance id, same tmux_session) — e.g.
    cc-finance-1 'offline' hb 2h alongside the LIVE cc-finance-2 on the SAME
    'finance' session. Both used to render, so a dead instance showed as a
    phantom card. We DISTINCT ON the session identity, keeping the
    FRESHEST-heartbeat row per session, so only the live instance survives. The
    key is COALESCE(tmux_session, agent_id): a lane that never self-registered a
    session dedups on its own unique id (NULL sessions must NOT collapse into one
    row). A hard age-drop of a genuinely-dead sole instance is done in app.py
    AFTER live-pane enrichment (a working lane with a stalled heartbeat writer
    must NOT be dropped for a stale hb — the live pane is the truth)."""
    sql = (
        "SELECT * FROM ( "
        "SELECT DISTINCT ON (COALESCE(s.tmux_session, s.agent_id)) "
        "  s.agent_id, "
        "  s.base_agent_id, "
        "  a.display_name, "
        "  s.status, "
        "  s.current_task, "
        "  s.tmux_session, "
        "  s.auth_account, "
        "  s.auth_fp, "
        "  s.host, "
        "  round(extract(epoch FROM (now() - s.last_heartbeat)))::int "
        "    AS heartbeat_age_s, "
        "  l.desired_state, "
        "  l.lane, "
        "  act.subject AS activity, "
        "  round(extract(epoch FROM (now() - act.created_at)))::int "
        "    AS activity_age_s "
        "FROM agent_status s "
        "LEFT JOIN agents a ON a.id = s.base_agent_id "
        # fleet_lanes match: PREFER the row whose `lane` == this instance's live
        # tmux_session (op#10550). A multi-lane FAMILY (the irsyad perimeter:
        # cc-irsyad-1..4, one base_agent_id, distinct sessions irsyad/-prog1/-prog2/
        # -coord) shares ONE base across several fleet_lanes rows, so joining by
        # base alone lets DISTINCT ON attribute an ARBITRARY perimeter row's
        # desired_state to each instance (why cc-irsyad-2 showed 'down'). Preferring
        # lane==tmux_session gives each instance ITS OWN row; fall back to a
        # base_agent_id match for a lane whose session != its fleet_lanes.lane.
        "LEFT JOIN LATERAL ("
        "  SELECT desired_state, lane FROM fleet_lanes fl "
        "  WHERE fl.lane = s.tmux_session OR fl.base_agent_id = s.base_agent_id "
        "  ORDER BY (fl.lane = s.tmux_session) DESC "
        "  LIMIT 1"
        ") l ON true "
        "LEFT JOIN LATERAL ("
        "  SELECT subject, created_at FROM agent_messages m "
        "  WHERE m.from_agent = s.base_agent_id "
        "  ORDER BY m.id DESC LIMIT 1"
        ") act ON true "
        # The three coordinators (cai, hub, Nazim) have their OWN cards in the
        # Coordinators section; they self-register a tmux_session in agent_status
        # too, so without this they ALSO render as worker-lane cards. That is not
        # just clutter: a coordinator (e.g. cai) rendered as both a coord card AND
        # a collapsed idle-lane card produces TWO .peek[data-peekbox="cai"] nodes,
        # and currentPeekBox() grabs the LAST (the hidden lane one) — so tapping
        # cai's coordinator card opened a peek on an invisible card and looked
        # dead (operator 2026-08-02: "cant peek on cai"). Excluding them here is
        # the single fix.
        # cc-fleet-health joins the coordinator cards (op#9770), so exclude it here
        # too — else it renders as BOTH a coord card and a lane card (the twin-peek
        # bug the exclusion above prevents for cai/hub/Nazim).
        # cc-finance (Head of Revenue) joins the coordinator cards too (see
        # build_coordinators_query), so exclude it here for the same reason —
        # else it renders as BOTH a coord card and a lane card (twin-peek bug).
        # Coordinator set is the single canonical list (coordinators.py); a new
        # coordinator is added there ONCE, not re-typed into every NOT IN / VALUES.
        "WHERE s.base_agent_id NOT IN " + coordinators.exclusion_sql() + " "
        # DISTINCT ON needs the dedup key first; freshest heartbeat wins the session.
        "ORDER BY COALESCE(s.tmux_session, s.agent_id), s.last_heartbeat DESC NULLS LAST "
        ") d "
        # Restore the display order after dedup (working-first re-sort happens in app.py).
        "ORDER BY d.base_agent_id, d.agent_id"
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
    """The coordinator/always-on brains (orch hub + Nazim + cai + the SRE
    cc-fleet-health, op#9770) - not lanes, so absent from fleet_lanes. The SRE is
    added so its OWN context% + host badge render here (it used to show neither);
    it is excluded from the lane + context-bloat sections to avoid a double card.
    Liveness = freshest of their latest bus row or
    outbound operator message; activity = latest bus subject. tmux_session names
    the body's live pane so the console can peek it like a lane (operator #3672) —
    'orch' is the hub session on the VPS; 'nazim' is Nazim's console session (on
    the Mac Mini, tmux 'nazim' — confirmed by orch-console 2026-08-03 #15049;
    peekable when that pane is in the local list-sessions); 'cai' is the
    governance node (Mini).

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
        # #25436: the session behind the ctx_tokens reading above, plus the identity's
        # ABSOLUTE freshest session (any context). When they differ the coordinator
        # recycled (e.g. cc-quality/cc-fleet-health reset) -> the reading is a frozen
        # pre-reset ghost and the card shows OFF. Supersession, not staleness.
        "  (SELECT cs.session_id FROM cc_session_costs cs "
        "     WHERE cs.cc_identity = c.agent_id AND cs.latest_context_tokens IS NOT NULL "
        "     ORDER BY COALESCE(cs.ended_at, cs.created_at) DESC LIMIT 1) AS ctx_session_id, "
        "  (SELECT cs.session_id FROM cc_session_costs cs "
        "     WHERE cs.cc_identity = c.agent_id "
        "     ORDER BY COALESCE(cs.ended_at, cs.created_at) DESC, cs.id DESC LIMIT 1) AS ctx_current_session_id, "
        "  (SELECT a.auth_fp FROM agent_status a "
        "     WHERE a.base_agent_id = c.agent_id AND a.auth_fp IS NOT NULL "
        "     ORDER BY a.last_heartbeat DESC NULLS LAST LIMIT 1) AS auth_fp, "
        "  (SELECT a.auth_account FROM agent_status a "
        "     WHERE a.base_agent_id = c.agent_id AND a.auth_fp IS NOT NULL "
        "     ORDER BY a.last_heartbeat DESC NULLS LAST LIMIT 1) AS auth_account, "
        # Physical host for the 📍 host badge (mirrors auth_fp): the freshest
        # self-registered agent_status.host, falling back to the static hint in the
        # VALUES table for a body with no agent_status row (the cross-host hub is on
        # the VPS but never self-registers here -> host stays NULL without the hint).
        "  COALESCE( "
        "    (SELECT a.host FROM agent_status a "
        "       WHERE a.base_agent_id = c.agent_id AND a.host IS NOT NULL "
        "       ORDER BY a.last_heartbeat DESC NULLS LAST LIMIT 1), "
        "    c.host_hint) AS host, "
        "  LEAST( "
        "    (SELECT round(extract(epoch FROM (now()-created_at)))::int FROM agent_messages m "
        "       WHERE m.from_agent = c.agent_id ORDER BY m.id DESC LIMIT 1), "
        "    (SELECT round(extract(epoch FROM (now()-created_at)))::int FROM operator_messages o "
        "       WHERE o.direction = 'outbound' AND o.tag = c.op_tag ORDER BY o.id DESC LIMIT 1) "
        "  ) AS last_seen_s "
        # host_hint: static physical-host fallback for a body with no agent_status
        # row. Only the cross-host hub (VPS) is asserted; the others self-register
        # (or are legitimately host-less), so leave them NULL rather than guess.
        # host_hint per body: the hub is on the VPS; cai, Nazim, and the SRE
        # (cc-fleet-health) all run on the Mac Mini (op#9770 — Mini hint for the
        # three Mini bodies, mirroring the hub's VPS hint). Still a FALLBACK only:
        # a self-registered agent_status.host always wins over the hint.
        # Coordinator rows come from the single canonical list (coordinators.py),
        # projected to the 6 cols this card needs (op_tag + tmux_session included
        # for the operator-message liveness LEAST + local pane peek).
        "FROM (" + coordinators.values_sql(
            ["agent_id", "short", "role_label", "op_tag", "tmux_session", "host_hint"]
        ) + ") AS c(agent_id, short, role_label, op_tag, tmux_session, host_hint) "
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


def build_pool_usage_query() -> Tuple[str, list]:
    """Latest Max weekly/5h usage per pool for the console header (op#9770).

    The SRE's weekly_limit_monitor UPSERTs one row per pool (Musa, Syed) into
    `pool_usage` each poll; the console reads it here and renders `pct_7d` up top.
    `updated_age_s` is the reading's freshness — if the monitor stalls, the client
    can grey the number out rather than show a frozen one (never show stale info)."""
    # op#12617: pace/projected_pct/runway_days are ADDITIVE (nullable) — the pace
    # layer's LATEST metrics for the header. Back-compat: older clients ignore the
    # extra keys; runway NULL == 'not burning / no runway concern'.
    sql = (
        "SELECT pool, pct_7d, pct_5h, resets_at, status_7d, "
        "  pace, projected_pct, runway_days, "
        "  round(extract(epoch FROM (now() - updated_at)))::int AS updated_age_s "
        "FROM pool_usage ORDER BY pool"
    )
    return sql, []


def fetch_pool_usage() -> List[dict]:
    sql, params = build_pool_usage_query()
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
        "SELECT id, ask, status, op_ref, note, sort_order, done_when, "
        "  round(extract(epoch FROM (now()-updated_at)))::int AS updated_age_s "
        "FROM operator_backlog "
        # done = history; dropped = operator swiped it away (op 2026-08-02). Both
        # stay in-table but leave the live "Your asks" plate.
        "WHERE status NOT IN ('done', 'dropped') "
        # sort_order is the PRIMARY key so the operator's swipe-to-prioritise
        # order IS his worklist (op 2026-08-02: "swipe right ... that is the
        # backlog you should work on"). A swipe-right sets sort_order below the
        # current min, floating that ask to the absolute top; status is only a
        # tiebreak + a per-card chip now, no longer the section grouping.
        "ORDER BY sort_order, "
        "  CASE status WHEN 'needs_you' THEN 0 WHEN 'in_progress' THEN 1 "
        "    WHEN 'parked' THEN 3 ELSE 4 END, id"
    )
    return sql, []


def fetch_backlog() -> List[dict]:
    sql, params = build_backlog_query()
    return _query(sql, params)


def build_asks_query() -> Tuple[str, list]:
    """The operator's LIVE "Your asks" board (op#13250 — replaces the stale
    operator_backlog "Your asks"). One row per OPEN operator_asks ledger row
    (migration 044), joined to the LATEST agent_messages on its thread.

    Status is DERIVED LIVE in SQL on every /api/fleet poll — NEVER stored — so it
    structurally cannot go stale (that staleness IS the bug operator_backlog had):

      on_nazim      thread_id IS NULL         -> captured, not yet delegated
      needs_you     newest thread msg is a reply BACK to orch-console,
                    requires_response AND responded_at IS NULL
                                              -> lane bounced a decision to the operator
      delegate_done responded_at IS NOT NULL  -> lane replied (REVIEW — not auto-done)
      in_progress   read_at IS NOT NULL       -> lane opened it, working
      pending       else                      -> delegated, lane hasn't picked it up

    `updated_age_s` is the age of the REAL last bus movement on the thread (or the
    ask itself if undelegated) — so a genuinely quiet thread looks quiet honestly,
    never hidden behind an edited timestamp. `asked_age_s` is the age of the ask.

    Ordered needs_you FIRST (pinned red hero, mirrors the mockup), then on_nazim,
    then freshest movement. Only OPEN asks (closed_at IS NULL): a delegate reply is
    NOT done — only the operator's swipe-to-confirm (closed_at) closes an ask."""
    sql = (
        "WITH latest AS ("
        "  SELECT DISTINCT ON (thread_id) "
        "         thread_id, from_agent, to_agent, requires_response, "
        "         responded_at, read_at, created_at "
        "  FROM agent_messages "
        "  WHERE thread_id IS NOT NULL AND is_test IS NOT TRUE "
        "  ORDER BY thread_id, id DESC"
        ") "
        "SELECT a.id, a.ask, a.delegated_to, "
        "  CASE "
        "    WHEN a.thread_id IS NULL                                THEN 'on_nazim' "
        "    WHEN l.from_agent <> 'orch-console' "
        "         AND l.to_agent = 'orch-console' "
        "         AND l.requires_response AND l.responded_at IS NULL THEN 'needs_you' "
        "    WHEN l.responded_at IS NOT NULL                         THEN 'delegate_done' "
        "    WHEN l.read_at IS NOT NULL                              THEN 'in_progress' "
        "    ELSE                                                         'pending' "
        "  END AS status, "
        "  round(extract(epoch FROM (now() - COALESCE(l.created_at, a.created_at))))::int AS updated_age_s, "
        "  round(extract(epoch FROM (now() - a.created_at)))::int AS asked_age_s "
        "FROM operator_asks a "
        "LEFT JOIN latest l ON l.thread_id = a.thread_id "
        "WHERE a.closed_at IS NULL "
        "ORDER BY "
        "  CASE "
        "    WHEN l.from_agent <> 'orch-console' AND l.to_agent = 'orch-console' "
        "         AND l.requires_response AND l.responded_at IS NULL THEN 0 "
        "    WHEN a.thread_id IS NULL                                THEN 1 "
        "    WHEN l.responded_at IS NOT NULL                         THEN 2 "
        "    ELSE                                                         3 "
        "  END, "
        "  COALESCE(l.created_at, a.created_at) DESC "
        "LIMIT 100"
    )
    return sql, []


def fetch_asks() -> List[dict]:
    sql, params = build_asks_query()
    return _query(sql, params)


def build_inbox_backlog_query() -> Tuple[str, list]:
    """The DRAIN-BOARD data source (fc-v52): every body's UNHANDLED bus inbox.

    A body drains its mail by stamping `read_at` (and `responded_at` for a
    requires_response row) — so `read_at IS NULL` is the live "still pending"
    set. This feeds the per-body work-board that SHRINKS on the normal
    /api/fleet poll as lanes catch up. Combines BOTH sources the operator asked
    for in ONE read: (a) organic bus traffic addressed to the body, and (b)
    console-assigned items (from_agent='orch-console'), which are just real bus
    rows to that body — so an assignment closes the loop and drains identically.

    Excludes: is_test (drill traffic never counts as real work), and the
    operator/musa pseudo-inboxes (those are the operator's own thread, not a
    fleet body's drain queue). Flags `assigned` (console-origin) and
    `needs_response` (a requires_response row still unanswered) so the board can
    badge them. Capped so a pathological backlog can't blow the mobile payload;
    the per-body grouping + trimming happens in app._drain_board."""
    sql = (
        "SELECT id, to_agent, from_agent, subject, message_type, priority, "
        "  requires_response, "
        "  (requires_response AND responded_at IS NULL) AS needs_response, "
        "  (from_agent = 'orch-console') AS assigned, "
        "  round(extract(epoch FROM (now()-created_at)))::int AS age_s "
        "FROM agent_messages "
        "WHERE read_at IS NULL "
        "  AND is_test IS NOT TRUE "
        "  AND lower(to_agent) NOT IN ('musa', 'operator') "
        "ORDER BY to_agent, id DESC "
        "LIMIT 400"
    )
    return sql, []


def fetch_inbox_backlog() -> List[dict]:
    sql, params = build_inbox_backlog_query()
    return _query(sql, params)


# NOTE on backlog WRITES (operator swipe): the console DB session is read-only by
# construction (SELECT-only role + conn.read_only) — a fault here structurally
# cannot write. So swipe-drop / swipe-prioritise do NOT write from here; the POST
# handler shells out to scripts/backlog_swipe.py (writable orchestrator env),
# exactly the vetted-script pattern /api/reset already uses. This module stays
# read-only.


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
        # DISTINCT ON (cc_identity, sub_tag) — obs-1 (op#10550): a multi-instance
        # FAMILY (the irsyad perimeter: one cc_identity 'cc-irsyad', distinct
        # sub_tags irsyad/-coord/-prog1/-prog2 == each instance's tmux_session)
        # writes ONE cc_session_costs row PER sub_tag. DISTINCT ON (cc_identity)
        # alone collapsed them to a single row, so all four lane cards folded in
        # the SAME gauge (operator: "4 identical ctx gauges"). Keying the dedup on
        # (cc_identity, sub_tag) gives each instance its own current-context row;
        # a solo lane has sub_tag=NULL -> exactly one row, unchanged. `sub_tag`
        # rides through so the client maps each gauge to its instance by
        # tmux_session (== sub_tag), falling back to the base id for solo lanes.
        "SELECT DISTINCT ON (cc_identity, sub_tag) "
        "  cc_identity, "
        "  sub_tag, "
        # #25436: the session that produced THIS (freshest-with-context) reading,
        # plus the identity's ABSOLUTE freshest session (any latest_context_tokens,
        # so a just-reset session that has not written context yet still counts).
        # When they differ the body recycled -> this reading is a frozen pre-reset
        # ghost and the app shows it OFF. Supersession, not staleness, is the signal.
        "  session_id, "
        "  (SELECT c2.session_id FROM cc_session_costs c2 "
        "     WHERE c2.cc_identity = cc_session_costs.cc_identity "
        "       AND c2.sub_tag IS NOT DISTINCT FROM cc_session_costs.sub_tag "
        "     ORDER BY COALESCE(c2.ended_at, c2.created_at) DESC, c2.id DESC "
        "     LIMIT 1) AS current_session_id, "
        "  latest_context_tokens AS ctx_tokens, "
        "  input_tokens, "
        "  round(extract(epoch FROM (now() - COALESCE(ended_at, created_at))))::int AS age_s, "
        "  COALESCE(ended_at, created_at) AS latest_at, "
        # Freshest OAuth fingerprint this identity self-registered in agent_status,
        # so each context-bloat row shows the same 🔑 token badge as the lane cards
        # (operator op#9088). cc_identity == agent_status.base_agent_id for lanes.
        "  (SELECT a.auth_fp FROM agent_status a "
        "     WHERE a.base_agent_id = cc_session_costs.cc_identity AND a.auth_fp IS NOT NULL "
        "     ORDER BY a.last_heartbeat DESC NULLS LAST LIMIT 1) AS auth_fp, "
        # Physical host for the 📍 host badge (mirrors auth_fp above).
        "  (SELECT a.host FROM agent_status a "
        "     WHERE a.base_agent_id = cc_session_costs.cc_identity AND a.host IS NOT NULL "
        "     ORDER BY a.last_heartbeat DESC NULLS LAST LIMIT 1) AS host "
        "FROM cc_session_costs "
        "WHERE cc_identity NOT LIKE 'operator-%%' "
        "  AND latest_context_tokens IS NOT NULL "
        "  AND COALESCE(ended_at, created_at) > now() - interval '45 days' "
        # DISTINCT ON leading exprs must lead ORDER BY; freshest reading per
        # (identity, sub_tag) wins that instance's row.
        "ORDER BY cc_identity, sub_tag, COALESCE(ended_at, created_at) DESC"
    )
    return sql, []


def fetch_context_bloat() -> List[dict]:
    sql, params = build_context_bloat_query()
    return _query(sql, params)


# op#13050-B: the FRESH pane-truth feed (CC's live "/clear to save {N}k" hint,
# published Mini->DB by the auto_recycle pane pass). This is the gauge-INDEPENDENT
# ground truth for the honest header + top-bloat glance — the DB context gauge lies
# for idle/mis-mapped workers (that IS the op#13050 bug), so we read pane_context and
# NEVER fall back to the gauge. A row older than the TTL is treated as absent (UNKNOWN)
# so a dead publisher fails SAFE, never a stale false-green. TTL tunable without redeploy.
# MUST exceed the publisher's cadence (the auto_recycle daemon runs every 300s): 660s
# tolerates one fully-missed cycle + margin, so a healthy feed never flaps to UNKNOWN
# between refreshes, while a genuinely-down publisher (>2 cycles dark) still trips it.
PANE_TTL_S = int(os.environ.get("CONSOLE_PANE_TTL_S", "660"))


_HAS_PCT_COL: Optional[bool] = None


def _pane_context_has_pct() -> bool:
    """Feature-detect the pane_context.pct column (op#13186 mig-043), cached once True.
    If the column is absent (mig-043 not yet applied / rolled back), the console degrades
    to the pct-less query below — falling back to pane_k, exactly the pre-op#13186
    behavior — so a migration hiccup can NEVER dark the whole health feed (cc-quality
    GATE-4 blocker 2). A transient information_schema failure returns False UNcached so
    the next poll re-checks."""
    global _HAS_PCT_COL
    if _HAS_PCT_COL:
        return True
    try:
        r = _query("SELECT 1 FROM information_schema.columns WHERE "
                   "table_name='pane_context' AND column_name='pct' LIMIT 1", [])
    except Exception:
        return False  # transient — do not cache; degrade to pane_k this poll
    _HAS_PCT_COL = bool(r)
    return _HAS_PCT_COL


def build_pane_context_query(ttl_s: int = None, with_pct: "bool | None" = None) -> Tuple[str, list]:
    ttl = PANE_TTL_S if ttl_s is None else ttl_s
    if with_pct is None:
        with_pct = _pane_context_has_pct()
    # pct-less path selects NULL::smallint AS pct so the row SHAPE is identical (pct key
    # present but None) -> downstream _pane_entry fails closed to pane_k, feed stays alive.
    pct_col = "pct" if with_pct else "NULL::smallint AS pct"
    pct_order = "pct DESC NULLS LAST, " if with_pct else ""
    sql = (
        f"SELECT session, base, pane_k, {pct_col}, idle_verdict, host, "
        "  round(extract(epoch FROM (now() - updated_at)))::int AS age_s "
        "FROM pane_context "
        "WHERE updated_at > now() - make_interval(secs => %s) "
        # op#13186: pct (the cliff truth) leads the raw order so a maxed lane with a NULL
        # pane_k sorts first; the console re-sorts by computed pct anyway (belt+braces).
        f"ORDER BY {pct_order}pane_k DESC NULLS LAST"
    )
    return sql, [ttl]


def fetch_pane_context(ttl_s: int = None) -> List[dict]:
    sql, params = build_pane_context_query(ttl_s)
    return _query(sql, params)
