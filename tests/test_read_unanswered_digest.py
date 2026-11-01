"""Acceptance tests for the read-but-unanswered digest (orch-console #88653/#88660).

The digest is REPORT-ONLY and must never claim more than it measured. The tests pin
the three claims that matter:

  1. A lane that is pane-DRAINED and owns such rows is reported as IDLE.
  2. A lane whose pane is BUSY is NOT reported as idle (it is counted as omitted).
  3. A lane we could not probe is NOT reported as idle either — 'could not measure'
     is its own state, never a negative finding (verify-not-assert).

Pure core, no DB, no tmux: collect() is exercised for the pane-state routing and
render() for the text, with read_unanswered/live_agents/lane_map stubbed.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import read_unanswered_digest as d  # noqa: E402


def _row(i, age_h=10.0, pri="P1", frm="orch-console", subj="a question"):
    """One DB row in read_unanswered's column order:
    (id, from_agent, message_type, priority, subject, age_h)."""
    return (i, frm, "question", pri, subj, age_h)


def _entry_row(i, age_h=10.0, pri="P1", frm="orch-console", subj="a question"):
    """The dict form collect() puts in an entry's rows[] (for direct render tests)."""
    return {"id": i, "from": frm, "type": "question", "priority": pri,
            "subject": subj, "age_h": age_h}


def _patch(monkeypatch, agents, rows_by_agent, pane, base_rows=None, lanes=None):
    monkeypatch.setattr(d, "live_agents", lambda cur, mh: agents)
    monkeypatch.setattr(d, "read_unanswered", lambda cur, a, h, p: rows_by_agent.get(a, []))
    monkeypatch.setattr(d, "read_unanswered_any",
                        lambda cur, ids, h, p: (base_rows or {}).get(ids[0], []))
    monkeypatch.setattr(d, "lane_map",
                        lambda cur: {"cc-a": "sess-a", "cc-b": "sess-b"} if lanes is None else lanes)
    monkeypatch.setattr(d, "pane_state", lambda s: pane.get(s, "probe_error"))
    monkeypatch.setattr(d, "local_hostnames", lambda: {"Sheikhs-Mini"})


def test_drained_lane_is_idle(monkeypatch):
    _patch(monkeypatch,
           [("cc-a-1", "cc-a", "Sheikhs-Mini", 2)],
           {"cc-a-1": [_row(1)]},
           {"sess-a": "drained"})
    out = d.collect(cur=None)
    assert [e["agent"] for e in out["idle"]] == ["cc-a-1"]
    assert out["not_probeable"] == [] and out["busy"] == 0


def test_busy_lane_is_not_idle(monkeypatch):
    """A working lane owes the reply but is demonstrably not idle — count, don't list."""
    _patch(monkeypatch,
           [("cc-a-1", "cc-a", "Sheikhs-Mini", 1)],
           {"cc-a-1": [_row(1)]},
           {"sess-a": "busy"})
    out = d.collect(cur=None)
    assert out["idle"] == [] and out["not_probeable"] == [] and out["busy"] == 1


def test_unprobeable_lane_is_not_idle(monkeypatch):
    _patch(monkeypatch,
           [("cc-a-1", "cc-a", "gzbai", 1)],
           {"cc-a-1": [_row(1)]},
           {"sess-a": "drained"})  # would be drained, but it is on ANOTHER host
    out = d.collect(cur=None)
    assert out["idle"] == []
    assert [e["agent"] for e in out["not_probeable"]] == ["cc-a-1"]
    assert out["not_probeable"][0]["pane"] == "not_probeable"


def test_probe_error_is_not_idle(monkeypatch):
    _patch(monkeypatch,
           [("cc-a-1", "cc-a", "Sheikhs-Mini", 1)],
           {"cc-a-1": [_row(1)]},
           {"sess-a": "probe_error"})
    out = d.collect(cur=None)
    assert out["idle"] == []
    assert out["not_probeable"][0]["pane"] == "probe_error"


def test_lane_with_no_rows_is_absent(monkeypatch):
    _patch(monkeypatch, [("cc-a-1", "cc-a", "Sheikhs-Mini", 1)], {}, {})
    out = d.collect(cur=None)
    assert out["idle"] == [] and out["not_probeable"] == [] and out["busy"] == 0


def test_self_excluded_by_default_but_flag_restores(monkeypatch):
    _patch(monkeypatch,
           [(d.SELF_AGENT, "cc-fleet-health", "Sheikhs-Mini", 1)],
           {d.SELF_AGENT: [_row(9)]},
           {})
    assert d.collect(cur=None)["not_probeable"] == []
    assert [e["agent"] for e in d.collect(cur=None, include_self=True)["not_probeable"]] \
        == [d.SELF_AGENT]


def test_render_empty_says_empty_not_reassuring(monkeypatch):
    """An empty digest must say nothing was found — not manufacture a health claim."""
    text = d.render({"generated_at": "T", "thresholds": {"min_age_h": 6,
                                                         "priorities": ["P1"],
                                                         "max_heartbeat_min": 30},
                     "idle": [], "not_probeable": [], "busy": 0})
    assert "Nothing to report" in text


