from collections import namedtuple

from legacy.ihsanos_drain.main import run_cycle
import pytest

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")


_Out = namedtuple("_Out", "ruling_ref status detail tokens_spent")


class FakeCur:
    def __init__(self, inbox_rows=(), candidate_rows=()):
        self.inbox_rows = list(inbox_rows)
        self.candidate_rows = list(candidate_rows)
        self.inserted = []
        self._mode = None

    def execute(self, sql, params=None):
        u = sql.strip().upper()
        if u.startswith("INSERT"):
            self.inserted.append((sql, params))
            self._mode = None
        elif "FROM AGENT_MESSAGES" in u:
            self._mode = "inbox"
        elif "FROM STRATEGIC_DECISIONS" in u:
            self._mode = "candidates"

    def fetchall(self):
        return self.candidate_rows if self._mode == "candidates" else self.inbox_rows

    def fetchone(self):
        return (0,)


def test_report_only_cycle_does_not_execute(monkeypatch):
    monkeypatch.setenv("DRAIN_EXECUTE_ENABLED", "false")
    monkeypatch.delenv("WINGMEN_IHSANOS_DRAIN_DISABLED", raising=False)
    cur = FakeCur(inbox_rows=[], candidate_rows=[])
    result = run_cycle(cur, caller_name="ihsanos-drain", token_cap=200_000)
    assert result["executed"] == 0
    assert result["mode"] == "report_only"
    assert any("INSERT INTO agent_messages" in s for s, _ in cur.inserted)


def test_cycle_reports_would_execute_and_held(monkeypatch):
    monkeypatch.delenv("WINGMEN_IHSANOS_DRAIN_DISABLED", raising=False)
    candidates = [
        dict(decision_ref="A", execution_status="granted",
             repos_affected=["ihsanos"], challenge_status="accepted_by_timeout",
             decision="x"),
        dict(decision_ref="B", execution_status="granted",
             repos_affected=["ihsanos"], challenge_status="challenge_window",
             decision="x"),
    ]
    cur = FakeCur(inbox_rows=[("m1",)], candidate_rows=candidates)
    result = run_cycle(cur, caller_name="ihsanos-drain", token_cap=200_000)
    # identifies the closed-window ruling as would-execute, holds the open one,
    # but still executes nothing (report-only).
    assert result["executable"] == ["A"]
    assert result["held"] == ["B"]
    assert result["executed"] == 0
    # the would-execute breakdown is in the report body
    insert_sql, params = cur.inserted[0]
    assert "A" in params["body"]


def test_execute_enabled_runs_executor_only_for_executable(monkeypatch):
    monkeypatch.setenv("DRAIN_EXECUTE_ENABLED", "true")
    monkeypatch.delenv("WINGMEN_IHSANOS_DRAIN_DISABLED", raising=False)
    candidates = [
        dict(decision_ref="A", execution_status="granted",
             repos_affected=["ihsanos"], challenge_status="accepted_by_timeout",
             decision="x"),
        dict(decision_ref="B", execution_status="granted",
             repos_affected=["ihsanos"], challenge_status="challenge_window",
             decision="x"),
    ]
    cur = FakeCur(inbox_rows=[], candidate_rows=candidates)
    called = []

    def fake_exec(ruling):
        called.append(ruling["decision_ref"])
        return _Out(ruling["decision_ref"], "pr_opened", "https://x/pr/1", 10)

    result = run_cycle(
        cur, caller_name="ihsanos-drain", token_cap=200_000, execute_fn=fake_exec
    )
    assert result["mode"] == "execute"
    assert called == ["A"]  # only the closed-window ruling, not the held one
    assert result["executed"] == 1
    assert result["outcomes"] == [("A", "pr_opened")]


def test_execute_disabled_never_calls_executor(monkeypatch):
    monkeypatch.setenv("DRAIN_EXECUTE_ENABLED", "false")
    monkeypatch.delenv("WINGMEN_IHSANOS_DRAIN_DISABLED", raising=False)
    candidates = [
        dict(decision_ref="A", execution_status="granted",
             repos_affected=["ihsanos"], challenge_status="accepted_by_timeout",
             decision="x"),
    ]
    cur = FakeCur(inbox_rows=[], candidate_rows=candidates)
    called = []
    run_cycle(
        cur, caller_name="ihsanos-drain", token_cap=200_000,
        execute_fn=lambda r: called.append(r) or _Out("A", "pr_opened", "", 0),
    )
    assert called == []  # flag off => executor never runs


def test_kill_switch_short_circuits(monkeypatch):
    monkeypatch.setenv("WINGMEN_IHSANOS_DRAIN_DISABLED", "1")
    cur = FakeCur(inbox_rows=[], candidate_rows=[])
    result = run_cycle(cur, caller_name="ihsanos-drain", token_cap=200_000)
    assert result["mode"] == "disabled"
    assert result["executed"] == 0
    assert cur.inserted == []
