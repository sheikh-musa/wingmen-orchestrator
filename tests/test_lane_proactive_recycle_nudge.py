"""Tests for scripts/lane_proactive_recycle_nudge.py — the proactive self-recycle nudge tier.

Covers: the PURE decision core (evaluate_proactive_nudge), the PURE message, the cross-tier dedup
SQL shape, and the run() wiring (nudge on cross / re-nudge on climb; INERT unless armed; lease
downgrade; never targets a singleton; state persistence semantics). No real DB / tmux.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import lane_proactive_recycle_nudge as pn  # noqa: E402
from scripts import lane_recycle_deadman as dm  # noqa: E402
from scripts import sre_lane_recycle as slr  # noqa: E402

T0 = 1_000_000.0
BAR = pn.PROACTIVE_PCT
GRACE = pn.GRACE_S


# ── PURE core ────────────────────────────────────────────────────────────────
def test_below_bar_clears_episode():
    assert pn.evaluate_proactive_nudge(True, BAR - 1, None, T0) == ("ok", None)


def test_blind_reading_never_nudges_and_clears():
    ep = {"first_over_at": T0, "last_nudge_at": T0, "last_nudge_pct": 82, "nudges": 1}
    assert pn.evaluate_proactive_nudge(False, None, ep, T0) == ("ok", None)


def test_first_crossing_nudges_and_starts_episode():
    v, ep = pn.evaluate_proactive_nudge(True, BAR + 1, None, T0)
    assert v == "nudge"
    assert ep["nudges"] == 1 and ep["last_nudge_pct"] == BAR + 1 and ep["last_nudge_at"] == T0


def test_within_grace_holds_even_when_climbing():
    ep = {"first_over_at": T0, "last_nudge_at": T0, "last_nudge_pct": BAR + 1, "nudges": 1}
    v, ep2 = pn.evaluate_proactive_nudge(True, BAR + 12, ep, T0 + 60)  # big climb, but grace not elapsed
    assert v == "hold" and ep2 == ep


def test_past_grace_but_small_climb_holds():
    ep = {"first_over_at": T0, "last_nudge_at": T0, "last_nudge_pct": BAR + 1, "nudges": 1}
    v, _ = pn.evaluate_proactive_nudge(True, BAR + 1 + (pn.RENUDGE_DELTA - 1), ep, T0 + GRACE + 1)
    assert v == "hold"


def test_past_grace_and_climb_renudges_and_escalates():
    ep = {"first_over_at": T0, "last_nudge_at": T0, "last_nudge_pct": BAR + 1, "nudges": 1}
    v, ep2 = pn.evaluate_proactive_nudge(True, BAR + 1 + pn.RENUDGE_DELTA, ep, T0 + GRACE + 1)
    assert v == "renudge"
    assert ep2["nudges"] == 2
    assert ep2["last_nudge_pct"] == BAR + 1 + pn.RENUDGE_DELTA
    assert ep2["last_nudge_at"] == T0 + GRACE + 1


def test_cap_stops_nudging_after_max():
    ep = {"first_over_at": T0, "last_nudge_at": T0, "last_nudge_pct": 90, "nudges": pn.MAX_NUDGES}
    v, ep2 = pn.evaluate_proactive_nudge(True, 99, ep, T0 + 10 * GRACE)
    assert v == "cap" and ep2 == ep  # no further send; dead-man page owns escalation past the cap


def test_drop_below_bar_after_episode_rearms():
    ep = {"first_over_at": T0, "last_nudge_at": T0, "last_nudge_pct": 88, "nudges": 2}
    assert pn.evaluate_proactive_nudge(True, BAR - 5, ep, T0 + 9000) == ("ok", None)


# ── PURE message ─────────────────────────────────────────────────────────────
def test_message_directs_handoff_before_self_recycle():
    subj, body = pn.proactive_nudge_message("cc-cosem-exams", 82)
    assert subj.startswith(pn.SUBJECT_PREFIX + " cc-cosem-exams:")
    assert "self_recycle.sh" in body
    assert "handoff" in body.lower()
    assert body.lower().index("handoff") < body.lower().index("self_recycle.sh")


def test_message_is_non_destructive_and_lane_decides():
    _, body = pn.proactive_nudge_message("cc-foo", 81)
    low = body.lower()
    assert "not a reset" in low or "never recycle you" in low
    assert "you decide" in low or "decline" in low


def test_message_escalates_on_renudge():
    _, body1 = pn.proactive_nudge_message("cc-foo", 81, nudge_n=1)
    subj2, body2 = pn.proactive_nudge_message("cc-foo", 90, nudge_n=2)
    assert "nudge #2" in subj2
    assert "still climbing" in body2.lower() and "nudge #2" in body2


# ── cross-tier dedup SQL shape ───────────────────────────────────────────────
class _RecCur:
    """Records execute() params and returns a preset fetchone()."""
    def __init__(self, fetch=None):
        self.params = None
        self._fetch = fetch

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return self._fetch


def test_nudged_recently_checks_both_prefixes():
    cur = _RecCur(fetch=None)
    assert pn._nudged_recently(cur, "cc-foo", cooldown_min=30) is False
    # both this tier's prefix AND the dead-man's prefix must be in the LIKE params
    joined = " ".join(str(p) for p in cur.params)
    assert pn.SUBJECT_PREFIX in joined
    assert pn.DEADMAN_PREFIX in joined
    assert "cc-foo" in joined


def test_nudged_recently_true_when_row_present():
    cur = _RecCur(fetch=(1,))
    assert pn._nudged_recently(cur, "cc-foo") is True


# ── run() wiring ─────────────────────────────────────────────────────────────
class _WireCur:
    def __init__(self):
        self.inserts = []
        self._dedup_hit = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._last_sql = sql
        if sql.strip().upper().startswith("INSERT"):
            self.inserts.append(params)
        self._is_dedup = "make_interval" in sql and "SELECT 1" in sql

    def fetchone(self):
        # dedup query -> return None (not recently nudged) so a send can proceed
        return None

    def fetchall(self):
        # op#29023: instance_id_for_session() queries agent_status for the session's instance.
        # Default [] -> resolver falls back to the base; a test can set _instance_rows to
        # exercise instance-addressing.
        return getattr(self, "_instance_rows", [])


class _WireConn:
    def __init__(self, cur):
        self._cur = cur
        self.commits = 0

    def cursor(self):
        return self._cur

    def commit(self):
        self.commits += 1


def _wire(monkeypatch, *, lanes, gauge_pct, enabled, dry, lease_ok=True, saved=None):
    """Wire run() with fakes. gauge_pct maps base -> pct (int) to feed resolve_lane_pct."""
    monkeypatch.setattr(slr, "discover_lanes", lambda conn, **kw: lanes)
    # feed the reading path: _gauge returns tokens for the pct, _pane blind, resolve = known/pct
    monkeypatch.setattr(dm, "_pane", lambda s: (None, None))

    def _fake_gauge(cur, base):
        p = gauge_pct.get(base)
        return (int(p / 100.0 * 1_000_000), 5) if p is not None else (None, None)
    monkeypatch.setattr(dm, "_gauge", _fake_gauge)
    monkeypatch.setattr(pn, "_lease_ok", lambda: (lease_ok, "test"))
    monkeypatch.setattr(pn, "ENABLED", enabled)
    monkeypatch.setattr(pn, "_load_state", lambda: {})
    if saved is not None:
        monkeypatch.setattr(pn, "_save_state", lambda st: saved.update(st))
    else:
        monkeypatch.setattr(pn, "_save_state", lambda st: None)


def test_run_nudges_a_bloated_worker_when_armed(monkeypatch):
    cur = _WireCur()
    conn = _WireConn(cur)
    lanes = [{"lane": "irsyad-coord", "base_agent_id": "cc-irsyad-coord", "tmux_session": "irsyad-coord"}]
    saved = {}
    _wire(monkeypatch, lanes=lanes, gauge_pct={"cc-irsyad-coord": 91}, enabled=True, dry=False, saved=saved)
    pn.run(conn, dry=False)
    assert len(cur.inserts) == 1                        # one nudge sent
    params = cur.inserts[0]
    assert params[0] == "cc-irsyad-coord"              # to_agent = base
    assert pn.SUBJECT_PREFIX in params[1]              # proactive subject
    assert slr.state_key_for("cc-irsyad-coord", "irsyad-coord") in saved  # episode persisted (armed + holder)


def test_run_is_inert_when_not_enabled(monkeypatch):
    cur = _WireCur()
    conn = _WireConn(cur)
    lanes = [{"lane": "irsyad-coord", "base_agent_id": "cc-irsyad-coord", "tmux_session": "irsyad-coord"}]
    saved = {}
    _wire(monkeypatch, lanes=lanes, gauge_pct={"cc-irsyad-coord": 91}, enabled=False, dry=False, saved=saved)
    pn.run(conn, dry=False)
    assert cur.inserts == []                            # NEVER sends while inert
    assert saved == {}                                 # and never mutates state


def test_run_downgrades_to_scanlog_when_lease_not_held(monkeypatch):
    cur = _WireCur()
    conn = _WireConn(cur)
    lanes = [{"lane": "irsyad-coord", "base_agent_id": "cc-irsyad-coord", "tmux_session": "irsyad-coord"}]
    saved = {}
    _wire(monkeypatch, lanes=lanes, gauge_pct={"cc-irsyad-coord": 91},
          enabled=True, dry=False, lease_ok=False, saved=saved)
    pn.run(conn, dry=False)
    assert cur.inserts == []                            # hub holds the lease -> no send
    assert saved == {}                                 # no state mutation while non-holder


def test_run_below_bar_does_not_nudge(monkeypatch):
    cur = _WireCur()
    conn = _WireConn(cur)
    # a WORKER lane below the bar (cc-quality is now a SINGLETON_BODY — CAI-1392 A —
    # so it is no longer a valid worker-lane fixture; a singleton here would correctly
    # raise via assert_sre_never_targets_singleton, tested separately below).
    lanes = [{"lane": "cc-cosem-video", "base_agent_id": "cc-cosem-video", "tmux_session": "cosem-video"}]
    _wire(monkeypatch, lanes=lanes, gauge_pct={"cc-cosem-video": 40}, enabled=True, dry=False)
    pn.run(conn, dry=False)
    assert cur.inserts == []


def test_run_never_targets_a_singleton(monkeypatch):
    import pytest
    cur = _WireCur()
    conn = _WireConn(cur)
    # a singleton slipping through discover_lanes must crash loudly, never be nudged
    lanes = [{"lane": "cai", "base_agent_id": "cai", "tmux_session": "cai"}]
    _wire(monkeypatch, lanes=lanes, gauge_pct={"cai": 95}, enabled=True, dry=False)
    with pytest.raises(fhb_boundary_error()):
        pn.run(conn, dry=False)
    assert cur.inserts == []


def fhb_boundary_error():
    from scripts.lib import fleet_health_boundaries as fhb
    return fhb.BoundaryViolation


# ── op#29023/#63284/#63300/#63312 — instance-addressing + stale-gauge age-gate ──
def test_run_nudges_the_instance_id_not_the_bare_base(monkeypatch):
    """A base with a live instance for the session nudges the INSTANCE id, not the bare base
    (a base-addressed nudge is declined by every sibling)."""
    cur = _WireCur()
    cur._instance_rows = [("cc-cosem-adcda-2", "cosem-adcda-urgent")]
    conn = _WireConn(cur)
    lanes = [{"lane": "cosem-adcda-urgent", "base_agent_id": "cc-cosem-adcda",
              "tmux_session": "cosem-adcda-urgent"}]
    _wire(monkeypatch, lanes=lanes, gauge_pct={"cc-cosem-adcda": 91}, enabled=True, dry=False)
    pn.run(conn, dry=False)
    assert len(cur.inserts) == 1
    assert cur.inserts[0][0] == "cc-cosem-adcda-2"     # instance id, NOT bare cc-cosem-adcda


def test_multi_sibling_same_base_episode_is_not_erased_by_a_later_sibling(monkeypatch):
    """Repro (bus #73009/#73011): once discover_lanes(per_instance=True) returns 2 rows for one
    base, a bare-base state key let whichever sibling is processed LAST in the pass overwrite
    (or pop) the FIRST sibling's just-started nudge episode, within the SAME sweep. x-1 crosses
    the proactive bar and starts an episode; x-3 (same base, processed right after it) reads
    comfortably below bar -> 'ok' pops state[base], erasing x-1's brand-new episode before it was
    ever saved."""
    cur = _WireCur()
    conn = _WireConn(cur)
    lanes = [
        {"lane": "cc-x-1", "base_agent_id": "cc-x", "tmux_session": "x-1"},
        {"lane": "cc-x-3", "base_agent_id": "cc-x", "tmux_session": "x-3"},
    ]
    saved = {}
    _wire(monkeypatch, lanes=lanes, gauge_pct={}, enabled=True, dry=False, saved=saved)
    monkeypatch.setattr(dm, "_pane", lambda s: (BAR + 5, None) if s == "x-1" else (BAR - 20, None))
    pn.run(conn, dry=False)
    key1 = slr.state_key_for("cc-x", "x-1")
    assert key1 in saved, (
        "x-1's nudge episode must survive x-3 (same base) being processed after it")


def test_run_skips_nudge_on_stale_high_gauge(monkeypatch):
    """A stale-high gauge (age > tight cutoff) with a blind pane re-samples to UNKNOWN -> NO
    nudge (the false-fire class that paged orch-console 6x)."""
    cur = _WireCur()
    conn = _WireConn(cur)
    lanes = [{"lane": "cosem-platform-author", "base_agent_id": "cc-cosem-platform",
              "tmux_session": "cosem-platform-author"}]
    _wire(monkeypatch, lanes=lanes, gauge_pct={"cc-cosem-platform": 86}, enabled=True, dry=False)
    # override the gauge to be STALE (age beyond the tight nudge cutoff); pane already blind
    monkeypatch.setattr(dm, "_gauge",
                        lambda cur, base: (int(0.86 * 1_000_000), pn.NUDGE_GAUGE_STALE_S + 900))
    pn.run(conn, dry=False)
    assert len(cur.inserts) == 0                        # stale-high gauge must NOT nudge
