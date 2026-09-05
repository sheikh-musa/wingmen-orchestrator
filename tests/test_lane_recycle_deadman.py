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


def test_truly_blind_lane_that_WAS_elevated_pages():
    # Both signals dead AND the last-known reading was high (the dangerous "was ~94%, now blind"
    # case — a bloated lane that went idle into the pane's blind band). Blocker page.
    verdict, first_over = dm.evaluate_deadman(False, None, first_over_at=5000.0, now_epoch=9000.0,
                                              hard_pct=HARD, sustain_s=SUSTAIN, last_known_pct=92)
    assert verdict == "page_unknown" and first_over is None


def test_blind_floor_is_inclusive():
    # exactly at the floor pages; just under it logs (Nazim 37788: reserve the blocker for high).
    v_at, _ = dm.evaluate_deadman(False, None, None, 9000.0, hard_pct=HARD, sustain_s=SUSTAIN,
                                  last_known_pct=70, unknown_floor=70)
    v_under, _ = dm.evaluate_deadman(False, None, None, 9000.0, hard_pct=HARD, sustain_s=SUSTAIN,
                                     last_known_pct=69, unknown_floor=70)
    assert v_at == "page_unknown" and v_under == "log_unknown"


def test_truly_blind_lane_that_WAS_low_is_LOGGED_not_paged():
    # Both signals dead but last-known was low: an alive (fresh-heartbeat) idle low lane whose gauge
    # froze and whose pane hint is absent because there's nothing to reclaim. Nazim 37788: still
    # SURFACE it (log_unknown), but do NOT blocker-page — an idle low lane does not bloat.
    verdict, first_over = dm.evaluate_deadman(False, None, first_over_at=None, now_epoch=9000.0,
                                              hard_pct=HARD, sustain_s=SUSTAIN, last_known_pct=8,
                                              unknown_floor=70)
    assert verdict == "log_unknown" and first_over is None


def test_truly_blind_lane_never_measured_is_logged_not_paged():
    verdict, _ = dm.evaluate_deadman(False, None, first_over_at=None, now_epoch=9000.0,
                                     hard_pct=HARD, sustain_s=SUSTAIN, last_known_pct=None,
                                     unknown_floor=70)
    assert verdict == "log_unknown"


def test_drop_below_hard_after_watching_resets():
    # a recycle (or organic drop) collapsed it below HARD — clear the timer, no page.
    verdict, first_over = dm.evaluate_deadman(True, 40, first_over_at=5000.0,
                                              now_epoch=5000.0 + SUSTAIN, hard_pct=HARD, sustain_s=SUSTAIN)
    assert verdict == "ok" and first_over is None


# ── resolve_lane_pct: gauge-first, pane fallback when the gauge is stale, truly-blind last ───
def test_resolve_fresh_gauge_is_known_gauge_first():
    known, pct, last_known, _ = dm.resolve_lane_pct(gauge_tokens=900_000, gauge_age_s=120,
                                                    pane_pct=None, pane_hint_k=None)
    assert known and pct == 90 and last_known == 90


def test_resolve_gauge_fresh_within_the_wider_deadman_cutoff():
    # 40m-old gauge is STALE to the executor's 30m fire cutoff but FRESH to the dead-man's wider
    # cutoff (Nazim 37788: set it well above the idle-refresh interval so a normally-idle lane is
    # not flagged). Read straight off the gauge, no pane needed.
    known, pct, _, _ = dm.resolve_lane_pct(gauge_tokens=460_000, gauge_age_s=2400,
                                           pane_pct=None, pane_hint_k=None)
    assert known and pct == 46


def test_resolve_very_stale_gauge_rescued_by_pane_hint():
    # gauge older than even the wide cutoff, but the pane hint reads it — NOT blind, do not page.
    known, pct, _, _ = dm.resolve_lane_pct(gauge_tokens=457_250, gauge_age_s=5000,
                                           pane_pct=None, pane_hint_k=457.3)
    assert known and pct == 46  # via the pane, not the very-stale gauge


def test_resolve_truly_blind_reports_unknown_with_last_known():
    # gauge very stale AND pane blind -> unknown; the stale gauge's pct is carried as last_known so
    # the page-floor can tell was-high (dangerous) from was-low (benign).
    known, pct, last_known, _ = dm.resolve_lane_pct(gauge_tokens=940_000, gauge_age_s=9000,
                                                    pane_pct=None, pane_hint_k=None)
    assert not known and pct is None and last_known == 94


def test_resolve_low_idle_blind_lane_is_unknown_but_last_known_low():
    known, pct, last_known, _ = dm.resolve_lane_pct(gauge_tokens=81_624, gauge_age_s=9000,
                                                    pane_pct=None, pane_hint_k=None)
    assert not known and last_known == 8  # -> evaluate_deadman logs (benign), does not page


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
