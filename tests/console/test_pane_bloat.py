"""op#13050-B: the honest pane-truth header + glance (console-side).

Locks the three-state header (_pane_header: clear/alert/unknown) and the worker-only
glance (_pane_bloat) built from the FRESH pane_context feed — the gauge-independent
ground truth that replaces the lying DB gauge in the console header. Amber/red use the
SAME _ctx_level thresholds (one vocabulary); no-hint rows (pane_k NULL) are not bloat;
coordinators are excluded from the GLANCE (own cards) but INCLUDED in the header honesty.
"""
from nervous_system.console import app as console_app


def _pane(session, pane_k, base=None, idle="IDLE_EMPTY", age_s=5, pct=None):
    return {"session": session, "base": base if base is not None else "cc-" + session,
            "pane_k": pane_k, "pct": pct, "idle_verdict": idle, "age_s": age_s}


# ── _pane_header: three honest states, NEVER a false 'All clear' ──────────────
def test_header_unknown_when_feed_empty():
    # No fresh pane rows (publisher down/stale) => UNKNOWN, never green.
    h = console_app._pane_header([])
    assert h["state"] == "unknown" and h["worst"] is None


def test_header_clear_when_all_below_amber():
    h = console_app._pane_header([_pane("storefront", 300.0), _pane("finance", None)])
    assert h["state"] == "clear" and h["worst"] is None


def test_header_alert_when_a_body_over_amber():
    # 652k => 65% => amber => ALERT with worst offender.
    h = console_app._pane_header([_pane("storefront", 300.0), _pane("shipforge", 652.4)])
    assert h["state"] == "alert"
    assert h["worst"]["session"] == "shipforge" and h["worst"]["level"] == "amber"


def test_header_alert_picks_highest_worst():
    h = console_app._pane_header([_pane("shipforge", 652.4), _pane("irsyad", 900.0)])
    assert h["worst"]["session"] == "irsyad" and h["worst"]["pct"] == 90


def test_header_includes_mini_singletons_for_honesty():
    # A bloated Mini singleton (cai) must trip the header — honesty covers them even
    # though the GLANCE excludes coordinators.
    h = console_app._pane_header([_pane("cai", 700.0, base="cai")])
    assert h["state"] == "alert" and h["worst"]["label"] == "cai"


def test_header_no_hint_rows_are_not_bloat():
    # pane_k NULL (below CC's nudge bar OR mid-turn) is never an alert.
    h = console_app._pane_header([_pane("irsyad", None, idle="WORKING")])
    assert h["state"] == "clear"


# ── _pane_bloat: worker-only glance from pane-truth ──────────────────────────
def test_glance_excludes_coordinators():
    rows = [_pane("cai", 700.0, base="cai"), _pane("shipforge", 652.4)]
    out = console_app._pane_bloat(rows)
    agents = {r["agent"] for r in out}
    assert "cc-shipforge" in agents
    assert "cai" not in agents  # coordinator excluded from the glance (op#9088)


def test_glance_skips_no_hint_and_sets_pane_source():
    out = console_app._pane_bloat([_pane("shipforge", 652.4), _pane("storefront", None)])
    assert len(out) == 1
    r = out[0]
    assert r["agent"] == "cc-shipforge" and r["pct"] == 65 and r["level"] == "amber"
    assert r["source"] == "pane" and r["sub_tag"] == "shipforge"


def test_glance_empty_when_feed_empty():
    assert console_app._pane_bloat([]) == []


# ── op#13186: the CLIFF signal — pct wins where the /clear hint has vanished ──
def test_cliff_lane_pct_only_is_top_bloat_and_red():
    # THE BUG: cc-ihsanos-1 @100% shows `% context used`, NO /clear hint -> pane_k NULL.
    # Before op#13186 it read as not-bloated (invisible). Now pct=100 makes it red.
    out = console_app._pane_bloat([_pane("ihsanos", None, pct=100),
                                   _pane("shipforge", 652.4)])
    lead = out[0]  # sorted fullest-first
    assert lead["agent"] == "cc-ihsanos" and lead["pct"] == 100
    assert lead["level"] == "red" and lead["src"] == "pct"


def test_cliff_lane_trips_header_alert():
    # A maxed lane with pane_k NULL but pct=100 MUST make the header alert (was false-green).
    h = console_app._pane_header([_pane("ihsanos", None, pct=100)])
    assert h["state"] == "alert"
    assert h["worst"]["session"] == "ihsanos" and h["worst"]["pct"] == 100


def test_pct_wins_over_hint_when_both_present():
    # Both signals present -> pct is authoritative (near the cliff CC can show both briefly).
    out = console_app._pane_bloat([_pane("ihsanos", 200.0, pct=97)])
    assert len(out) == 1 and out[0]["pct"] == 97 and out[0]["src"] == "pct"


def test_hint_path_marks_src_k_when_no_pct():
    # Below the cliff (no pct line) the /clear hint still drives the reading, marked 'k'.
    out = console_app._pane_bloat([_pane("shipforge", 652.4)])
    assert len(out) == 1 and out[0]["pct"] == 65 and out[0]["src"] == "k"


def test_both_signals_absent_is_not_bloat():
    # No pct AND no hint (below nudge bar OR mid-turn) => dropped, fail-closed (not green).
    assert console_app._pane_bloat([_pane("finance", None, pct=None)]) == []


