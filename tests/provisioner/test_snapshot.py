"""snapshot.py — the verified-snapshot half of #80298 condition 1, unit
level: the marker INSERT is EARNED (file written 0600 in a 0700 dir, bytes
re-read from disk, COPY line count reconciled against the SQL count) and
anything short of that raises SnapshotRefused with NO marker written.

The same-module happy path against REAL PostgreSQL (marker insert, guard
interplay, byte-identical restore) is proven by the PG17 harness in
test_install_store_role_pg17.py; these tests pin the refusal classes and
the on-disk contract with a scripted cursor, cheaply.
"""
import hashlib
import sys
from pathlib import Path

import pytest

ORCH_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ORCH_DIR / "scripts" / "lib"))

from provisioner import snapshot as snap  # noqa: E402
from provisioner.spec import load_spec, spec_path  # noqa: E402

SPEC = load_spec(spec_path("bariq"))

# two well-formed COPY streams — psycopg3's COPY TO surface carries the raw
# rows and no "\." trailer (verified against real PG17 in the harness)
_CONTENT_COPY = (b"62617269-7100-4000-8000-000000000001\thero\t{\"h\": \"live\"}\t"
                 b"2026-10-27 09:00:00+00\tseed\n"
                 b"62617269-7100-4000-8000-000000000001\tsite\t{\"s\": 1}\t"
                 b"2026-10-27 09:00:01+00\tseed\n"
                 b"62617269-7100-4000-8000-000000000001\ttestimonials\t{\"t\": []}\t"
                 b"2026-10-27 09:00:02+00\tseed\n")
_HISTORY_COPY = (b"1\t62617269-7100-4000-8000-000000000001\thero\t\\N\t{\"h\": \"live\"}\t"
                 b"seed\t2026-10-27 09:00:00+00\n")


class _Cur:
    """Scripted cursor: fetchone pops scripted rows in order, execute records."""

    def __init__(self, results):
        self._results = list(results)
        self.executed = []  # (sql, params) tuples

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return self

    def fetchone(self):
        return self._results.pop(0) if self._results else None


def _happy_cursor():
    # fetchone script: content exists -> 3 rows, history exists -> 1 row,
    # then the epoch read for the filename stamp
    return _Cur([(True,), (3,), (True,), (1,), (1730000000,)])


def _marker_inserts(cur):
    return [e for e in cur.executed
            if e[0].startswith("INSERT INTO public.provisioner_revert_snapshots")]


def test_snapshot_earns_the_marker_with_0600_files_and_exact_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(snap, "_copy_bytes",
                        lambda cur, table, order: {"bariq_site_content": _CONTENT_COPY,
                                                   "bariq_site_content_history": _HISTORY_COPY}[table])
    cur = _happy_cursor()
    info = snap.ensure_verified_snapshot(cur, SPEC, tmp_path)
    assert info == {"content_rows": 3, "history_rows": 1, "skipped": False}
    out = tmp_path / "provisioner_snapshots"
    assert (out.stat().st_mode & 0o777) == 0o700
    files = {p.name.split(".", 1)[1]: p for p in out.iterdir()}
    assert set(files) == {"bariq_site_content.copy", "bariq_site_content_history.copy"}
    for p in files.values():
        assert (p.stat().st_mode & 0o777) == 0o600
    # the marker INSERT carries ops metadata only: counts + shas of the
    # on-disk bytes + the path — never tenant data, never credentials
    (sql, params), = _marker_inserts(cur)
    assert sql.startswith("INSERT INTO public.provisioner_revert_snapshots")
    assert params is not None and len(params) == 6
    client_key, c_rows, h_rows, c_sha, h_sha, path = params
    assert (client_key, c_rows, h_rows) == ("bariq", 3, 1)
    assert c_sha == hashlib.sha256(_CONTENT_COPY).hexdigest()
    assert h_sha == hashlib.sha256(_HISTORY_COPY).hexdigest()
    assert str(out) in path


def test_re_read_mismatch_refuses_and_writes_no_marker(tmp_path, monkeypatch):
    # the verification IS the re-read: if the bytes come back off disk
    # different from the export, refuse — the revert this would authorize
    # is exactly the one that must not run
    monkeypatch.setattr(snap, "_copy_bytes", lambda cur, table, order: _CONTENT_COPY)
    real_write = snap._write_0600

    def corrupt(path, data):
        real_write(path, data + b"tampered")

    monkeypatch.setattr(snap, "_write_0600", corrupt)
    cur = _happy_cursor()
    with pytest.raises(snap.SnapshotRefused, match="re-read mismatch"):
        snap.ensure_verified_snapshot(cur, SPEC, tmp_path)
    assert _marker_inserts(cur) == []


def test_line_count_mismatch_against_sql_count_refuses(tmp_path, monkeypatch):
    # byte-equal re-read but the WRONG number of rows (2 on disk vs 3 from
    # SQL): a snapshot that does not reconcile with the live table refuses
    two_rows = _CONTENT_COPY.replace(
        b"62617269-7100-4000-8000-000000000001\ttestimonials\t{\"t\": []}\t"
        b"2026-10-27 09:00:02+00\tseed\n", b"")
    monkeypatch.setattr(snap, "_copy_bytes", lambda cur, table, order: two_rows)
    cur = _happy_cursor()
    with pytest.raises(snap.SnapshotRefused, match="line count 2 != SQL count 3"):
        snap.ensure_verified_snapshot(cur, SPEC, tmp_path)
    assert _marker_inserts(cur) == []


def test_empty_tables_are_a_verified_no_op(tmp_path):
    cur = _Cur([(True,), (0,), (True,), (0,)])
    info = snap.ensure_verified_snapshot(cur, SPEC, tmp_path)
    assert info == {"content_rows": 0, "history_rows": 0, "skipped": True}
    assert not (tmp_path / "provisioner_snapshots").exists()
    assert _marker_inserts(cur) == []


def test_missing_tables_count_as_zero(tmp_path):
    # a revert on a half-dropped world: to_regclass NULL -> count 0 -> the
    # no-op path (and the guard in the rendered revert re-counts live anyway)
    cur = _Cur([(False,), (False,)])
    assert snap.ensure_verified_snapshot(cur, SPEC, tmp_path)["skipped"] is True


def test_empty_side_table_gets_the_empty_hash(tmp_path, monkeypatch):
    # content holds rows, history is EMPTY: history must still be pinned in
    # the marker (sha256 of empty bytes) so the counts/shas always reconcile
    monkeypatch.setattr(snap, "_copy_bytes", lambda cur, table, order: _CONTENT_COPY)
    cur = _Cur([(True,), (3,), (True,), (0,), (1730000000,)])
    info = snap.ensure_verified_snapshot(cur, SPEC, tmp_path)
    assert info == {"content_rows": 3, "history_rows": 0, "skipped": False}
    (_, params), = _marker_inserts(cur)
    assert params[4] == hashlib.sha256(b"").hexdigest()  # history_sha256
    only = list((tmp_path / "provisioner_snapshots").iterdir())
    assert len(only) == 1 and only[0].name.endswith("bariq_site_content.copy")
