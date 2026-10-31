"""Tests for the party-update-chase net (op#34349/34354 Phase 2,
docs/superpowers/plans/2026-10-31-nothing-goes-dark-phase2.md Task 2):
detection + pure target selection for operator-facing "gone dark" items.

Mirrors tests/test_sla_asks_chase.py's structure (same dead-man's-switch
shape, same per-scan cap) for the new net scripts/priority_sla_watchdog.py
adds alongside client_asks_chase/captured_triage.
"""
import scripts.priority_sla_watchdog as w
from scripts.priority_sla_watchdog import (
    party_update_chase_targets,
    PARTY_UPDATE_CHASE_EVERY_MIN,
    MAX_PARTY_UPDATE_CHASE_PER_RUN,
)

NOW = 1_800_000_000.0  # arbitrary fixed epoch seconds


def test_client_waiting_party_is_never_a_target():
    rows = [dict(id=1, kind="commitment", waiting_party="client",
                 overdue=True, overdue_reason="quiet", owner="cc-irsyad-coord")]
    assert party_update_chase_targets(rows, now=NOW, chase_state={},
                                       active_turn_check=lambda a: False) == []


def test_fired_commitment_is_excluded_sweeper_already_owns_it():
    rows = [dict(id=2, kind="commitment", waiting_party="operator",
                 overdue=True, overdue_reason="fired", owner="orch-console")]
    assert party_update_chase_targets(rows, now=NOW, chase_state={},
                                       active_turn_check=lambda a: False) == []


def test_quiet_operator_row_with_no_prior_update_is_a_target():
    rows = [dict(id=3, kind="ask", waiting_party="operator",
                 overdue=True, overdue_reason="quiet", owner="cc-scholar")]
    out = party_update_chase_targets(rows, now=NOW, chase_state={},
                                      active_turn_check=lambda a: False)
    assert [t["id"] for t in out] == [3]


def test_active_turn_owner_is_suppressed():
    rows = [dict(id=4, kind="ask", waiting_party="operator",
                 overdue=True, overdue_reason="quiet", owner="cc-irsyad-3")]
    out = party_update_chase_targets(rows, now=NOW, chase_state={},
                                      active_turn_check=lambda a: a == "cc-irsyad-3")
    assert out == []


def test_within_cadence_of_last_chase_is_suppressed():
    state = {"ask#4": {"last_chase_ts": NOW - 60}}  # just chased, well under the 240m cadence
    rows = [dict(id=4, kind="ask", waiting_party="operator",
                 overdue=True, overdue_reason="quiet", owner="cc-scholar")]
    assert party_update_chase_targets(rows, now=NOW, chase_state=state,
                                       active_turn_check=lambda a: False) == []


def test_at_cadence_boundary_second_chase_fires():
    state = {"ask#4": {"last_chase_ts": NOW - PARTY_UPDATE_CHASE_EVERY_MIN * 60}}
    rows = [dict(id=4, kind="ask", waiting_party="operator",
                 overdue=True, overdue_reason="quiet", owner="cc-scholar")]
    out = party_update_chase_targets(rows, now=NOW, chase_state=state,
                                      active_turn_check=lambda a: False)
    assert [t["id"] for t in out] == [4]


def test_chase_is_capped_per_scan():
    rows = [dict(id=i, kind="ask", waiting_party="operator", overdue=True,
                 overdue_reason="quiet", owner="cc-scholar") for i in range(5)]
    out = party_update_chase_targets(rows, now=NOW, chase_state={},
                                      active_turn_check=lambda a: False,
                                      max_targets=MAX_PARTY_UPDATE_CHASE_PER_RUN)
    assert len(out) == MAX_PARTY_UPDATE_CHASE_PER_RUN


# ---- chase_party_update: the action (Task 3) --------------------------------

def test_chase_dry_run_sends_nothing_and_stamps_nothing():
    calls = []
    n, sent = w.chase_party_update(
        [dict(id=5, owner="cc-scholar", title="x")], dry=True, now=NOW,
        chase_state={}, send_chase=lambda o, t: calls.append((o, t)) or True,
        max_chases=3)
    assert n == 0 and calls == [] and sent == []


def test_chase_stamps_state_only_on_successful_send():
    state = {}
    n, sent = w.chase_party_update(
        [dict(id=6, owner="cc-scholar", title="x")], dry=False, now=NOW,
        chase_state=state, send_chase=lambda o, t: False, max_chases=3)
    assert n == 0 and "6" not in state


