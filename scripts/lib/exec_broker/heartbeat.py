#!/usr/bin/env python3
"""heartbeat.py — hc#95 exec-broker dead-man HEARTBEAT writer (cutover spec
bus #89814 §5.2; console #89827/#89841 assignment).

WHY: absence of a broker must never read as "no executions happened". The
broker daemon writes ONE heartbeat row per GATED ref (BrokerServer's standing
LIVE_GATED_REFS set) every `interval_seconds`; cc-fleet-health's watchdog
(hc#1001) pages when a gated ref's row is STALE or MISSING.

SHAPE REUSED, NOT REINVENTED: this mirrors scripts/lib/fleet_health_lease.py
(migrations/023): a keyed row, a CAS acquire at boot (`take`: succeeds only
when the row is mine OR expired, never steals a fresh foreign holder), a cheap
renew (`renew`: stamps the DB clock only if THIS boot still holds the row),
and a TTL after which the row is expired/reclaim-eligible. The same pure
decision core (is_stale / take semantics) is mirrored by the atomic SQL below.

TABLE (migrations/144_hc95_exec_broker_heartbeat.sql), one row per gated ref:
  project_ref      text PK   the gated ref this broker serves
  holder_host      text      fleet_host_id() of the broker body
  broker_pid       integer   daemon pid
  boot_id          uuid      random per daemon boot (restart / dual-broker detector)
  started_at       timestamptz  when this boot took the row
  beat_at          timestamptz  DB now() of the last beat (the dead-man stamp)
  beat_seq         bigint    beats since started_at (0 at take)
  interval_seconds integer   promised cadence
  ttl_seconds      integer   stale threshold (default 3 x interval)
  taken_over_from  text      previous holder's boot_id, stamped whenever a take
                             changes boot_id (same host OR a different host)
  takeover_reason  text      'same_host_new_boot' | 'host_change'
  taken_over_from_host text  previous holder's holder_host (same arms as taken_over_from)
  loop_ok          boolean   last self-probe of the daemon's OWN listener got a
                             well-formed response line (NULL until the first tick)
  loop_checked_at  timestamptz  DB now() when that probe result was written
  loop_latency_ms  integer   probe round-trip
  loop_error       text      short reason when loop_ok is false (NULL on ok)
STALE := row missing  OR  now() > beat_at + make_interval(secs => ttl_seconds).
NOT SERVING := fresh row AND loop_ok IS NOT TRUE  (process alive, accept loop not
responding) -- distinct from STALE (process dead / cannot reach the DB).
Every timestamp is the DATABASE clock, never the broker host's.

FAILURE POLICY: boot refuses (fail closed) if any gated ref cannot be taken --
a DB error or a FRESH foreign holder (two brokers claiming one gated ref is a
misconfiguration, never resolved by stealing). After boot, a failed beat is
logged and retried next tick and never touches the request path; a beat that
finds the row taken over (renew misses) does NOT steal it back unless the CAS
take would succeed (same host or expired). A dead or wedged daemon simply stops
beating, which is exactly what the watchdog detects.

LISTENER SELF-PROBE (cc-fleet-health bus #89888): each tick, BEFORE writing
the beat, the thread connects to the daemon's own unix socket, sends one b"\n",
and reads ONE response line (2s timeout). loop_ok is true only if a well-formed
broker error response came back -- proof that accept() AND respond ran. A bare
connect is NOT the signal: the kernel completes the handshake into the listen
backlog even when accept() is wedged. The blank line is answered by server.py
before the ACL with no DB work and no audit row. A failed probe never stops the
beat: beat_at is still written (process alive) with loop_ok=false, so the
watchdog can tell "dead process" (stale) from "alive but not serving".
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
from datetime import datetime, timedelta

logger = logging.getLogger("exec_broker.heartbeat")

DEFAULT_INTERVAL_SECONDS = 60
MIN_INTERVAL_SECONDS = 10
MAX_INTERVAL_SECONDS = 900
TTL_MULTIPLIER = 3


def ttl_for(interval_seconds: int) -> int:
    return interval_seconds * TTL_MULTIPLIER


# ── Pure decision core (no DB, no clock) -- the SQL below mirrors it ─────────

def is_stale(row: "dict | None", now: datetime) -> bool:
    """The watchdog's rule. A MISSING row is stale (absence is never health)."""
    if row is None:
        return True
    return now > row["beat_at"] + timedelta(seconds=row["ttl_seconds"])


