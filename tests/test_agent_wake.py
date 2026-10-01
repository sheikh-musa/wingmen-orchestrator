"""Tests for nervous_system.agent_wake — #111 trigger policy + 5/5min loud cap
(CAI-RESP-259). Pure: no DB, no tmux."""
import json
import pytest

from nervous_system import agent_wake


# ── should_auto_wake (CAI-RESP-259 Q1) ────────────────────────────────────────
@pytest.mark.parametrize("to_agent,mtype,rr,prio,is_test,expected", [
    ("cc-ihsanos", "update", True, "P2", False, True),       # worker + rr
    ("cc-ihsanos", "blocker", False, "P2", False, True),     # actionable type
    ("cc-ihsanos", "update", False, "P1", False, True),      # P1 floor
    ("cc-ihsanos", "challenge", False, "P2", False, True),   # challenge wakes (window)
    ("cc-ihsanos", "update", False, "P2", False, False),     # plain FYI -> no wake
    ("cai", "decision", False, "P2", False, True),           # cai is wakeable
    # CAI-451/CAI-RESP-786: the hub IS auto-wake-eligible, but ONLY on the narrow
    # floor (P0/P1 AND requires_response). "NEVER cc-orchestrator" was a regression.
    ("cc-orchestrator", "decision", True, "P1", False, True),   # hub: P1 + rr -> wake
    ("cc-orchestrator", "question", True, "P0", False, True),   # hub: P0 + rr -> wake
    ("cc-orchestrator", "blocker", False, "P1", False, False),  # hub: P1 but rr=False -> no
    ("cc-orchestrator", "decision", True, "P2", False, False),  # hub: rr but P2 -> no
    ("orch-console", "update", True, "P2", False, True),        # console (wake-A): fully eligible + rr
    ("orch-console", "update", False, "P2", False, False),      # console: plain FYI -> no realtime wake
    ("musa", "question", True, "P0", False, False),          # never wake the operator
    ("cc-ihsanos", "blocker", True, "P1", True, False),      # is_test never
    ("cc-ihsanos", "blocker", True, "P3", False, False),     # P3 never
])
def test_should_auto_wake(to_agent, mtype, rr, prio, is_test, expected):
    assert agent_wake.should_auto_wake(to_agent, mtype, rr, prio, is_test) is expected


# ── kill-switch ───────────────────────────────────────────────────────────────
def test_kill_switch_default_off(monkeypatch):
    monkeypatch.delenv("AUTO_WAKE_ENABLED", raising=False)
    assert agent_wake.auto_wake_enabled() is False
    monkeypatch.setenv("AUTO_WAKE_ENABLED", "1")
    assert agent_wake.auto_wake_enabled() is True
    monkeypatch.setenv("AUTO_WAKE_ENABLED", "0")
    assert agent_wake.auto_wake_enabled() is False


# ── rolling-window cap (CAI-RESP-259 Q4) ──────────────────────────────────────
@pytest.fixture
def wake_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_wake, "_WAKE_DIR", tmp_path)
    return tmp_path


def _seed(wake_dir, agent, epochs):
    (wake_dir / f"{agent}.json").write_text(json.dumps({"wakes": epochs}))


def test_cap_allows_when_fresh(wake_dir):
    assert agent_wake.cap_state("cc-ihsanos", now=1000.0)["allow"] is True


def test_cap_debounces_recent(wake_dir):
    _seed(wake_dir, "cc-ihsanos", [1000.0])
    g = agent_wake.cap_state("cc-ihsanos", now=1010.0)  # 10s later
    assert g["allow"] is False and g["why"] == "debounced"


def test_cap_hits_at_five_in_window(wake_dir):
    # 5 wakes within 300s, none within 45s of now -> cap hit, fails loud
    _seed(wake_dir, "cc-ihsanos", [800.0, 850.0, 900.0, 920.0, 940.0])
    g = agent_wake.cap_state("cc-ihsanos", now=1000.0)
    assert g["allow"] is False and g.get("cap_hit") is True and g["count"] == 5


def test_cap_window_expires_old_wakes(wake_dir):
    # only 2 of these are within 300s of now=1000 -> allowed
    _seed(wake_dir, "cc-ihsanos", [600.0, 650.0, 800.0, 850.0])
    assert agent_wake.cap_state("cc-ihsanos", now=1000.0)["allow"] is True


