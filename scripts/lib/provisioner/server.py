"""server.py — ProvisionerServer: the unix-socket accept loop + per-connection
handler tying protocol, peer/acl, ledger, gate, render, and the store apply
together (plan §7, mirroring exec_broker's lifecycle discipline).

Order (see tests/provisioner/test_server.py's docstring for the full ladder):

  read line FIRST (always drain the peer's write before any response or
  close — a fast refusal racing the caller's in-flight sendall surfaces as
  spurious EPIPE on their side) -> peer uid (any mechanism failure is
  fail-closed INTERNAL_ERROR, never "allow") -> ACL (no ledger row yet, no
  bus connection) -> parse -> verb membership -> spec load + render (pure
  local CPU, pre-audit) -> log_started + COMMIT (the audit invariant: NO
  store connection opens before a committed started row) -> spent-check +
  gate require (audited refusals) -> COMMIT BEFORE external work (an
  idle-in-transaction bus connection must not ride along a long store call
  — exec_broker's pooler lesson) -> store connect -> server-major >= 16
  assert (finding 2: pre-PG16 CREATEROLE escalation rules — fail-closed
  UPSTREAM_ERROR, never apply) -> apply the bundle's forward/revert SQL in
  one transaction -> finalize the SAME row -> respond.

Fail-closed: handle_connection never lets an exception escape — any
unanticipated bug responds INTERNAL_ERROR and logs; it never crashes
serve_forever or hangs a peer (bounded read timeout).
"""
from __future__ import annotations

import logging
import os
import socket

from . import peer
from .gate import require_provision_gate
from .ledger import AccessLogEntry, gate_already_used, log_finalized, log_started
from .protocol import ErrorCode, MalformedRequest, encode_error, encode_ok, parse_request
from .registry import Refuse
from .render import render_bundle
from .spec import load_spec, spec_path

logger = logging.getLogger("provisioner")

VERBS = frozenset({"provision", "revert"})

# ceayj runs PG 17.6; CREATEROLE semantics changed at 16 (finding 2, plan §8):
# below 16 a CREATEROLE holder can grant itself superuser-class membership.
# The floor is asserted fail-closed against the LIVE server, never assumed.
_MIN_SERVER_MAJOR = 16

_READ_TIMEOUT_SECONDS = 30.0
_MAX_REQUEST_BYTES = 1_048_576  # 1 MiB


class _PeerUidError(Exception):
    pass


