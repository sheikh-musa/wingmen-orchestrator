"""Migration-immutability guard (CAI-RESP-420 #50) — off-live tests (fake ledger).

The guard's value is a hard fail when an already-applied migration's file body
changes (the 061->092 in-place amend). These drive that with a fake connection
backing migration_ledger with a dict — no DB, CI-runnable.
"""
import pytest

pytest.importorskip("psycopg")

import sys, pathlib  # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts" / "gates"))
import migration_immutability_guard as g  # noqa: E402

REF = "silo-ref-x"


class _Cur:
    def __init__(self, s):
        self.s = s
        self._r = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        p = params or ()
        if "to_regclass" in sql:
            self._r = [(1 if self.s["exists"] else None,)]
        elif "set_config" in sql:
            self._r = None
        elif sql.lstrip().startswith("SELECT sha256"):
            v = self.s["ledger"].get((p[0], p[1], p[2]))
            self._r = [(v,)] if v is not None else []
        elif "INSERT INTO migration_ledger" in sql:
            self.s["ledger"].setdefault((p[0], p[1], p[2]), p[3])   # ON CONFLICT DO NOTHING
            self._r = None
        else:
            raise AssertionError(f"unhandled SQL: {sql[:60]}")

    def fetchone(self):
        return self._r[0] if self._r else None


class FakeConn:
    def __init__(self, exists=True, ledger=None):
        self.s = {"exists": exists, "ledger": ledger or {}}

    def cursor(self):
        return _Cur(self.s)

    def commit(self):
        pass


def _mig(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body)
    return p


def test_sha256_is_deterministic(tmp_path):
    p = _mig(tmp_path, "m.sql", "CREATE TABLE x();")
    assert g.sha256_file(p) == g.sha256_file(p)


def test_new_migration_passes(tmp_path):
    conn = FakeConn()
    assert g.check_one(conn, "ihsanos", "061.sql", REF, _mig(tmp_path, "061.sql", "A")) is None


def test_unchanged_body_passes_after_record(tmp_path):
    conn = FakeConn()
    f = _mig(tmp_path, "061.sql", "CREATE TABLE pos_orders();")
    g.record(conn, "ihsanos", "061.sql", REF, f)
    assert g.check_one(conn, "ihsanos", "061.sql", REF, f) is None   # same body -> ok


def test_amended_body_hard_fails(tmp_path):
    conn = FakeConn()
    f = _mig(tmp_path, "061.sql", "original body")
    g.record(conn, "ihsanos", "061.sql", REF, f)
    f.write_text("original body\nALTER TABLE ... -- amended in place")   # the 061->092 fault
    with pytest.raises(g.ImmutabilityViolation):
        g.check_one(conn, "ihsanos", "061.sql", REF, f)


def test_record_refuses_a_changed_body(tmp_path):
    conn = FakeConn()
    f = _mig(tmp_path, "061.sql", "v1")
    g.record(conn, "ihsanos", "061.sql", REF, f)
    f.write_text("v2")
    with pytest.raises(g.ImmutabilityViolation):
        g.record(conn, "ihsanos", "061.sql", REF, f)


def test_per_silo_independence(tmp_path):
    # The same migration is tracked per silo; recording on ceayj doesn't mask a
    # divergent body applied to goumlyne (exactly the 092 cross-silo case).
    conn = FakeConn()
    f = _mig(tmp_path, "061.sql", "body")
    g.record(conn, "ihsanos", "061.sql", "ceayj", f)
    assert g.check_one(conn, "ihsanos", "061.sql", "goumlyne", f) is None   # not yet on goumlyne
    f.write_text("body-DIFFERENT-on-goumlyne")
    # ceayj still has the old hash -> a check against ceayj now fails
    with pytest.raises(g.ImmutabilityViolation):
        g.check_one(conn, "ihsanos", "061.sql", "ceayj", f)


def test_missing_ledger_FAILS_CLOSED(tmp_path):
    # Ledger table absent/unreadable => refuse to apply (never silently skip).
    conn = FakeConn(exists=False)
    with pytest.raises(g.LedgerUnavailable):
        g.check_one(conn, "orchestrator", "999.sql", REF, _mig(tmp_path, "999.sql", "x"))


def test_missing_ledger_bootstrap_escape_hatch(tmp_path):
    # The ONLY sanctioned pass on a missing ledger: bootstrapping the migration
    # that creates it, via the explicit flag.
    conn = FakeConn(exists=False)
    assert g.check_one(conn, "orchestrator", "023_migration_ledger.sql", REF,
                       _mig(tmp_path, "023.sql", "x"), allow_missing_ledger=True) is None
