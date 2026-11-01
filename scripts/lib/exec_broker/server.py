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

import hashlib
import logging
import os
import socket
from datetime import datetime, timezone

from . import acl
from .gate_validation import (
    REASON_GATE_ALREADY_CONSUMED,
    BrokerValidationError,
    check_project_ref,
    require_gate,
)
from .mgmt_api import UpstreamError
from .protocol import ErrorCode, MalformedRequest, encode_error, encode_ok, parse_request
from .read_only import READ_ONLY_PROLOGUE, check_read_only_override, is_plain_read
from .vault_log import (
    UNIQUE_VIOLATION_SQLSTATE,
    VaultLogEntry,
    consume_gate,
    log_finalized,
    log_started,
    record_executed,
)

logger = logging.getLogger("exec_broker")

_READ_TIMEOUT_SECONDS = 30.0
# Generous for any real guarded_sql + metadata the contract describes, but
# bounded -- an unbounded readline() lets one peer (even an ACL-allowed one,
# or a bug on the caller's side) hold an open-ended amount of memory per
# connection.
_MAX_REQUEST_BYTES = 1_048_576  # 1 MiB


class BrokerServer:
    def __init__(
        self,
        sock_path: str,
        allowed_uids: frozenset[int],
        db_conn_factory,
        token: str,
        mgmt_api_fn,
        peer_uid_fn=acl.peer_uid,
        *,
        uid_override: "frozenset[int] | None" = None,
        uid_override_expires_at: "datetime | None" = None,
        ref_override: "str | None" = None,
        ref_override_expires_at: "datetime | None" = None,
        live_gated_refs: "frozenset[str] | None" = None,
        gate_max_age_seconds: "int | None" = None,
        gate_not_before: "datetime | None" = None,
    ) -> None:
        self.sock_path = sock_path
        self.allowed_uids = allowed_uids
        self.db_conn_factory = db_conn_factory
        self.token = token
        self.mgmt_api_fn = mgmt_api_fn
        self.peer_uid_fn = peer_uid_fn
        # BOUNDED OVERRIDE (op#34322/#76985/#76989, 2026-10-26) -- see acl.py /
        # gate_validation.py docstrings for the wall-clock-reverts-by-
        # construction design. All four default to inert (None).
        self.uid_override = uid_override
        self.uid_override_expires_at = uid_override_expires_at
        self.ref_override = ref_override
        self.ref_override_expires_at = ref_override_expires_at
        # B1 (bus #89814): the standing LIVE_GATED_REFS call-allowlist, loaded
        # once at daemon boot. None -> gate_validation.DEFAULT_LIVE_GATED_REFS
        # (QA only), identical to the pre-B1 hard-coded allowlist.
        self.live_gated_refs = live_gated_refs
        # B3 (bus #89814 §4.1): None -> gate_validation.DEFAULT_GATE_MAX_AGE_SECONDS (24h).
        self.gate_max_age_seconds = gate_max_age_seconds
        # B3 cutover floor (console #89841 ruling e): None -> gates refused on
        # every non-QA ref (fail closed), see require_gate.
        self.gate_not_before = gate_not_before

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
            line = conn.makefile("rb").readline(_MAX_REQUEST_BYTES + 1)
        except (socket.timeout, OSError):
            self._respond(conn, encode_error(ErrorCode.INTERNAL_ERROR, "request timeout"))
            return
        if len(line) > _MAX_REQUEST_BYTES:
            # Size check FIRST (cc-fleet-health review): an oversized line --
            # even an all-whitespace one -- is reported as too large, never as
            # the blank-line probe answer below.
            self._respond(conn, encode_error(ErrorCode.INTERNAL_ERROR, "request too large"))
            return
        if not line.strip():
            # EOF or a BLANK line (b"\n" is the heartbeat's own liveness probe,
            # cc-fleet-health bus #89888): answered BEFORE the ACL check, with
            # no DB connection, no exec_broker_access_log row and no log line.
            # Not a bypass: a blank line carries no request, so nothing past
            # this point (ACL, parse, audit, gate, mgmt API) is reachable by
            # it -- it only proves accept()+respond ran. Answering it before
            # the ACL just avoids a per-tick ACL_DENIED WARNING for the
            # daemon's own uid without widening the ACL. Any non-blank line
            # still hits the ACL first, exactly as before.
            self._respond(conn, encode_error(ErrorCode.INTERNAL_ERROR, "empty request"))
            return

        try:
            uid = self.peer_uid_fn(conn)
        except Exception:  # noqa: BLE001 -- unsupported platform / getsockopt failure
            logger.exception("exec_broker: failed to resolve peer uid")
            self._respond(conn, encode_error(ErrorCode.INTERNAL_ERROR, "could not resolve peer uid"))
            return

        now = datetime.now(timezone.utc)
        try:
            acl.check_acl(
                uid,
                self.allowed_uids,
                override_uids=self.uid_override,
                override_expires_at=self.uid_override_expires_at,
                now=now,
            )
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
                    gate_ref=req.ref,
                )
                row_id = log_started(cur, entry)
                db_conn.commit()

                try:
                    # Order: ref allowlist first, then the RO mode, then the
                    # gate — so an armed-but-expired override still yields
                    # BAD_PROJECT_REF for its ref, and RO refusals are only
                    # reachable for refs that passed the allowlist (pinned by
                    # test_ref_override_readonly_allowlist_runs_before_ro_pin,
                    # bus #79103). RO itself is SCOPED to the override's ref
                    # (bus #79039): baseline refs keep today's behavior while
                    # the override is armed.
                    check_project_ref(
                        req.project_ref,
                        override_ref=self.ref_override,
                        override_expires_at=self.ref_override_expires_at,
                        now=now,
                        allowed_refs=self.live_gated_refs,
                    )
                    check_read_only_override(
                        req.guarded_sql,
                        project_ref=req.project_ref,
                        ref_override=self.ref_override,
                        ref_override_expires_at=self.ref_override_expires_at,
                        now=now,
                    )
                    gate_used = require_gate(
                        req.guarded_sql, req.gate_id, req.project_ref, cur,
                        migration=req.migration, pr=req.pr, ref=req.ref,
                        gate_max_age_seconds=self.gate_max_age_seconds,
                        gate_not_before=self.gate_not_before,
                    )
                    if gate_used:
                        executed_sql = req.guarded_sql
                    else:
                        # Amended ruling C (console bus #89846/#89875): a
                        # gate-free statement is a plain read (require_gate
                        # returns False ONLY for is_plain_read -- re-checked
                        # here, fail closed). The broker itself enforces
                        # read-only by prepending the prologue into the SAME
                        # query string the mgmt API runs as one implicit
                        # transaction. (A SET on db_conn would enforce
                        # nothing: the statement never runs there.)
                        if not is_plain_read(req.guarded_sql):
                            raise RuntimeError("gate-free statement is not a plain read")
                        executed_sql = READ_ONLY_PROLOGUE + req.guarded_sql
                        record_executed(
                            cur, row_id, READ_ONLY_PROLOGUE,
                            hashlib.sha256(executed_sql.encode("utf-8")).hexdigest(),
                        )
                except BrokerValidationError as e:
                    log_finalized(cur, row_id, "error", e.code.value, e.reason)
                    db_conn.commit()
                    self._respond(conn, encode_error(e.code, e.message, e.reason))
                    return
                except Exception:  # noqa: BLE001 -- e.g. a transient DB error inside the gate lookup
                    logger.exception("exec_broker: unexpected error during re-validation")
                    try:
                        log_finalized(cur, row_id, "error", ErrorCode.INTERNAL_ERROR.value)
                        db_conn.commit()
                    except Exception:  # noqa: BLE001 -- best-effort; still respond
                        logger.exception("exec_broker: failed to finalize exec_broker_access_log row")
                    self._respond(conn, encode_error(ErrorCode.INTERNAL_ERROR, "internal error"))
                    return

                if gate_used:
                    # B3 single-use (bus #89814 §4.2-4.3): bind gate_id to THIS
                    # row's exec_ledger_id via the DB's partial UNIQUE index,
                    # inside the same transaction, committed by the commit
                    # below BEFORE the mgmt-API call -- so a replay or a
                    # concurrent duplicate fails closed instead of racing.
                    # A consumed gate stays consumed even if the mgmt-API call
                    # then fails (an upstream timeout may still have executed):
                    # the caller needs a fresh gate, never a silent re-run.
                    try:
                        consume_gate(cur, row_id)
                    except Exception as e:  # noqa: BLE001 -- classify by SQLSTATE, fail closed either way
                        replay = getattr(e, "sqlstate", None) == UNIQUE_VIOLATION_SQLSTATE
                        code = ErrorCode.GATE_INVALID if replay else ErrorCode.INTERNAL_ERROR
                        if not replay:
                            logger.exception("exec_broker: failed to consume gate %s", req.gate_id)
                        reason = REASON_GATE_ALREADY_CONSUMED if replay else None
                        try:
                            db_conn.rollback()
                            log_finalized(cur, row_id, "error", code.value, reason)
                            db_conn.commit()
                        except Exception:  # noqa: BLE001 -- best-effort; still respond
                            logger.exception("exec_broker: failed to finalize exec_broker_access_log row")
                        message = (
                            f"gate {req.gate_id} was already consumed by another execution "
                            "(single-use, bus #89814) -- get a fresh gate"
                            if replay else "internal error"
                        )
                        self._respond(conn, encode_error(code, message, reason))
                        return

                # Commit BEFORE the blocking mgmt-API call (up to 28s) -- require_gate's
                # own SELECT (DDL path) would otherwise leave this connection
                # idle-in-transaction for the whole network call. If a pooler
                # killed that idle transaction AFTER a real DDL/DML already
                # executed upstream but BEFORE this process finalized+committed,
                # the caller would see INTERNAL_ERROR for a call that actually
                # succeeded -- a retry on that error risks double-executing
                # already-guarded SQL. Closing the transaction here removes
                # that window; the next statement (log_finalized) opens a new,
                # short one.
                #
                # B3 burn-first (console #89841 ruling d): when a gate was used,
                # THIS commit is what makes the burn durable, and it happens
                # strictly BEFORE execution. If it fails, nothing executes.
                try:
                    db_conn.commit()
                except Exception:  # noqa: BLE001 -- fail closed: no durable burn, no execution
                    logger.exception("exec_broker: pre-execution commit failed; refusing to execute")
                    try:
                        db_conn.rollback()
                        log_finalized(cur, row_id, "error", ErrorCode.INTERNAL_ERROR.value)
                        db_conn.commit()
                    except Exception:  # noqa: BLE001 -- best-effort; still respond
                        logger.exception("exec_broker: failed to finalize exec_broker_access_log row")
                    self._respond(conn, encode_error(ErrorCode.INTERNAL_ERROR, "internal error"))
                    return

                try:
                    rows = self.mgmt_api_fn(executed_sql, req.project_ref, self.token)
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
                        logger.exception("exec_broker: failed to finalize exec_broker_access_log row")
                    self._respond(conn, encode_error(ErrorCode.INTERNAL_ERROR, "internal error"))
                    return

                log_finalized(cur, row_id, "ok", None)
                db_conn.commit()
                self._respond(conn, encode_ok(rows))

    @staticmethod
    def _respond(conn: socket.socket, payload: bytes) -> None:
        """Best-effort: if the peer already dropped the connection, there is
        nothing more this call can do but it must not raise -- the "never
        raises" contract on handle_connection would otherwise be violated by
        exactly the path (an error response) that depends on it most."""
        try:
            conn.sendall(payload)
        except OSError:
            logger.warning("exec_broker: failed to send response (peer likely disconnected)")
