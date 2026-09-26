"""A brand-new lane that has NEVER written a bus row (last_write_age = +inf) but has unread rows
must not crash the wedge alerts. int(float('inf')) raised OverflowError and paged
'Lane-wedge watchdog CRASHED' every scan (op#22553-55, Nazim #43478). Render 'never' instead,
and dedup the crash page. Pure functions — no DB/tmux."""
import pytest

from nervous_system import lane_wedge_watchdog as w


def _never_wrote_obs():
    bus = w.BusSignal(unread=3, oldest_unread_age=1800.0, last_write_age=float("inf"),
                      actionable=3, wake_eligible=3)
    return w.AgentObs("cc-oeh", "lane", "oeh", bus, w.ComposerSignal(w.COMP_EMPTY))


def test_wedge_alert_never_wrote_lane_renders_never_not_crash():
    txt = w._wedge_alert(_never_wrote_obs(), elapsed_min=30, unsafe=False, armed=False)
    assert "never" in txt
    assert "inf" not in txt.lower()


def test_menu_trap_alert_never_wrote_lane_renders_never_not_crash():
    txt = w._menu_trap_alert(_never_wrote_obs(), elapsed_min=30)
    assert "never" in txt


def test_quiet_helpers_render_never_for_inf():
    assert w._quiet_token(float("inf")) == "never"
    assert w._quiet_token(300) == "5m"
    assert w._quiet_clause(float("inf")) == "has never written to the bus"
    assert w._quiet_clause(300).endswith("in 5 min")


def test_crash_page_dedups_then_rearms_on_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "_CRASH_STATE_FILE", tmp_path / "crash.json")
    fp = w._crash_fingerprint(OverflowError("cannot convert float infinity to integer"))
    assert w._crash_page_due(fp) is True    # first sighting -> page
    assert w._crash_page_due(fp) is False   # same crash -> stay quiet
    w._clear_crash_state()                  # a clean scan re-arms
    assert w._crash_page_due(fp) is True    # new sighting after clear -> page again