class ProvisionerServer:
    def __init__(
        self,
        sock_path: str,
        allowed_uids: frozenset[int],
        bus_conn_factory,
        store_conn_factory,
        peer_uid_fn=peer.peer_uid,
    ) -> None:
        self.sock_path = sock_path
        self.allowed_uids = allowed_uids
        self.bus_conn_factory = bus_conn_factory
        self.store_conn_factory = store_conn_factory
        self.peer_uid_fn = peer_uid_fn

    def serve_forever(self) -> None:
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(self.sock_path)
        sock.listen(5)
        try:
            while True:
                conn, _ = sock.accept()
                try:
                    self.handle_connection(conn)
                finally:
                    conn.close()
        finally:
            sock.close()

    def handle_connection(self, conn: socket.socket) -> None:
        """One request/response cycle. Always writes exactly one response
        line, never raises."""
        try:
            self._handle_connection_inner(conn)
        except Exception:  # noqa: BLE001 -- fail-closed, last-resort catch-all
            logger.exception("provisioner: unhandled exception in handle_connection")
            self._respond(conn, encode_error(ErrorCode.INTERNAL_ERROR, "internal error"))

    def _handle_connection_inner(self, conn: socket.socket) -> None:
        conn.settimeout(_READ_TIMEOUT_SECONDS)
        try:
            line = conn.makefile("rb").readline(_MAX_REQUEST_BYTES + 1)
        except (socket.timeout, OSError):
            self._respond(conn, encode_error(ErrorCode.INTERNAL_ERROR, "request timeout"))
            return
        if not line:
            self._respond(conn, encode_error(ErrorCode.INTERNAL_ERROR, "empty request"))
            return
        if len(line) > _MAX_REQUEST_BYTES:
            self._respond(conn, encode_error(ErrorCode.INTERNAL_ERROR, "request too large"))
            return

        try:
            uid = self.peer_uid_fn(conn)
        except Exception:  # noqa: BLE001 -- unsupported platform / getsockopt failure
            logger.exception("provisioner: failed to resolve peer uid")
            self._respond(conn, encode_error(ErrorCode.INTERNAL_ERROR, "could not resolve peer uid"))
            return

        if uid not in self.allowed_uids:
            logger.warning("provisioner: ACL denied for uid %s", uid)
            self._respond(
                conn,
                encode_error(ErrorCode.ACL_DENIED, f"uid {uid} is not on the provisioner's allowlist"),
            )
            return

        try:
            req = parse_request(line.decode("utf-8"))
        except (MalformedRequest, UnicodeDecodeError) as e:
            self._respond(conn, encode_error(ErrorCode.INTERNAL_ERROR, f"malformed request: {e}"))
            return

        if req.verb not in VERBS:
            self._refuse_after_audit(conn, req, ErrorCode.BAD_VERB,
                                     f"verb {req.verb!r} is not one of {sorted(VERBS)}")
            return

        try:
            spec = load_spec(spec_path(req.client_key))
            bundle = render_bundle(spec)
        except Exception as e:  # noqa: BLE001 -- Refuse and friends
            code = ErrorCode.SPEC_INVALID if isinstance(e, Refuse) else ErrorCode.INTERNAL_ERROR
            self._refuse_after_audit(conn, req, code,
                                     f"client spec {req.client_key!r} refused: {e}")
            return

        with self.bus_conn_factory() as bus:
            with bus.cursor() as cur:
                row_id = log_started(cur, AccessLogEntry(
                    verb=req.verb, client_key=spec.client_key, silo_ref=bundle.silo_ref,
                    bundle_sha256=bundle.sha256, gate_id=req.gate_id, peer_uid=str(uid),
                    reason=req.reason))
                bus.commit()

                if gate_already_used(cur, req.gate_id, req.verb, exclude_row_id=row_id):
                    self._finalize_and_respond(conn, bus, cur, row_id, ErrorCode.GATE_INVALID,
                                               f"gate {req.gate_id} is already spent on a "
                                               f"'{req.verb}' — one artifact per gate")
                    return
                try:
                    require_provision_gate(req.gate_id, spec=spec, bundle=bundle, verb=req.verb, cur=cur)
                except Exception as e:  # noqa: BLE001 -- Refuse and transient DB alike
                    self._finalize_and_respond(conn, bus, cur, row_id, ErrorCode.GATE_INVALID, str(e))
                    return

                # commit BEFORE the blocking store call — see module docstring
                bus.commit()

                try:
                    with self.store_conn_factory() as store:
                        with store.cursor() as scur:
                            scur.execute("SELECT current_setting('server_version_num')::int")
                            major = scur.fetchone()[0] // 10000
                            if major < _MIN_SERVER_MAJOR:
                                self._finalize_and_respond(
                                    conn, bus, cur, row_id, ErrorCode.UPSTREAM_ERROR,
                                    f"store server major {major} < {_MIN_SERVER_MAJOR} floor — "
                                    "pre-PG16 CREATEROLE escalation rules, refusing fail-closed")
                                return
                            scur.execute(bundle.forward_sql if req.verb == "provision"
                                         else bundle.revert_sql)
                        store.commit()
                except Exception as e:  # noqa: BLE001 -- any apply/upstream failure
                    logger.exception("provisioner: store apply failed")
                    self._finalize_and_respond(conn, bus, cur, row_id,
                                               ErrorCode.UPSTREAM_ERROR, str(e))
                    return

                log_finalized(cur, row_id, "ok")
                bus.commit()
                self._respond(conn, encode_ok(client_key=spec.client_key, verb=req.verb,
                                              bundle_sha256=bundle.sha256))

    def _refuse_after_audit(self, conn, req, code: ErrorCode, message: str) -> None:
        """Audit a refusal that happens BEFORE the bus row can carry artifact
        details (bad verb, unusable spec): still a started->error row — even a
        refused call is audited, never a silent no-op. The store is never
        touched on this path."""
        try:
            with self.bus_conn_factory() as bus:
                with bus.cursor() as cur:
                    row_id = log_started(cur, AccessLogEntry(
                        verb=req.verb, client_key=req.client_key, silo_ref="",
                        bundle_sha256="", gate_id=req.gate_id, peer_uid="", reason=req.reason))
                    log_finalized(cur, row_id, "error", code.value)
                    bus.commit()
        except Exception:  # noqa: BLE001 -- best-effort audit; still respond
            logger.exception("provisioner: failed to audit pre-render refusal")
        self._respond(conn, encode_error(code, message))

    def _finalize_and_respond(self, conn, bus, cur, row_id, code: ErrorCode, message: str) -> None:
        log_finalized(cur, row_id, "error", code.value)
        bus.commit()
        self._respond(conn, encode_error(code, message))

    @staticmethod
    def _respond(conn: socket.socket, payload: bytes) -> None:
        """Best-effort: if the peer already dropped, nothing more this call
        can do — but it must not raise (the never-raises contract)."""
        try:
            conn.sendall(payload)
        except OSError:
            logger.warning("provisioner: failed to send response (peer likely disconnected)")