def takeover_stamp(row: "dict | None", host: str, boot_id: str) -> "tuple[str | None, str | None, str | None]":
    """(taken_over_from, takeover_reason, taken_over_from_host) a successful take writes -- mirrors
    _TAKE_SQL (cc-fleet-health review): stamped whenever the boot_id changes,
    so a second daemon on the SAME host re-taking the row is visible
    ('same_host_new_boot'), not only a cross-host take ('host_change').
    taken_over_from is the previous holder's boot_id and taken_over_from_host
    its holder_host, both stamped in the SAME arms (any boot_id change)."""
    if row is None:
        return None, None, None
    if row.get("boot_id") == boot_id:
        return row.get("taken_over_from"), row.get("takeover_reason"), row.get("taken_over_from_host")
    reason = "same_host_new_boot" if row.get("holder_host") == host else "host_change"
    return str(row.get("boot_id")), reason, row.get("holder_host")


def may_take(row: "dict | None", host: str, now: datetime) -> bool:
    """CAS-acquire rule (fleet_health_lease.apply_take): no row, or held by this
    host (a restart of the same body), or expired."""
    return row is None or row.get("holder_host") == host or is_stale(row, now)


# ── DB layer (atomic SQL) ─────────────────────────────────────────────────────

_TAKE_SQL = (
    "INSERT INTO exec_broker_heartbeat "
    "(project_ref, holder_host, broker_pid, boot_id, started_at, beat_at, beat_seq, "
    " interval_seconds, ttl_seconds) "
    "VALUES (%(ref)s, %(host)s, %(pid)s, %(boot)s, now(), now(), 0, %(interval)s, %(ttl)s) "
    "ON CONFLICT (project_ref) DO UPDATE SET "
    "  holder_host = EXCLUDED.holder_host, broker_pid = EXCLUDED.broker_pid, "
    "  boot_id = EXCLUDED.boot_id, started_at = now(), beat_at = now(), beat_seq = 0, "
    "  interval_seconds = EXCLUDED.interval_seconds, ttl_seconds = EXCLUDED.ttl_seconds, "
    "  taken_over_from = CASE WHEN exec_broker_heartbeat.boot_id <> EXCLUDED.boot_id "
    "      THEN exec_broker_heartbeat.boot_id::text "
    "      ELSE exec_broker_heartbeat.taken_over_from END, "
    "  taken_over_from_host = CASE WHEN exec_broker_heartbeat.boot_id <> EXCLUDED.boot_id "
    "      THEN exec_broker_heartbeat.holder_host "
    "      ELSE exec_broker_heartbeat.taken_over_from_host END, "
    "  takeover_reason = CASE "
    "      WHEN exec_broker_heartbeat.boot_id = EXCLUDED.boot_id THEN exec_broker_heartbeat.takeover_reason "
    "      WHEN exec_broker_heartbeat.holder_host = EXCLUDED.holder_host THEN 'same_host_new_boot' "
    "      ELSE 'host_change' END "
    "WHERE exec_broker_heartbeat.holder_host = EXCLUDED.holder_host "
    "   OR exec_broker_heartbeat.beat_at + make_interval(secs => exec_broker_heartbeat.ttl_seconds) < now() "
    "RETURNING boot_id"
)

_RENEW_SQL = (
    "UPDATE exec_broker_heartbeat SET beat_at = now(), beat_seq = beat_seq + 1, "
    "  loop_ok = %(loop_ok)s, loop_checked_at = now(), loop_latency_ms = %(latency)s, "
    "  loop_error = %(error)s "
    "WHERE project_ref = %(ref)s AND boot_id = %(boot)s RETURNING beat_seq"
)

PROBE_TIMEOUT_SECONDS = 2.0
_LOOP_ERROR_MAX = 120


def probe_listener(sock_path: "str | None", timeout: float = PROBE_TIMEOUT_SECONDS) -> "tuple[bool, int | None, str | None]":
    """Connect to the daemon's own socket, send b"\n", read ONE line. Returns
    (loop_ok, latency_ms, loop_error). Never raises."""
    if not sock_path:
        return False, None, "probe not configured"
    start = time.monotonic()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(sock_path)
            s.sendall(b"\n")
            line = s.makefile("rb").readline(4096)
        latency = int((time.monotonic() - start) * 1000)
        if not line:
            return False, latency, "no response (closed without a reply)"
        try:
            obj = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return False, latency, "malformed response line"
        if not (isinstance(obj, dict) and obj.get("status") == "error"
                and isinstance(obj.get("error"), dict) and isinstance(obj["error"].get("code"), str)):
            return False, latency, "unexpected response shape"
        return True, latency, None
    except socket.timeout:
        return False, int((time.monotonic() - start) * 1000), f"timeout after {timeout:g}s (accept loop not responding)"
    except OSError as e:
        return False, int((time.monotonic() - start) * 1000), f"{e.__class__.__name__}"[:_LOOP_ERROR_MAX]
    except Exception as e:  # noqa: BLE001 -- the probe must never kill the beat
        return False, None, f"probe error {e.__class__.__name__}"[:_LOOP_ERROR_MAX]


