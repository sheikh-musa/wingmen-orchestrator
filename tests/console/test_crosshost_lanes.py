"""Cross-host lanes + the hub's model on the fleet console (2026-10-01).

Operator, 2026-10-01: (1) "I don't see any irsyad lanes in fleet console";
(2) "I still don't see the hub's model".

Causes locked here:
  * app._enrich_lanes_live classified a lane ONLY by a LOCAL tmux pane, so every
    gzb lane (no pane on the Mini) read offline — dark tile / folded / dropped.
    Now a cross-host lane is classified from its own fresh heartbeat.
  * db.build_lanes_query's base_agent_id fallback join gave a session-less
    instance an ARBITRARY sibling's lane label (cc-irsyad-2 -> 'irsyad-worker-1'),
    so _dedupe_lanes_by_family collapsed one live irsyad body into another.
  * Coordinators resolved their model from LOCAL proc only (host Mini), so the gzb
    hub had no model source. Now its own heartbeat-stamped `model=` token is read.
"""
import pytest

from nervous_system.console import app as console_app
from nervous_system.console import db
from nervous_system.console import hosted_view


@pytest.fixture(autouse=True)
def _mini_console(monkeypatch):
    """This console runs on the Mini; no local tmux pane for any gzb session."""
    monkeypatch.setattr(console_app, "_LOCAL_HOST_CACHE", {"v": "sheikhs-mini"})
    monkeypatch.setattr(console_app.panes, "capture",
                        lambda s, live=None: ({"running": True, "state": "idle"}, ""))


def _row(agent_id, host, sess, hb, lane=None, act=None, desired=None, ct=None):
    return {"agent_id": agent_id, "base_agent_id": agent_id.rsplit("-", 1)[0],
            "host": host, "tmux_session": sess, "lane": lane, "heartbeat_age_s": hb,
            "activity_age_s": act, "desired_state": desired, "current_task": ct}


def _fleet_lanes(rows, live=()):
    lanes = console_app._dedupe_lanes_by_family(
        console_app._enrich_lanes_live(rows, live=set(live)))
    return {l["agent_id"]: console_app._lane_bucket(l) for l in lanes}


# --- (A/D) a gzb lane with host set appears, live -----------------------------

def test_gzb_lane_with_host_and_fresh_heartbeat_reads_live_not_dark():
    out = _fleet_lanes([_row("cc-irsyad-1", "gzbai", "irsyad-worker-1", 74,
                             lane="irsyad-worker-1", act=19000, desired="up")])
    # was ('offline', True) = a DARK tile for a running lane
    assert out == {"cc-irsyad-1": ("idle", False)}


def test_gzb_lane_recent_bus_activity_reads_working():
    out = _fleet_lanes([_row("cc-irsyad-1", "gzbai", "irsyad-worker-1", 30, act=60)])
    assert out["cc-irsyad-1"] == ("working", False)


def test_gzb_lane_with_stale_heartbeat_stays_offline_never_guessed():
    out = _fleet_lanes([_row("cc-irsyad-9", "gzbai", "irsyad-worker-9", 5000,
                             desired="up")])
    assert out["cc-irsyad-9"] == ("offline", True)


def test_local_lane_without_a_pane_stays_dark_pane_truth_wins():
    """A Mini lane whose tmux died must still read dark even with a fresh hb."""
    out = _fleet_lanes([_row("cc-oeh-1", "Sheikhs-Mini", "oeh", 20, desired="up")])
    assert out["cc-oeh-1"] == ("offline", True)


def test_local_host_alias_is_not_mistaken_for_cross_host():
    """'Sheikhs-Mac-mini.local' is the Mini (fleet_hosts.json alias) — not remote."""
    out = _fleet_lanes([_row("cc-oeh-1", "Sheikhs-Mac-mini.local", "oeh", 20,
                             desired="up")])
    assert out["cc-oeh-1"] == ("offline", True)


def test_session_less_host_less_row_with_fresh_heartbeat_is_shown():
    """The lost-boot-stamp signature (cc-irsyad-2 live on 2026-10-01): no host, no
    session, fresh heartbeat. No local pane can ever bind it, so its heartbeat is
    the only signal — it must appear, not vanish."""
    out = _fleet_lanes([_row("cc-irsyad-2", None, None, 65, act=19000)])
    assert out == {"cc-irsyad-2": ("idle", False)}


def test_hostless_row_with_a_session_stays_pane_truth():
    out = _fleet_lanes([_row("cc-x-1", None, "x", 20, desired="up")])
    assert out["cc-x-1"] == ("offline", True)


# --- (D) a live row beats a ghost --------------------------------------------

def test_live_row_beats_offline_ghost_of_the_same_lane():
    """cc-irsyad-coord-2 = offline ghost (hb 5.5h) still holding host+session;
    cc-irsyad-coord-1 = the LIVE coordinator (stamp lost). Same lane label. The
    live one must be the card; the ghost must not hide it."""
    rows = [
        _row("cc-irsyad-coord-1", None, None, 112, lane="irsyad-coord", act=300,
             desired="down"),
        _row("cc-irsyad-coord-2", "gzbai", "irsyad-coord", 19893, lane="irsyad-coord",
             desired="down"),
    ]
    out = _fleet_lanes(rows)
    assert list(out) == ["cc-irsyad-coord-1"]
    assert out["cc-irsyad-coord-1"] == ("working", False)


