"""ProvisionerServer — the accept-loop handler tying protocol, peer/acl,
ledger, gate, render, and the store apply together (plan §7).

Ordering discipline (mirrors exec_broker, adapted): read line FIRST -> peer
uid (fail-closed) -> ACL (no ledger row yet) -> parse -> verb/spec/render
(pure, local, pre-audit) -> log_started + COMMIT (audit begins; the
invariant is NO store connection opens before a committed started row) ->
spent-check + gate require (audited refusals) -> commit before external
work -> store connect -> server-major >=16 assert (finding 2, fail-closed)
-> apply in one txn -> finalize same row -> respond. handle_connection
never raises: any unanticipated bug responds INTERNAL_ERROR.
"""
import json
import socket
import sys
import threading
from pathlib import Path

import pytest

ORCH_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ORCH_DIR / "scripts" / "lib"))

from provisioner import server as server_mod  # noqa: E402
from provisioner.registry import store_ref  # noqa: E402
from provisioner.render import render_bundle  # noqa: E402
from provisioner.spec import load_spec, spec_path  # noqa: E402

_REF = store_ref("ihsanos multi-tenant DB")


class FakeCursor:
    def __init__(self, results=None, exc=None):
        self.results = list(results or [])
        self.exc = exc
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executed.append(sql)
        if self.exc:
            raise self.exc

    def fetchone(self):
        return self.results.pop(0) if self.results else None


class FakeBusConn:
    def __init__(self, results):
        self.cur = FakeCursor(results)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeStoreConn:
    def __init__(self, major=170000, apply_exc=None):
        # store conn uses default tuple rows (only the BUS conn is dict_row)
        self.cur = FakeCursor([(major,)], exc=apply_exc)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *a):
        # mirror psycopg3 Connection.__exit__: rollback on exception
        if exc_type is not None:
            self.rollbacks += 1
        return False


def _spec():
    return load_spec(spec_path("bariq"))


def _go_row(bundle, **overrides):
    row = dict(
        from_agent="orch-console",
        message_type="decision",
        subject="GO: provision bariq tenant",
        body=f"approved bundle bariq {_REF} sha256 {bundle.sha256}",
        is_past=True,
    )
    row.update(overrides)
    return row


def _request(verb="provision", client_key="bariq", gate_id="78463"):
    return json.dumps(dict(verb=verb, client_key=client_key, gate_id=gate_id,
                           reason="client onboarding", caller="orch-console"))


def _run(server, request_line):
    """Drive one handle_connection cycle over a real socketpair."""
    a, b = socket.socketpair()
    try:
        result = {}

        def target():
            server.handle_connection(a)

        t = threading.Thread(target=target)
        t.start()
        b.sendall(request_line.encode() + b"\n")
        response = b.makefile("rb").readline()
        t.join(timeout=10)
        result["response"] = json.loads(response)
        return result["response"]
    finally:
        a.close()
        b.close()


def _server(bus_results, store=None, allowed=frozenset({501}), peer_uid=501):
    bus = FakeBusConn(bus_results)
    stores = [store or FakeStoreConn()]
    srv = server_mod.ProvisionerServer(
        sock_path="/tmp/unused-prov.sock",
        allowed_uids=allowed,
        bus_conn_factory=lambda: bus,
        store_conn_factory=lambda: stores[0],
        peer_uid_fn=lambda conn: peer_uid,
    )
    return srv, bus, stores[0]


def test_happy_path_provision_applies_the_bundle_and_audits():
    bundle = render_bundle(_spec())
    bus_results = [{"id": 7}, None, _go_row(bundle)]  # RETURNING, spent, gate
    srv, bus, store = _server(bus_results)
    resp = _run(srv, _request())
    assert resp["status"] == "ok"
    assert resp["client_key"] == "bariq"
    assert resp["bundle_sha256"] == bundle.sha256
    # audit: started row committed BEFORE any store connection opened, and
    # the same row finalized to ok
    assert any(s.startswith("INSERT INTO provisioner_access_log") for s in bus.cur.executed)
    assert any(s.startswith("UPDATE provisioner_access_log") for s in bus.cur.executed)
    assert bus.commits >= 2
    # store: major assert ran, forward SQL applied, committed
    assert store.cur.executed[0].startswith("SELECT current_setting('server_version_num')")
    assert any("CREATE TABLE" in s for s in store.cur.executed)
    assert store.commits == 1 and store.rollbacks == 0


def test_happy_path_revert_applies_the_revert_twin():
    bundle = render_bundle(_spec())
    bus_results = [{"id": 8}, None, _go_row(bundle)]
    srv, bus, store = _server(bus_results)
    resp = _run(srv, _request(verb="revert"))
    assert resp["status"] == "ok" and resp["verb"] == "revert"
    assert any("DROP TABLE" in s for s in store.cur.executed)
    assert not any("CREATE TABLE" in s for s in store.cur.executed)


