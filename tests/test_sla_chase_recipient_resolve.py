"""Recipient-resolve guard for the SLA chase nets (bus#67757/#67841/#67958/#67965,
2026-10-15): in one hour, delegated_to hit the same failure class 3 times --
a lane/session name used instead of the registered agents.id (irsyad-coord,
cosem-exams, cosem-port, cosem-tdu-coord, angullia), and a valid agents.id
whose lane is desired_state='down' (cc-oeh) so it can never act on the nudge.
resolve_recipient() resolves the former and skips the latter, BEFORE either
chase net attempts an insert that would FK-violate or page a dead lane."""
import importlib
import time

w = importlib.import_module("scripts.priority_sla_watchdog")


# ── resolve_recipient (pure) ──────────────────────────────────────────────

def test_resolve_recipient_passes_through_a_valid_agent_id():
    resolved, reason = w.resolve_recipient(
        "cc-cosem-exams-1", valid_agent_ids={"cc-cosem-exams-1"},
        tmux_to_agent={}, down_lanes=set())
    assert resolved == "cc-cosem-exams-1"
    assert reason == "ok"


def test_resolve_recipient_resolves_a_lane_name_to_its_agent_id():
    # the exact bug: delegated_to held the tmux/lane name, not the agents.id
    resolved, reason = w.resolve_recipient(
        "cosem-exams", valid_agent_ids={"cc-cosem-exams-1"},
        tmux_to_agent={"cosem-exams": "cc-cosem-exams-1"}, down_lanes=set())
    assert resolved == "cc-cosem-exams-1"


def test_resolve_recipient_none_when_unresolvable():
    resolved, reason = w.resolve_recipient(
        "totally-made-up", valid_agent_ids={"cc-cosem-exams-1"},
        tmux_to_agent={"cosem-exams": "cc-cosem-exams-1"}, down_lanes=set())
    assert resolved is None
    assert "not a registered agents.id" in reason


def test_resolve_recipient_skips_a_down_lane_given_by_name():
    resolved, reason = w.resolve_recipient(
        "angullia", valid_agent_ids={"cc-angullia-1"},
        tmux_to_agent={"angullia": "cc-angullia-1"}, down_lanes={"angullia"})
    assert resolved is None
    assert "down" in reason


def test_resolve_recipient_skips_a_down_lane_given_as_the_agent_id():
    # bus#67757: cc-oeh IS a valid agents.id, but its lane is down
    resolved, reason = w.resolve_recipient(
        "cc-oeh", valid_agent_ids={"cc-oeh"},
        tmux_to_agent={"oeh": "cc-oeh"}, down_lanes={"oeh", "cc-oeh"})
    assert resolved is None
    assert "down" in reason


def test_resolve_recipient_none_owner():
    resolved, reason = w.resolve_recipient(
        None, valid_agent_ids=set(), tmux_to_agent={}, down_lanes=set())
    assert resolved is None


# ── wiring into the two send functions ────────────────────────────────────

class _FakeCursor:
    def __init__(self, executed):
        self._executed = executed

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._executed.append((sql, params))


class _FakeConn:
    def __init__(self):
        self.executed = []
        self.committed = False

    def cursor(self):
        return _FakeCursor(self.executed)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass


def test_send_asks_chase_skips_without_attempting_insert_when_unresolvable(monkeypatch):
    monkeypatch.setattr(w, "dry_identity_guard", lambda conn: True)
    conn = _FakeConn()
    resolution = (set(), {}, set())  # nothing resolves
    ok = w._send_asks_chase(conn, "irsyad-coord", {"id": 1, "ask": "x"},
                             recipient_resolution=resolution)
    assert ok is False
    assert conn.executed == []  # never even tried the insert


def test_send_asks_chase_resolves_lane_name_and_sends_to_the_real_agent_id(monkeypatch):
    monkeypatch.setattr(w, "dry_identity_guard", lambda conn: True)
    conn = _FakeConn()
    resolution = ({"cc-irsyad-coord"}, {"irsyad-coord": "cc-irsyad-coord"}, set())
    ok = w._send_asks_chase(conn, "irsyad-coord", {"id": 1, "ask": "x"},
                             recipient_resolution=resolution)
    assert ok is True
    [(sql, params)] = conn.executed
    assert params[0] == "cc-irsyad-coord"


