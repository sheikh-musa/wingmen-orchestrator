"""op#11774 #5 INTERIM (ship-first, page-only) — escalate a READ-but-PARKED hub.

The silent-watchdog root cause: attended_for():608 treats a hub `read` row as
attended and suppresses it forever, so a hub that READS a P1+rr route then PARKS
(responded_at NULL) is never surfaced — 18492/18537 sat ~5h with zero escalation.

This interim is a SEPARATE, page-only safety net (the oracle version supersedes it
in Phase 1): a hub P0/P1 requires_response row that is READ, UNRESPONDED, and past
a ~60m grace -> ESCALATE TO A SUPERVISOR (console->operator page), NEVER renudge the
parked body (nudging via the same broken wake is useless). Longer than legit VPS
wake-latency (>20m, why the suppression existed), well short of the hours it took.
"""
import importlib

w = importlib.import_module("scripts.priority_sla_watchdog")


def row(to_agent="cc-orchestrator", priority="P1", rr=True, read=True,
        responded=False, elapsed=90):
    return {
        "id": 1, "to_agent": to_agent, "priority": priority,
        "requires_response": rr,
        "read_at": "2026-08-11T07:28:00Z" if read else None,
        "responded_at": "2026-08-11T07:40:00Z" if responded else None,
        "elapsed_minutes": elapsed,
    }


# ── the pure selection predicate ────────────────────────────────────────────
def test_read_parked_hub_p1_past_grace_is_a_target():
    targets = w.read_parked_hub_targets([row(elapsed=90)], interim_min=60)
    assert [t["id"] for t in targets] == [1]


def test_within_grace_is_not_yet_a_target():
    assert w.read_parked_hub_targets([row(elapsed=45)], interim_min=60) == []


def test_unread_hub_row_is_not_this_nets_job():
    # a NOT-read hub row is the existing 'hub not woken' alarm (#16246), not this.
    assert w.read_parked_hub_targets([row(read=False, elapsed=90)], interim_min=60) == []


def test_responded_row_is_never_a_target():
    assert w.read_parked_hub_targets([row(responded=True, elapsed=999)], interim_min=60) == []


def test_non_rr_row_is_not_a_target():
    assert w.read_parked_hub_targets([row(rr=False, elapsed=999)], interim_min=60) == []


def test_low_priority_is_not_a_target():
    assert w.read_parked_hub_targets([row(priority="P2", elapsed=999)], interim_min=60) == []


def test_non_hub_agent_is_not_this_nets_job():
    # a normal lane read-but-unresponded is already covered by the standard ladder.
    assert w.read_parked_hub_targets([row(to_agent="cc-quality", elapsed=999)], interim_min=60) == []


def test_p0_hub_is_a_target():
    targets = w.read_parked_hub_targets([row(priority="P0", elapsed=61)], interim_min=60)
    assert len(targets) == 1


def test_beyond_upper_bound_is_not_a_target():
    # responded_at is chronically unstamped by the hub, so an UNBOUNDED
    # responded-NULL predicate backfills days-old cruft (found live: 30+ historical
    # rows). The upper bound keeps this a RECENT-park net, not a history replay.
    old = row(elapsed=100_000)  # ~70 days
    assert w.read_parked_hub_targets([old], interim_min=60, interim_max=360) == []


def test_within_upper_bound_is_a_target():
    ok = row(elapsed=120)
    assert len(w.read_parked_hub_targets([ok], interim_min=60, interim_max=360)) == 1


# ── BACKFILL GUARD (the flood fix): id-watermark set at enable-time ──────────
def test_watermark_excludes_the_entire_preexisting_backlog():
    # The exact regression: ~180 pre-existing hub P1 stalls (id <= watermark) must
    # produce ZERO new escalations. The suppression had hidden them; un-hiding must
    # not page each. (op#11774 flood, 2026-08-11.)
    backlog = [dict(row(elapsed=5000), id=i) for i in range(1, 181)]  # ids 1..180
    assert w.read_parked_hub_targets(backlog, interim_min=60, interim_max=360,
                                     watermark_id=180) == []


def test_a_fresh_stall_past_the_watermark_pages_once():
    fresh = dict(row(elapsed=90), id=500)  # created after the watermark line
    got = w.read_parked_hub_targets([fresh], interim_min=60, interim_max=360,
                                    watermark_id=180)
    assert [t["id"] for t in got] == [500]


# ── RATE-LIMIT / cascade-guard: hard cap on escalations per scan ────────────
def test_escalations_are_capped_per_scan():
    calls = {"escalate": 0}
    targets = [dict(row(elapsed=90), id=200 + i) for i in range(10)]
    w.escalate_read_parked(
        targets, dry=False,
        already_paged=lambda mid: False,
        escalate_internally=lambda t: calls.__setitem__("escalate", calls["escalate"] + 1) or True,
        send_page=lambda text: True,
        max_escalations=3,
    )
    assert calls["escalate"] == 3, "a misfire must not flood — hard cap per scan"


# ── the action: escalate to supervisor, NEVER renudge ───────────────────────
def test_escalate_read_parked_pages_supervisor_and_never_nudges():
    calls = {"escalate": 0, "page": 0, "nudge": 0}
    w.escalate_read_parked(
        [row(elapsed=90)],
        dry=False,
        already_paged=lambda mid: False,
        escalate_internally=lambda t: (calls.__setitem__("escalate", calls["escalate"] + 1) or True),
        send_page=lambda text: calls.__setitem__("page", calls["page"] + 1) or True,
        renudge=lambda *a, **k: calls.__setitem__("nudge", calls["nudge"] + 1),
    )
    assert calls["escalate"] == 1        # supervisor (console) escalation fired
    assert calls["nudge"] == 0           # the parked body was NEVER nudged


def test_escalate_read_parked_dedupes_already_paged():
    calls = {"escalate": 0, "page": 0}
    w.escalate_read_parked(
        [row(elapsed=90)],
        dry=False,
        already_paged=lambda mid: True,   # already surfaced
        escalate_internally=lambda t: calls.__setitem__("escalate", calls["escalate"] + 1) or True,
        send_page=lambda text: calls.__setitem__("page", calls["page"] + 1) or True,
        renudge=lambda *a, **k: None,
    )
    assert calls["escalate"] == 0 and calls["page"] == 0   # no double-page


def test_escalate_read_parked_falls_through_to_operator_page_if_internal_fails():
    calls = {"page": 0}
    w.escalate_read_parked(
        [row(elapsed=90)],
        dry=False,
        already_paged=lambda mid: False,
        escalate_internally=lambda t: False,   # internal path not alive/failed
        send_page=lambda text: calls.__setitem__("page", calls["page"] + 1) or True,
        renudge=lambda *a, **k: None,
    )
    assert calls["page"] == 1   # human still hears — operator page