def test_render_bounds_rows_per_lane():
    entry = {"agent": "cc-a-1", "base": "cc-a", "host": "Sheikhs-Mini",
             "hb_age_min": 1, "pane": "drained", "session": "sess-a",
             "rows": [_entry_row(i, age_h=100 - i) for i in range(10)]}
    text = d.render({"generated_at": "T",
                     "thresholds": {"min_age_h": 6, "priorities": ["P1"],
                                    "max_heartbeat_min": 30},
                     "idle": [entry], "not_probeable": [], "busy": 0})
    assert "... +7 more row(s)" in text
    assert text.count("#1  ") <= 1
    assert "#8  " not in text          # the 9th row is summarised, not printed


def test_render_includes_stamp_gap_caveat():
    entry = {"agent": "cc-a-1", "base": "cc-a", "host": "Sheikhs-Mini",
             "hb_age_min": 1, "pane": "drained", "session": "sess-a",
             "rows": [_entry_row(1)]}
    text = d.render({"generated_at": "T",
                     "thresholds": {"min_age_h": 6, "priorities": ["P1"],
                                    "max_heartbeat_min": 30},
                     "idle": [entry], "not_probeable": [], "busy": 0})
    assert "stamping gap" in text


# ── F1 (cc-quality, #789 review): BASE-addressed mail must not be invisible ──
# A row addressed to `cc-irsyad` while four live instances run is the same class
# this digest exists to catch. It must appear EXACTLY ONCE, never N-fold.

def test_base_addressed_rows_reported_once(monkeypatch):
    _patch(monkeypatch,
           [("cc-a-1", "cc-a", "Sheikhs-Mini", 1),
            ("cc-a-2", "cc-a", "Sheikhs-Mini", 2)],
           {},                                    # no instance-addressed mail
           {"sess-a": "drained"},
           base_rows={"cc-a": [_row(5)]})         # ...only base-addressed mail
    out = d.collect(cur=None)
    assert [e["agent"] for e in out["idle"]] == ["cc-a"], "attributed once, not per sibling"
    assert len(out["idle"][0]["rows"]) == 1


def test_base_entry_not_idle_when_pane_busy(monkeypatch):
    _patch(monkeypatch,
           [("cc-a-1", "cc-a", "Sheikhs-Mini", 1),
            ("cc-a-2", "cc-a", "Sheikhs-Mini", 2)],
           {}, {"sess-a": "busy"}, base_rows={"cc-a": [_row(5)]})
    out = d.collect(cur=None)
    assert out["idle"] == []
    assert out["busy"] == 1


def test_base_entry_not_idle_when_a_sibling_is_on_another_host(monkeypatch):
    """One sibling we cannot probe makes the whole base NOT PROBEABLE — the mail
    might be being worked there, so 'idle' would be a false claim."""
    _patch(monkeypatch,
           [("cc-a-1", "cc-a", "Sheikhs-Mini", 1),
            ("cc-a-2", "cc-a", "gzbai", 2)],
           {}, {"sess-a": "drained"}, base_rows={"cc-a": [_row(5)]})
    out = d.collect(cur=None)
    assert out["idle"] == []
    assert [e["agent"] for e in out["not_probeable"]] == ["cc-a"]


def test_singleton_running_as_its_base_is_not_double_reported(monkeypatch):
    _patch(monkeypatch,
           [("cc-a", "cc-a", "Sheikhs-Mini", 1)],
           {"cc-a": [_row(1)]},
           {"sess-a": "drained"},
           base_rows={"cc-a": [_row(2)]})          # same mailbox, second query
    out = d.collect(cur=None)
    assert [e["agent"] for e in out["idle"]] == ["cc-a"]
    assert len(out["idle"]) == 1


# ── Follow-up to #790: an INSTANCE may key fleet_lanes by its own id ──
# agent_status.base_agent_id is not always the fleet_lanes key. cc-quality-2
# carries base 'cc-quality' in agent_status while fleet_lanes and its own env both
# say 'cc-quality-2' (orch-console #88758). Resolving the pane from the base alone
# probes the wrong lane's pane, so the digest reports one lane's activity as
# evidence about another's.

def test_instance_with_its_own_lane_row_probes_its_own_pane(monkeypatch):
    """Own-lane row wins over the base's: the instance is drained, the base is busy."""
    _patch(monkeypatch,
           [("cc-a-2", "cc-a", "Sheikhs-Mini", 1)],
           {"cc-a-2": [_row(1)]},
           {"sess-inst": "drained", "sess-base": "busy"},
           lanes={"cc-a": "sess-base", "cc-a-2": "sess-inst"})
    out = d.collect(cur=None)
    assert [e["agent"] for e in out["idle"]] == ["cc-a-2"], "own pane wins over the base's"
    assert out["idle"][0]["session"] == "sess-inst"
    assert out["busy"] == 0


def test_base_pass_probes_each_sibling_on_its_own_pane(monkeypatch):
    """A busy sibling must be seen as busy even when the BASE's session is drained.
    Reading one base session for every member made a busy sibling look idle."""
    _patch(monkeypatch,
           [("cc-a-1", "cc-a", "Sheikhs-Mini", 1),
            ("cc-a-2", "cc-a", "Sheikhs-Mini", 2)],
           {},                                        # no instance-addressed mail
           {"sess-1": "drained", "sess-2": "busy", "sess-a": "drained"},
           base_rows={"cc-a": [_row(5)]},
           lanes={"cc-a": "sess-a", "cc-a-1": "sess-1", "cc-a-2": "sess-2"})
    out = d.collect(cur=None)
    assert out["idle"] == [], "sibling 2 is busy -> the base is busy, never idle"
    assert out["busy"] == 1
