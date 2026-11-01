"""Ruling-3 (Nazim, 2026-09-16): aged unanswered P0/P1 requires_response re-page.

The #40129 gap: a P1 requires_response build order (hub->cc-scholar) sat UNREAD ~40h
and never re-paged. The SLA watchdog drops any violation older than MAX_VIOLATION_AGE_MIN
(180m) out of its actionable set, so an aged P1 goes SILENT ("P3-decay") — no re-nudge,
no escalation, invisible until a human happens to notice.

Ruling-3 adds a SEPARATE net: a P0/P1 requires_response row that is unanswered
(responded_at NULL) AND still unread (read_at NULL) past 6h re-pages the OWNER body
(the issuer: hub for hub-directed lanes, orch-console for the SRE's own) at P1, every 6h,
NEVER the operator, until the row is read. This complements read_parked (hub-only,
60-360m, READ-but-parked, pages the operator): this net starts at 6h, any owner, unread,
pages the owner body, never the operator.
"""
import importlib

w = importlib.import_module("scripts.priority_sla_watchdog")

HOUR = 3600.0


def row(id=1, from_agent="cc-orchestrator", to_agent="cc-scholar", priority="P1",
        rr=True, read=False, responded=False, elapsed=400):
    return {
        "id": id,
        "from_agent": from_agent,
        "to_agent": to_agent,
        "priority": priority,
        "requires_response": rr,
        "read_at": "2026-09-14T15:31:00Z" if read else None,
        "responded_at": "2026-09-14T16:00:00Z" if responded else None,
        "elapsed_minutes": elapsed,
    }


# ── owner_of: who gets re-paged (the issuer, never the operator) ─────────────
def test_owner_of_hub_directed_row_is_the_hub():
    assert w.owner_of("cc-orchestrator") == "cc-orchestrator"


def test_owner_of_sre_own_row_is_the_console_not_itself():
    # the SRE runs this watchdog; re-paging itself is useless — console supervises.
    assert w.owner_of("cc-fleet-health") == "orch-console"


def test_owner_of_any_other_issuer_is_that_issuer():
    assert w.owner_of("cc-quality") == "cc-quality"


# ── selection predicate: the 6h floor ────────────────────────────────────────
def test_just_under_6h_is_not_yet_a_target():
    r = row(elapsed=359)
    assert w.aged_rr_repage_targets([r], now=1000 * HOUR, repage_state={}) == []


def test_at_6h_is_a_target_and_carries_its_owner():
    r = row(id=40129, elapsed=360)
    got = w.aged_rr_repage_targets([r], now=1000 * HOUR, repage_state={})
    assert [t["id"] for t in got] == [40129]
    assert got[0]["owner"] == "cc-orchestrator"


# ── stop conditions: read OR responded ends the re-page ──────────────────────
def test_read_row_is_never_a_target():
    # "until read" — once the recipient has read it, the owner re-page stops.
    r = row(read=True, elapsed=5000)
    assert w.aged_rr_repage_targets([r], now=1000 * HOUR, repage_state={}) == []


def test_responded_row_is_never_a_target():
    r = row(responded=True, elapsed=5000)
    assert w.aged_rr_repage_targets([r], now=1000 * HOUR, repage_state={}) == []


def test_non_requires_response_row_is_not_a_target():
    r = row(rr=False, elapsed=5000)
    assert w.aged_rr_repage_targets([r], now=1000 * HOUR, repage_state={}) == []


def test_p2_p3_never_repage():
    assert w.aged_rr_repage_targets([row(priority="P2", elapsed=5000)],
                                    now=1000 * HOUR, repage_state={}) == []
    assert w.aged_rr_repage_targets([row(priority="P3", elapsed=5000)],
                                    now=1000 * HOUR, repage_state={}) == []


def test_p0_aged_is_also_a_target():
    # a P0 that ages past the fresh window must not go silent either (fail toward action).
    got = w.aged_rr_repage_targets([row(priority="P0", elapsed=360)],
                                   now=1000 * HOUR, repage_state={})
    assert len(got) == 1


# ── cadence: re-page every 6h, not every scan ────────────────────────────────
def test_within_6h_of_last_repage_is_suppressed():
    now = 1000 * HOUR
    state = {"40129": now - 3 * HOUR}          # last re-paged 3h ago
    assert w.aged_rr_repage_targets([row(id=40129, elapsed=800)],
                                    now=now, repage_state=state) == []