def test_chase_targets_owner_never_operator_or_console_fallback():
    seen = []
    w.chase_party_update(
        [dict(id=7, owner="cc-scholar", title="x")], dry=False, now=NOW,
        chase_state={}, send_chase=lambda o, t: seen.append(o) or True, max_chases=3)
    assert seen == ["cc-scholar"]


def test_party_update_chase_message_type_is_constraint_valid():
    # same guard as test_asks_chase_message_type_is_constraint_valid — the
    # message_type posted by _send_party_update_chase (via PAGE_MESSAGE_TYPE)
    # must be one of the agent_messages_message_type_check allowed values
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
        f"party-update-chase would fail SILENTLY")


# ---- review-pass fixes (final whole-branch review, op#34349/34354) ---------

def test_ask_waiting_on_musa_is_never_a_target():
    # already owned by the EXISTING asks_chase net ("go chase Musa") — this
    # net chasing the same row too would tell the owner the opposite of what
    # asks_chase just told them
    rows = [dict(id=60, kind="ask", waiting_party="operator", overdue=True,
                 overdue_reason="quiet", owner="cc-scholar", status="waiting_on_musa")]
    assert party_update_chase_targets(rows, now=NOW, chase_state={},
                                       active_turn_check=lambda a: False) == []


def test_commitment_past_due_is_excluded_sweeper_races_it():
    # pending + due_at passed is exactly commitment_sweeper.py's own fire
    # condition (status IN ('pending','fired') AND due_at <= now()) — double
    # escalating it before the sweeper's own pass is the same class of bug
    # the 'fired' exclusion already prevents
    rows = [dict(id=61, kind="commitment", waiting_party="operator", overdue=True,
                 overdue_reason="past_due", owner="orch-console")]
    assert party_update_chase_targets(rows, now=NOW, chase_state={},
                                       active_turn_check=lambda a: False) == []


def test_ask_past_due_not_waiting_on_musa_is_still_a_target():
    # a past_due ask nobody is already chasing (no waiting_on_operator) is
    # still this net's job
    rows = [dict(id=62, kind="ask", waiting_party="operator", overdue=True,
                 overdue_reason="past_due", owner="cc-scholar", status="pending")]
    out = party_update_chase_targets(rows, now=NOW, chase_state={},
                                      active_turn_check=lambda a: False)
    assert [t["id"] for t in out] == [62]


def test_ask_and_commitment_sharing_same_numeric_id_track_independently():
    # operator_asks and held_commitments are separate tables with overlapping
    # ids — a bare str(id) chase_state key let chasing one silence the other
    rows = [dict(id=50, kind="ask", source="operator_asks#50", waiting_party="operator",
                 overdue=True, overdue_reason="quiet", owner="cc-scholar"),
            dict(id=50, kind="commitment", source="held_commitments#50", waiting_party="operator",
                 overdue=True, overdue_reason="quiet", owner="orch-console")]
    state = {"held_commitments#50": {"last_chase_ts": NOW - 60}}  # commitment#50 just chased
    out = party_update_chase_targets(rows, now=NOW, chase_state=state,
                                      active_turn_check=lambda a: False)
    assert [(t["id"], t["kind"]) for t in out] == [(50, "ask")]


def test_base_agent_id_strips_trailing_instance_number():
    # all_lane_map() is keyed by BASE agent id; an owner recorded as a live
    # numbered instance (e.g. delegated_to='cc-irsyad-coord-3') looked up
    # against it directly always misses and reads as idle
    assert w._base_agent_id("cc-irsyad-coord-3") == "cc-irsyad-coord"
    assert w._base_agent_id("cc-scholar") == "cc-scholar"
    assert w._base_agent_id(None) == ""


def test_send_skips_unresolvable_owner_without_touching_conn_or_raising():
    # bus_send.send's refuse_if_undeliverable raises SystemExit for an owner
    # that is a tmux name (not a registered agents.id) or 'unassigned' —
    # resolve_recipient must short-circuit BEFORE any send is attempted
    class _UnreachableConn:
        def cursor(self):
            raise AssertionError("must not touch the DB for an unresolvable owner")

    ok = w._send_party_update_chase(
        _UnreachableConn(), "irsyad-coord", dict(kind="ask", id=70, title="x"),
        recipient_resolution=(set(), {}, set(), {}))
    assert ok is False
