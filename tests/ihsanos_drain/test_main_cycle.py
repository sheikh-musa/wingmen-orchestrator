from ihsanos_drain.main import run_cycle


class FakeCur:
    def __init__(self, rows):
        self._rows = rows
        self.inserted = []

    def execute(self, sql, params=None):
        if sql.strip().upper().startswith("INSERT"):
            self.inserted.append((sql, params))
        self._last = sql

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return (0,)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_report_only_cycle_does_not_execute(monkeypatch):
    monkeypatch.setenv("DRAIN_EXECUTE_ENABLED", "false")
    monkeypatch.delenv("WINGMEN_IHSANOS_DRAIN_DISABLED", raising=False)
    cur = FakeCur(rows=[])
    result = run_cycle(cur, caller_name="ihsanos-drain", token_cap=200_000)
    assert result["executed"] == 0
    assert result["mode"] == "report_only"
    assert any("INSERT INTO agent_messages" in s for s, _ in cur.inserted)


def test_kill_switch_short_circuits(monkeypatch):
    monkeypatch.setenv("WINGMEN_IHSANOS_DRAIN_DISABLED", "1")
    cur = FakeCur(rows=[])
    result = run_cycle(cur, caller_name="ihsanos-drain", token_cap=200_000)
    assert result["mode"] == "disabled"
    assert result["executed"] == 0