def test_at_12h_second_repage_fires():
    # first re-page at 6h; at 12h (6h since last) it fires again, still unread.
    now = 1000 * HOUR
    state = {"40129": now - 6 * HOUR}          # last re-paged exactly 6h ago
    got = w.aged_rr_repage_targets([row(id=40129, elapsed=720)],
                                   now=now, repage_state=state)
    assert [t["id"] for t in got] == [40129]


# ── backfill guard: enabling the net must not page the whole history ─────────
def test_watermark_excludes_the_preexisting_backlog():
    backlog = [row(id=i, elapsed=9000) for i in range(1, 51)]   # ids 1..50, all aged
    assert w.aged_rr_repage_targets(backlog, now=1000 * HOUR, repage_state={},
                                    watermark_id=50) == []


def test_a_row_past_the_watermark_still_pages():
    fresh = row(id=99, elapsed=400)
    got = w.aged_rr_repage_targets([fresh], now=1000 * HOUR, repage_state={},
                                   watermark_id=50)
    assert [t["id"] for t in got] == [99]


# ── the action: capped, stamp-on-success-only, never the operator ────────────
def test_repage_is_capped_per_scan():
    sent = {"n": 0}
    targets = [dict(row(id=200 + i, elapsed=400), owner="cc-orchestrator") for i in range(10)]
    state = {}
    n = w.repage_aged_owners(targets, dry=False, now=1000 * HOUR, repage_state=state,
                             send_repage=lambda owner, t: sent.__setitem__("n", sent["n"] + 1) or True,
                             max_repages=3)
    assert n == 3 and sent["n"] == 3


def test_repage_stamps_state_only_on_success():
    # a failed send must NOT be stamped — leave it unstamped so the next scan retries
    # (dead-man's switch: never suppress a re-page behind a silent failure).
    state = {}
    w.repage_aged_owners([dict(row(id=7, elapsed=400), owner="cc-orchestrator")],
                         dry=False, now=1000 * HOUR, repage_state=state,
                         send_repage=lambda owner, t: False)   # send failed
    assert "7" not in state, "failed re-page must remain unstamped for retry"


def test_repage_stamps_state_on_success():
    state = {}
    now = 1000 * HOUR
    w.repage_aged_owners([dict(row(id=7, elapsed=400), owner="cc-orchestrator")],
                         dry=False, now=now, repage_state=state,
                         send_repage=lambda owner, t: True)
    assert state["7"] == now


def test_repage_dry_run_sends_nothing_and_stamps_nothing():
    sent = {"n": 0}
    state = {}
    w.repage_aged_owners([dict(row(id=7, elapsed=400), owner="cc-orchestrator")],
                         dry=True, now=1000 * HOUR, repage_state=state,
                         send_repage=lambda owner, t: sent.__setitem__("n", sent["n"] + 1) or True)
    assert sent["n"] == 0 and state == {}


# ── CONSTRAINT LOCK (Nazim #40859) ───────────────────────────────────────────
# The re-page's message_type must satisfy agent_messages_message_type_check, or an ARMED
# re-page raises a check_violation and fails SILENTLY — the exact silent-gap this net
# closes. The unit tests above mock the INSERT, so they can't catch a bad type; this test
# checks the REAL constraint (live via pg_get_constraintdef; a pinned copy offline) so a
# future constraint change fails the suite instead of production.
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


def test_aged_rr_message_type_is_constraint_valid():
    assert w.PAGE_MESSAGE_TYPE in _allowed_message_types(), (
        f"{w.PAGE_MESSAGE_TYPE!r} not in agent_messages_message_type_check — an armed "
        f"re-page would fail SILENTLY (Nazim #40859)")


# ── orch-console #90244: hold rows addressed to a STOPPED lane ───────────────
# The net keys on read_at, so a row addressed to a lane the registry has stopped
# (fleet_lanes.desired_state='down') can never be read and would re-page its
# issuer's owner every 6h forever. Live case: 89685/89772/89780, all to
# cc-hifz-1 while lane 'hifz' (base cc-hifz) was stopped until 2026-11-05.
DOWN = {"hifz", "cc-hifz"}


def test_a_row_to_a_stopped_lane_is_held_not_re_paged():
    r = row(id=89772, from_agent="cc-fleet-health", to_agent="cc-hifz-1", elapsed=400)
    got = w.aged_rr_repage_targets([r], now=1000 * HOUR, repage_state={},
                                   down_addressees=DOWN, live_bases=set())
    assert got == []


