"""Hosted (public, VPS) fleet-console data layer — field-MINIMIZED + SCRUBBED.

The operator is in Abu Dhabi; the Mini console is tailnet-only in Singapore, so
every request is a cross-continent Tailscale relay (chronic drops). This module
backs a DB-backed READ-ONLY console that runs on the VPS and is exposed via an
interim ngrok tunnel + bearer token (cai: cond-0 proven, cond-2 field-list signed
off #15362, interim allowed w/ 2026-08-15 Access time-box).

SECURITY MODEL (why this file is small + paranoid):
  * cond-0: reads ONLY via CONSOLE_DB_URL (the console_readonly role — SELECT-only
    + default_transaction_read_only; CAI-511-proven). NEVER a service-role DSN.
  * cond-2: exposes the SIGNED-OFF fields ONLY — metadata, no bodies, no reasoning,
    no auth_fp/auth_account/scope_paths/blocked_on_description. Enumerated columns
    only; SELECT * is never used, so a new sensitive column can't leak by default.
  * SCRUB: every exposed free-text value passes through scrub() — currency/$ amounts
    always, plus a configurable SENSITIVE_TERMS list (external client/org/person
    identifiers). Proven on RENDERED output (see prove_scrub / __main__), not just
    "the regex exists" — cai's CAI-702 mask-in-shipped-bytes discipline.

Run `python -m nervous_system.console.hosted_view --prove` for the rendered-scrub
proof to send cai before go-live.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

# ── scrub configuration ──────────────────────────────────────────────────────
# Currency / money amounts, always scrubbed. Covers S$1.7M, $1,700, RM500,
# "1.5k SGD", "2 million ringgit", etc.
_MONEY_RE = re.compile(
    r"""(?ix)
    (?: (?:S\$|US\$|\$|RM|SGD|MYR|USD|AED|£|€) \s?\d[\d,]*(?:\.\d+)?\s?[kmb]? )   # $1.7M / RM500
    | (?: \b\d[\d,]*(?:\.\d+)?\s?[kmb]?\s?(?:ringgit|dollars?|dirhams?|sgd|myr|usd|aed)\b )  # 500 ringgit
    | (?: \b\d[\d,]*(?:\.\d+)?\s?(?:k|m|mil|million|bil|billion)\b )              # 1.7M (bare, near money)
    """,
)

# External client / org / person identifiers whose *relationship* must not leak
# behind the token. CONFIGURABLE via env CONSOLE_SCRUB_TERMS (comma-sep, appended).
# Deliberately does NOT include the operator's internal project/feature codenames
# (irsyad, tabung, cosem, storefront, shipforge, ihsanos) — he needs those to read
# his own board, and they leak far less than $-figures / named end-clients / PII.
# >>> This split is the one policy call flagged to cai on the rendered sample. <<<
_DEFAULT_SENSITIVE_TERMS = [
    "gazzabyte", "elly", "adcda", "sushitei", "dookana", "hadi",
    "nahar", "hariz", "zuremi", "desmond shen", "shen", "hafiz",
    "al-blooshi", "blooshi",
]

# Emirates-ID / military-ID / long digit runs (PII) — always scrubbed.
_PII_DIGITS_RE = re.compile(r"\b\d{6,}\b")

# Full UUIDs (user_ids etc.) — always scrubbed. Only the unambiguous 8-4-4-4-12
# form, so we don't clobber exposed git shas (7-40 hex) elsewhere.
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)


def _sensitive_terms() -> List[str]:
    extra = [t.strip() for t in os.environ.get("CONSOLE_SCRUB_TERMS", "").split(",") if t.strip()]
    return _DEFAULT_SENSITIVE_TERMS + extra


def _term_res() -> List[re.Pattern]:
    # word-ish boundaries, case-insensitive; multiword terms handled literally
    return [re.compile(r"(?i)(?<![\w-])" + re.escape(t) + r"(?![\w-])") for t in _sensitive_terms()]


# Subjects/titles describing a LIVE security issue get WHOLLY redacted (cai
# CAI-RESP-720 hardening): a leaked token must not hand an attacker a map of live
# vulns / auth-bypasses / RLS-holes. Strong indicators only (avoid false-hits on
# benign 'leak-check passed' etc.).
_SECURITY_RE = re.compile(
    r"""(?ix)
    \bSEV-?\d | \bCVE-\d | \bRLS\b | \bSECDEF\b
    | auth[\s._-]?bypass | privilege[\s._-]?escalation | \bpriv[\s._-]?esc\b
    | \bexploit\b | \bvuln\w* | \b(sql[\s-]?)?injection\b | \bXSS\b | \bCSRF\b | \bSSRF\b
    | \bbackdoor\b | unauthorized[\s-]?access | security[\s-]?hole
    """,
)


def scrub(text: Optional[str]) -> Optional[str]:
    """Redact money amounts, PII digit-runs, UUIDs, and sensitive client/org/person
    identifiers from a free-text value. Best-effort by nature — ALWAYS validate
    on rendered output (prove_scrub), never trust the regex blind (cai)."""
    if not text:
        return text
    out = _UUID_RE.sub("[uuid]", text)
    out = _MONEY_RE.sub("[amount]", out)
    out = _PII_DIGITS_RE.sub("[id]", out)
    for rx in _term_res():
        out = rx.sub("[client]", out)
    return out


def scrub_field(text: Optional[str]) -> Optional[str]:
    """scrub() + WHOLE-field redaction of live-security-issue descriptions to
    '[security item]' (cai CAI-RESP-720). Use for every exposed free-text field."""
    if not text:
        return text
    if _SECURITY_RE.search(text):
        return "[security item]"
    return scrub(text)


# ── coarse current_task genericiser (cond-2 (a): coarse label, not raw) ──────
_ACTIVITY = [
    (re.compile(r"(?i)session-launch"), "launching"),
    (re.compile(r"(?i)\b(build|building|implement|coding|writ)"), "building"),
    (re.compile(r"(?i)\b(verify|verifying|proving|proven|test)"), "verifying"),
    (re.compile(r"(?i)\b(deploy|deploying|ship|merg)"), "deploying"),
    (re.compile(r"(?i)\b(review|reviewing|assess)"), "reviewing"),
    (re.compile(r"(?i)\b(idle|standby|waiting|await)"), "idle/waiting"),
    (re.compile(r"(?i)\b(reconcil|drain|inbox|sweep)"), "reconciling"),
]


def coarse_task(current_task: Optional[str]) -> Optional[str]:
    """Reduce a raw current_task to a coarse activity label, THEN scrub — so no
    project/$/client specifics ride along even if a verb match is missed."""
    if not current_task:
        return None
    for rx, label in _ACTIVITY:
        if rx.search(current_task):
            return label
    return "active"  # unknown → generic, never the raw string


# ── minimized queries (enumerated columns ONLY — never SELECT *) ─────────────
def _dsn() -> str:
    dsn = os.environ.get("CONSOLE_DB_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("no CONSOLE_DB_URL for hosted view")
    return dsn


def build_minimized_payload(conn) -> Dict[str, Any]:
    """Return the scrubbed, field-minimized fleet view. `conn` is a psycopg
    connection on the READ-ONLY role. Only the cai-signed-off columns are read."""
    cur = conn.cursor()

    cur.execute("SELECT pool, pct_7d, pct_5h, resets_at, status_7d, updated_at FROM pool_usage ORDER BY pool")
    pools = [dict(pool=r[0], pct_7d=int(r[1]), pct_5h=int(r[2]),
                  resets_at=str(r[3]), status=r[4], updated_at=str(r[5])) for r in cur.fetchall()]

    cur.execute("""SELECT base_agent_id, status, current_task, last_heartbeat, host,
                          last_commit_repo, last_commit_sha
                   FROM agent_status
                   WHERE last_heartbeat > now() - interval '2 hours'
                   ORDER BY last_heartbeat DESC""")
    agents = [dict(agent=r[0], status=r[1], activity=coarse_task(r[2]),
                   last_heartbeat=str(r[3]), host=r[4],
                   repo=r[5], sha=(r[6] or "")[:8]) for r in cur.fetchall()]

    cur.execute("""SELECT lane, title, status, priority_rank, updated_at
                   FROM lane_tasks WHERE status <> 'done'
                   ORDER BY priority_rank NULLS LAST, updated_at DESC LIMIT 60""")
    tasks = [dict(lane=r[0], title=scrub_field(r[1]), status=r[2],
                  priority=r[3], updated_at=str(r[4])) for r in cur.fetchall()]

    cur.execute("""SELECT ask, status, op_ref, done_when FROM operator_backlog
                   WHERE status NOT IN ('done','dropped') ORDER BY sort_order NULLS LAST, id DESC""")
    backlog = [dict(ask=scrub_field(r[0]), status=r[1], op_ref=r[2], done_when=scrub_field(r[3])) for r in cur.fetchall()]

    cur.execute("""SELECT subject, from_agent, to_agent, priority, read_at, created_at
                   FROM agent_messages ORDER BY created_at DESC LIMIT 40""")
    bus = [dict(subject=scrub_field(r[0]), from_agent=r[1], to_agent=r[2], priority=r[3],
                read=bool(r[4]), created_at=str(r[5])) for r in cur.fetchall()]

    return dict(pools=pools, agents=agents, tasks=tasks, backlog=backlog, bus=bus)


# ── Mini-shape CLONE (DB-only, scrubbed) — feeds the cloned fleet.js UI ───────
# The hosted console serves the Mini's fleet.html/fleet.js verbatim (a visual
# clone), so /api/fleet must return the SAME payload SHAPE that fleet.js reads:
#   { pulse{working,idle,offline,flagged,needs_you}, needs_you[], backlog[],
#     coordinators[], lanes[], context_bloat[], queue[], pool_usage[], deploys[] }
# reproduced DB-ONLY (no tmux pane-captures — impossible/disallowed on the VPS)
# and SCRUBBED (every free-text value through scrub_field). Where the Mini derives
# a field from a live pane (the working/idle/offline bucket, the peek), we degrade:
#   * bucket -> a DB-only heuristic (heartbeat freshness + recent bus activity);
#   * peek/peekable -> off (no live pane on the public host).
# Enumerated columns ONLY (never SELECT *), same read-only-role contract.

_CTX_WINDOW = int(os.environ.get("CONSOLE_CTX_WINDOW", "1000000"))
_CTX_SOFT = 0.60
_CTX_HARD = 0.80
_CTX_STALE_DROP_S = int(os.environ.get("CONSOLE_CTX_STALE_DROP_S", str(48 * 3600)))
_LANE_STALE_DROP_S = int(os.environ.get("CONSOLE_LANE_STALE_DROP_S", str(6 * 3600)))
# DB-only bucket thresholds (no live pane): a heartbeat older than _HB_DEAD_S reads
# as no live process -> offline; a lane that posted to the bus within _ACTIVE_RECENT_S
# reads as working; otherwise idle. Best-effort — the bucket only drives a dot
# colour + label and degrades safely.
_HB_DEAD_S = int(os.environ.get("CONSOLE_HB_DEAD_S", "900"))
_ACTIVE_RECENT_S = int(os.environ.get("CONSOLE_ACTIVE_RECENT_S", "1800"))
_COORD_IDS = ("cai", "cc-orchestrator", "orch-console", "cc-fleet-health")


def _ctx_level(ctx) -> tuple:
    """(pct, level) of the model window for a current-context token count, or
    (None, None) if missing / non-positive / impossibly large. Mirrors app.py
    _ctx_level so the cloned cards use the SAME thresholds the Mini does."""
    if ctx is None or _CTX_WINDOW <= 0:
        return (None, None)
    try:
        ctx = int(ctx)
    except (TypeError, ValueError):
        return (None, None)
    if ctx <= 0 or ctx > _CTX_WINDOW:
        return (None, None)
    frac = ctx / _CTX_WINDOW
    pct = round(min(frac, 1.0) * 100)
    level = "red" if frac >= _CTX_HARD else ("amber" if frac >= _CTX_SOFT else "green")
    return pct, level


def _lane_bucket_db(hb, activity_age_s, desired_state) -> tuple:
    """DB-only working/idle/offline classification (NO live pane on the VPS)."""
    desired_up = str(desired_state or "").lower() == "up"
    if hb is None or hb > _HB_DEAD_S:
        state = "offline"
    elif activity_age_s is not None and activity_age_s < _ACTIVE_RECENT_S:
        state = "working"
    else:
        state = "idle"
    flagged = desired_up and state == "offline"
    return state, flagged


def _is_coord_id(name) -> bool:
    n = str(name or "")
    return any(n == k or n.startswith(k + "-") for k in _COORD_IDS)


# Each section is guarded independently: one signal failing (e.g. a table the
# read-only role can't see, or an empty relation) must NOT blank the whole
# aggregate — it returns [] for that section, exactly like app.py's _fleet_payload.
def _safe(fn, default):
    try:
        return fn()
    except Exception:  # noqa: BLE001 — a bad section degrades to empty, never 500s
        return default


def _clone_pool_usage(cur) -> List[Dict[str, Any]]:
    cur.execute(
        "SELECT pool, pct_7d, pct_5h, resets_at, status_7d, "
        "  round(extract(epoch FROM (now() - updated_at)))::int AS updated_age_s "
        "FROM pool_usage ORDER BY pool"
    )
    out = []
    for r in cur.fetchall():
        out.append(dict(
            pool=scrub_field(r[0]), pct_7d=(int(r[1]) if r[1] is not None else None),
            pct_5h=(int(r[2]) if r[2] is not None else None),
            resets_at=(str(r[3]) if r[3] is not None else None),
            status=r[4], updated_age_s=r[5],
        ))
    return out


def _clone_lanes(cur) -> List[Dict[str, Any]]:
    # agent_status ⋈ agents ⋈ fleet_lanes ⋈ latest-bus, DISTINCT ON the tmux
    # session (freshest heartbeat wins) — the Mini's build_lanes_query, trimmed to
    # the columns fleet.js reads. Coordinators are excluded (own cards).
    cur.execute(
        "SELECT agent_id, base_agent_id, status, current_task, tmux_session, "
        "  auth_fp, host, heartbeat_age_s, desired_state, lane, activity, activity_age_s "
        "FROM ( "
        "  SELECT DISTINCT ON (COALESCE(s.tmux_session, s.agent_id)) "
        "    s.agent_id, s.base_agent_id, s.status, s.current_task, s.tmux_session, "
        "    s.auth_fp, s.host, "
        "    round(extract(epoch FROM (now() - s.last_heartbeat)))::int AS heartbeat_age_s, "
        "    l.desired_state, l.lane, "
        "    act.subject AS activity, "
        "    round(extract(epoch FROM (now() - act.created_at)))::int AS activity_age_s "
        "  FROM agent_status s "
        # obs-2 (op#10550): PREFER the fleet_lanes row whose `lane` == this
        # instance's live tmux_session. A multi-lane FAMILY (the irsyad perimeter:
        # one base_agent_id, distinct sessions irsyad/-prog1/-prog2/-coord) shares
        # ONE base across several fleet_lanes rows, so joining by base alone let
        # DISTINCT ON attribute an ARBITRARY perimeter row's desired_state to each
        # instance (why a live instance showed 'down'/duplicated). lane==tmux_session
        # gives each instance ITS OWN row; fall back to a base match for a lane whose
        # session != its fleet_lanes.lane. (Mirrors db.py build_lanes_query exactly.)
        "  LEFT JOIN LATERAL ( "
        "    SELECT desired_state, lane FROM fleet_lanes fl "
        "    WHERE fl.lane = s.tmux_session OR fl.base_agent_id = s.base_agent_id "
        "    ORDER BY (fl.lane = s.tmux_session) DESC "
        "    LIMIT 1 "
        "  ) l ON true "
        "  LEFT JOIN LATERAL ( "
        "    SELECT subject, created_at FROM agent_messages m "
        "    WHERE m.from_agent = s.base_agent_id ORDER BY m.id DESC LIMIT 1 "
        "  ) act ON true "
        "  WHERE s.base_agent_id NOT IN "
        "    ('cai','cc-orchestrator','orch-console','cc-fleet-health') "
        "  ORDER BY COALESCE(s.tmux_session, s.agent_id), s.last_heartbeat DESC NULLS LAST "
        ") d ORDER BY d.base_agent_id, d.agent_id"
    )
    lanes = []
    for r in cur.fetchall():
        agent_id, base_agent_id, status, current_task, tmux_session = r[0], r[1], r[2], r[3], r[4]
        auth_fp, host, hb, desired_state, lane, activity, activity_age_s = r[5], r[6], r[7], r[8], r[9], r[10], r[11]
        state, flagged = _lane_bucket_db(hb, activity_age_s, desired_state)
        # Dead-instance drop (mirrors app.py op#9770): offline + not flagged +
        # heartbeat older than the stale-drop threshold = a dead lane, never render.
        if state == "offline" and not flagged and hb is not None and hb > _LANE_STALE_DROP_S:
            continue
        lanes.append(dict(
            # Internal ids can embed a client codename (e.g. cc-adcda) — scrub them.
            agent_id=scrub_field(agent_id),
            base_agent_id=scrub_field(base_agent_id),
            tmux_session=scrub_field(tmux_session),
            lane=scrub_field(lane),
            host=host,
            auth_fp=auth_fp,
            desired_state=desired_state,
            heartbeat_age_s=hb,
            activity_age_s=activity_age_s,
            activity=scrub_field(activity),
            current_task=coarse_task(current_task),  # coarse label, then generic — never raw
            bucket=state,
            flagged=flagged,
        ))
    # working first, then idle, then offline; flagged floats up within its group
    # (same ordering fleet.js expects — matches app.py's final sort).
    order = {"working": 0, "idle": 1, "offline": 2}
    lanes.sort(key=lambda l: (0 if l["flagged"] else 1, order.get(l["bucket"], 3)))
    return lanes


def _clone_coordinators(cur) -> List[Dict[str, Any]]:
    # The four always-on brains. last_seen_s/activity come from their latest bus
    # row ONLY (the Mini also LEASTs an outbound operator_messages age, but that
    # table may not live on the read-only console DB — a bus-only signal is the
    # safe DB-only degrade). ctx_tokens -> pct/level via the shared thresholds.
    cur.execute(
        "SELECT c.agent_id, c.short, c.role_label, c.host_hint, "
        "  (SELECT subject FROM agent_messages m "
        "     WHERE m.from_agent = c.agent_id ORDER BY m.id DESC LIMIT 1) AS activity, "
        "  (SELECT round(extract(epoch FROM (now()-created_at)))::int FROM agent_messages m "
        "     WHERE m.from_agent = c.agent_id ORDER BY m.id DESC LIMIT 1) AS activity_age_s, "
        "  (SELECT cs.latest_context_tokens FROM cc_session_costs cs "
        "     WHERE cs.cc_identity = c.agent_id AND cs.latest_context_tokens IS NOT NULL "
        "     ORDER BY COALESCE(cs.ended_at, cs.created_at) DESC LIMIT 1) AS ctx_tokens, "
        "  (SELECT a.auth_fp FROM agent_status a "
        "     WHERE a.base_agent_id = c.agent_id AND a.auth_fp IS NOT NULL "
        "     ORDER BY a.last_heartbeat DESC NULLS LAST LIMIT 1) AS auth_fp, "
        "  COALESCE( "
        "    (SELECT a.host FROM agent_status a "
        "       WHERE a.base_agent_id = c.agent_id AND a.host IS NOT NULL "
        "       ORDER BY a.last_heartbeat DESC NULLS LAST LIMIT 1), c.host_hint) AS host "
        "FROM (VALUES "
        "  ('cc-orchestrator','Hub','Orchestrates the fleet','VPS'), "
        "  ('orch-console','Nazim','CTO console / 2nd coordinator','Mini'), "
        "  ('cai','cai','governance / strategic node','Mini'), "
        "  ('cc-fleet-health','SRE','fleet reliability / health','Mini') "
        ") AS c(agent_id, short, role_label, host_hint) "
        "ORDER BY c.agent_id"
    )
    out = []
    for r in cur.fetchall():
        agent_id, short, role_label = r[0], r[1], r[2]
        activity, activity_age_s, ctx_tokens, auth_fp, host = r[4], r[5], r[6], r[7], r[8]
        ctx_pct, ctx_level = _ctx_level(ctx_tokens)
        out.append(dict(
            agent_id=agent_id,           # fixed singleton ids — no client term
            short=short, role_label=role_label,
            activity=scrub_field(activity),
            activity_age_s=activity_age_s,
            last_seen_s=activity_age_s,  # bus-only liveness signal (DB-only degrade)
            auth_fp=auth_fp, host=host,
            ctx_pct=ctx_pct, ctx_level=ctx_level,
            peekable=False,              # no live pane on the public host -> no peek affordance
        ))
    return out


def _clone_context_bloat(cur) -> List[Dict[str, Any]]:
    cur.execute(
        # obs-1 (op#10550): DISTINCT ON (cc_identity, sub_tag) so each FAMILY
        # instance (irsyad perimeter: one cc_identity, one cc_session_costs row per
        # sub_tag == tmux_session) gets its OWN current-context row instead of the
        # four cards folding in one shared gauge. Solo lane = sub_tag NULL = one row,
        # unchanged. `sub_tag` rides through so fleet.js maps each gauge to its
        # instance. (Mirrors db.py build_context_bloat_query exactly.)
        "SELECT DISTINCT ON (cc_identity, sub_tag) cc_identity, sub_tag, latest_context_tokens AS ctx_tokens, "
        "  round(extract(epoch FROM (now() - COALESCE(ended_at, created_at))))::int AS age_s, "
        "  (SELECT a.auth_fp FROM agent_status a "
        "     WHERE a.base_agent_id = cc_session_costs.cc_identity AND a.auth_fp IS NOT NULL "
        "     ORDER BY a.last_heartbeat DESC NULLS LAST LIMIT 1) AS auth_fp, "
        "  (SELECT a.host FROM agent_status a "
        "     WHERE a.base_agent_id = cc_session_costs.cc_identity AND a.host IS NOT NULL "
        "     ORDER BY a.last_heartbeat DESC NULLS LAST LIMIT 1) AS host "
        "FROM cc_session_costs "
        "WHERE cc_identity NOT LIKE 'operator-%%' "
        "  AND latest_context_tokens IS NOT NULL "
        "  AND COALESCE(ended_at, created_at) > now() - interval '45 days' "
        # DISTINCT ON leading exprs must lead ORDER BY; freshest per (identity, sub_tag).
        "ORDER BY cc_identity, sub_tag, COALESCE(ended_at, created_at) DESC"
    )
    out = []
    for r in cur.fetchall():
        ident, sub_tag, ctx_tokens, age_s, auth_fp, host = r[0], r[1], r[2], r[3], r[4], r[5]
        if _is_coord_id(ident):
            continue  # coordinators live on their own cards
        if age_s is not None and age_s > _CTX_STALE_DROP_S:
            continue  # dead identity's frozen reading — never show stale
        pct, level = _ctx_level(ctx_tokens)
        if pct is None:
            continue  # bad data — not a real current-context reading
        out.append(dict(
            # sub_tag = the instance discriminator (obs-1, op#10550): fleet.js keys
            # laneCtxIndex by (agent, sub_tag) so each family card folds in its own
            # gauge. NULL for a solo lane (client falls back to the base id).
            agent=scrub_field(ident), sub_tag=scrub_field(sub_tag),
            ctx_tokens=int(ctx_tokens), window=_CTX_WINDOW,
            pct=pct, level=level, age_s=age_s, auth_fp=auth_fp, host=host,
        ))
    out.sort(key=lambda x: x["pct"], reverse=True)
    return out


def _clone_queue(cur) -> List[Dict[str, Any]]:
    cur.execute(
        "SELECT lane, title, status, "
        "  round(extract(epoch FROM (now() - updated_at)))::int AS updated_age_s, "
        "  round(extract(epoch FROM (now() - started_at))/60)::int AS elapsed_min, "
        "  (sla_minutes IS NOT NULL AND started_at IS NOT NULL "
        "     AND now() > started_at + (sla_minutes || ' minutes')::interval) AS over_sla "
        "FROM lane_tasks WHERE status <> 'done' "
        "ORDER BY lane, (status = 'blocked'), id"
    )
    return [dict(lane=scrub_field(r[0]), title=scrub_field(r[1]), status=r[2],
                 updated_age_s=r[3], elapsed_min=r[4], over_sla=bool(r[5]))
            for r in cur.fetchall()]


def _clone_backlog(cur) -> List[Dict[str, Any]]:
    cur.execute(
        "SELECT id, ask, status, op_ref, note, done_when "
        "FROM operator_backlog WHERE status NOT IN ('done','dropped') "
        "ORDER BY sort_order, "
        "  CASE status WHEN 'needs_you' THEN 0 WHEN 'in_progress' THEN 1 "
        "    WHEN 'parked' THEN 3 ELSE 4 END, id"
    )
    return [dict(id=r[0], ask=scrub_field(r[1]), status=r[2], op_ref=scrub_field(r[3]),
                 note=scrub_field(r[4]), done_when=scrub_field(r[5]))
            for r in cur.fetchall()]


def _clone_deploys(cur) -> List[Dict[str, Any]]:
    cur.execute(
        "SELECT workstream, repo, stage, url, "
        "  round(extract(epoch FROM (now() - updated_at)))::int AS updated_age_s "
        "FROM deploy_status ORDER BY (stage = 'blocked') DESC, updated_at DESC"
    )
    # url scrubbed too: a preview URL can carry a client subdomain (adcda.vercel.app).
    return [dict(workstream=scrub_field(r[0]), repo=scrub_field(r[1]), stage=r[2],
                 url=scrub_field(r[3]), updated_age_s=r[4])
            for r in cur.fetchall()]


def _clone_needs_you(cur) -> List[Dict[str, Any]]:
    # The NEEDS-YOU hero feed. Bodies are NEVER emitted (cai: no reasoning-bodies):
    # `what` is the SUBJECT only (never left(body,N)); a missing subject -> a
    # generic placeholder. Everything free-text passes scrub_field.
    cur.execute(
        "SELECT * FROM ("
        "  SELECT 'response' AS kind, from_agent AS who, 'needs response' AS tag, "
        "         COALESCE(NULLIF(subject,''),'(no subject)') AS what, "
        "         round(extract(epoch FROM (now()-created_at)))::int AS age_s, "
        "         COALESCE(priority,'P2') AS priority, 'operator' AS audience "
        "  FROM agent_messages "
        "  WHERE requires_response AND responded_at IS NULL AND skipped_at IS NULL "
        "    AND lower(to_agent) IN ('musa','operator') "
        "    AND created_at > now() - interval '14 days' "
        "  UNION ALL "
        "  SELECT * FROM ("
        "    SELECT DISTINCT ON (from_agent) 'response' AS kind, from_agent AS who, "
        "           'needs response' AS tag, "
        "           COALESCE(NULLIF(subject,''),'(no subject)') AS what, "
        "           round(extract(epoch FROM (now()-created_at)))::int AS age_s, "
        "           COALESCE(priority,'P2') AS priority, 'fleet' AS audience "
        "    FROM agent_messages "
        "    WHERE requires_response AND responded_at IS NULL AND skipped_at IS NULL "
        "      AND to_agent = 'cc-orchestrator' AND priority IN ('P0','P1') "
        "      AND created_at > now() - interval '24 hours' "
        "    ORDER BY from_agent, created_at DESC"
        "  ) hub "
        "  UNION ALL "
        "  SELECT 'blocked_lane', agent_id, 'lane blocked', 'blocked', "
        "         round(extract(epoch FROM (now()-updated_at)))::int, 'P1', 'fleet' "
        "  FROM agent_status WHERE status = 'blocked' "
        "    AND updated_at > now() - interval '7 days' "
        "  UNION ALL "
        "  SELECT 'blocked_task', lane, 'task blocked', title, "
        "         round(extract(epoch FROM (now()-updated_at)))::int, 'P1', 'fleet' "
        "  FROM lane_tasks WHERE status = 'blocked' "
        "    AND updated_at > now() - interval '7 days' "
        "  UNION ALL "
        "  SELECT 'blocked_deploy', workstream, 'deploy blocked', workstream, "
        "         round(extract(epoch FROM (now()-updated_at)))::int, 'P0', 'fleet' "
        "  FROM deploy_status WHERE stage = 'blocked' "
        "    AND updated_at > now() - interval '7 days' "
        ") q ORDER BY (audience = 'operator') DESC, (kind LIKE 'blocked%%') DESC, "
        "             priority ASC, age_s ASC LIMIT 12"
    )
    return [dict(kind=r[0], who=scrub_field(r[1]), tag=r[2], what=scrub_field(r[3]),
                 age_s=r[4], priority=r[5], audience=r[6])
            for r in cur.fetchall()]


def build_cloned_payload(conn) -> Dict[str, Any]:
    """Return the SCRUBBED, DB-only fleet payload in the Mini SHAPE that the
    cloned fleet.js reads. `conn` is a psycopg connection on the READ-ONLY role.
    Every section is independently guarded; a failing section degrades to empty."""
    cur = conn.cursor()
    pool_usage = _safe(lambda: _clone_pool_usage(cur), [])
    lanes = _safe(lambda: _clone_lanes(cur), [])
    coordinators = _safe(lambda: _clone_coordinators(cur), [])
    context_bloat = _safe(lambda: _clone_context_bloat(cur), [])
    queue = _safe(lambda: _clone_queue(cur), [])
    backlog = _safe(lambda: _clone_backlog(cur), [])
    deploys = _safe(lambda: _clone_deploys(cur), [])
    needs_you = _safe(lambda: _clone_needs_you(cur), [])

    counts = {"working": 0, "idle": 0, "offline": 0, "flagged": 0}
    for l in lanes:
        counts[l["bucket"]] = counts.get(l["bucket"], 0) + 1
        if l["flagged"]:
            counts["flagged"] += 1
    needs_ops = sum(1 for n in needs_you if n.get("audience") == "operator")

    return dict(
        pulse={**counts, "needs_you": needs_ops},
        needs_you=needs_you,
        backlog=backlog,
        coordinators=coordinators,
        lanes=lanes,
        context_bloat=context_bloat,
        queue=queue,
        pool_usage=pool_usage,
        deploys=deploys,
    )


def _collect_strings(node, out: List[str]) -> None:
    """Recursively gather every STRING value in the payload. The scrub contract
    covers free-text ONLY — numeric telemetry columns (ctx_tokens, window, and the
    various *_age_s second-counts) are structured integers that legitimately run to
    6+ digits (a 2-day age = 172800), so a JSON-wide digit-run scan would false-hit
    on them exactly as it would on the Mini's own payload. The PII/money/UUID/term
    checks belong on the string values that passed through scrub_field."""
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for v in node.values():
            _collect_strings(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_strings(v, out)


def prove_scrub_cloned(conn) -> Dict[str, Any]:
    """The go-live proof for the CLONED payload: render the actual Mini-shape
    payload and assert no money/PII-digit-run/UUID/sensitive-term/security leak
    survives into any exposed TEXT field. Returns a report to send cai."""
    payload = build_cloned_payload(conn)
    strings: List[str] = []
    _collect_strings(payload, strings)
    text = "\n".join(strings)
    low = text.lower()
    leaks: List[str] = []
    if _MONEY_RE.search(text):
        leaks.append("money amount survived into a text field")
    if _PII_DIGITS_RE.search(text):
        leaks.append("6+ digit run (PII) survived into a text field")
    if _UUID_RE.search(text):
        leaks.append("UUID survived into a text field")
    for t in _sensitive_terms():
        if re.search(r"(?i)(?<![\w-])" + re.escape(t) + r"(?![\w-])", low):
            leaks.append(f"sensitive term '{t}' survived into a text field")
    if _SECURITY_RE.search(text):
        leaks.append("live-security-issue indicator survived into a text field")
    # sample scrubbed rows so the reviewer can see redaction actually fired
    samples = [s for s in strings if "[client]" in s or "[amount]" in s
               or "[id]" in s or "[uuid]" in s or "[security item]" in s][:8]
    return dict(leaks=leaks, clean=(not leaks), scrubbed_samples=samples,
                counts={k: (len(v) if isinstance(v, list) else v) for k, v in payload.items()})


# ── the go-live proof (cai): sensitive terms MUST NOT survive into output ────
def prove_scrub(conn) -> Dict[str, Any]:
    """Render the ACTUAL payload and assert no money/PII/sensitive-term leaks
    through into any exposed text field. Returns a report to send cai."""
    import json
    payload = build_minimized_payload(conn)
    blob = json.dumps(payload, ensure_ascii=False).lower()

    leaks: List[str] = []
    # money / PII patterns must be gone from the rendered blob
    if _MONEY_RE.search(blob):
        leaks.append("money amount survived into output")
    for t in _sensitive_terms():
        if re.search(r"(?i)(?<![\w-])" + re.escape(t) + r"(?![\w-])", blob):
            leaks.append(f"sensitive term '{t}' survived into output")
    # security-issue descriptions must be wholly redacted, not sitting in output
    if _SECURITY_RE.search(blob):
        leaks.append("live-security-issue indicator survived into output")
    # sample: rows that DID contain sensitive content pre-scrub, shown post-scrub
    samples = [t["title"] for t in payload["tasks"] if "[client]" in (t.get("title") or "")][:5]
    samples += [b["subject"] for b in payload["bus"] if "[client]" in (b.get("subject") or "")][:5]
    return dict(leaks=leaks, clean=(not leaks),
                scrubbed_samples=samples,
                counts={k: len(v) for k, v in payload.items()})


if __name__ == "__main__":
    import json
    import sys
    import psycopg
    cloned = "--clone" in sys.argv
    with psycopg.connect(_dsn(), connect_timeout=15) as c:
        rep = prove_scrub_cloned(c) if cloned else prove_scrub(c)
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    label = "CLONED SCRUB PROOF:" if cloned else "SCRUB PROOF:"
    print("\n" + label, "CLEAN ✓" if rep["clean"] else "LEAKS ✗ " + str(rep["leaks"]))