def test_two_live_gzb_instances_both_shown():
    rows = [
        _row("cc-irsyad-1", "gzbai", "irsyad-worker-1", 70, lane="irsyad-worker-1"),
        _row("cc-irsyad-2", "gzbai", "irsyad-tabung-jumaat", 60, lane=None),
    ]
    assert set(_fleet_lanes(rows)) == {"cc-irsyad-1", "cc-irsyad-2"}


def test_lanes_sql_base_fallback_only_for_a_single_lane_base():
    """The base_agent_id fallback must be unambiguous: a multi-lane family's
    session-less instance gets NO lane label instead of an arbitrary sibling's
    (which made the dedupe collapse cc-irsyad-2 into cc-irsyad-1)."""
    for sql in (db.build_lanes_query()[0],):
        low = " ".join(sql.lower().split())
        assert "fl.lane = s.tmux_session or (fl.base_agent_id = s.base_agent_id" in low
        assert "where f2.base_agent_id = s.base_agent_id) = 1" in low
        assert "desc nulls last" in low


def test_hosted_lanes_sql_mirrors_the_single_lane_base_fallback():
    import inspect
    src = " ".join(inspect.getsource(hosted_view._clone_lanes).lower().split())
    assert "where f2.base_agent_id = s.base_agent_id) = 1" in src


# --- (C) a cross-host hub shows its model from its heartbeat ------------------

def test_cross_host_hub_model_comes_from_its_heartbeat_token():
    task = "hub — always-on orchestrator (VPS) model=claude-opus-4-8"
    # 'orch' is not a local session on the Mini -> no proc truth -> hb token.
    assert console_app._resolve_model("orch", task, None, {}) == ("claude-opus-4-8", "hb")


def test_hub_without_a_stamped_model_shows_unknown_never_a_guess():
    assert console_app._resolve_model("orch", "hub — always-on orchestrator (VPS)",
                                      None, {}) == (None, None)


def test_local_proc_truth_still_wins_over_heartbeat_token():
    task = "x model=claude-opus-4-8"
    assert console_app._resolve_model("nazim", task, None,
                                      {"nazim": "claude-opus-5-5"}) == ("claude-opus-5-5", "proc")


def test_boot_string_still_parses_as_boot():
    assert console_app._resolve_model(
        None, "session-launch model=claude-opus-4-8 repo=r", None, {}) == ("claude-opus-4-8", "boot")


def test_free_text_task_is_never_misread_as_a_model():
    for t in ("we discussed the model=pricing doc", "xmodel=claude-1", "model=; rm"):
        assert console_app._hb_model(t) is None


def test_coordinators_query_selects_current_task():
    sql, _ = db.build_coordinators_query()
    assert "a.current_task from agent_status a" in " ".join(sql.lower().split())


def test_fleet_payload_hub_card_carries_heartbeat_model(monkeypatch):
    """End-to-end through _fleet_payload: the cross-host hub coordinator card gets
    its model from the heartbeat-stamped current_task, and the raw current_task
    itself is NOT leaked into the payload."""
    hub = {"agent_id": "cc-orchestrator", "short": "Hub", "tmux_session": "orch",
           "host": "gzbai", "auth_fp": None, "ctx_tokens": None,
           "ctx_session_id": None, "ctx_current_session_id": None,
           "current_task": "hub — always-on orchestrator (VPS) model=claude-opus-4-8"}
    for name, val in (("fetch_lanes", []), ("fetch_deploys", []), ("fetch_needs_you", []),
                      ("fetch_coordinators", [hub]), ("fetch_backlog", []),
                      ("fetch_pane_context", []), ("fetch_pool_usage", []),
                      ("fetch_queue", []), ("fetch_inbox_backlog", []), ("fetch_asks", [])):
        monkeypatch.setattr(db, name, (lambda v: (lambda: v))(val))
    monkeypatch.setattr(console_app.panes, "live_sessions", lambda: [])
    monkeypatch.setattr(console_app, "_proc_models", lambda: {})
    out = console_app._fleet_payload()
    card = out["coordinators"][0]
    assert (card["model"], card["model_src"]) == ("claude-opus-4-8", "hb")
    assert "current_task" not in card


def test_hosted_coordinator_model_from_heartbeat_token():
    assert hosted_view._coord_model(
        "hub — always-on orchestrator (VPS) model=claude-opus-4-8") == ("claude-opus-4-8", "hb")
    assert hosted_view._coord_model("hub — always-on orchestrator (VPS)") == (None, None)
    assert hosted_view._coord_model(None) == (None, None)


def test_local_host_comes_from_fleet_host_id_not_a_legacy_label(monkeypatch):
    """The reader must use the SAME resolver lanes write host with; a legacy
    CONSOLE_HOST_LABEL='Mini' must not mark every local lane cross-host."""
    from scripts.lib import fleet_host_id as fhi
    monkeypatch.setattr(console_app, "_LOCAL_HOST_CACHE", {})
    monkeypatch.setenv("CONSOLE_HOST_LABEL", "Mini")
    monkeypatch.setattr(fhi, "fleet_host_id", lambda: "Sheikhs-Mini")
    assert console_app._local_host_label() == "sheikhs-mini"