def take(cur, *, project_ref: str, host: str, pid: int, boot_id: str,
         interval_seconds: int, ttl_seconds: int) -> bool:
    """CAS acquire. True iff this boot now holds the row."""
    cur.execute(_TAKE_SQL, {"ref": project_ref, "host": host, "pid": pid, "boot": boot_id,
                            "interval": interval_seconds, "ttl": ttl_seconds})
    return cur.fetchone() is not None


def renew(cur, *, project_ref: str, boot_id: str, loop_ok: "bool | None" = None,
          loop_latency_ms: "int | None" = None, loop_error: "str | None" = None) -> bool:
    """Beat (+ the latest listener-probe result). True iff this boot still
    holds the row (never acquires)."""
    cur.execute(_RENEW_SQL, {"ref": project_ref, "boot": boot_id, "loop_ok": loop_ok,
                             "latency": loop_latency_ms,
                             "error": loop_error[:_LOOP_ERROR_MAX] if loop_error else None})
    return cur.fetchone() is not None


class HeartbeatWriter:
    """Writes one heartbeat per gated ref. `db_conn_factory` returns a fresh
    psycopg connection (context manager), resolved per call like the server's."""

    def __init__(self, db_conn_factory, refs, *, host: str, pid: int, boot_id: str,
                 interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
                 sock_path: "str | None" = None, probe=probe_listener) -> None:
        if not (MIN_INTERVAL_SECONDS <= interval_seconds <= MAX_INTERVAL_SECONDS):
            raise ValueError(f"heartbeat interval {interval_seconds}s outside "
                             f"{MIN_INTERVAL_SECONDS}..{MAX_INTERVAL_SECONDS}")
        if not refs:
            raise ValueError("heartbeat needs at least one gated ref")
        self.db_conn_factory = db_conn_factory
        self.refs = tuple(sorted(refs))
        self.host = host
        self.pid = pid
        self.boot_id = boot_id
        self.interval_seconds = interval_seconds
        self.ttl_seconds = ttl_for(interval_seconds)
        self.sock_path = sock_path
        self._probe = probe
        self._stop = threading.Event()
        self._thread: "threading.Thread | None" = None

    def _take(self, cur, ref: str) -> bool:
        return take(cur, project_ref=ref, host=self.host, pid=self.pid, boot_id=self.boot_id,
                    interval_seconds=self.interval_seconds, ttl_seconds=self.ttl_seconds)

    def take_all(self) -> "list[str]":
        """Boot-time CAS for every gated ref, in ONE transaction. Returns the
        refs that could NOT be taken (fresh foreign holder); raises on DB
        error. Nothing is committed unless every ref was taken."""
        with self.db_conn_factory() as conn:
            with conn.cursor() as cur:
                failed = [ref for ref in self.refs if not self._take(cur, ref)]
            if failed:
                conn.rollback()
            else:
                conn.commit()
        return failed

    def beat_once(self) -> "dict[str, str]":
        """One tick: renew every ref (re-take only via the same CAS if renew
        misses). Never raises; returns {ref: outcome} for logging/tests."""
        outcomes: "dict[str, str]" = {}
        try:
            loop_ok, latency, loop_error = self._probe(self.sock_path)
        except Exception as e:  # noqa: BLE001 -- a probe failure never stops the beat
            loop_ok, latency, loop_error = False, None, f"probe error {e.__class__.__name__}"
        self.last_probe = (loop_ok, latency, loop_error)
        if not loop_ok:
            logger.error("exec_broker heartbeat: listener self-probe FAILED (%s) -- beating with "
                         "loop_ok=false", loop_error)
        for ref in self.refs:
            try:
                with self.db_conn_factory() as conn:
                    with conn.cursor() as cur:
                        if renew(cur, project_ref=ref, boot_id=self.boot_id, loop_ok=loop_ok,
                                 loop_latency_ms=latency, loop_error=loop_error):
                            outcomes[ref] = "beat"
                        elif self._take(cur, ref):
                            outcomes[ref] = "retaken"
                            logger.warning("exec_broker heartbeat: re-took row for %s", ref)
                        else:
                            outcomes[ref] = "lost"
                            logger.error("exec_broker heartbeat: row for %s is held by another "
                                         "live broker -- not stealing it", ref)
                    conn.commit()
            except Exception:  # noqa: BLE001 -- a beat failure must never kill the writer
                outcomes[ref] = "error"
                logger.exception("exec_broker heartbeat: beat failed for %s", ref)
        return outcomes

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.beat_once()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="exec-broker-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
