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

TABLE (migrations/drafts/hc95_exec_broker_heartbeat.sql), one row per gated ref:
  project_ref      text PK   the gated ref this broker serves
  holder_host      text      fleet_host_id() of the broker body
  broker_pid       integer   daemon pid
  boot_id          uuid      random per daemon boot (restart / dual-broker detector)
  started_at       timestamptz  when this boot took the row
  beat_at          timestamptz  DB now() of the last beat (the dead-man stamp)
  beat_seq         bigint    beats since started_at (0 at take)
  interval_seconds integer   promised cadence
  ttl_seconds      integer   stale threshold (default 3 x interval)
  taken_over_from  text      prior holder_host:pid when a different host took over
  takeover_reason  text
STALE := row missing  OR  now() > beat_at + make_interval(secs => ttl_seconds).
Every timestamp is the DATABASE clock, never the broker host's.

FAILURE POLICY: boot refuses (fail closed) if any gated ref cannot be taken --
a DB error or a FRESH foreign holder (two brokers claiming one gated ref is a
misconfiguration, never resolved by stealing). After boot, a failed beat is
logged and retried next tick and never touches the request path; a beat that
finds the row taken over (renew misses) does NOT steal it back unless the CAS
take would succeed (same host or expired). A dead or wedged daemon simply stops
beating, which is exactly what the watchdog detects.

KNOWN LIMITATION: the writer is a thread inside the daemon process, so it
proves the PROCESS is alive and can reach the DB -- not that the accept loop
is serving. A hung accept loop with a live thread keeps beating. Compensating
control: the B4 daily reconcile (out of scope here) compares executions.
"""
from __future__ import annotations

import logging
import threading
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
    "  taken_over_from = CASE WHEN exec_broker_heartbeat.holder_host <> EXCLUDED.holder_host "
    "      THEN exec_broker_heartbeat.holder_host || ':' || exec_broker_heartbeat.broker_pid "
    "      ELSE exec_broker_heartbeat.taken_over_from END, "
    "  takeover_reason = CASE WHEN exec_broker_heartbeat.holder_host <> EXCLUDED.holder_host "
    "      THEN %(reason)s ELSE exec_broker_heartbeat.takeover_reason END "
    "WHERE exec_broker_heartbeat.holder_host = EXCLUDED.holder_host "
    "   OR exec_broker_heartbeat.beat_at + make_interval(secs => exec_broker_heartbeat.ttl_seconds) < now() "
    "RETURNING boot_id"
)

_RENEW_SQL = (
    "UPDATE exec_broker_heartbeat SET beat_at = now(), beat_seq = beat_seq + 1 "
    "WHERE project_ref = %s AND boot_id = %s RETURNING beat_seq"
)


def take(cur, *, project_ref: str, host: str, pid: int, boot_id: str,
         interval_seconds: int, ttl_seconds: int, reason: str = "broker boot") -> bool:
    """CAS acquire. True iff this boot now holds the row."""
    cur.execute(_TAKE_SQL, {"ref": project_ref, "host": host, "pid": pid, "boot": boot_id,
                            "interval": interval_seconds, "ttl": ttl_seconds, "reason": reason})
    return cur.fetchone() is not None


def renew(cur, *, project_ref: str, boot_id: str) -> bool:
    """Beat. True iff this boot still holds the row (never acquires)."""
    cur.execute(_RENEW_SQL, (project_ref, boot_id))
    return cur.fetchone() is not None


class HeartbeatWriter:
    """Writes one heartbeat per gated ref. `db_conn_factory` returns a fresh
    psycopg connection (context manager), resolved per call like the server's."""

    def __init__(self, db_conn_factory, refs, *, host: str, pid: int, boot_id: str,
                 interval_seconds: int = DEFAULT_INTERVAL_SECONDS) -> None:
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
        for ref in self.refs:
            try:
                with self.db_conn_factory() as conn:
                    with conn.cursor() as cur:
                        if renew(cur, project_ref=ref, boot_id=self.boot_id):
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
