"""op#13050-B: the honest pane-truth header + glance (console-side).

Locks the three-state header (_pane_header: clear/alert/unknown) and the worker-only
glance (_pane_bloat) built from the FRESH pane_context feed — the gauge-independent
ground truth that replaces the lying DB gauge in the console header. Amber/red use the
SAME _ctx_level thresholds (one vocabulary); no-hint rows (pane_k NULL) are not bloat;
coordinators are excluded from the GLANCE (own cards) but INCLUDED in the header honesty.
"""
from nervous_system.console import app as console_app


def _pane(session, pane_k, base=None, idle="IDLE_EMPTY", age_s=5):
    return {"session": session, "base": base if base is not None else "cc-" + session,
            "pane_k": pane_k, "idle_verdict": idle, "age_s": age_s}


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