def test_send_client_ask_chase_skips_a_down_lane_without_inserting(monkeypatch):
    monkeypatch.setattr(w, "dry_identity_guard", lambda conn: True)
    conn = _FakeConn()
    resolution = ({"cc-angullia-1"}, {"angullia": "cc-angullia-1"}, {"angullia"})
    row = {"id": 861, "ask": "x", "created_epoch": time.time(), "committed_date": None}
    ok = w._send_client_ask_chase(conn, "angullia", row, recipient_resolution=resolution)
    assert ok is False
    assert conn.executed == []


def test_send_client_ask_chase_resolves_and_sends_once_to_owner_only(monkeypatch):
    monkeypatch.setattr(w, "dry_identity_guard", lambda conn: True)
    conn = _FakeConn()
    resolution = ({"cc-cosem-platform-1"}, {"cosem-port": "cc-cosem-platform-1"}, set())
    row = {"id": 1812, "ask": "x", "created_epoch": time.time(), "committed_date": None}
    ok = w._send_client_ask_chase(conn, "cosem-port", row, recipient_resolution=resolution)
    assert ok is True
    [(sql, params)] = conn.executed
    assert params[0] == "cc-cosem-platform-1"


# ── console summary (replaces the per-ask CC) ─────────────────────────────

def test_client_ask_chase_summary_empty_for_no_sends():
    assert w.client_ask_chase_summary([]) == {}


def test_client_ask_chase_summary_lists_every_sent_ask():
    payload = w.client_ask_chase_summary([(654, "cc-angullia-1"), (1812, "cc-cosem-platform-1")])
    assert "2 client-channel" in payload["subject"]
    assert "#654" in payload["body"] and "#1812" in payload["body"]
    assert "cc-angullia-1" in payload["body"] and "cc-cosem-platform-1" in payload["body"]


def test_send_client_ask_chase_summary_sends_one_insert_to_console(monkeypatch):
    monkeypatch.setattr(w, "dry_identity_guard", lambda conn: True)
    conn = _FakeConn()
    ok = w.send_client_ask_chase_summary(conn, [(654, "cc-angullia-1")])
    assert ok is True
    [(sql, params)] = conn.executed
    assert "'orch-console'" in sql  # recipient is hardcoded in the SQL, not a param
    assert "654" in params[2]  # the ask id appears in the summary body
    assert conn.committed is True


def test_send_client_ask_chase_summary_no_op_for_empty_batch(monkeypatch):
    conn = _FakeConn()
    ok = w.send_client_ask_chase_summary(conn, [])
    assert ok is True
    assert conn.executed == []  # never even connects for an empty batch


# ── 10-27: a lane row 'down' on THIS registry is intent, not liveness ────────
def test_down_lane_with_live_instance_on_another_host_routes_to_the_instance():
    resolved, reason = w.resolve_recipient(
        "irsyad-coord", {"cc-irsyad-coord", "cc-irsyad-coord-1"},
        {"irsyad-coord": "cc-irsyad-coord"}, {"irsyad-coord", "cc-irsyad-coord"},
        {"cc-irsyad-coord": ["cc-irsyad-coord-1"]})
    assert resolved == "cc-irsyad-coord-1" and "is live" in reason


def test_down_base_id_owner_with_one_live_instance_routes_to_it():
    resolved, _ = w.resolve_recipient(
        "cc-irsyad-coord", {"cc-irsyad-coord"}, {}, {"cc-irsyad-coord"},
        {"cc-irsyad-coord": ["cc-irsyad-coord-1"]})
    assert resolved == "cc-irsyad-coord-1"


def test_down_lane_with_several_live_instances_is_ambiguous_never_guessed():
    resolved, reason = w.resolve_recipient(
        "cc-cosem-adcda", {"cc-cosem-adcda"}, {}, {"cc-cosem-adcda"},
        {"cc-cosem-adcda": ["cc-cosem-adcda-3", "cc-cosem-adcda-1", "cc-cosem-adcda-2"]})
    assert resolved is None and "ambiguous" in reason and "3 live instances" in reason


def test_down_lane_with_no_live_instance_still_skips():
    resolved, reason = w.resolve_recipient(
        "cc-oeh", {"cc-oeh"}, {}, {"cc-oeh"}, {"cc-other": ["cc-other-1"]})
    assert resolved is None and "desired_state=down" in reason


def test_without_live_instances_behaviour_is_unchanged():
    resolved, reason = w.resolve_recipient("cc-oeh", {"cc-oeh"}, {}, {"cc-oeh"})
    assert resolved is None and "desired_state=down" in reason