# ── cap-hit alert debounce (CAI-RESP-262 fast-follow #1) ──────────────────────
def test_cap_alert_due_first_time(wake_dir):
    assert agent_wake.cap_alert_due("cc-ihsanos", now=1000.0) is True


def test_cap_alert_debounced_within_window(wake_dir):
    _seed(wake_dir, "cc-ihsanos", [])
    (wake_dir / "cc-ihsanos.json").write_text(json.dumps({"wakes": [], "cap_alerted": 1000.0}))
    assert agent_wake.cap_alert_due("cc-ihsanos", now=1100.0) is False   # 100s < 300s window
    assert agent_wake.cap_alert_due("cc-ihsanos", now=1400.0) is True    # 400s >= window, due again


def test_wake_agent_cap_sets_alert_due_once(wake_dir, monkeypatch):
    # 5 wakes in window -> cap hit; first call alert_due=True, second False (debounced)
    monkeypatch.setattr(agent_wake, "resolve_tmux_session", lambda a: "sess")
    monkeypatch.setattr(agent_wake, "_pane_busy", lambda s: False)
    _seed(wake_dir, "cc-ihsanos", [800.0, 850.0, 900.0, 920.0, 940.0])
    r1 = agent_wake.wake_agent("cc-ihsanos", now=1000.0)
    assert r1.get("cap_hit") is True and r1.get("alert_due") is True
    r2 = agent_wake.wake_agent("cc-ihsanos", now=1010.0)
    assert r2.get("cap_hit") is True and r2.get("alert_due") is False


# ── CAI-817: VERIFIED SUBMIT (port of lane_nudge's clear->type->Enter->verify) ──
# The bug this fixes: the old wake_agent did a raw `send-keys -l SIGNAL` + a single
# unverified `Enter`. A lone Enter can fail to commit (TUI focus / dim composer),
# so the wake sat STAGED-UNSUBMITTED and the body wedged idle — while wake_agent
# returned woke=True (false confidence, a dead-man's-switch violation). The fix
# delegates the actual keystroke submit to the fleet's ONE verified-submit
# (scripts/lane_nudge.sh) via _verified_submit(), and REPORTS the real outcome.
@pytest.fixture
def _wake_ready(wake_dir, monkeypatch):
    monkeypatch.setattr(agent_wake, "resolve_tmux_session", lambda a: "sess")
    monkeypatch.setattr(agent_wake, "_pane_busy", lambda s: False)
    return wake_dir


def test_wake_delegates_to_verified_submit_not_raw_sendkeys(_wake_ready, monkeypatch):
    # wake_agent must NOT touch tmux send-keys directly anymore; it must route the
    # submit through the verified-submit seam. Blow up if it shells out to tmux raw.
    def _boom(*a, **k):
        raise AssertionError(f"wake_agent used raw subprocess (send-keys?): {a}")
    monkeypatch.setattr(agent_wake.subprocess, "run", _boom)
    calls = []
    monkeypatch.setattr(agent_wake, "_verified_submit", lambda s, sig, row_id=None: calls.append((s, sig)) or 0)
    r = agent_wake.wake_agent("cc-ihsanos", now=1000.0)
    assert r["woke"] is True
    assert calls == [("sess", agent_wake._SIGNAL)]


def test_wake_records_and_reports_true_on_verified_submit(_wake_ready, monkeypatch):
    monkeypatch.setattr(agent_wake, "_verified_submit", lambda s, sig, row_id=None: 0)  # rc 0 = verified
    r = agent_wake.wake_agent("cc-ihsanos", now=1000.0)
    assert r["woke"] is True and r["session"] == "sess"
    assert agent_wake._read_wakes("cc-ihsanos") == [1000.0]  # recorded


def test_wake_reports_false_loud_on_unverified_submit(_wake_ready, monkeypatch):
    # rc 3 = lane_nudge could NOT verify submission (staged/wedged/dialog). Must NOT
    # claim woke=True; must flag submit_failed so the caller can escalate; and must
    # RECORD the attempt so repeated failures trip the 5/5min cap -> loud alert_due.
    monkeypatch.setattr(agent_wake, "_verified_submit", lambda s, sig, row_id=None: 3)
    r = agent_wake.wake_agent("cc-ihsanos", now=1000.0)
    assert r["woke"] is False and r.get("submit_failed") is True
    assert agent_wake._read_wakes("cc-ihsanos") == [1000.0]  # attempt counted vs cap


