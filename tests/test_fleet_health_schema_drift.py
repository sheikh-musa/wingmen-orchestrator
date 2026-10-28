"""fleet_health step 9 -- live-schema vs test-fixture snapshot drift watch (orch-console #80025).

The operator_asks fixture is generated from tests/fixtures/schema/operator_asks.live.json
(PR#584). If a migration changes the LIVE checks/triggers and nobody refreshes the snapshot,
tests go back to passing against a weaker table than prod. This step runs the read-only
snapshot_live_checks comparison on every ~10 min sweep and pages orch-console ONCE per
distinct drift, clearing when live matches again. Pure-logic tests, no live DB.
"""
import importlib

fh = importlib.import_module("scripts.fleet_health")

DRIFT = ["checks: operator_asks_new_chk is LIVE but not in the snapshot"]


def test_no_drift_pages_nothing_and_clears_state():
    page, new = fh.schema_drift_plan("operator_asks", [], {"operator_asks": "abc"})
    assert page is False and "operator_asks" not in new


def test_new_drift_pages_once_and_remembers_it():
    page, new = fh.schema_drift_plan("operator_asks", DRIFT, {})
    assert page is True and new["operator_asks"]
    page2, new2 = fh.schema_drift_plan("operator_asks", DRIFT, new)
    assert page2 is False and new2 == new


def test_a_different_drift_pages_again():
    _, seen = fh.schema_drift_plan("operator_asks", DRIFT, {})
    page, _ = fh.schema_drift_plan("operator_asks", DRIFT + ["triggers: x differs (live vs snapshot)"], seen)
    assert page is True


def test_drift_after_a_clear_pages_again():
    _, seen = fh.schema_drift_plan("operator_asks", DRIFT, {})
    _, cleared = fh.schema_drift_plan("operator_asks", [], seen)
    page, _ = fh.schema_drift_plan("operator_asks", DRIFT, cleared)
    assert page is True


def test_watch_never_raises_when_the_live_read_fails(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(fh, "SCHEMA_DRIFT_STATE", tmp_path / "s.json")

    def boom(dsn, table):
        raise RuntimeError("connection refused")
    sent = []
    paged = fh.schema_drift_watch(read_live=boom, send=lambda *a, **k: sent.append(a))
    assert paged == [] and sent == []
    assert "SCHEMA DRIFT WATCH could not read" in capsys.readouterr().out


def test_watch_pages_on_drift_then_stays_quiet(monkeypatch, tmp_path):
    monkeypatch.setattr(fh, "SCHEMA_DRIFT_STATE", tmp_path / "s.json")
    snap = fh._load_schema_snapshot("operator_asks")
    live = {"table": "operator_asks", "checks": dict(snap["checks"], operator_asks_new_chk="CHECK (true)"),
            "triggers": snap["triggers"]}
    sent = []
    send = lambda *a, **k: sent.append((a, k))
    assert fh.schema_drift_watch(read_live=lambda dsn, t: live, send=send) == ["operator_asks"]
    assert fh.schema_drift_watch(read_live=lambda dsn, t: live, send=send) == []
    assert len(sent) == 1
    subject, body = sent[0][0][3], sent[0][0][4]
    assert "operator_asks" in subject and "operator_asks_new_chk" in body and "--write" in body


def test_watch_matching_live_is_silent(monkeypatch, tmp_path):
    monkeypatch.setattr(fh, "SCHEMA_DRIFT_STATE", tmp_path / "s.json")
    snap = fh._load_schema_snapshot("operator_asks")
    sent = []
    assert fh.schema_drift_watch(read_live=lambda dsn, t: snap, send=lambda *a, **k: sent.append(a)) == []
    assert sent == []


def test_a_failed_page_is_not_remembered_so_it_retries(monkeypatch, tmp_path):
    monkeypatch.setattr(fh, "SCHEMA_DRIFT_STATE", tmp_path / "s.json")
    snap = fh._load_schema_snapshot("operator_asks")
    live = dict(snap, checks=dict(snap["checks"], operator_asks_new_chk="CHECK (true)"))

    def fail(*a, **k):
        raise RuntimeError("bus down")
    assert fh.schema_drift_watch(read_live=lambda d, t: live, send=fail) == []
    ok = []
    assert fh.schema_drift_watch(read_live=lambda d, t: live, send=lambda *a, **k: ok.append(a)) == ["operator_asks"]


def test_an_import_failure_is_loud_and_not_fatal(monkeypatch, tmp_path, capsys):
    import sys
    monkeypatch.setattr(fh, "SCHEMA_DRIFT_STATE", tmp_path / "s.json")
    monkeypatch.setitem(sys.modules, "scripts.snapshot_live_checks", None)   # import -> ImportError
    # `from scripts import X` reads the package attribute first, so drop that too
    monkeypatch.delattr(sys.modules["scripts"], "snapshot_live_checks", raising=False)
    assert fh.schema_drift_watch() == []
    assert "could not import snapshot_live_checks" in capsys.readouterr().out


def test_the_sweep_step_contains_any_escape_and_continues(monkeypatch, capsys):
    def escape(*a, **k):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(fh, "schema_drift_watch", escape)
    assert fh.run_schema_drift_step() == []
    assert "SCHEMA DRIFT WATCH FAILED (sweep continues)" in capsys.readouterr().out


def test_main_calls_the_contained_step_not_the_raw_watch():
    import inspect
    src = inspect.getsource(fh.main)
    assert "run_schema_drift_step()" in src and "schema_drift_watch()" not in src