def test_acl_denial_never_reaches_the_bus_or_store():
    srv, bus, store = _server([{"id": 9}], peer_uid=4242)
    resp = _run(srv, _request())
    assert resp == {"status": "error",
                    "error": {"code": "ACL_DENIED", "message": "uid 4242 is not on the provisioner's allowlist"}}
    assert bus.cur.executed == [] and store.cur.executed == []


def test_bad_verb_is_audited_and_never_touches_the_store():
    srv, bus, store = _server([{"id": 10}])
    resp = _run(srv, _request(verb="drop_everything"))
    assert resp["error"]["code"] == "BAD_VERB"
    assert any(s.startswith("UPDATE provisioner_access_log") for s in bus.cur.executed)
    assert store.cur.executed == []


def test_unknown_client_is_spec_invalid():
    srv, bus, store = _server([{"id": 11}])
    resp = _run(srv, _request(client_key="nonexistent"))
    assert resp["error"]["code"] == "SPEC_INVALID"
    assert store.cur.executed == []


def test_spent_gate_refuses_before_any_store_connection():
    bundle = render_bundle(_spec())
    bus_results = [{"id": 12}, {"1": 1}, _go_row(bundle)]  # spent row exists
    srv, bus, store = _server(bus_results)
    resp = _run(srv, _request())
    assert resp["error"]["code"] == "GATE_INVALID"
    assert "spent" in resp["error"]["message"]
    assert store.cur.executed == []  # one artifact per gate, enforced pre-store


def test_gate_refusal_is_audited():
    bundle = render_bundle(_spec())
    bus_results = [{"id": 13}, None, _go_row(bundle, from_agent="cc-bariq")]
    srv, bus, store = _server(bus_results)
    resp = _run(srv, _request())
    assert resp["error"]["code"] == "GATE_INVALID"
    assert any(s.startswith("UPDATE provisioner_access_log") for s in bus.cur.executed)
    assert store.cur.executed == []


def test_pre16_store_major_fails_closed_finding2():
    bundle = render_bundle(_spec())
    bus_results = [{"id": 14}, None, _go_row(bundle)]
    srv, bus, store = _server(bus_results, store=FakeStoreConn(major=150000))
    resp = _run(srv, _request())
    assert resp["error"]["code"] == "UPSTREAM_ERROR"
    assert "16" in resp["error"]["message"]  # names the CREATEROLE floor
    assert not any("CREATE TABLE" in s for s in store.cur.executed)  # no DDL ran


def test_apply_failure_rolls_back_and_reports_upstream():
    bundle = render_bundle(_spec())
    bus_results = [{"id": 15}, None, _go_row(bundle)]
    srv, bus, store = _server(
        bus_results, store=FakeStoreConn(apply_exc=RuntimeError("boom at postgresql://x@y")))
    resp = _run(srv, _request())
    assert resp["error"]["code"] == "UPSTREAM_ERROR"
    assert "postgresql://" not in json.dumps(resp)  # leak discipline holds
    assert store.rollbacks == 1 and store.commits == 0


def test_peer_uid_failure_fails_closed():
    srv, bus, store = _server([{"id": 16}], peer_uid=None)
    # peer_uid None means "peer_uid_fn raises" — build that server by hand
    srv.peer_uid_fn = lambda conn: (_ for _ in ()).throw(OSError("no cred mechanism"))
    resp = _run(srv, _request())
    assert resp["error"]["code"] == "INTERNAL_ERROR"
    assert bus.cur.executed == [] and store.cur.executed == []


def test_malformed_request_maps_to_internal_error():
    srv, bus, store = _server([{"id": 17}])
    resp = _run(srv, "this is not json")
    assert resp["error"]["code"] == "INTERNAL_ERROR"


def test_no_store_connection_before_a_committed_started_row():
    # the audit invariant: store_conn_factory must not even be CONSTRUCTED
    # until log_started's commit landed — proven by ordering of factory calls
    calls = []

    def bus_factory():
        calls.append("bus")
        return FakeBusConn([{"id": 18}, None, _go_row(render_bundle(_spec()))])

    def store_factory():
        calls.append("store")
        return FakeStoreConn()

    srv = server_mod.ProvisionerServer(
        sock_path="/tmp/unused-prov.sock", allowed_uids=frozenset({501}),
        bus_conn_factory=bus_factory, store_conn_factory=store_factory,
        peer_uid_fn=lambda conn: 501)
    resp = _run(srv, _request())
    assert resp["status"] == "ok"
    assert calls == ["bus", "store"]