def test_wake_reports_false_on_session_raced_gone(_wake_ready, monkeypatch):
    # rc 2 = session vanished between resolve and submit; nothing was delivered, so
    # do NOT burn a cap slot (no record) but DO report the failure.
    monkeypatch.setattr(agent_wake, "_verified_submit", lambda s, sig, row_id=None: 2)
    r = agent_wake.wake_agent("cc-ihsanos", now=1000.0)
    assert r["woke"] is False and r.get("submit_failed") is True
    assert agent_wake._read_wakes("cc-ihsanos") == []  # NOT recorded


def test_wake_repeated_unverified_trips_loud_cap(_wake_ready, monkeypatch):
    # End-to-end of the self-wiring loud escalation: 5 unverified submits inside the
    # window -> the 6th call is a cap hit with alert_due=True (existing telegram path).
    monkeypatch.setattr(agent_wake, "_verified_submit", lambda s, sig, row_id=None: 3)
    # 50s apart: >45s debounce (each records) AND all 5 stay inside the 300s window
    # when the 6th call lands at +250s (1000..1200 are all >950).
    for i in range(5):
        agent_wake.wake_agent("cc-ihsanos", now=1000.0 + i * 50)
    capped = agent_wake.wake_agent("cc-ihsanos", now=1000.0 + 5 * 50)
    assert capped.get("cap_hit") is True and capped.get("alert_due") is True


def test_wake_forwards_row_id_to_verified_submit(_wake_ready, monkeypatch):
    # Per-row ceiling wiring (Nazim #43063): the row driving the wake must reach
    # lane_nudge (via _verified_submit) so the ceiling is keyed by it.
    seen = {}
    monkeypatch.setattr(agent_wake, "_verified_submit",
                        lambda s, sig, row_id=None: seen.setdefault("row_id", row_id) or 0)
    agent_wake.wake_agent("cc-ihsanos", now=1000.0, row_id="R42")
    assert seen["row_id"] == "R42"


def test_wake_row_ceiling_unavailable_on_rc8_is_labeled_and_no_cap_slot(_wake_ready, monkeypatch):
    # rc 8 = lane_nudge fail-closed (ceiling state unavailable). Distinct greppable label,
    # NOT the generic submit-error; nothing delivered => no cap slot burned (Nazim #43121).
    monkeypatch.setattr(agent_wake, "_verified_submit", lambda s, sig, row_id=None: 8)
    r = agent_wake.wake_agent("cc-ihsanos", now=1000.0, row_id="R8")
    assert r["woke"] is False and r.get("row_ceiling_unavailable") is True
    assert r.get("why") == "row-ceiling state unavailable"
    assert r.get("submit_failed") is not True
    assert agent_wake._read_wakes("cc-ihsanos") == []


def test_wake_row_capped_on_rc7_does_not_burn_a_cap_slot(_wake_ready, monkeypatch):
    # rc 7 = lane_nudge's per-row ceiling refused (row already delivered enough). It is a
    # DELIBERATE bound, not a wedge: report row_capped, do NOT record a wake (nothing typed).
    monkeypatch.setattr(agent_wake, "_verified_submit", lambda s, sig, row_id=None: 7)
    r = agent_wake.wake_agent("cc-ihsanos", now=1000.0, row_id="R7")
    assert r["woke"] is False and r.get("row_capped") is True
    assert r.get("submit_failed") is not True
    assert agent_wake._read_wakes("cc-ihsanos") == []  # ceiling refusal must not burn the per-agent cap


# ── _candidate_sessions offline-sibling filter (Nazim #40426; phantom hijack 2026-09-16) ──
class _FakeCur:
    def __init__(self, captured, rows):
        self._captured, self._rows = captured, rows
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params):
        self._captured["sql"] = " ".join(sql.split())
        self._captured["params"] = params
    def fetchall(self): return self._rows