def test_without_the_guard_the_same_row_still_pages_old_behaviour():
    # the control: this is what the net did before the fix (the noise console saw)
    r = row(id=89772, from_agent="cc-fleet-health", to_agent="cc-hifz-1", elapsed=400)
    got = w.aged_rr_repage_targets([r], now=1000 * HOUR, repage_state={})
    assert [t["id"] for t in got] == [89772]


def test_the_addressee_is_matched_on_its_instance_stripped_base():
    # fleet_lanes records 'hifz'/'cc-hifz', never the live instance id
    for to_agent in ("cc-hifz-1", "cc-hifz", "hifz"):
        r = row(id=1, to_agent=to_agent, elapsed=400)
        assert w.aged_rr_repage_targets([r], now=1000 * HOUR, repage_state={},
                                        down_addressees=DOWN, live_bases=set()) == [], to_agent


def test_a_down_registry_row_with_a_LIVE_instance_elsewhere_still_pages():
    # desired_state is this host's intent, not liveness: irsyad-coord reads down on
    # the Mini while cc-irsyad-coord-1 runs on gzb. Holding that would silence a
    # genuine stall (same reasoning as resolve_recipient).
    r = row(id=5, from_agent="cc-orchestrator", to_agent="cc-irsyad-coord-1", elapsed=400)
    got = w.aged_rr_repage_targets([r], now=1000 * HOUR, repage_state={},
                                   down_addressees={"irsyad-coord", "cc-irsyad-coord"},
                                   live_bases={"cc-irsyad-coord"})
    assert [t["id"] for t in got] == [5]


def test_an_unreadable_guard_set_holds_nothing_fail_toward_paging():
    # a guard that cannot read the registry must not silence the net
    r = row(id=7, to_agent="cc-hifz-1", elapsed=400)
    assert [t["id"] for t in w.aged_rr_repage_targets(
        [r], now=1000 * HOUR, repage_state={}, down_addressees=None, live_bases=None)] == [7]


def test_a_row_not_addressed_to_a_down_lane_is_untouched():
    r = row(id=9, from_agent="cc-orchestrator", to_agent="cc-scholar", elapsed=400)
    assert [t["id"] for t in w.aged_rr_repage_targets(
        [r], now=1000 * HOUR, repage_state={}, down_addressees=DOWN, live_bases=set())] == [9]


def test_held_rows_are_reported_so_the_suppression_is_on_the_record():
    held_row = row(id=89772, from_agent="cc-fleet-health", to_agent="cc-hifz-1", elapsed=400)
    other = row(id=9, from_agent="cc-orchestrator", to_agent="cc-scholar", elapsed=400)
    held = w.held_down_addressees([held_row, other], down_addressees=DOWN, live_bases=set())
    assert [h["id"] for h in held] == [89772]


def test_the_held_log_only_names_rows_that_would_otherwise_have_paged():
    # read / answered / too young / below the watermark rows were never due -- naming
    # them in the HELD log would misreport a suppression that did not happen.
    rows = [
        row(id=1, to_agent="cc-hifz-1", read=True, elapsed=400),
        row(id=2, to_agent="cc-hifz-1", responded=True, elapsed=400),
        row(id=3, to_agent="cc-hifz-1", elapsed=10),
        row(id=4, to_agent="cc-hifz-1", rr=False, elapsed=400),
        row(id=5, to_agent="cc-hifz-1", priority="P3", elapsed=400),
    ]
    assert w.held_down_addressees(rows, down_addressees=DOWN, live_bases=set()) == []
    assert w.held_down_addressees([row(id=6, to_agent="cc-hifz-1", elapsed=400)],
                                  down_addressees=DOWN, live_bases=set(),
                                  watermark_id=10) == []


def test_a_live_BASE_singleton_is_never_held_by_a_stale_down_row():
    # fleet_lanes says fleet-health is down (2026-10-07) while agent_status says
    # cc-fleet-health is working. Holding the SRE's own mail would silence the net
    # on the one inbox that supervises it. live_bases carries live base ids, not
    # only instance-stripped ones (fetch_live_bases).
    r = row(id=31, from_agent="cc-orchestrator", to_agent="cc-fleet-health", elapsed=400)
    got = w.aged_rr_repage_targets([r], now=1000 * HOUR, repage_state={},
                                   down_addressees={"fleet-health", "cc-fleet-health"},
                                   live_bases={"cc-fleet-health"})
    assert [t["id"] for t in got] == [31]
    assert w.held_down_addressees([r], down_addressees={"fleet-health", "cc-fleet-health"},
                                  live_bases={"cc-fleet-health"}) == []
