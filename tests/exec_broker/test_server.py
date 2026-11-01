"""hc#95 exec-broker BrokerServer end-to-end (full request lifecycle).

Runs against a REAL AF_UNIX socket (works on macOS and Linux); only the
SO_PEERCRED uid lookup itself is injected (peer_uid_fn) so these tests don't
need Linux — Task 6's own tests already cover the real SO_PEERCRED syscall.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "lib"))

from exec_broker.mgmt_api import UpstreamError  # noqa: E402
from exec_broker.server import BrokerServer, _MAX_REQUEST_BYTES  # noqa: E402

TEST_UID = os.getuid()


class FakeCursor:
    def __init__(self, gate_row=None, raise_on_select=False):
        self.gate_row = gate_row
        self.raise_on_select = raise_on_select
        self.calls = []
        self.vault_rows = {}
        self._next_id = 1
        self._pending_fetch = None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        upper = sql.strip().upper()
        if upper.startswith("INSERT INTO EXEC_BROKER_ACCESS_LOG"):
            row_id = self._next_id
            self._next_id += 1
            self.vault_rows[row_id] = {"status": "started"}
            self._pending_fetch = {"id": row_id}
        elif upper.startswith("UPDATE EXEC_BROKER_ACCESS_LOG"):
            status, error_code, row_id = params
            self.vault_rows[row_id]["status"] = status
            self.vault_rows[row_id]["error_code"] = error_code
            self._pending_fetch = None
        elif upper.startswith("SELECT FROM_AGENT"):
            if self.raise_on_select:
                raise RuntimeError("simulated transient DB error inside the gate lookup")
            self._pending_fetch = self.gate_row
        else:
            self._pending_fetch = None

    def fetchone(self):
        return self._pending_fetch

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeDbConn:
    def __init__(self, cursor_obj):
        self._cursor = cursor_obj
        self.commits = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeDb:
    def __init__(self, gate_row=None, raise_on_select=False):
        self.cursor_obj = FakeCursor(gate_row=gate_row, raise_on_select=raise_on_select)
        self.conn = FakeDbConn(self.cursor_obj)

    def __call__(self):
        return self.conn

    @property
    def vault_rows(self):
        return self.cursor_obj.vault_rows


def _wait_until_accepting(sock_path: str, timeout: float = 2.0) -> None:
    """Blocks until sock_path is actually accepting connections, not merely
    present on disk. BrokerServer.serve_forever's bind() creates the path
    BEFORE listen() is called, so a readiness check based on os.path.exists
    alone has a real race window: a connect() landing in that window gets
    ConnectionRefusedError even though the file "exists" (the pre-existing
    ~1-in-3 test_server.py:136 flake, reported against commit 850a4c4).
    Proves readiness the only way that's actually true: a real connect
    succeeds, then is immediately closed without sending anything."""
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.connect(sock_path)
            finally:
                probe.close()
            return
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            last_err = exc
            time.sleep(0.02)
    raise RuntimeError(f"broker socket never started accepting connections: {last_err}")


def _make_server(
    tmp_path, allowed_uids, db=None, mgmt_api_fn=None, peer_uid_val=TEST_UID,
    uid_override=None, uid_override_expires_at=None,
    ref_override=None, ref_override_expires_at=None,
):
    # NOT tmp_path -- pytest's tmp_path is often >104 bytes deep, which overflows
    # AF_UNIX's sun_path limit (104 on macOS/BSD, 108 on Linux) and makes bind()
    # fail silently inside the daemon thread. A fresh shallow mkdtemp() stays short.
    sock_dir = tempfile.mkdtemp(prefix="eb-")
    sock_path = str(Path(sock_dir) / "broker.sock")
    db = db or FakeDb()
    server = BrokerServer(
        sock_path=sock_path,
        allowed_uids=allowed_uids,
        db_conn_factory=db,
        token="test-token",
        mgmt_api_fn=mgmt_api_fn or (lambda *a, **k: []),
        peer_uid_fn=lambda conn: peer_uid_val,
        uid_override=uid_override,
        uid_override_expires_at=uid_override_expires_at,
        ref_override=ref_override,
        ref_override_expires_at=ref_override_expires_at,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_until_accepting(sock_path)
    return server, sock_path, db


def _send(sock_path, request: dict) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect(sock_path)
        client.sendall(json.dumps(request).encode("utf-8") + b"\n")
        data = client.makefile("rb").readline()
    return json.loads(data.decode("utf-8"))


def _send_raw(sock_path, raw: bytes) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect(sock_path)
        client.sendall(raw)
        data = client.makefile("rb").readline()
    return json.loads(data.decode("utf-8"))


def test_wait_until_accepting_blocks_until_listen_is_called():
    """Pins the fix for the pre-existing ~1-in-3 ConnectionRefusedError flake
    (test_server.py:136, reported against commit 850a4c4): _make_server's old
    readiness wait polled os.path.exists(sock_path), but bind() creates that
    path BEFORE listen() is called -- a connect() landing in that window gets
    ECONNREFUSED even though the socket "exists". This test recreates the
    exact race window directly (bind a real AF_UNIX socket, delay listen())
    and asserts _wait_until_accepting does not return before listen() runs."""
    sock_dir = tempfile.mkdtemp(prefix="eb-wait-")
    sock_path = str(Path(sock_dir) / "broker.sock")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(sock_path)  # bound, NOT listening -- os.path.exists is already True here
    listened = threading.Event()

    def listen_after_delay():
        time.sleep(0.2)
        sock.listen(5)
        listened.set()

    threading.Thread(target=listen_after_delay, daemon=True).start()
    try:
        start = time.monotonic()
        _wait_until_accepting(sock_path, timeout=2.0)
        elapsed = time.monotonic() - start
        assert listened.is_set(), "returned before listen() was ever called"
        assert elapsed >= 0.2, f"returned too early ({elapsed}s) -- didn't actually wait for listen()"
    finally:
        sock.close()


def test_dml_only_success_end_to_end(tmp_path):
    db = FakeDb()
    _, sock_path, db = _make_server(tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=lambda *a, **k: [{"a": 1}])
    resp = _send(sock_path, {
        "project_ref": "oovskwogvefnznfowjky", "guarded_sql": "select 1",
        "reason": "r", "gate_id": None, "caller": "cc-irsyad-2", "exec_ledger_id": "e1",
    })
    assert resp == {"status": "ok", "rows": [{"a": 1}]}
    assert list(db.vault_rows.values())[0]["status"] == "ok"


def test_acl_denied_for_unlisted_uid(tmp_path):
    _, sock_path, _ = _make_server(tmp_path, frozenset({TEST_UID + 1}))
    # repeated to catch the close-before-client-write race this path once hit
    # (the server used to respond+close before reading, which could race a
    # same-process client's still-in-flight sendall into an EPIPE)
    for _ in range(20):
        resp = _send(sock_path, {
            "project_ref": "x", "guarded_sql": "select 1", "reason": "", "gate_id": None,
            "caller": "c", "exec_ledger_id": "e",
        })
        assert resp["error"]["code"] == "ACL_DENIED"


def test_malformed_json_returns_internal_error(tmp_path):
    _, sock_path, _ = _make_server(tmp_path, frozenset({TEST_UID}))
    resp = _send_raw(sock_path, b"not json\n")
    assert resp["error"]["code"] == "INTERNAL_ERROR"


def test_bad_project_ref_logs_started_then_error(tmp_path):
    db = FakeDb()
    _, sock_path, db = _make_server(tmp_path, frozenset({TEST_UID}), db=db)
    resp = _send(sock_path, {
        "project_ref": "not-a-real-ref", "guarded_sql": "select 1", "reason": "",
        "gate_id": None, "caller": "c", "exec_ledger_id": "e",
    })
    assert resp["error"]["code"] == "BAD_PROJECT_REF"
    row = list(db.vault_rows.values())[0]
    assert row["status"] == "error"
    assert row["error_code"] == "BAD_PROJECT_REF"


def test_gate_required_for_ddl_without_gate(tmp_path):
    db = FakeDb()
    _, sock_path, db = _make_server(tmp_path, frozenset({TEST_UID}), db=db)
    resp = _send(sock_path, {
        "project_ref": "oovskwogvefnznfowjky", "guarded_sql": "drop table t", "reason": "",
        "gate_id": None, "caller": "c", "exec_ledger_id": "e",
    })
    assert resp["error"]["code"] == "GATE_REQUIRED"
    assert list(db.vault_rows.values())[0]["status"] == "error"


def test_ddl_with_fully_valid_shaped_gate_still_refused_end_to_end(tmp_path):
    # FAIL-CLOSED (bus #70929): even a gate that is shape-valid on every axis
    # (right agent/type/past/silo) must be refused end-to-end -- the broker
    # cannot independently verify which migration/PR it authorizes, so DDL
    # is refused rather than allowed by default in milestone (a).
    gate_row = {"from_agent": "orch-console", "message_type": "decision",
                "subject": "go for qa", "body": "", "is_past": True}
    db = FakeDb(gate_row=gate_row)
    _, sock_path, db = _make_server(tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=lambda *a, **k: [])
    resp = _send(sock_path, {
        "project_ref": "oovskwogvefnznfowjky", "guarded_sql": "drop table t", "reason": "",
        "gate_id": "56485", "caller": "c", "exec_ledger_id": "e",
    })
    assert resp["error"]["code"] == "GATE_INVALID"
    assert list(db.vault_rows.values())[0]["status"] == "error"


def test_ddl_passes_end_to_end_when_wire_migration_matches_the_gate(tmp_path):
    # Part A (rollout step (c), bus #79024/#79465): once the wire request
    # carries migration/pr, server.py must thread them into require_gate so
    # the full independent match (not the unconditional refusal above) is
    # used, end to end through the real socket.
    gate_row = {"from_agent": "orch-console", "message_type": "decision",
                "subject": "apply migration 435 to qa", "body": "", "is_past": True}
    db = FakeDb(gate_row=gate_row)
    _, sock_path, db = _make_server(tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=lambda *a, **k: [])
    resp = _send(sock_path, {
        "project_ref": "oovskwogvefnznfowjky", "guarded_sql": "drop table t", "reason": "",
        "gate_id": "56485", "caller": "c", "exec_ledger_id": "e",
        "migration": "435", "pr": None,
    })
    assert resp == {"status": "ok", "rows": []}


def test_ddl_still_refused_end_to_end_when_wire_migration_does_not_match_the_gate(tmp_path):
    gate_row = {"from_agent": "orch-console", "message_type": "decision",
                "subject": "apply migration 435 to qa", "body": "", "is_past": True}
    db = FakeDb(gate_row=gate_row)
    _, sock_path, db = _make_server(tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=lambda *a, **k: [])
    resp = _send(sock_path, {
        "project_ref": "oovskwogvefnznfowjky", "guarded_sql": "drop table t", "reason": "",
        "gate_id": "56485", "caller": "c", "exec_ledger_id": "e",
        "migration": "999", "pr": None,
    })
    assert resp["error"]["code"] == "GATE_INVALID"


# --- Part A 2/2 (orch-console ruling bus #79477): the byte binding for
# LEXICALLY HAZARDOUS SQL, end to end through the real socket. The unit-level
# pins live in test_gate_validation.py's Part A 2/2 section; these two prove
# the wiring (wire migration/pr + the gate's cited sha12 both reach
# require_gate) and that GATE_MISMATCH actually crosses the wire as an
# additive §5 code.

_HAZ_SQL = 'SELECT "x--y"; DROP TABLE t'


def test_hazardous_passes_end_to_end_when_gate_cites_the_executed_bytes_sha12(tmp_path):
    sha12 = hashlib.sha256(_HAZ_SQL.encode("utf-8")).hexdigest()[:12]
    gate_row = {"from_agent": "orch-console", "message_type": "decision",
                "subject": "apply migration 435 to qa", "is_past": True,
                "body": f"commit 5053826e; raw-sha12: 0123456789ab\nwrapped-sha12: {sha12}"}
    db = FakeDb(gate_row=gate_row)
    _, sock_path, db = _make_server(tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=lambda *a, **k: [])
    resp = _send(sock_path, {
        "project_ref": "oovskwogvefnznfowjky", "guarded_sql": _HAZ_SQL, "reason": "",
        "gate_id": "56485", "caller": "c", "exec_ledger_id": "e",
        "migration": "435", "pr": None,
    })
    assert resp == {"status": "ok", "rows": []}


def test_hazardous_one_byte_diff_refused_end_to_end_with_gate_mismatch(tmp_path):
    # Same gate row as the passing test above; the EXECUTED bytes differ by a
    # single trailing space. Every text-matched axis still matches -- only the
    # digest differs -- so this is the byte binding doing the work, and the
    # refusal must cross the wire as the additive GATE_MISMATCH code (not
    # GATE_INVALID), with the vault row finalized as an error.
    sha12 = hashlib.sha256(_HAZ_SQL.encode("utf-8")).hexdigest()[:12]
    gate_row = {"from_agent": "orch-console", "message_type": "decision",
                "subject": "apply migration 435 to qa", "is_past": True,
                "body": f"commit 5053826e; raw-sha12: 0123456789ab\nwrapped-sha12: {sha12}"}
    db = FakeDb(gate_row=gate_row)
    _, sock_path, db = _make_server(tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=lambda *a, **k: [])
    resp = _send(sock_path, {
        "project_ref": "oovskwogvefnznfowjky", "guarded_sql": _HAZ_SQL + " ", "reason": "",
        "gate_id": "56485", "caller": "c", "exec_ledger_id": "e",
        "migration": "435", "pr": None,
    })
    assert resp["status"] == "error"
    assert resp["error"]["code"] == "GATE_MISMATCH"
    assert list(db.vault_rows.values())[0]["error_code"] == "GATE_MISMATCH"


def test_old_six_key_dml_caller_unaffected_by_migration_pr_amendment_end_to_end(tmp_path):
    # The deploy-safety property the whole optional-key design rests on
    # (cc-fleet-health #79465): an old, pre-amendment caller sending exactly
    # the original 6 keys (no migration/pr at all) must get byte-identical
    # behaviour on a plain DML call -- no MalformedRequest, ok response.
    db = FakeDb()
    _, sock_path, db = _make_server(tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=lambda *a, **k: [{"n": 1}])
    resp = _send(sock_path, {
        "project_ref": "oovskwogvefnznfowjky", "guarded_sql": "select count(*) from t", "reason": "",
        "gate_id": None, "caller": "c", "exec_ledger_id": "e",
    })
    assert resp == {"status": "ok", "rows": [{"n": 1}]}


def test_dml_still_succeeds_with_no_gate_even_though_ddl_is_refused(tmp_path):
    # DML-only SQL never needs a gate and is unaffected by the DDL refusal
    # above -- confirms the fail-closed fix didn't overreach into DML.
    db = FakeDb()
    _, sock_path, db = _make_server(tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=lambda *a, **k: [{"n": 1}])
    resp = _send(sock_path, {
        "project_ref": "oovskwogvefnznfowjky", "guarded_sql": "select count(*) from t", "reason": "",
        "gate_id": None, "caller": "c", "exec_ledger_id": "e",
    })
    assert resp == {"status": "ok", "rows": [{"n": 1}]}


def test_real_non_qa_ref_rejected_end_to_end(tmp_path):
    # Milestone (a) scope, enforced in code (orch-console gate ruling, bus
    # #70919): goumlyne is a REAL, CURRENT irsyad ref -- must still be
    # rejected end-to-end, not just at the unit level, until step (c) lands.
    db = FakeDb()
    _, sock_path, db = _make_server(tmp_path, frozenset({TEST_UID}), db=db)
    resp = _send(sock_path, {
        "project_ref": "goumlynecruxrlmzlntp", "guarded_sql": "select 1", "reason": "",
        "gate_id": None, "caller": "c", "exec_ledger_id": "e",
    })
    assert resp["error"]["code"] == "BAD_PROJECT_REF"
    assert list(db.vault_rows.values())[0]["status"] == "error"


def test_upstream_error_propagates(tmp_path):
    def _boom(*a, **k):
        raise UpstreamError("upstream down")
    db = FakeDb()
    _, sock_path, db = _make_server(tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=_boom)
    resp = _send(sock_path, {
        "project_ref": "oovskwogvefnznfowjky", "guarded_sql": "select 1", "reason": "",
        "gate_id": None, "caller": "c", "exec_ledger_id": "e",
    })
    assert resp["error"]["code"] == "UPSTREAM_ERROR"
    assert list(db.vault_rows.values())[0]["status"] == "error"


def test_unexpected_exception_in_mgmt_api_maps_to_internal_error(tmp_path):
    def _boom(*a, **k):
        raise RuntimeError("totally unexpected bug")
    db = FakeDb()
    _, sock_path, db = _make_server(tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=_boom)
    resp = _send(sock_path, {
        "project_ref": "oovskwogvefnznfowjky", "guarded_sql": "select 1", "reason": "",
        "gate_id": None, "caller": "c", "exec_ledger_id": "e",
    })
    assert resp["error"]["code"] == "INTERNAL_ERROR"


def test_peer_uid_resolution_failure_maps_to_internal_error(tmp_path):
    sock_path = str(Path(tempfile.mkdtemp(prefix="eb-")) / "broker.sock")
    db = FakeDb()
    server = BrokerServer(
        sock_path=sock_path, allowed_uids=frozenset({TEST_UID}), db_conn_factory=db,
        token="t", mgmt_api_fn=lambda *a, **k: [],
        peer_uid_fn=lambda conn: (_ for _ in ()).throw(OSError("no SO_PEERCRED here")),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_until_accepting(sock_path)
    resp = _send(sock_path, {
        "project_ref": "x", "guarded_sql": "select 1", "reason": "", "gate_id": None,
        "caller": "c", "exec_ledger_id": "e",
    })
    assert resp["error"]["code"] == "INTERNAL_ERROR"


def test_oversized_request_rejected_without_hanging(tmp_path):
    _, sock_path, _ = _make_server(tmp_path, frozenset({TEST_UID}))
    oversized = b'{"project_ref":"' + b"x" * (_MAX_REQUEST_BYTES + 10) + b'"}\n'
    resp = _send_raw(sock_path, oversized)
    assert resp["error"]["code"] == "INTERNAL_ERROR"


def test_respond_never_raises_on_an_already_closed_peer():
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    b.close()  # peer side gone before the server tries to respond
    try:
        BrokerServer._respond(a, b'{"status":"error"}\n')  # must not raise
    finally:
        a.close()


def test_transient_db_error_during_revalidation_still_finalizes_the_row(tmp_path):
    # A transient DB error inside require_gate's gate lookup (NOT a
    # BrokerValidationError) must still finalize the exec_broker_access_log row to
    # error -- never leave it stuck at status=started (itself a P1 per the
    # migration's own comment) just because the broker didn't crash.
    gate_row = {"from_agent": "orch-console", "message_type": "decision", "subject": "go for qa", "body": "", "is_past": True}
    db = FakeDb(gate_row=gate_row, raise_on_select=True)
    _, sock_path, db = _make_server(tmp_path, frozenset({TEST_UID}), db=db)
    resp = _send(sock_path, {
        "project_ref": "oovskwogvefnznfowjky", "guarded_sql": "drop table t", "reason": "",
        "gate_id": "56485", "caller": "c", "exec_ledger_id": "e",
    })
    assert resp["error"]["code"] == "INTERNAL_ERROR"
    assert list(db.vault_rows.values())[0]["status"] == "error"


def test_success_path_commits_before_the_mgmt_api_call(tmp_path):
    # The transaction opened by log_started (and, on a DDL path, by
    # require_gate's gate lookup) must be committed BEFORE the blocking
    # mgmt-API call, not held idle-in-transaction across it (pooler risk /
    # retry-double-execution risk on a killed idle transaction). 3 commits
    # for a success: started, pre-call, finalized.
    db = FakeDb()
    _, sock_path, db = _make_server(tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=lambda *a, **k: [])
    resp = _send(sock_path, {
        "project_ref": "oovskwogvefnznfowjky", "guarded_sql": "select 1", "reason": "",
        "gate_id": None, "caller": "c", "exec_ledger_id": "e",
    })
    assert resp == {"status": "ok", "rows": []}
    assert db.conn.commits == 3


# --- bounded override (op#34322/#76985/#76989/#76998), end-to-end ----------

_FUTURE = datetime.now(timezone.utc) + timedelta(hours=1)
_PAST = datetime.now(timezone.utc) - timedelta(seconds=1)


def test_ref_override_allows_non_qa_ref_within_expiry_end_to_end(tmp_path):
    db = FakeDb()
    _, sock_path, db = _make_server(
        tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=lambda *a, **k: [],
        ref_override="goumlynecruxrlmzlntp", ref_override_expires_at=_FUTURE,
    )
    resp = _send(sock_path, {
        "project_ref": "goumlynecruxrlmzlntp", "guarded_sql": "select 1", "reason": "",
        "gate_id": None, "caller": "c", "exec_ledger_id": "e",
    })
    assert resp == {"status": "ok", "rows": []}


def test_ref_override_denies_after_expiry_end_to_end(tmp_path):
    db = FakeDb()
    _, sock_path, db = _make_server(
        tmp_path, frozenset({TEST_UID}), db=db,
        ref_override="goumlynecruxrlmzlntp", ref_override_expires_at=_PAST,
    )
    resp = _send(sock_path, {
        "project_ref": "goumlynecruxrlmzlntp", "guarded_sql": "select 1", "reason": "",
        "gate_id": None, "caller": "c", "exec_ledger_id": "e",
    })
    assert resp["error"]["code"] == "BAD_PROJECT_REF"


def test_ref_override_does_not_widen_an_unrelated_ref_end_to_end(tmp_path):
    # An active override for goumlyne must not accidentally widen ceayj too.
    db = FakeDb()
    _, sock_path, db = _make_server(
        tmp_path, frozenset({TEST_UID}), db=db,
        ref_override="goumlynecruxrlmzlntp", ref_override_expires_at=_FUTURE,
    )
    resp = _send(sock_path, {
        "project_ref": "ceayjeamtmcyzzvqflus", "guarded_sql": "select 1", "reason": "",
        "gate_id": None, "caller": "c", "exec_ledger_id": "e",
    })
    assert resp["error"]["code"] == "BAD_PROJECT_REF"


def test_uid_override_allows_unlisted_uid_within_expiry_end_to_end(tmp_path):
    db = FakeDb()
    _, sock_path, db = _make_server(
        tmp_path, frozenset({TEST_UID + 1}), db=db, mgmt_api_fn=lambda *a, **k: [],
        uid_override=frozenset({TEST_UID}), uid_override_expires_at=_FUTURE,
    )
    resp = _send(sock_path, {
        "project_ref": "oovskwogvefnznfowjky", "guarded_sql": "select 1", "reason": "",
        "gate_id": None, "caller": "c", "exec_ledger_id": "e",
    })
    assert resp == {"status": "ok", "rows": []}


def test_uid_override_denies_after_expiry_end_to_end(tmp_path):
    db = FakeDb()
    _, sock_path, db = _make_server(
        tmp_path, frozenset({TEST_UID + 1}), db=db,
        uid_override=frozenset({TEST_UID}), uid_override_expires_at=_PAST,
    )
    resp = _send(sock_path, {
        "project_ref": "oovskwogvefnznfowjky", "guarded_sql": "select 1", "reason": "",
        "gate_id": None, "caller": "c", "exec_ledger_id": "e",
    })
    assert resp["error"]["code"] == "ACL_DENIED"


# --- SELECT-only override mode (GO #78974, assignment bus #79024) ------------

_GOUMLYNE = "goumlynecruxrlmzlntp"  # == SILO_ALIASES["goumlyne"], already committed


@pytest.mark.parametrize("guarded_sql", [
    "insert into t values (1)",
    "update t set x = 1",
    "delete from t",
    "DO $$ BEGIN UPDATE t SET x = 1; END $$",
    "SELECT set_config('a', 'b', false)",
])
def test_ref_override_readonly_refuses_writes_end_to_end(tmp_path, guarded_sql):
    # RO promised (bus #78968) is now RO enforced (GO #78974): while the ref
    # override is armed, the assignment's five write shapes to the OVERRIDE
    # ref are all refused, the audit row is finalized, and the mgmt API is
    # never called.
    seen = []
    db = FakeDb()
    _, sock_path, db = _make_server(
        tmp_path, frozenset({TEST_UID}), db=db,
        mgmt_api_fn=lambda sql, ref, tok: seen.append((sql, ref)) or [],
        ref_override=_GOUMLYNE, ref_override_expires_at=_FUTURE,
    )
    resp = _send(sock_path, {"project_ref": _GOUMLYNE, "guarded_sql": guarded_sql,
                             "reason": "", "gate_id": None, "caller": "c", "exec_ledger_id": "e"})
    assert resp["error"]["code"] == "READ_ONLY_OVERRIDE"
    row = list(db.vault_rows.values())[0]
    assert row["status"] == "error" and row["error_code"] == "READ_ONLY_OVERRIDE"
    assert seen == []  # refusal never reaches the mgmt API (review Minor #3)


def test_ref_override_readonly_allows_cte_read_end_to_end(tmp_path):
    db = FakeDb()
    _, sock_path, db = _make_server(
        tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=lambda *a, **k: [{"n": 1}],
        ref_override=_GOUMLYNE, ref_override_expires_at=_FUTURE,
    )
    resp = _send(sock_path, {
        "project_ref": _GOUMLYNE,
        "guarded_sql": "with x as (select id from t) select count(*) from x",
        "reason": "", "gate_id": None, "caller": "c", "exec_ledger_id": "e"})
    assert resp == {"status": "ok", "rows": [{"n": 1}]}


# B1 (bus #89814): every non-plain-read statement on a gated ref -- QA
# included -- now needs a gate with a specific anchor, mirroring
# exec_prod._require_gate (console #81213). The four write tests below pin
# OVERRIDE SCOPING, not gating, so they now carry a valid ref-anchored gate;
# the ungated-write refusal itself is pinned in the B1 section at the end.
_QA_REF_GATE_ROW = {"from_agent": "orch-console", "message_type": "decision",
                    "subject": "GO data fix op#51 on qa", "body": "", "is_past": True}


def test_ref_override_readonly_scoped_to_override_ref_baseline_write_passes(tmp_path):
    # SCOPE RULING (cc-fleet-health bus #79039, 2026-10-28 — supersedes the
    # broker-wide reading of GO #78974): while the override is armed, a write
    # to the ALWAYS-ALLOWED QA ref passes exactly as it does today — RO binds
    # only requests targeting the override's ref.
    seen = []
    db = FakeDb(gate_row=_QA_REF_GATE_ROW)
    _, sock_path, db = _make_server(
        tmp_path, frozenset({TEST_UID}), db=db,
        mgmt_api_fn=lambda sql, ref, tok: seen.append((sql, ref)) or [],
        ref_override=_GOUMLYNE, ref_override_expires_at=_FUTURE,
    )
    resp = _send(sock_path, {"project_ref": "oovskwogvefnznfowjky",
                             "guarded_sql": "insert into t values (1)",
                             "reason": "", "gate_id": "56485", "ref": "op#51",
                             "caller": "c", "exec_ledger_id": "e"})
    assert resp == {"status": "ok", "rows": []}
    assert seen == [("insert into t values (1)", "oovskwogvefnznfowjky")]
    row = list(db.vault_rows.values())[0]
    assert row["status"] == "ok"


def test_ref_override_readonly_allowlist_runs_before_ro_pin(tmp_path):
    # PIN (cc-fleet-health bus #79103): the ref allowlist check runs BEFORE
    # the RO check — a plain SELECT to a non-allowlisted ref under an armed
    # override gets BAD_PROJECT_REF (the class check never runs), so a future
    # reorder of the validation chain fails this test loudly. Synthetic
    # non-allowlisted ref: the property under test is ordering, not any real
    # store (console_irsyad_guard fired on a real-ref fixture; ledgered).
    db = FakeDb()
    _, sock_path, db = _make_server(
        tmp_path, frozenset({TEST_UID}), db=db,
        ref_override=_GOUMLYNE, ref_override_expires_at=_FUTURE,
    )
    resp = _send(sock_path, {"project_ref": "fakeplatypusref00001",
                             "guarded_sql": "select 1",
                             "reason": "", "gate_id": None, "caller": "c", "exec_ledger_id": "e"})
    assert resp["error"]["code"] == "BAD_PROJECT_REF"


def test_expired_ref_override_lifts_readonly_for_qa_write(tmp_path):
    # Expiry reverts BOTH the ref widening and the RO mode, by construction.
    db = FakeDb(gate_row=_QA_REF_GATE_ROW)
    _, sock_path, db = _make_server(
        tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=lambda *a, **k: [],
        ref_override=_GOUMLYNE, ref_override_expires_at=_PAST,
    )
    resp = _send(sock_path, {"project_ref": "oovskwogvefnznfowjky",
                             "guarded_sql": "insert into t values (1)",
                             "reason": "", "gate_id": "56485", "ref": "op#51",
                             "caller": "c", "exec_ledger_id": "e"})
    assert resp == {"status": "ok", "rows": []}   # QA write unaffected once the override expired


def test_uid_override_alone_does_not_impose_readonly(tmp_path):
    # bus #79024 ties RO to the REF override only; a uid-override window
    # must not silently turn the broker read-only.
    db = FakeDb(gate_row=_QA_REF_GATE_ROW)
    _, sock_path, db = _make_server(
        tmp_path, frozenset({TEST_UID + 1}), db=db, mgmt_api_fn=lambda *a, **k: [],
        uid_override=frozenset({TEST_UID}), uid_override_expires_at=_FUTURE,
    )
    resp = _send(sock_path, {"project_ref": "oovskwogvefnznfowjky",
                             "guarded_sql": "insert into t values (1)",
                             "reason": "", "gate_id": "56485", "ref": "op#51",
                             "caller": "c", "exec_ledger_id": "e"})
    assert resp == {"status": "ok", "rows": []}


def test_no_override_write_behavior_unchanged_end_to_end(tmp_path):
    # No override armed at all: a GATED INSERT reaches the mgmt API exactly
    # as before this feature existed (B1 adds the gate requirement, nothing
    # else about the write path changes).
    seen = []
    db = FakeDb(gate_row=_QA_REF_GATE_ROW)
    _, sock_path, db = _make_server(
        tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=lambda sql, ref, tok: seen.append((sql, ref)) or [],
    )
    resp = _send(sock_path, {"project_ref": "oovskwogvefnznfowjky",
                             "guarded_sql": "insert into t values (1)",
                             "reason": "", "gate_id": "56485", "ref": "op#51",
                             "caller": "c", "exec_ledger_id": "e"})
    assert resp == {"status": "ok", "rows": []}
    assert seen == [("insert into t values (1)", "oovskwogvefnznfowjky")]


# --- B1 + B2 + --ref end to end (bus #89814) --------------------------------

def _insert_params(db):
    return [p for sql, p in db.cursor_obj.calls if sql.strip().upper().startswith("INSERT INTO EXEC_BROKER_ACCESS_LOG")]


def test_b1_ungated_dml_on_default_qa_ref_is_gate_required_end_to_end(tmp_path):
    seen = []
    db = FakeDb()
    _, sock_path, db = _make_server(
        tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=lambda *a, **k: seen.append(a) or [],
    )
    resp = _send(sock_path, {"project_ref": "oovskwogvefnznfowjky", "guarded_sql": "update t set a = 1",
                             "reason": "", "gate_id": None, "caller": "c", "exec_ledger_id": "e"})
    assert resp["error"]["code"] == "GATE_REQUIRED"
    assert seen == [], "a refused statement must never reach the mgmt API"
    row = list(db.vault_rows.values())[0]
    assert row == {"status": "error", "error_code": "GATE_REQUIRED"}


def test_b2_ref_threads_through_to_require_gate_and_the_ledger_end_to_end(tmp_path):
    seen = []
    db = FakeDb(gate_row=_QA_REF_GATE_ROW)
    _, sock_path, db = _make_server(
        tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=lambda sql, ref, tok: seen.append(sql) or [],
    )
    resp = _send(sock_path, {"project_ref": "oovskwogvefnznfowjky", "guarded_sql": "update t set a = 1",
                             "reason": "", "gate_id": "56485", "ref": "op#51",
                             "caller": "c", "exec_ledger_id": "e"})
    assert resp == {"status": "ok", "rows": []}
    assert seen == ["update t set a = 1"]
    (params,) = _insert_params(db)
    assert params[-1] == "op#51"  # gate_ref recorded on the audit row


def test_b2_ref_not_in_gate_text_is_gate_invalid_end_to_end(tmp_path):
    seen = []
    db = FakeDb(gate_row=_QA_REF_GATE_ROW)  # names op#51, never op#510
    _, sock_path, db = _make_server(
        tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=lambda *a, **k: seen.append(a) or [],
    )
    resp = _send(sock_path, {"project_ref": "oovskwogvefnznfowjky", "guarded_sql": "update t set a = 1",
                             "reason": "", "gate_id": "56485", "ref": "op#510",
                             "caller": "c", "exec_ledger_id": "e"})
    assert resp["error"]["code"] == "GATE_INVALID"
    assert seen == []


def test_b2_ref_plus_migration_refused_end_to_end(tmp_path):
    db = FakeDb(gate_row={**_QA_REF_GATE_ROW, "body": "migration 479"})
    _, sock_path, db = _make_server(tmp_path, frozenset({TEST_UID}), db=db)
    resp = _send(sock_path, {"project_ref": "oovskwogvefnznfowjky", "guarded_sql": "update t set a = 1",
                             "reason": "", "gate_id": "56485", "ref": "op#51", "migration": "479",
                             "caller": "c", "exec_ledger_id": "e"})
    assert resp["error"]["code"] == "GATE_INVALID"
    assert list(db.vault_rows.values())[0]["error_code"] == "GATE_INVALID"


def test_b2_malformed_ref_is_audited_then_refused_end_to_end(tmp_path):
    db = FakeDb(gate_row=_QA_REF_GATE_ROW)
    _, sock_path, db = _make_server(tmp_path, frozenset({TEST_UID}), db=db)
    resp = _send(sock_path, {"project_ref": "oovskwogvefnznfowjky", "guarded_sql": "select 1",
                             "reason": "", "gate_id": None, "ref": "OP#51",
                             "caller": "c", "exec_ledger_id": "e"})
    assert resp["error"]["code"] == "GATE_INVALID"
    assert list(db.vault_rows.values())[0]["status"] == "error"


def test_b1_plain_read_on_gated_ref_needs_no_gate_end_to_end(tmp_path):
    db = FakeDb()
    _, sock_path, db = _make_server(tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=lambda *a, **k: [{"n": 1}])
    resp = _send(sock_path, {"project_ref": "oovskwogvefnznfowjky", "guarded_sql": "select count(*) from t",
                             "reason": "", "gate_id": None, "caller": "c", "exec_ledger_id": "e"})
    assert resp == {"status": "ok", "rows": [{"n": 1}]}
    # no gate lookup was made for a plain read
    assert not any(sql.strip().upper().startswith("SELECT FROM_AGENT") for sql, _ in db.cursor_obj.calls)


def test_b1_widened_live_gated_refs_allows_and_gates_a_real_ref_end_to_end(tmp_path):
    goumlyne_gate = {"from_agent": "orch-console", "message_type": "decision",
                     "subject": "GO op#77 data fix on goumlyne", "body": "", "is_past": True}
    seen = []
    db = FakeDb(gate_row=goumlyne_gate)
    server, sock_path, db = _make_server(
        tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=lambda sql, ref, tok: seen.append(ref) or [],
    )
    server.live_gated_refs = frozenset({"oovskwogvefnznfowjky", _GOUMLYNE})
    # ungated DML on the newly gated ref -> GATE_REQUIRED
    resp = _send(sock_path, {"project_ref": _GOUMLYNE, "guarded_sql": "update t set a = 1",
                             "reason": "", "gate_id": None, "caller": "c", "exec_ledger_id": "e1"})
    assert resp["error"]["code"] == "GATE_REQUIRED"
    # valid ref-anchored gate -> executes
    resp = _send(sock_path, {"project_ref": _GOUMLYNE, "guarded_sql": "update t set a = 1",
                             "reason": "", "gate_id": "56485", "ref": "op#77",
                             "caller": "c", "exec_ledger_id": "e2"})
    assert resp == {"status": "ok", "rows": []}
    assert seen == [_GOUMLYNE]


def test_b1_default_live_gated_refs_still_rejects_real_refs_end_to_end(tmp_path):
    db = FakeDb(gate_row={"from_agent": "orch-console", "message_type": "decision",
                          "subject": "GO op#77 on goumlyne", "body": "", "is_past": True})
    server, sock_path, db = _make_server(tmp_path, frozenset({TEST_UID}), db=db)
    assert server.live_gated_refs is None
    resp = _send(sock_path, {"project_ref": _GOUMLYNE, "guarded_sql": "update t set a = 1",
                             "reason": "", "gate_id": "56485", "ref": "op#77",
                             "caller": "c", "exec_ledger_id": "e"})
    assert resp["error"]["code"] == "BAD_PROJECT_REF"