class _FakeConn:
    def __init__(self, captured, rows): self._captured, self._rows = captured, rows
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def cursor(self): return _FakeCur(self._captured, self._rows)


def test_candidate_sessions_includes_offline_family_hijack_guard_at_resolve(monkeypatch):
    """Candidate SELECTION now includes OFFLINE base-family rows too (so a base-id wake can
    reach an offline-but-live-pane instance, e.g. cc-cosem-tdu-coord-1). The op#11297 self
    rule still holds (self kept unconditionally). The Nazim #40426 sibling-hijack guard is NOT
    dropped — it MOVES to resolve time (live has-session AND reverse-ownership), so an offline
    drift row can still be a CANDIDATE here but is rejected before it is woken. This pins the
    widened query shape so a later reader cannot silently re-add the status<>'offline' sibling
    filter (which would re-break the tdu-coord recovery)."""
    captured = {}
    monkeypatch.setattr(agent_wake, "_DSN", "postgres://x")
    monkeypatch.setattr(agent_wake.psycopg, "connect",
                        lambda *a, **k: _FakeConn(captured, [("cc-orchestrator", "orch", "online")]))
    out = agent_wake._candidate_sessions("cc-orchestrator")
    sql, params = captured["sql"], captured["params"]
    # Widened predicate: self OR any base-family row, offline or not.
    assert "(agent_id=%s OR base_agent_id=%s)" in sql
    # The old sibling status gate must be GONE (its re-presence would drop offline-but-live).
    assert "base_agent_id=%s AND status<>'offline'" not in sql
    # Self is still not itself status-gated (op#11297).
    assert "(agent_id=%s AND status" not in sql
    assert params == ("cc-orchestrator", "cc-orchestrator")
    assert out == ["orch"]  # flows through rank_candidates unchanged


# ── resolve-time hijack guard: offline-but-live-pane wake vs #40426 sibling-hijack ──────────
def test_offline_base_instance_with_live_own_pane_is_woken(monkeypatch):
    """tdu-coord RECOVERY. A bus row to the BASE id cc-cosem-tdu-coord whose only instance
    (cc-cosem-tdu-coord-1) is offline-with-stale-heartbeat BUT whose pane is live must still
    be woken. The session is live (has-session) and owned by NO OTHER non-offline agent
    (its only owner is the offline instance), so the reverse-ownership guard lets it through."""
    candidates = ["tdu-coord-sess"]  # surfaced now that offline family rows are candidates
    live = lambda s: s == "tdu-coord-sess"                      # pane is genuinely alive
    owners = lambda s: [("cc-cosem-tdu-coord-1", "offline")]    # only owner, and it's offline
    out = agent_wake._first_live_session(
        candidates, has_session=live, agent_id="cc-cosem-tdu-coord", owner_source=owners)
    assert out == "tdu-coord-sess"


def test_offline_sibling_session_owned_by_foreign_live_agent_is_not_woken(monkeypatch):
    """#40426 NON-HIJACK. The phantom offline cc-orchestrator-1's tmux_session is actually
    cc-substrate's LIVE pane (a pool-move drift row). has-session is TRUE (the pane is alive),
    but it belongs to a DIFFERENT non-offline family, so the reverse-ownership guard rejects it
    and the wake for cc-orchestrator does NOT land on cc-substrate."""
    candidates = ["substrate-cleanup"]
    live = lambda s: s == "substrate-cleanup"                   # live — but not ours
    owners = lambda s: [("cc-orchestrator-1", "offline"),       # the drift row
                        ("cc-substrate", "online")]             # the real, foreign, live owner
    out = agent_wake._first_live_session(
        candidates, has_session=live, agent_id="cc-orchestrator", owner_source=owners)
    assert out is None


def test_live_same_family_sibling_owner_is_not_treated_as_hijack(monkeypatch):
    """The guard is FAMILY-aware: a live same-family sibling legitimately owning the session
    (op#11297 family fallback) is NOT a foreign hijack and must still be woken."""
    candidates = ["irsyad-prog1"]
    live = lambda s: True
    owners = lambda s: [("cc-irsyad-4", "online")]  # live sibling, same base family
    out = agent_wake._first_live_session(
        candidates, has_session=live, agent_id="cc-irsyad-9", owner_source=owners)
    assert out == "irsyad-prog1"
