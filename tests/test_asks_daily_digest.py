"""scripts/asks_daily_digest.py — op#22669 piece 4 (second half): a simple
once-daily roll-up of open operator_asks. render_digest() is pure formatting;
_already_sent_today/_mark_sent are a trivial date-stamp dedup. Both are tested
with no DB/network."""
import importlib
import json
from types import SimpleNamespace

add = importlib.import_module("scripts.asks_daily_digest")


def row(id=1, ask="do the thing", delegated_to="cc-scholar", waiting_on_operator=False,
        chase_by=None, created_at="2026-09-27T10:00:00Z"):
    return {"id": id, "ask": ask, "delegated_to": delegated_to,
            "waiting_on_operator": waiting_on_operator, "chase_by": chase_by,
            "created_at": created_at}


# ── render_digest: pure formatting ────────────────────────────────────────────
def test_empty_rows_says_ledger_is_clear():
    assert "nothing open" in add.render_digest([])


def test_header_counts_total_and_waiting():
    rows = [row(id=1, waiting_on_operator=True), row(id=2), row(id=3)]
    digest = add.render_digest(rows)
    assert "3 open" in digest
    assert "1 waiting on you" in digest


def test_waiting_rows_appear_under_waiting_on_you_section():
    rows = [row(id=1, ask="approve the migration", waiting_on_operator=True)]
    digest = add.render_digest(rows)
    assert "WAITING ON YOU:" in digest
    assert "#1" in digest and "approve the migration" in digest


def test_non_waiting_rows_appear_under_open_in_progress():
    rows = [row(id=2, ask="ship the thing", waiting_on_operator=False)]
    digest = add.render_digest(rows)
    assert "Open / in progress:" in digest
    assert "#2" in digest and "ship the thing" in digest


def test_waiting_section_omitted_when_none_waiting():
    rows = [row(id=2, waiting_on_operator=False)]
    digest = add.render_digest(rows)
    assert "WAITING ON YOU:" not in digest


def test_others_section_omitted_when_all_waiting():
    rows = [row(id=1, waiting_on_operator=True)]
    digest = add.render_digest(rows)
    assert "Open / in progress:" not in digest


def test_unassigned_delegated_to_falls_back_to_label():
    rows = [row(id=9, delegated_to=None, waiting_on_operator=False)]
    digest = add.render_digest(rows)
    assert "unassigned" in digest


# ── dedup: at most one send per UTC calendar day ──────────────────────────────
def test_already_sent_today_false_when_state_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(add, "STATE_FILE", tmp_path / "nope.json")
    assert add._already_sent_today("2026-09-27") is False


def test_mark_sent_then_already_sent_today_true(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(add, "STATE_FILE", state_file)
    add._mark_sent("2026-09-27")
    assert add._already_sent_today("2026-09-27") is True
    assert json.loads(state_file.read_text()) == {"last_sent_date": "2026-09-27"}


def test_already_sent_today_false_for_a_different_date(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(add, "STATE_FILE", state_file)
    add._mark_sent("2026-09-26")
    assert add._already_sent_today("2026-09-27") is False


def test_already_sent_today_false_on_corrupt_state_file(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    state_file.write_text("not json")
    monkeypatch.setattr(add, "STATE_FILE", state_file)
    assert add._already_sent_today("2026-09-27") is False


# ── main(): dry-run / force / already-sent skip ───────────────────────────────
class _FakeCur:
    def execute(self, *a, **k): pass
    def fetchall(self): return []
    @property
    def description(self): return []
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakeConn:
    def cursor(self): return _FakeCur()
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_main_dry_run_prints_digest_and_does_not_send(monkeypatch, capsys):
    monkeypatch.setattr(add, "_dsn", lambda: "postgresql://unused")
    monkeypatch.setattr(add.psycopg, "connect", lambda *a, **k: _FakeConn())
    monkeypatch.setattr(add.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("dry-run must not send")))
    rc = add.main(["--dry-run"])
    assert rc == 0
    assert "nothing open" in capsys.readouterr().out


def test_main_skips_when_already_sent_today_and_not_forced(monkeypatch, tmp_path):
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(add, "STATE_FILE", state_file)
    import datetime
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    add._mark_sent(today)
    monkeypatch.setattr(add.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not send")))
    rc = add.main([])
    assert rc == 0


def test_main_no_dsn_returns_2(monkeypatch, tmp_path):
    monkeypatch.setattr(add, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(add, "_dsn", lambda: None)
    rc = add.main(["--force"])
    assert rc == 2


def test_main_sends_via_nazim_send_not_tg_send(monkeypatch, tmp_path):
    """op#22669 / orch-console bus #43972: this launchd job is Mini/console
    hosted and the asks ledger is Nazim's own thread, not the hub's — sending
    via tg_send.sh fail-closes for the console body (ORCH-TOPOLOGY-001) and
    would silently never reach the operator."""
    monkeypatch.setattr(add, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(add, "_dsn", lambda: "postgresql://unused")
    monkeypatch.setattr(add.psycopg, "connect", lambda *a, **k: _FakeConn())
    captured = {}

    def fake_run(cmd, *a, **k):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(add.subprocess, "run", fake_run)
    rc = add.main(["--force"])
    assert rc == 0
    assert captured["cmd"][:2] == ["bash", str(add.ORCH / "scripts" / "nazim_send.sh")]
    assert "tg_send.sh" not in " ".join(captured["cmd"])
