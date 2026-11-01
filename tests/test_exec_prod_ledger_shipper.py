"""exec_prod ledger shipper (queue row 271): JSONL -> exec_prod_ledger, idempotent."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "exec_prod_ledger_shipper.py"
_spec = importlib.util.spec_from_file_location("exec_prod_ledger_shipper", _PATH)
shipper = importlib.util.module_from_spec(_spec)
sys.modules["exec_prod_ledger_shipper"] = shipper
_spec.loader.exec_module(shipper)

_MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "drafts" / "exec_prod_ledger.sql"


def _entry(exec_id, *, end=False, reason="unit", outcome=None, **extra):
    e = {
        "exec_id": exec_id, "ts_start": "2026-11-01T10:00:00+00:00",
        "ts_end": "2026-11-01T10:00:02+00:00" if end else None,
        "actor": "ca2a4a9c-f745-40c3-9536-7a021fd42bb9", "project_ref": "syntheticref",
        "sql_sha256": "ab" * 32, "reason": reason, "gate_id": "56485", "gate_ref": "op#51",
        "transport": "broker", "outcome": outcome if end else None,
    }
    e.update(extra)
    return json.dumps(e) + "\n"


class FakeTable:
    """Simulates exec_prod_ledger + ON CONFLICT (host, exec_id, line_kind) DO NOTHING."""

    def __init__(self, fail=False, fail_runs=False):
        self.rows = {}
        self.runs = {}
        self.commits = 0
        self.fail = fail                      # fail every statement (DB down)
        self.fail_runs = fail_runs or fail    # fail the run-heartbeat upsert

    def __call__(self):
        table = self

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def cursor(self):
                class _Cur:
                    def __enter__(self):
                        return self

                    def __exit__(self, *a):
                        return False

                    rowcount = -1

                    def execute(self, sql, row):
                        if sql == shipper.RUN_UPSERT_SQL:
                            if table.fail_runs:
                                raise RuntimeError("db down")
                            prev = table.runs.get(row["host"], {})
                            table.runs[row["host"]] = {
                                **row, "last_ok_at": "now" if row["exit_code"] == 0 else prev.get("last_ok_at")}
                            return
                        if table.fail:
                            raise RuntimeError("db down")
                        assert sql == shipper.INSERT_SQL
                        key = (row["host"], row["exec_id"], row["line_kind"])
                        self.rowcount = 0 if key in table.rows else 1
                        table.rows.setdefault(key, row)

                return _Cur()

            def commit(self):
                table.commits += 1

        return _Conn()


def _ship(tmp_path, table, **kw):
    return shipper.ship(ledger=tmp_path / "ledger.jsonl", cursor_path=tmp_path / "cursor.json",
                        host="gzb", conn_factory=table, **kw)


def test_ships_start_and_end_lines_with_derived_line_kind(tmp_path):
    (tmp_path / "ledger.jsonl").write_text(_entry("e1") + _entry("e1", end=True, outcome="ok"))
    t = FakeTable()
    rep = _ship(tmp_path, t)
    assert rep.shipped_lines == 2 and rep.malformed == []
    assert set(t.rows) == {("gzb", "e1", "start"), ("gzb", "e1", "end")}
    assert t.rows[("gzb", "e1", "end")]["outcome"] == "ok"
    assert t.rows[("gzb", "e1", "start")]["ts_end"] is None


def test_reason_is_truncated_to_200_chars(tmp_path):
    (tmp_path / "ledger.jsonl").write_text(_entry("e1", reason="x" * 1000))
    t = FakeTable()
    _ship(tmp_path, t)
    assert t.rows[("gzb", "e1", "start")]["reason"] == "x" * 200


def test_rerun_is_a_noop_and_reshipping_from_zero_changes_nothing(tmp_path):
    (tmp_path / "ledger.jsonl").write_text(_entry("e1") + _entry("e1", end=True))
    t = FakeTable()
    _ship(tmp_path, t)
    before = dict(t.rows)
    assert _ship(tmp_path, t).shipped_lines == 0          # cursor: nothing new
    (tmp_path / "cursor.json").unlink()
    assert _ship(tmp_path, t).shipped_lines == 2          # re-shipped from 0 ...
    assert t.rows == before                               # ... and ON CONFLICT made it a no-op


def test_only_complete_lines_are_consumed(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    full = _entry("e2")
    ledger.write_text(_entry("e1") + full[:20])
    t = FakeTable()
    rep = _ship(tmp_path, t)
    assert rep.shipped_lines == 1 and set(t.rows) == {("gzb", "e1", "start")}
    ledger.write_text(_entry("e1") + full)
    assert _ship(tmp_path, t).shipped_lines == 1
    assert ("gzb", "e2", "start") in t.rows


def test_truncation_restarts_from_zero_idempotently(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(_entry("e1") + _entry("e1", end=True) + _entry("e2"))
    t = FakeTable()
    _ship(tmp_path, t)
    ledger.write_text(_entry("e3"))           # same inode, now shorter than the cursor
    rep = _ship(tmp_path, t)
    assert rep.restarted.startswith("truncated") and rep.start == 0
    assert ("gzb", "e3", "start") in t.rows and len(t.rows) == 4


def test_rotation_restarts_from_zero(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(_entry("e1") * 3)
    t = FakeTable()
    _ship(tmp_path, t)
    ledger.unlink()
    ledger.write_text(_entry("e1") + _entry("e9") * 5)   # new file (new inode), longer
    rep = _ship(tmp_path, t)
    if rep.restarted is not None:                     # inode reuse is filesystem-dependent
        assert rep.start == 0
    shipper.write_cursor(tmp_path / "cursor.json", 10, inode=-1)  # force an inode mismatch
    rep = _ship(tmp_path, t)
    assert rep.restarted == "rotated (inode changed)" and rep.start == 0
    assert ("gzb", "e9", "start") in t.rows


def test_malformed_lines_are_counted_reported_by_offset_and_never_crash(tmp_path):
    good = _entry("e1")
    lines = [good, "not json\n", "[1,2]\n", json.dumps({"ts_start": "2026-11-01T00:00:00Z"}) + "\n",
             _entry("e2", ts_start="yesterday"), _entry("e3", outcome=5, end=True),
             _entry("e4", reason=["x"]), "\n", _entry("e5")]
    (tmp_path / "ledger.jsonl").write_text("".join(lines))
    t = FakeTable()
    rep = _ship(tmp_path, t)
    assert {k[1] for k in t.rows} == {"e1", "e5"}
    assert len(rep.malformed) == 6                     # blank line is skipped, not malformed
    assert rep.malformed[0][0] == len(good.encode())   # reported by BYTE OFFSET
    assert all(isinstance(why, str) for _, why in rep.malformed)


def test_db_failure_does_not_advance_the_cursor(tmp_path):
    (tmp_path / "ledger.jsonl").write_text(_entry("e1"))
    with pytest.raises(RuntimeError):
        _ship(tmp_path, FakeTable(fail=True))
    assert not (tmp_path / "cursor.json").exists()
    t = FakeTable()
    assert _ship(tmp_path, t).shipped_lines == 1


def test_batches_commit_and_advance_the_cursor_incrementally(tmp_path, monkeypatch):
    monkeypatch.setattr(shipper, "BATCH_LINES", 2)
    (tmp_path / "ledger.jsonl").write_text("".join(_entry(f"e{i}") for i in range(5)))
    t = FakeTable()
    rep = _ship(tmp_path, t)
    assert rep.shipped_lines == 5 and t.commits == 3 and len(t.rows) == 5
    assert shipper.read_cursor(tmp_path / "cursor.json")["offset"] == (tmp_path / "ledger.jsonl").stat().st_size


def test_missing_ledger_is_not_an_error(tmp_path):
    assert _ship(tmp_path, FakeTable()).shipped_lines == 0


def test_dry_run_writes_neither_db_nor_cursor(tmp_path):
    (tmp_path / "ledger.jsonl").write_text(_entry("e1"))
    t = FakeTable()
    rep = _ship(tmp_path, t, dry_run=True)
    assert rep.shipped_lines == 1 and t.rows == {} and not (tmp_path / "cursor.json").exists()


def test_corrupt_cursor_file_restarts_from_zero(tmp_path):
    (tmp_path / "ledger.jsonl").write_text(_entry("e1"))
    (tmp_path / "cursor.json").write_text("{garbage")
    assert _ship(tmp_path, FakeTable()).shipped_lines == 1


def _main(monkeypatch, tmp_path, table, host="gzb"):
    import fleet_host_id
    if isinstance(host, Exception):
        def _h():
            raise host
        monkeypatch.setattr(fleet_host_id, "fleet_host_id", _h)
    else:
        monkeypatch.setattr(fleet_host_id, "fleet_host_id", lambda: host)
    monkeypatch.setattr(shipper, "_conn_factory", table)
    return shipper.main(["--ledger", str(tmp_path / "ledger.jsonl"), "--cursor", str(tmp_path / "cursor.json")])


def test_main_exit_codes(monkeypatch, tmp_path, capsys):
    (tmp_path / "ledger.jsonl").write_text(_entry("e1"))
    assert _main(monkeypatch, tmp_path, FakeTable()) == 0
    with (tmp_path / "ledger.jsonl").open("a") as f:
        f.write("not json\n")
    assert _main(monkeypatch, tmp_path, FakeTable()) == 2
    err = capsys.readouterr().err
    assert "MALFORMED line at byte" in err and "not json" not in err   # offset, never content
    with (tmp_path / "ledger.jsonl").open("a") as f:
        f.write(_entry("e2"))
    assert _main(monkeypatch, tmp_path, FakeTable(fail=True)) == 1
    assert _main(monkeypatch, tmp_path, FakeTable(), host=OSError("no hostname")) == 1


def test_insert_is_on_conflict_do_nothing_on_the_unique_key():
    assert "ON CONFLICT (host, exec_id, line_kind) DO NOTHING" in shipper.INSERT_SQL


def test_migration_has_the_unique_key_index_and_grant_note():
    sql = _MIGRATION.read_text()
    assert "UNIQUE (host, exec_id, line_kind)" in sql
    assert "ON exec_prod_ledger (project_ref, ts_start)" in sql
    assert "REVOKE ALL ON exec_prod_ledger FROM PUBLIC, authenticated, anon, service_role" in sql
    assert "WATCHDOG" in sql and "SELECT" in sql



# --- cc-fleet-health review fixes (bus #89909) --------------------------------

def test_conflicted_is_attempted_minus_inserted(tmp_path):
    (tmp_path / "ledger.jsonl").write_text(_entry("e1") + _entry("e1", end=True))
    t = FakeTable()
    rep = _ship(tmp_path, t)
    assert (rep.shipped_lines, rep.inserted, rep.conflicted) == (2, 2, 0)
    (tmp_path / "cursor.json").unlink()
    rep = _ship(tmp_path, t)
    assert (rep.shipped_lines, rep.inserted, rep.conflicted) == (2, 0, 2)


@pytest.mark.parametrize("ts", ["2026-11-01", "2026-11-01T", "20261101T100000", "10:00:00"])
def test_date_only_or_timeless_timestamps_are_malformed(tmp_path, ts):
    (tmp_path / "ledger.jsonl").write_text(_entry("e1", ts_start=ts) + _entry("e2"))
    t = FakeTable()
    rep = _ship(tmp_path, t)
    assert len(rep.malformed) == 1 and {k[1] for k in t.rows} == {"e2"}


def test_space_separated_datetime_is_accepted(tmp_path):
    (tmp_path / "ledger.jsonl").write_text(_entry("e1", ts_start="2026-11-01 10:00:00+00:00"))
    assert _ship(tmp_path, FakeTable()).malformed == []


def test_run_row_is_written_on_every_run_exit_0_2_and_empty(monkeypatch, tmp_path, capsys):
    t = FakeTable()
    assert _main(monkeypatch, tmp_path, t) == 0                     # empty run: no ledger yet
    assert t.runs["gzb"]["exit_code"] == 0 and t.runs["gzb"]["last_ok_at"] == "now"
    (tmp_path / "ledger.jsonl").write_text(_entry("e1") + "not json\n")
    assert _main(monkeypatch, tmp_path, t) == 2
    run = t.runs["gzb"]
    assert run["exit_code"] == 2 and run["shipped"] == 1 and run["malformed"] == 1
    assert run["conflicted"] == 0 and run["ledger_size"] == (tmp_path / "ledger.jsonl").stat().st_size
    assert run["cursor_offset"] == run["ledger_size"]
    assert run["last_ok_at"] == "now"          # carried over from the earlier exit-0 run, not advanced
    out = capsys.readouterr().out
    assert "conflicted=0" in out and "inserted=1" in out


def test_failed_ship_still_writes_an_exit_1_run_row_when_the_db_allows(monkeypatch, tmp_path):
    (tmp_path / "ledger.jsonl").write_text(_entry("e1"))

    class _InsertsFail(FakeTable):
        pass

    t = _InsertsFail()
    t.fail = True
    t.fail_runs = False
    assert _main(monkeypatch, tmp_path, t) == 1
    assert t.runs["gzb"]["exit_code"] == 1 and t.runs["gzb"]["cursor_offset"] is None


def test_db_down_exits_1_with_no_run_row(monkeypatch, tmp_path):
    (tmp_path / "ledger.jsonl").write_text(_entry("e1"))
    t = FakeTable(fail=True)
    assert _main(monkeypatch, tmp_path, t) == 1 and t.runs == {}


def test_run_row_write_failure_after_a_clean_ship_is_exit_1(monkeypatch, tmp_path):
    (tmp_path / "ledger.jsonl").write_text(_entry("e1"))
    t = FakeTable(fail_runs=True)
    assert _main(monkeypatch, tmp_path, t) == 1
    assert len(t.rows) == 1        # the lines themselves were shipped


def test_dry_run_writes_no_run_row(monkeypatch, tmp_path):
    import fleet_host_id
    monkeypatch.setattr(fleet_host_id, "fleet_host_id", lambda: "gzb")
    t = FakeTable()
    monkeypatch.setattr(shipper, "_conn_factory", t)
    (tmp_path / "ledger.jsonl").write_text(_entry("e1"))
    assert shipper.main(["--ledger", str(tmp_path / "ledger.jsonl"),
                         "--cursor", str(tmp_path / "cursor.json"), "--dry-run"]) == 0
    assert t.runs == {} and t.rows == {}


def test_a_second_concurrent_run_skips_without_touching_cursor_or_db(monkeypatch, tmp_path):
    (tmp_path / "ledger.jsonl").write_text(_entry("e1"))
    held = shipper._acquire_lock(tmp_path / "cursor.json")
    try:
        t = FakeTable()
        assert _main(monkeypatch, tmp_path, t) == 0
        assert t.rows == {} and t.runs == {} and not (tmp_path / "cursor.json").exists()
    finally:
        held.close()
    t = FakeTable()
    assert _main(monkeypatch, tmp_path, t) == 0 and len(t.rows) == 1


def test_run_table_and_reconcile_key_are_documented_in_the_migration():
    sql = _MIGRATION.read_text()
    assert "CREATE TABLE IF NOT EXISTS exec_prod_ledger_shipper_runs" in sql
    assert "host             text PRIMARY KEY" in sql
    assert "CHECK (exit_code IN (0, 1, 2))" in sql
    for col in ("last_run_at", "last_ok_at", "cursor_offset", "ledger_size", "shipped_last",
                "malformed_last", "conflicted_last"):
        assert col in sql
    assert "executed_sql_sha256" in sql and "project_ref, transport) AND sql_sha256" in sql


def test_cron_alternative_is_comment_only_and_flock_wrapped():
    svc = (_PATH.parent.parent / "deploy" / "wingmen-exec-prod-ledger-shipper.service").read_text()
    cron = [line for line in svc.splitlines() if "*/5" in line]
    assert cron and all(line.lstrip().startswith("#") and "flock -n" in line for line in cron)
