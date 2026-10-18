"""hc#95 exec-broker BrokerServer end-to-end (full request lifecycle).

Runs against a REAL AF_UNIX socket (works on macOS and Linux); only the
SO_PEERCRED uid lookup itself is injected (peer_uid_fn) so these tests don't
need Linux — Task 6's own tests already cover the real SO_PEERCRED syscall.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "lib"))

from exec_broker.mgmt_api import UpstreamError  # noqa: E402
from exec_broker.server import BrokerServer, _MAX_REQUEST_BYTES  # noqa: E402

TEST_UID = os.getuid()


class FakeCursor:
    def __init__(self, gate_row=None):
        self.gate_row = gate_row
        self.calls = []
        self.vault_rows = {}
        self._next_id = 1
        self._pending_fetch = None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        upper = sql.strip().upper()
        if upper.startswith("INSERT INTO VAULT_ACCESS_LOG"):
            row_id = self._next_id
            self._next_id += 1
            self.vault_rows[row_id] = {"status": "started"}
            self._pending_fetch = {"id": row_id}
        elif upper.startswith("UPDATE VAULT_ACCESS_LOG"):
            status, error_code, row_id = params
            self.vault_rows[row_id]["status"] = status
            self.vault_rows[row_id]["error_code"] = error_code
            self._pending_fetch = None
        elif upper.startswith("SELECT FROM_AGENT"):
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
    def __init__(self, gate_row=None):
        self.cursor_obj = FakeCursor(gate_row=gate_row)
        self.conn = FakeDbConn(self.cursor_obj)

    def __call__(self):
        return self.conn

    @property
    def vault_rows(self):
        return self.cursor_obj.vault_rows


def _make_server(tmp_path, allowed_uids, db=None, mgmt_api_fn=None, peer_uid_val=TEST_UID):
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
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for _ in range(100):
        if os.path.exists(sock_path):
            break
        time.sleep(0.02)
    else:
        raise RuntimeError("broker socket never appeared")
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


def test_ddl_with_valid_gate_succeeds(tmp_path):
    gate_row = {"from_agent": "orch-console", "message_type": "decision",
                "subject": "go for qa", "body": "", "is_past": True}
    db = FakeDb(gate_row=gate_row)
    _, sock_path, db = _make_server(tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=lambda *a, **k: [])
    resp = _send(sock_path, {
        "project_ref": "oovskwogvefnznfowjky", "guarded_sql": "drop table t", "reason": "",
        "gate_id": "56485", "caller": "c", "exec_ledger_id": "e",
    })
    assert resp == {"status": "ok", "rows": []}
    assert list(db.vault_rows.values())[0]["status"] == "ok"


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
    for _ in range(100):
        if os.path.exists(sock_path):
            break
        time.sleep(0.02)
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
