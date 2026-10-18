#!/usr/bin/env python3
"""server.py — BrokerServer: the unix-socket accept loop + per-connection
handler that ties protocol, acl, gate_validation, vault_log, and mgmt_api
together (contract, full request lifecycle).

Order: read one line first (always drain the peer's write before any response
or close — see _handle_connection_inner's comment) -> ACL check (no vault row
yet) -> parse -> log_started (BEFORE any re-validation or mgmt-API call, so
even a refused call is audited, never a silent no-op, per §4 point 3) ->
re-validate (project_ref, then gate) -> on pass, call the mgmt API -> finalize
the SAME row to ok/error either way -> respond.

Fail-closed: handle_connection never lets an exception escape to the accept
loop -- any unanticipated bug responds INTERNAL_ERROR and logs, it never
crashes serve_forever or hangs a peer indefinitely (30s read timeout).
"""
from __future__ import annotations

import logging
import os
import socket

from . import acl
from .gate_validation import BrokerValidationError, check_project_ref, require_gate
from .mgmt_api import UpstreamError
from .protocol import ErrorCode, MalformedRequest, encode_error, encode_ok, parse_request
from .vault_log import VaultLogEntry, log_finalized, log_started

logger = logging.getLogger("exec_broker")

_READ_TIMEOUT_SECONDS = 30.0


class BrokerServer:
    def __init__(
        self,
        sock_path: str,
        allowed_uids: frozenset[int],
        db_conn_factory,
        token: str,
        mgmt_api_fn,
        peer_uid_fn=acl.peer_uid,
    ) -> None:
        self.sock_path = sock_path
        self.allowed_uids = allowed_uids
        self.db_conn_factory = db_conn_factory
        self.token = token
        self.mgmt_api_fn = mgmt_api_fn
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
            logger.exception("exec_broker: unhandled exception in handle_connection")
            self._respond(conn, encode_error(ErrorCode.INTERNAL_ERROR, "internal error"))

    def _handle_connection_inner(self, conn: socket.socket) -> None:
        # Read the request line FIRST, before any check or response — closing
        # the connection before draining the peer's write can race a fast-path
        # refusal (e.g. ACL denial) against the caller's still-in-flight
        # sendall() and surface as a spurious EPIPE/ECONNRESET on their side.
        # SO_PEERCRED reflects the credentials captured at connect() time, so
        # reading first costs nothing security-wise — the ACL check below still
        # gates everything the request's content could otherwise cause.
        conn.settimeout(_READ_TIMEOUT_SECONDS)
        try:
            line = conn.makefile("rb").readline()
        except (socket.timeout, OSError):
            self._respond(conn, encode_error(ErrorCode.INTERNAL_ERROR, "request timeout"))
            return
        if not line:
            self._respond(conn, encode_error(ErrorCode.INTERNAL_ERROR, "empty request"))
            return

        try:
            uid = self.peer_uid_fn(conn)
        except Exception:  # noqa: BLE001 -- unsupported platform / getsockopt failure
            logger.exception("exec_broker: failed to resolve peer uid")
            self._respond(conn, encode_error(ErrorCode.INTERNAL_ERROR, "could not resolve peer uid"))
            return

        try:
            acl.check_acl(uid, self.allowed_uids)
        except acl.AclDenied as e:
            logger.warning("exec_broker: ACL denied for uid %s: %s", uid, e)
            self._respond(conn, encode_error(ErrorCode.ACL_DENIED, str(e)))
            return

        try:
            req = parse_request(line.decode("utf-8"))
        except (MalformedRequest, UnicodeDecodeError) as e:
            self._respond(conn, encode_error(ErrorCode.INTERNAL_ERROR, f"malformed request: {e}"))
            return

        with self.db_conn_factory() as db_conn:
            with db_conn.cursor() as cur:
                entry = VaultLogEntry(
                    exec_ledger_id=req.exec_ledger_id,
                    caller=req.caller,
                    project_ref=req.project_ref,
                    guarded_sql=req.guarded_sql,
                    reason=req.reason,
                    gate_id=req.gate_id,
                )
                row_id = log_started(cur, entry)
                db_conn.commit()

                try:
                    check_project_ref(req.project_ref)
                    require_gate(req.guarded_sql, req.gate_id, req.project_ref, cur)
                except BrokerValidationError as e:
                    log_finalized(cur, row_id, "error", e.code.value)
                    db_conn.commit()
                    self._respond(conn, encode_error(e.code, e.message))
                    return

                try:
                    rows = self.mgmt_api_fn(req.guarded_sql, req.project_ref, self.token)
                except UpstreamError as e:
                    log_finalized(cur, row_id, "error", ErrorCode.UPSTREAM_ERROR.value)
                    db_conn.commit()
                    self._respond(conn, encode_error(ErrorCode.UPSTREAM_ERROR, str(e)))
                    return
                except Exception:  # noqa: BLE001 -- truly unexpected bug in the mgmt call
                    logger.exception("exec_broker: unexpected error calling mgmt API")
                    try:
                        log_finalized(cur, row_id, "error", ErrorCode.INTERNAL_ERROR.value)
                        db_conn.commit()
                    except Exception:  # noqa: BLE001 -- best-effort; still respond
                        logger.exception("exec_broker: failed to finalize vault_access_log row")
                    self._respond(conn, encode_error(ErrorCode.INTERNAL_ERROR, "internal error"))
                    return

                log_finalized(cur, row_id, "ok", None)
                db_conn.commit()
                self._respond(conn, encode_ok(rows))

    @staticmethod
    def _respond(conn: socket.socket, payload: bytes) -> None:
        conn.sendall(payload)
