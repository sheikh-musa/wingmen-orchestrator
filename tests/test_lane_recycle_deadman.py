"""lane_recycle_deadman — the LOUD backstop for the self-recycle loop (bus 37752/37775).

Nazim's non-negotiable pre-arm (37775): before the recycler can auto-fire, a dead-man must be
LIVE that PAGES when the loop is failing to keep a lane below the line. Two failure modes, both
must get LOUDER not quieter (the stale-gauge fail-open is a hard blocker):

  * SUSTAINED bloat — a lane sits at >= HARD% for longer than the sustain window with nothing
    bringing it down. A successful recycle would have collapsed it below HARD, so "still >= HARD
    after 30 min" IS the "no recycle fired" signal — no separate fire-history needed.
  * UNKNOWN — a live lane whose gauge is unreadable/stale. Never "assume quiet": a lane we
    cannot measure is exactly the one a blind loop would skip, so it must page.

These lock the PURE decision core (`evaluate_deadman`); the DB/state/paging shell wraps it.
State is per-lane `first_over_at` (epoch it first crossed HARD), so we can measure duration
across runs and reset cleanly when it drops or recycles.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import lane_recycle_deadman as dm  # noqa: E402

HARD = 85
SUSTAIN = 1800  # 30 min


def test_below_hard_is_ok_and_clears_state():
    verdict, first_over = dm.evaluate_deadman(True, 60, first_over_at=1000.0,
                                              now_epoch=2000.0, hard_pct=HARD, sustain_s=SUSTAIN)
    assert verdict == "ok" and first_over is None


def test_just_crossed_hard_starts_watching():
    verdict, first_over = dm.evaluate_deadman(True, 88, first_over_at=None,
                                              now_epoch=5000.0, hard_pct=HARD, sustain_s=SUSTAIN)
    assert verdict == "watching" and first_over == 5000.0


def test_over_hard_but_under_sustain_keeps_watching():
    verdict, first_over = dm.evaluate_deadman(True, 90, first_over_at=5000.0,
                                              now_epoch=5000.0 + 600, hard_pct=HARD, sustain_s=SUSTAIN)
    assert verdict == "watching" and first_over == 5000.0


def test_sustained_over_hard_pages():
    verdict, first_over = dm.evaluate_deadman(True, 92, first_over_at=5000.0,
                                              now_epoch=5000.0 + SUSTAIN, hard_pct=HARD, sustain_s=SUSTAIN)
    assert verdict == "page_sustained" and first_over == 5000.0


def test_unknown_gauge_on_live_lane_pages_and_clears():
    # unreadable gauge = the stale-gauge fail-open Nazim made a hard blocker: page, never skip.
    verdict, first_over = dm.evaluate_deadman(False, None, first_over_at=5000.0,
                                              now_epoch=9000.0, hard_pct=HARD, sustain_s=SUSTAIN)
    assert verdict == "page_unknown" and first_over is None


def test_drop_below_hard_after_watching_resets():
    # a recycle (or organic drop) collapsed it below HARD — clear the timer, no page.
    verdict, first_over = dm.evaluate_deadman(True, 40, first_over_at=5000.0,
                                              now_epoch=5000.0 + SUSTAIN, hard_pct=HARD, sustain_s=SUSTAIN)
    assert verdict == "ok" and first_over is None


# ── page_message: the dedup anchor is load-bearing; the two verdicts read differently ─────
def test_page_subject_prefix_is_the_dedup_anchor():
    # `_paged_today` LIKEs "[dead-man] {lane}: {verdict}%" — if this prefix drifts, dedup breaks
    # and the operator gets spammed. Lock it exactly.
    subj, _ = dm.page_message("cc-irsyad", "page_sustained", "gauge=91% (age 60s)")
    assert subj.startswith("[dead-man] cc-irsyad: page_sustained")


def test_sustained_page_body_explains_no_recycle_fired():
    _, body = dm.page_message("cc-irsyad", "page_sustained", "gauge=91% (age 60s)", sustain_s=1800)
    assert "30 min" in body and "recycle" in body.lower()
    assert "gauge=91%" in body  # the reading is carried into the page


def test_unknown_page_body_explains_the_blind_gauge():
    _, body = dm.page_message("cc-irsyad", "page_unknown", "UNKNOWN — gauge is stale (age 9000s)")
    assert "unreadable" in body.lower() or "stale" in body.lower()
    assert "UNKNOWN" in body  # the reading is carried into the page
