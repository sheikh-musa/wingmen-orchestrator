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