# ── header ↔ glance consistency invariant (console 21518) ────────────────────
def test_glance_includes_coords_with_friendly_label():
    # include_coords=True => the whole-fleet glance; nazim -> friendly 'orch-console'.
    rows = [_pane("nazim", 640.0, base=None), _pane("shipforge", 652.4)]
    out = console_app._pane_bloat(rows, include_coords=True)
    labels = {r["agent"] for r in out}
    assert "orch-console" in labels and "cc-shipforge" in labels


def test_header_alert_iff_glance_has_amber_body():
    # The exact contradiction console caught: a coord (orch-console) amber in the glance
    # MUST make the header alert — header derives from the SAME entries as the glance.
    rows = [_pane("nazim", 640.0, base=None)]           # orch-console 64% => amber
    glance = console_app._pane_bloat(rows, include_coords=True)
    header = console_app._pane_header(rows)
    assert any(e["level"] in ("amber", "red") for e in glance)
    assert header["state"] == "alert" and header["worst"]["label"] == "orch-console"


def test_true_clear_has_all_green_glance_and_clear_header():
    # console's rule: an all-GREEN banner (nothing >= amber) <-> 'All clear' header.
    rows = [_pane("cai", 500.0, base="cai"), _pane("storefront", 480.0)]  # 50%,48% => green
    glance = console_app._pane_bloat(rows, include_coords=True)
    header = console_app._pane_header(rows)
    assert glance and all(e["level"] == "green" for e in glance)
    assert header["state"] == "clear"


# ── SRE disposition per worker lane (item 2, Nazim #31750 — "am i sre?") ──────
# Each lane entry carries the SRE's current disposition, DERIVED live from pct +
# idle_verdict (no separate stored field that could go stale). So a red/amber lane
# ALWAYS renders "and here's who's on it" — the thing that lets Musa look away.
def test_sre_disposition_healthy_below_amber():
    # green (< 60%) => SRE has nothing to do.
    e = console_app._pane_entry(_pane("finance", 400.0))          # 40% green
    assert e["sre_disposition"]["state"] == "healthy"


def test_sre_disposition_watching_amber_below_recycle_bar():
    # amber but below the ~65% idle-recycle bar => SRE watching, not yet acting.
    e = console_app._pane_entry(_pane("finance", 620.0))          # 62% amber, < 65 bar
    assert e["sre_disposition"]["state"] == "watching"


def test_sre_disposition_recycling_when_bloated_and_idle():
    # at/above the recycle bar AND idle => SRE recycles it (Stage-1: SRE acts on the WARN).
    e = console_app._pane_entry(_pane("irsyad", 700.0, idle="IDLE_EMPTY"))   # 70% idle
    assert e["sre_disposition"]["state"] == "recycling"


def test_sre_disposition_held_when_bloated_but_active():
    # at/above the bar BUT busy => SRE HOLDS (never-interrupt active work) — the coord lesson.
    e = console_app._pane_entry(_pane("irsyad", 700.0, idle="WORKING"))      # 70% busy
    assert e["sre_disposition"]["state"] == "held"


def test_sre_recycle_bar_stays_coupled_to_the_daemon_fire_bar():
    # cc-quality LOW#1: _SRE_RECYCLE_PCT is comment-coupled to the daemon's PANE_FIRE_K.
    # Guard it: if the daemon bar moves and this doesn't, the console would show a
    # disposition at a threshold the daemon no longer acts on. (Display-only, but a
    # silent drift = exactly the lie this whole feature exists to avoid.)
    from scripts.auto_recycle_on_bloat import PANE_FIRE_K
    assert console_app._SRE_RECYCLE_PCT == PANE_FIRE_K * 100 // 1000


def test_sre_disposition_wedged_lane_not_labeled_active_work():
    # cc-quality LOW#2: a wedged/unsure lane isn't "active work" (it's stuck; a separate
    # watchdog recovers it). 'held' is still correct (not-recycling), but the label must
    # not claim active work for a wedged lane.
    for v in ("GHOST_WEDGED", "UNSURE", "UNSURE:TimeoutError"):
        e = console_app._pane_entry(_pane("irsyad", 700.0, idle=v))
        assert e["sre_disposition"]["state"] == "held"
        assert "active work" not in e["sre_disposition"]["label"].lower()


def test_sre_disposition_busy_lane_still_labeled_active_work():
    # a genuinely busy lane (WORKING/STAGED) keeps the informative "active work" label.
    for v in ("WORKING", "STAGED"):
        e = console_app._pane_entry(_pane("irsyad", 700.0, idle=v))
        assert e["sre_disposition"]["state"] == "held"
        assert "active work" in e["sre_disposition"]["label"].lower()


def test_header_worst_carries_sre_disposition():
    # The RESTING banner shows the worst lane; it must carry the SRE disposition so it
    # reads "irsyad 90% — SRE recycling", not "— context building" (the am-i-sre fix).
    h = console_app._pane_header([_pane("irsyad", 900.0, idle="IDLE_EMPTY")])   # 90% idle
    assert h["worst"]["sre_disposition"]["state"] == "recycling"
    h2 = console_app._pane_header([_pane("irsyad", 900.0, idle="WORKING")])     # 90% busy
    assert h2["worst"]["sre_disposition"]["state"] == "held"


def test_sre_disposition_cliff_idle_is_recycling():
    # pct-only cliff lane (pane_k NULL, pct=98) idle => still 'recycling' (SRE on it).
    e = console_app._pane_entry(_pane("irsyad", None, pct=98, idle="IDLE_EMPTY"))
    assert e["sre_disposition"]["state"] == "recycling"
