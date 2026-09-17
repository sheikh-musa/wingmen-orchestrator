"""queue_age_watchdog (Nazim #40835): a claimable coord_dispatch_queue row that ages
past its per-priority floor (P1>30m, P2>4h) pages the pool owner (hub) AND orch-console,
re-paging every 60m until claimed. Trigger op#20774: P1 rows sat 8.5h/12h unclaimed with
ZERO pages. P3 never pages; a held row (claimed_by='hold:*') or a claimed/done row is not
claimable.
"""
import importlib

q = importlib.import_module("scripts.queue_age_watchdog")

HOUR = 3600.0
NOW = 1000 * HOUR


def row(id=1, priority="P1", title="BUILD X", family="cc-irsyad",
        age_min=45, claimed_by=None, done=False):
    return {
        "id": id, "priority": priority, "title": title, "family": family,
        "created_epoch": NOW - age_min * 60,
        "claimed_by": claimed_by,
        "done_at": "2026-09-16T00:00:00Z" if done else None,
    }


# ── P1 floor (30 min) ────────────────────────────────────────────────────────
def test_p1_just_over_30m_is_a_target_with_age_and_owners():
    got = q.queue_age_targets([row(id=9, priority="P1", age_min=31)], now=NOW, page_state={})
    assert [t["id"] for t in got] == [9]
    assert got[0]["age_min"] == 31
    assert got[0]["owners"] == ["cc-orchestrator", "orch-console"]


def test_p1_under_30m_is_not_a_target():
    assert q.queue_age_targets([row(priority="P1", age_min=29)], now=NOW, page_state={}) == []


# ── P2 floor (4 h) ───────────────────────────────────────────────────────────
def test_p2_just_over_4h_is_a_target():
    assert len(q.queue_age_targets([row(priority="P2", age_min=241)], now=NOW, page_state={})) == 1


def test_p2_under_4h_is_not_a_target():
    assert q.queue_age_targets([row(priority="P2", age_min=239)], now=NOW, page_state={}) == []


# ── not-claimable exclusions ─────────────────────────────────────────────────
def test_claimed_row_is_never_a_target():
    assert q.queue_age_targets([row(claimed_by="cc-irsyad-1", age_min=999)], now=NOW, page_state={}) == []


def test_held_row_is_never_a_target():
    # a hold:* row carries a non-null claimed_by -> not claimable.
    assert q.queue_age_targets([row(claimed_by="hold:money-lane", age_min=999)], now=NOW, page_state={}) == []


def test_done_row_is_never_a_target():
    assert q.queue_age_targets([row(done=True, age_min=999)], now=NOW, page_state={}) == []


def test_p3_never_pages():
    assert q.queue_age_targets([row(priority="P3", age_min=99999)], now=NOW, page_state={}) == []


# ── 60-min re-page cadence ───────────────────────────────────────────────────
def test_within_60m_of_last_page_is_suppressed():
    state = {"9": NOW - 30 * 60}  # paged 30 min ago
    assert q.queue_age_targets([row(id=9, priority="P1", age_min=200)], now=NOW, page_state=state) == []


def test_at_60m_since_last_page_repages():
    state = {"9": NOW - 60 * 60}
    assert [t["id"] for t in q.queue_age_targets([row(id=9, priority="P1", age_min=200)], now=NOW, page_state=state)] == [9]


# ── the action: page hub+console, capped, stamp-on-success-only ──────────────
def test_page_is_capped_per_scan():
    sent = {"n": 0}
    targets = [dict(row(id=200 + i, age_min=45), age_min=45, owners=["cc-orchestrator", "orch-console"]) for i in range(8)]
    n = q.page_queue_owners(targets, dry=False, now=NOW, page_state={},
                            send_page=lambda t: sent.__setitem__("n", sent["n"] + 1) or True, max_pages=3)
    assert n == 3 and sent["n"] == 3


def test_page_stamps_state_only_on_success():
    state = {}
    q.page_queue_owners([dict(row(id=7, age_min=45), age_min=45, owners=["cc-orchestrator", "orch-console"])],
                        dry=False, now=NOW, page_state=state, send_page=lambda t: False)
    assert "7" not in state, "a failed page must stay unstamped so the next scan retries (dead-man)"


def test_page_stamps_state_on_success():
    state = {}
    q.page_queue_owners([dict(row(id=7, age_min=45), age_min=45, owners=["cc-orchestrator", "orch-console"])],
                        dry=False, now=NOW, page_state=state, send_page=lambda t: True)
    assert state["7"] == NOW


def test_page_dry_run_sends_and_stamps_nothing():
    sent = {"n": 0}
    state = {}
    q.page_queue_owners([dict(row(id=7, age_min=45), age_min=45, owners=["cc-orchestrator", "orch-console"])],
                        dry=True, now=NOW, page_state=state,
                        send_page=lambda t: sent.__setitem__("n", sent["n"] + 1) or True)
    assert sent["n"] == 0 and state == {}


# ── CONSTRAINT LOCK (Nazim #40859) ───────────────────────────────────────────
# The page's message_type must satisfy agent_messages_message_type_check, or an ARMED page
# raises a check_violation and fails SILENTLY — the very gap this net closes. The unit
# tests mock the INSERT, so this checks the REAL constraint (live pg_get_constraintdef;
# pinned copy offline) — a future constraint change fails the suite, not production.
def _allowed_message_types():
    import os
    import re
    PINNED = {"review_request", "question", "decision", "agreed", "challenge",
              "update", "blocker", "counter"}
    dsn = os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        return PINNED
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=15) as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conname='agent_messages_message_type_check'")
            r = cur.fetchone()
        return set(re.findall(r"'([a-z_]+)'::text", r[0])) if r else PINNED
    except Exception:
        return PINNED


def test_queue_age_message_type_is_constraint_valid():
    assert q.PAGE_MESSAGE_TYPE in _allowed_message_types(), (
        f"{q.PAGE_MESSAGE_TYPE!r} not in agent_messages_message_type_check — an armed "
        f"page would fail SILENTLY (Nazim #40859)")
