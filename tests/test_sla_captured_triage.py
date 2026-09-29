"""migration 082 (bus #45552 -> #45555 -> #45557 GO condition 3): a single
aggregate page to the owning triage body once the oldest untriaged 'captured'
operator_asks row ages past CAPTURED_AGE_MIN, re-paged at most every
CAPTURED_REPAGE_EVERY_MIN (dead-man's-switch dedup, keyed on the literal
'captured' in page_state -- there is only ever one bucket). Mirrors
test_sla_asks_chase.py's pure-logic, no-live-DB style function-for-function.
"""
import importlib

w = importlib.import_module("scripts.priority_sla_watchdog")

MIN = 60.0


def rows(*, oldest_id=1, oldest_epoch=0, owner="orch-console", n=1):
    out = [(oldest_id, oldest_epoch, owner)]
    for i in range(1, n):
        out.append((oldest_id + i, oldest_epoch + i * MIN, owner))
    return out


# ── selection predicate: due past age_min, deduped by repage cadence ─────────
def test_empty_rows_is_not_a_target():
    assert w.captured_triage_page_target([], now=1000 * MIN, page_state={}) is None


def test_row_under_age_floor_is_not_yet_due():
    now = 1000 * MIN
    r = rows(oldest_epoch=now - 10 * MIN)  # well under the default 12h floor
    assert w.captured_triage_page_target(r, now=now, page_state={}) is None


def test_row_past_age_floor_is_a_target():
    now = 1000 * MIN
    r = rows(oldest_id=9, oldest_epoch=now - (w.CAPTURED_AGE_MIN + 1) * MIN)
    got = w.captured_triage_page_target(r, now=now, page_state={})
    assert got is not None
    assert got["oldest_id"] == 9
    assert got["count"] == 1


def test_target_carries_count_of_all_rows_not_just_oldest():
    now = 1000 * MIN
    r = rows(oldest_id=3, oldest_epoch=now - (w.CAPTURED_AGE_MIN + 1) * MIN, n=4)
    got = w.captured_triage_page_target(r, now=now, page_state={})
    assert got["count"] == 4


def test_within_repage_cadence_is_suppressed():
    now = 1000 * MIN
    state = {"captured": now - 10 * MIN}  # paged 10m ago; default cadence is 12h
    r = rows(oldest_epoch=now - (w.CAPTURED_AGE_MIN + 1) * MIN)
    assert w.captured_triage_page_target(r, now=now, page_state=state) is None


def test_at_repage_cadence_boundary_fires_again():
    now = 1000 * MIN
    state = {"captured": now - w.CAPTURED_REPAGE_EVERY_MIN * MIN}
    r = rows(oldest_id=4, oldest_epoch=now - (w.CAPTURED_AGE_MIN + 1) * MIN)
    got = w.captured_triage_page_target(r, now=now, page_state=state)
    assert got is not None and got["oldest_id"] == 4


def test_owner_falls_back_to_default_when_none():
    now = 1000 * MIN
    r = rows(owner=None, oldest_epoch=now - (w.CAPTURED_AGE_MIN + 1) * MIN)
    got = w.captured_triage_page_target(r, now=now, page_state={})
    assert got["owner"] == w.DEFAULT_CAPTURED_OWNER


# ── the action: dead-man's-switch stamp-on-success-only, dry-run no-op ───────
def test_page_dry_run_sends_nothing_and_stamps_nothing():
    now = 1000 * MIN
    target = {"count": 2, "oldest_id": 1, "oldest_epoch": 0, "owner": "orch-console"}
    sent = {"n": 0}
    state = {}
    ok = w.page_captured_triage(
        target, dry=True, now=now, page_state=state,
        send_page=lambda owner, t: sent.__setitem__("n", sent["n"] + 1) or True,
    )
    assert ok is False
    assert sent["n"] == 0
    assert state == {}


def test_page_none_target_is_a_noop():
    state = {}
    ok = w.page_captured_triage(
        None, dry=False, now=1000 * MIN, page_state=state,
        send_page=lambda owner, t: True,
    )
    assert ok is False
    assert state == {}


def test_page_stamps_state_only_on_success():
    state = {}
    target = {"count": 1, "oldest_id": 1, "oldest_epoch": 0, "owner": "orch-console"}
    w.page_captured_triage(
        target, dry=False, now=1000 * MIN, page_state=state,
        send_page=lambda owner, t: False,
    )
    assert "captured" not in state, "failed page must remain unstamped for retry"


def test_page_stamps_state_on_success():
    state = {}
    now = 1000 * MIN
    target = {"count": 1, "oldest_id": 1, "oldest_epoch": 0, "owner": "orch-console"}
    w.page_captured_triage(
        target, dry=False, now=now, page_state=state,
        send_page=lambda owner, t: True,
    )
    assert state["captured"] == now


def test_page_passes_target_owner_through_to_send():
    seen = {}
    target = {"count": 1, "oldest_id": 1, "oldest_epoch": 0, "owner": "cc-orchestrator"}
    w.page_captured_triage(
        target, dry=False, now=1000 * MIN, page_state={},
        send_page=lambda owner, t: seen.setdefault("owner", owner) or True,
    )
    assert seen["owner"] == "cc-orchestrator"


# ── channel/tag -> owner mapping (ORCH-TOPOLOGY-001 per-channel ownership) ───
def test_captured_tag_owner_mapping_matches_topology():
    assert w.CAPTURED_TAG_OWNER["nazim-console"] == "orch-console"
    assert w.CAPTURED_TAG_OWNER["tmux-console"] == "orch-console"
    assert w.CAPTURED_TAG_OWNER["orch-channel"] == "cc-orchestrator"


# ── CONSTRAINT LOCK: reuse the same allowed-message-types check as ruling-3 ──
def test_captured_triage_message_type_is_constraint_valid():
    import os
    import re
    PINNED = {"review_request", "question", "decision", "agreed", "challenge",
              "update", "blocker", "counter"}
    dsn = os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL")
    allowed = PINNED
    if dsn:
        try:
            import psycopg
            with psycopg.connect(dsn, connect_timeout=15) as conn, conn.cursor() as cur:
                cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                            "WHERE conname='agent_messages_message_type_check'")
                r = cur.fetchone()
            if r:
                allowed = set(re.findall(r"'([a-z_]+)'::text", r[0]))
        except Exception:
            allowed = PINNED
    assert w.PAGE_MESSAGE_TYPE in allowed, (
        f"{w.PAGE_MESSAGE_TYPE!r} not in agent_messages_message_type_check — an armed "
        f"captured-triage page would fail SILENTLY")
