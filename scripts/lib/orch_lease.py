#!/usr/bin/env python3
"""orch_lease.py — ORCH-TOPOLOGY-001 pen gate + DR lease tools.

The five singleton pens — (i) bus-drain/mark, (ii) lane prompt submission,
(iii) watchdog, (iv) tg_send/tg-out + operator declarations, (v) fleet-status
assertions — belong to the holder of the 'orch-hub' row in substrate table
orch_lease. NOT to a hostname, NOT to whichever body feels live (A2: lease,
not hostname; A3: enforce in the table, not by promise — a behavioral promise
from a degraded brain is the norm, not the guard).

CLI:
  orch_lease.py check              exit 0 = this body may use the pens
                                   exit 3 = REFUSED (reason on stdout)
  orch_lease.py status             show the lease row
  orch_lease.py renew              hub heartbeat: stamp renewed_at (+ self-stamp
                                   holder_host on first run)
  orch_lease.py take --reason ...  DR takeover (compare-and-swap, loud). Only
                                   when the hub is genuinely dead. A failed-over
                                   hub inherits bus/watchdog/tg-out/declarations,
                                   NOT the Studio-hosted tmux lanes — never imply
                                   seamless failover.

Gate logic (fail-safe for the hub, fail-closed for known non-holders):
  1. ORCH_BODY_ROLE=console  -> REFUSE, always. The console body (Nazim) holds
     no pens in steady state. In a genuine DR event it runs `take` FIRST (the
     lease flips in the table), flips ORCH_BODY_ROLE to hub, and only then
     touches pens. ORCH_DR_OVERRIDE=1 is the break-glass bypass — loud.
  2. lease.holder_host set and != this hostname -> REFUSE (positive mismatch).
  3. lease unreadable / holder_host NULL -> ALLOW with a stderr warning. Never
     strand the hub: operator deafness is the worse failure mode.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys

import psycopg
from dotenv import load_dotenv

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
load_dotenv(os.path.join(ROOT, ".env"))

LEASE_KEY = "orch-hub"


def _dsn():
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def _role() -> str:
    return os.environ.get("ORCH_BODY_ROLE", "").strip().lower()


def _me() -> str:
    return socket.gethostname()


def _fetch_lease(cur):
    cur.execute(
        "SELECT holder, holder_host, acquired_at, renewed_at, ttl_seconds, "
        "taken_over_from, takeover_reason FROM orch_lease WHERE lease_key=%s",
        (LEASE_KEY,))
    return cur.fetchone()


def check() -> "tuple":
    """Returns (ok: bool, reason: str). Pure decision — no side effects."""
    if _role() == "console":
        if os.environ.get("ORCH_DR_OVERRIDE") == "1":
            print("⚠️  ORCH_DR_OVERRIDE=1 — console body bypassing pen gate "
                  "(break-glass; must accompany a genuine DR lease takeover)",
                  file=sys.stderr)
            return True, "dr-override"
        return False, ("ORCH_BODY_ROLE=console — the console body (Nazim) holds no "
                       "singleton pens (ORCH-TOPOLOGY-001). Reply in-console; the "
                       "operator's phone hears the hub's voice only. DR: orch_lease.py take")
    try:
        with psycopg.connect(_dsn(), connect_timeout=5) as conn, conn.cursor() as cur:
            row = _fetch_lease(cur)
    except Exception as exc:  # noqa: BLE001 — any substrate failure = fail-safe path
        print(f"⚠️  orch_lease unreadable ({exc}) — allowing (never strand the hub)",
              file=sys.stderr)
        return True, "lease-unreadable-failsafe"
    if row is None:
        print("⚠️  orch_lease row missing — allowing (pre-migration fail-safe)",
              file=sys.stderr)
        return True, "lease-missing-failsafe"
    holder, holder_host = row[0], row[1]
    if holder_host and holder_host != _me():
        return False, (f"lease '{LEASE_KEY}' is held by {holder}@{holder_host}; "
                       f"this host is {_me()} — pens refused (ORCH-TOPOLOGY-001)")
    return True, "ok"


def cmd_check() -> int:
    ok, reason = check()
    if not ok:
        print(reason)
        return 3
    return 0


def cmd_status() -> int:
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        row = _fetch_lease(cur)
    if row is None:
        print("no lease row — run scripts/apply_orch_lease.py")
        return 1
    print(f"holder={row[0]} host={row[1]} acquired={row[2]:%Y-%m-%d %H:%M}Z "
          f"renewed={row[3]:%Y-%m-%d %H:%M}Z ttl={row[4]}s")
    if row[5]:
        print(f"taken over from {row[5]}: {row[6]}")
    print(f"this body: host={_me()} role={_role() or '(unset)'}")
    return 0


def cmd_renew() -> int:
    """Hub heartbeat. Also self-stamps holder_host on first run (NULL)."""
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE orch_lease SET renewed_at=now(), holder_host=COALESCE(holder_host,%s) "
            "WHERE lease_key=%s AND (holder_host IS NULL OR holder_host=%s) "
            "RETURNING holder, holder_host",
            (_me(), LEASE_KEY, _me()))
        row = cur.fetchone()
        conn.commit()
    if row is None:
        print(f"renew REFUSED — lease not held by this host ({_me()})")
        return 3
    print(f"renewed: holder={row[0]} host={row[1]}")
    return 0


def cmd_take(reason: str) -> int:
    """DR takeover, compare-and-swap on the current holder+host. LOUD."""
    holder_id = os.environ.get("ORCH_AGENT_ID", "cc-orchestrator")
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        row = _fetch_lease(cur)
        if row is None:
            print("no lease row — run scripts/apply_orch_lease.py first")
            return 1
        old_holder, old_host = row[0], row[1]
        cur.execute(
            "UPDATE orch_lease SET holder=%s, holder_host=%s, acquired_at=now(), "
            "renewed_at=now(), taken_over_from=%s, takeover_reason=%s "
            "WHERE lease_key=%s AND holder=%s AND holder_host IS NOT DISTINCT FROM %s "
            "RETURNING holder",
            (holder_id, _me(), f"{old_holder}@{old_host}", reason,
             LEASE_KEY, old_holder, old_host))
        got = cur.fetchone()
        conn.commit()
    if got is None:
        print("take FAILED — lease changed under us (CAS mismatch); re-read status")
        return 3
    print("=" * 68)
    print(f"🚨 DR LEASE TAKEOVER: {holder_id}@{_me()} now holds '{LEASE_KEY}'")
    print(f"   from: {old_holder}@{old_host}   reason: {reason}")
    print("   Inherited: bus-drain, watchdog, tg-out, declarations, status.")
    print("   NOT inherited: tmux lanes hosted on the dead machine (re-home them).")
    print("   Set ORCH_BODY_ROLE=hub in .env; notify cai + the operator NOW.")
    print("=" * 68)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "status", "renew", "take"])
    ap.add_argument("--reason", help="required for take")
    a = ap.parse_args()
    if a.cmd == "check":
        return cmd_check()
    if a.cmd == "status":
        return cmd_status()
    if a.cmd == "renew":
        return cmd_renew()
    if not a.reason:
        print("take requires --reason 'why the hub is dead'")
        return 2
    return cmd_take(a.reason)


if __name__ == "__main__":
    sys.exit(main())
