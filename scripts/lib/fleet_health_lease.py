#!/usr/bin/env python3
"""fleet_health_lease.py — CAI-RESP-501 single-owner lease for the watchdog +
fleet-status pens.

ORCH-TOPOLOGY-001 named five hub singleton pens bound to the `orch_lease` holder.
CAI-RESP-501 moves TWO of them — (iii) watchdog and (v) fleet-status assertions —
onto their OWN single-owner lease so the fleet's SRE (`cc-fleet-health`, Mini)
can run detect->act self-healing without the hub. The rule is identical to
orch_lease's: the pens belong to whoever holds the row in the substrate table,
NOT to a hostname and NOT to whichever body feels live.

  - DEFAULT holder = `cc-fleet-health` (the SRE). It TAKES the lease at boot and
    RENEWS it in its heartbeat (boot_fleet_health.sh).
  - DEAD-MAN FALLBACK: on the SRE's lease EXPIRY (death/stall — renewed_at + ttl
    elapsed) the HUB (the orch_lease holder) may RECLAIM the pens via `take`.
  - NEVER two concurrent holders: at most one body can hold a FRESH lease (the
    CAS in `take` serializes ownership); every OTHER body's `check` REFUSES
    against a fresh, different holder. This mirrors orch_lease exactly.

This lease gates the watchdog / fleet-status ACTIONS the same way orch_lease.py
gates tg_send: fail-SAFE for the recorded holder (never strand the monitor),
fail-CLOSED on POSITIVE evidence that a *different live body* holds the pen.

Identity: this lease is the SRE's tool, so the default acting identity is
`cc-fleet-health`. The HUB, when it reclaims, declares itself explicitly with
`--as cc-orchestrator` (or env FLEET_HEALTH_LEASE_AS). We deliberately do NOT
derive identity from ORCH_BODY_ROLE — the Mini's .env carries ORCH_BODY_ROLE=
console (Nazim), but the co-located SRE daemons are a DISTINCT identity.

CLI:
  fleet_health_lease.py check              exit 0 = this body may run the
                                           watchdog/fleet-status pen; exit 3 =
                                           REFUSED (a different live body holds it)
  fleet_health_lease.py status             show the lease row
  fleet_health_lease.py take [--reason ..] CAS acquire (SRE boot / hub reclaim).
                                           Succeeds if already mine OR expired.
  fleet_health_lease.py renew              holder heartbeat: stamp renewed_at
  fleet_health_lease.py reclaim --reason.. HUB dead-man reclaim (loud `take` as
                                           cc-orchestrator). Only when the SRE is
                                           genuinely dead (lease expired).
  Common: --as <agent_id> to declare the acting identity (else env
  FLEET_HEALTH_LEASE_AS, else 'cc-fleet-health').
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
from datetime import datetime, timedelta, timezone

import psycopg
from dotenv import load_dotenv

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
load_dotenv(os.path.join(ROOT, ".env"))

LEASE_KEY = "fleet-health"
SRE_AGENT_ID = "cc-fleet-health"
HUB_AGENT_ID = "cc-orchestrator"


def _dsn():
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def _me() -> str:
    return socket.gethostname()


def _agent_id(override: str | None = None) -> str:
    """The identity this invocation acts AS. Default is the SRE — the hub must
    declare itself explicitly (--as / env) to reclaim. NEVER derived from
    ORCH_BODY_ROLE (the Mini's .env is console=Nazim, a different body)."""
    return (override
            or os.environ.get("FLEET_HEALTH_LEASE_AS")
            or SRE_AGENT_ID).strip()


# ── Pure decision core (no DB, no clock) — unit-tested offline; the DB layer
#    below mirrors these exact conditions in atomic SQL. Keep them in sync. ────

def _is_expired(renewed_at: datetime, ttl_seconds: int, now: datetime) -> bool:
    return now > renewed_at + timedelta(seconds=ttl_seconds)


def decide(row: dict | None, agent_id: str, host: str, now: datetime) -> "tuple[bool, str]":
    """Whether `agent_id`@`host` may act as the watchdog/fleet-status pen holder.

    Pure. Mirrors orch_lease's fail-safe-for-holder / fail-closed-for-known-
    non-holder rule. REFUSE only on POSITIVE evidence that a *different* body
    holds a FRESH lease — otherwise ALLOW (never strand the monitor)."""
    if row is None:
        return True, "lease-missing-failsafe"       # pre-migration: never strand
    holder = row.get("holder")
    if holder is None:
        return True, "holder-null-failsafe"
    holder_host = row.get("holder_host")
    expired = _is_expired(row["renewed_at"], row["ttl_seconds"], now)
    host_ok = holder_host is None or holder_host == host
    held_by_me = holder == agent_id and host_ok
    if held_by_me and not expired:
        return True, "holder-current"
    if held_by_me:
        # Still recorded as mine, just stale — no other body has reclaimed (a
        # reclaim would have flipped `holder`). Safe to proceed + renew.
        return True, "holder-stale-self"
    if not expired:
        return False, (f"fleet_health_lease held by {holder}@{holder_host} (fresh); "
                       f"this body is {agent_id}@{host} — pen deferred (CAI-RESP-501)")
    # Different holder, but EXPIRED (presumed dead) — reclaim-eligible.
    return True, f"prior holder {holder}@{holder_host} EXPIRED — reclaim-eligible"


def apply_take(row: dict | None, agent_id: str, host: str, now: datetime,
               reason: str | None = None) -> dict | None:
    """Pure CAS-acquire. Returns the new row, or None if the CAS would FAIL (a
    live different holder). Mirrors the atomic `take` SQL below."""
    if row is None:
        return None
    expired = _is_expired(row["renewed_at"], row["ttl_seconds"], now)
    host_ok = row.get("holder_host") is None or row["holder_host"] == host
    already_mine = row.get("holder") == agent_id and host_ok
    if not (already_mine or expired):
        return None                                  # CAS fail: live other holder
    new = dict(row)
    if row.get("holder") != agent_id:
        new["taken_over_from"] = f'{row.get("holder")}@{row.get("holder_host") or "?"}'
        new["takeover_reason"] = reason
    new["holder"] = agent_id
    new["holder_host"] = host
    new["acquired_at"] = now
    new["renewed_at"] = now
    return new


def apply_renew(row: dict | None, agent_id: str, host: str, now: datetime) -> dict | None:
    """Pure heartbeat. Returns the new row, or None if this body is NOT the
    holder (renew never acquires — that's `take`'s job). Mirrors `renew` SQL."""
    if row is None:
        return None
    host_ok = row.get("holder_host") is None or row["holder_host"] == host
    if row.get("holder") != agent_id or not host_ok:
        return None
    new = dict(row)
    new["renewed_at"] = now
    new["holder_host"] = row.get("holder_host") or host
    return new


# ── DB layer (atomic SQL mirroring the pure core above) ──────────────────────

def _fetch(cur) -> dict | None:
    cur.execute(
        "SELECT holder, holder_host, acquired_at, renewed_at, ttl_seconds, "
        "taken_over_from, takeover_reason FROM fleet_health_lease WHERE lease_key=%s",
        (LEASE_KEY,))
    r = cur.fetchone()
    if r is None:
        return None
    return {"holder": r[0], "holder_host": r[1], "acquired_at": r[2],
            "renewed_at": r[3], "ttl_seconds": r[4], "taken_over_from": r[5],
            "takeover_reason": r[6]}


def check(agent_id: str | None = None, host: str | None = None) -> "tuple[bool, str]":
    """Returns (ok, reason). Reads the lease and applies `decide`. Fail-SAFE on a
    substrate read error (never strand the monitor)."""
    agent_id = _agent_id(agent_id)
    host = host or _me()
    try:
        with psycopg.connect(_dsn(), connect_timeout=5) as conn, conn.cursor() as cur:
            row = _fetch(cur)
    except Exception as exc:  # noqa: BLE001 — any substrate failure = fail-safe
        print(f"⚠️  fleet_health_lease unreadable ({exc}) — allowing "
              f"(never strand the monitor)", file=sys.stderr)
        return True, "lease-unreadable-failsafe"
    return decide(row, agent_id, host, datetime.now(timezone.utc))


def gate() -> "tuple[bool, str]":
    """Convenience for the watchdog scripts: check() under the ambient identity."""
    return check()


def cmd_check(agent_id: str | None) -> int:
    ok, reason = check(agent_id)
    print(reason)
    return 0 if ok else 3


def cmd_status() -> int:
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        row = _fetch(cur)
    if row is None:
        print("no lease row — run scripts/apply_fleet_health_lease.py")
        return 1
    now = datetime.now(timezone.utc)
    expired = _is_expired(row["renewed_at"], row["ttl_seconds"], now)
    print(f"holder={row['holder']} host={row['holder_host']} "
          f"acquired={row['acquired_at']:%Y-%m-%d %H:%M}Z "
          f"renewed={row['renewed_at']:%Y-%m-%d %H:%M}Z ttl={row['ttl_seconds']}s "
          f"expired={expired}")
    if row["taken_over_from"]:
        print(f"taken over from {row['taken_over_from']}: {row['takeover_reason']}")
    print(f"this body: host={_me()} acting-as={_agent_id()}")
    return 0


def cmd_take(agent_id: str | None, reason: str | None, loud: bool = False) -> int:
    me = _agent_id(agent_id)
    host = _me()
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        row = _fetch(cur)
        if row is None:
            print("no lease row — run scripts/apply_fleet_health_lease.py first")
            return 1
        old = f"{row['holder']}@{row['holder_host']}"
        cur.execute(
            "UPDATE fleet_health_lease "
            "SET holder=%(me)s, holder_host=%(host)s, acquired_at=now(), renewed_at=now(), "
            "    taken_over_from = CASE WHEN holder <> %(me)s "
            "        THEN holder || '@' || COALESCE(holder_host,'?') ELSE taken_over_from END, "
            "    takeover_reason = CASE WHEN holder <> %(me)s THEN %(reason)s ELSE takeover_reason END "
            "WHERE lease_key=%(key)s AND ("
            "    (holder=%(me)s AND (holder_host IS NULL OR holder_host=%(host)s)) "
            "    OR (renewed_at + make_interval(secs => ttl_seconds) < now()) ) "
            "RETURNING holder, holder_host",
            {"me": me, "host": host, "reason": reason, "key": LEASE_KEY})
        got = cur.fetchone()
        conn.commit()
    if got is None:
        print(f"take FAILED — a live holder still owns the lease (CAS mismatch): {old}. "
              f"Not expired; refusing to steal it. Re-read status.")
        return 3
    if loud or row["holder"] != me:
        print("=" * 68)
        print(f"🚨 FLEET-HEALTH LEASE {'RECLAIM' if row['holder'] != me else 'TAKE'}: "
              f"{me}@{host} now holds '{LEASE_KEY}'")
        print(f"   from: {old}   reason: {reason or '(boot)'}")
        print("   Pens moved: (iii) watchdog, (v) fleet-status assertions.")
        print("   NOT included: singleton-body reset authority (CAI-500-gated, separate).")
        print("=" * 68)
    else:
        print(f"take OK: holder={got[0]} host={got[1]}")
    return 0


def cmd_renew(agent_id: str | None) -> int:
    me = _agent_id(agent_id)
    host = _me()
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE fleet_health_lease SET renewed_at=now(), holder_host=COALESCE(holder_host,%s) "
            "WHERE lease_key=%s AND holder=%s AND (holder_host IS NULL OR holder_host=%s) "
            "RETURNING holder, holder_host",
            (host, LEASE_KEY, me, host))
        got = cur.fetchone()
        conn.commit()
    if got is None:
        print(f"renew REFUSED — lease not held by {me}@{host} (take it first if expired)")
        return 3
    print(f"renewed: holder={got[0]} host={got[1]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "status", "take", "renew", "reclaim"])
    ap.add_argument("--as", dest="as_agent", default=None,
                    help="acting identity (default env FLEET_HEALTH_LEASE_AS or cc-fleet-health)")
    ap.add_argument("--reason", help="required for reclaim; optional for take")
    a = ap.parse_args()
    if a.cmd == "check":
        return cmd_check(a.as_agent)
    if a.cmd == "status":
        return cmd_status()
    if a.cmd == "renew":
        return cmd_renew(a.as_agent)
    if a.cmd == "take":
        return cmd_take(a.as_agent, a.reason)
    # reclaim = loud hub take (explicit identity required or defaults to hub id)
    if not a.reason:
        print("reclaim requires --reason 'why the SRE is dead / lease is being reclaimed'")
        return 2
    return cmd_take(a.as_agent or HUB_AGENT_ID, a.reason, loud=True)


if __name__ == "__main__":
    sys.exit(main())
