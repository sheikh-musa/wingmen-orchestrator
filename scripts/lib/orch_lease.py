#!/usr/bin/env python3
"""orch_lease.py — ORCH-TOPOLOGY-001 pen gate + DR lease tools.

The five singleton pens — (i) bus-drain/mark, (ii) lane prompt submission,
(iii) watchdog, (iv) tg_send/tg-out + operator declarations, (v) fleet-status
assertions — belong to the holder of the 'orch-hub' row in substrate table
orch_lease. NOT to a hostname, NOT to whichever body feels live (A2: lease,
not hostname; A3: enforce in the table, not by promise — a behavioral promise
from a degraded brain is the norm, not the guard).

TTL / expiry (mirrors fleet_health_lease.py): the lease carries renewed_at +
ttl_seconds. A HEALTHY, recently-renewed holder is now PROTECTED — a `take` no
longer steals the pens from a live hub on a human's mistaken belief it's dead.
Expiry is an ADDITIONAL guard, not a replacement for the DR break-glass: a
genuinely-expired lease is still takeable, and a genuine DR against a
not-yet-expired-but-dead holder stays possible via `take --force` (loud). Same
fail-safe-for-holder / fail-closed-for-known-non-holder shape as
fleet_health_lease.py.

CLI:
  orch_lease.py check              exit 0 = this body may use the pens
                                   exit 3 = REFUSED (reason on stdout)
  orch_lease.py status             show the lease row (+ expired=?)
  orch_lease.py renew              hub heartbeat: stamp renewed_at (+ self-stamp
                                   holder_host on first run)
  orch_lease.py take --reason ...  DR takeover (compare-and-swap, loud). Succeeds
                                   if this body already holds it OR the lease is
                                   EXPIRED. Refuses to steal a fresh, live holder
                                   unless --force is given. Only when the hub is
                                   genuinely dead. A failed-over hub inherits
                                   bus/watchdog/tg-out/declarations, NOT the
                                   Studio-hosted tmux lanes — never imply seamless
                                   failover.
  orch_lease.py take --reason .. --force
                                   break-glass: override the expiry guard and
                                   steal even a fresh holder (genuine split-brain
                                   DR where the holder is dead but not yet
                                   TTL-expired). LOUD.

Gate logic (fail-safe for the hub, fail-closed for known non-holders):
  1. ORCH_BODY_ROLE=console  -> REFUSE, always. The console body (Nazim) holds
     no pens in steady state. In a genuine DR event it runs `take` FIRST (the
     lease flips in the table), flips ORCH_BODY_ROLE to hub, and only then
     touches pens. ORCH_DR_OVERRIDE=1 is the break-glass bypass — loud.
  2. lease.holder_host set and != this hostname, and the lease is FRESH ->
     REFUSE (positive mismatch against a live holder).
  3. lease.holder_host set and != this hostname, but EXPIRED -> ALLOW
     (reclaim-eligible; the recorded holder is presumed dead).
  4. lease unreadable / holder_host NULL / row missing -> ALLOW with a stderr
     warning. Never strand the hub: operator deafness is the worse failure mode.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone

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
    # Short host label — collapse the macOS gethostname() flap where the same machine reports
    # "Mac-Studio" vs "Mac-Studio.local" on network-state changes, which false-refused the hub's
    # pens against a stored holder_host="Mac-Studio" (fleet bugfix folded from the Studio checkout,
    # cc-orchestrator 2026-07-22). "mac-mini" stays "mac-mini" — cross-body protection intact.
    return socket.gethostname().split(".")[0]


def _holder_id() -> str:
    return os.environ.get("ORCH_AGENT_ID", "cc-orchestrator")


# ── Pure decision core (no DB, no clock) — unit-tested offline; the DB layer
#    below mirrors these exact conditions in atomic SQL. Keep them in sync.
#    Mirrors fleet_health_lease.py, but the orch-hub body identity is its HOST
#    (holder_host), not an agent_id — so "held by me" == holder_host matches. ──

def _is_expired(renewed_at: datetime, ttl_seconds: int, now: datetime) -> bool:
    return now > renewed_at + timedelta(seconds=ttl_seconds)


def decide(row: dict | None, host: str, now: datetime) -> "tuple[bool, str]":
    """Whether the body on `host` may use the pens. Pure. Fail-SAFE for the hub,
    fail-CLOSED only on POSITIVE evidence that a *different live host* holds a
    FRESH lease. NOTE: the console-role refusal + DR override live in check()
    (env, not row) — this covers only the row/expiry decision."""
    if row is None:
        return True, "lease-missing-failsafe"          # pre-migration: never strand
    holder_host = row.get("holder_host")
    if holder_host is None:
        return True, "holder-host-null-failsafe"        # not yet self-stamped
    expired = _is_expired(row["renewed_at"], row["ttl_seconds"], now)
    held_by_me = holder_host == host
    if held_by_me and not expired:
        return True, "holder-current"
    if held_by_me:
        # Still recorded as this host, just stale — no other body reclaimed (a
        # reclaim would have flipped holder_host). Safe to proceed + renew.
        return True, "holder-stale-self"
    if not expired:
        return False, (f"lease '{LEASE_KEY}' is held by {row.get('holder')}@{holder_host} "
                       f"(fresh); this host is {host} — pens refused (ORCH-TOPOLOGY-001)")
    # Different host, but EXPIRED (presumed dead) — reclaim-eligible.
    return True, (f"prior holder {row.get('holder')}@{holder_host} EXPIRED — "
                  f"reclaim-eligible")


def apply_take(row: dict | None, new_holder: str, host: str, now: datetime,
               reason: str | None = None, force: bool = False,
               snapshot: dict | None = None) -> dict | None:
    """Pure CAS-acquire. Returns the new row, or None if the CAS would FAIL.
    Mirrors the atomic `take` SQL below. Two CAS layers:
      1. SNAPSHOT-CAS (CAI-RESP-513): if `snapshot` is given (the row as first
         read), the take FAILS unless the CURRENT `row` still matches it on
         holder + holder_host — applies even under --force, so two concurrent
         `take --force` cannot lost-update each other (split-brain). None = fail.
      2. FRESHNESS guard: a fresh, live *different* host is refused unless --force.
    `force` overrides layer 2 (freshness) ONLY — never layer 1 (the snapshot-CAS)."""
    if row is None:
        return None
    if snapshot is not None and (
        row.get("holder") != snapshot.get("holder")
        or row.get("holder_host") != snapshot.get("holder_host")
    ):
        return None                                     # snapshot-CAS fail: row changed under us
    expired = _is_expired(row["renewed_at"], row["ttl_seconds"], now)
    already_mine = row.get("holder_host") is None or row["holder_host"] == host
    if not (already_mine or expired or force):
        return None                                     # CAS fail: fresh other holder
    new = dict(row)
    if row.get("holder_host") != host or row.get("holder") != new_holder:
        new["taken_over_from"] = f'{row.get("holder")}@{row.get("holder_host") or "?"}'
        new["takeover_reason"] = reason
    new["holder"] = new_holder
    new["holder_host"] = host
    new["acquired_at"] = now
    new["renewed_at"] = now
    return new


def apply_renew(row: dict | None, host: str, now: datetime) -> dict | None:
    """Pure heartbeat. Returns the new row, or None if this host is NOT the
    holder (renew never acquires — that's `take`'s job). Self-stamps a NULL
    holder_host on first run. Mirrors the `renew` SQL."""
    if row is None:
        return None
    holder_host = row.get("holder_host")
    if holder_host is not None and holder_host != host:
        return None
    new = dict(row)
    new["renewed_at"] = now
    new["holder_host"] = holder_host or host
    return new


# ── DB layer (atomic SQL mirroring the pure core above) ──────────────────────

def _fetch_lease(cur) -> dict | None:
    cur.execute(
        "SELECT holder, holder_host, acquired_at, renewed_at, ttl_seconds, "
        "taken_over_from, takeover_reason FROM orch_lease WHERE lease_key=%s",
        (LEASE_KEY,))
    r = cur.fetchone()
    if r is None:
        return None
    return {"holder": r[0], "holder_host": r[1], "acquired_at": r[2],
            "renewed_at": r[3], "ttl_seconds": r[4], "taken_over_from": r[5],
            "takeover_reason": r[6]}


def check() -> "tuple":
    """Returns (ok: bool, reason: str). Reads the lease and applies `decide`
    (with an expiry guard). Fail-SAFE on a substrate read error (never strand
    the hub)."""
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
    return decide(row, _me(), datetime.now(timezone.utc))


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
    now = datetime.now(timezone.utc)
    expired = _is_expired(row["renewed_at"], row["ttl_seconds"], now)
    print(f"holder={row['holder']} host={row['holder_host']} "
          f"acquired={row['acquired_at']:%Y-%m-%d %H:%M}Z "
          f"renewed={row['renewed_at']:%Y-%m-%d %H:%M}Z ttl={row['ttl_seconds']}s "
          f"expired={expired}")
    if row["taken_over_from"]:
        print(f"taken over from {row['taken_over_from']}: {row['takeover_reason']}")
    print(f"this body: host={_me()} role={_role() or '(unset)'}")
    return 0


# ── op#11774 #4b (operator-P1): hub agent_status heartbeat + REAL auth_fp ──────
# The hub's boot path never wrote auth_fp (and its hb-loop is dead code — the real
# launcher is orch_supervisor.sh, not boot_orch.sh), so the console showed the hub
# as auth_fp=NULL + chronically-stale hb — the blind spot that hid the token-
# exhaustion incident (op#18976). The lease-renew is the PROVEN periodic writer
# (it renewed straight through the re-token while the boot registration wrote
# nothing), so we stamp the hub's heartbeat + real token fp HERE. auth_fp source =
# /proc (GROUND TRUTH: what the process actually runs — a pointer would re-create
# the blind spot the moment the process drifts). FAIL-SOFT is mandatory: a fp read
# hiccup must NEVER fail the lease renew (critical infra), and must RETAIN the
# last-known fp rather than NULL it (COALESCE) — a stale-but-present key beats an
# invisible one.
HUB_AGENT = "cc-orchestrator"
_HUB_HB_SQL = (
    "UPDATE agent_status SET last_heartbeat=now(), status='working', "
    "auth_fp=COALESCE(%s, auth_fp), updated_at=now() "
    "WHERE agent_id=%s"
)


def _hub_auth_fp_from_environ(environ_text):
    """sha256(CLAUDE_CODE_OAUTH_TOKEN)[:12] from a NUL-separated /proc/<pid>/environ
    dump — the console badge fp format. None if the var is absent/empty."""
    if not environ_text:
        return None
    for kv in environ_text.split("\x00"):
        if kv.startswith("CLAUDE_CODE_OAUTH_TOKEN="):
            tok = kv.split("=", 1)[1]
            return hashlib.sha256(tok.encode()).hexdigest()[:12] if tok else None
    return None


def _default_find_orch_pid():
    """The hub's live claude pid — the one that ACTUALLY carries the token in its
    environ (skips the tmux-wrapper match + any racy/just-exited pid). Fail-soft
    -> None; NEVER raises."""
    try:
        out = subprocess.check_output(
            ["pgrep", "-f", "claude --dangerously-skip-permissions --continue"],
            text=True, stderr=subprocess.DEVNULL)
        for p in reversed(out.split()):
            if not p.strip().isdigit():
                continue
            try:
                with open(f"/proc/{int(p)}/environ", "r") as f:
                    if "CLAUDE_CODE_OAUTH_TOKEN=" in f.read():
                        return int(p)
            except Exception:
                continue
        return None
    except Exception:
        return None


def _default_read_environ(pid):
    with open(f"/proc/{pid}/environ", "r") as f:
        return f.read()


def _read_hub_auth_fp(find_pid=None, read_environ=None):
    """The hub's REAL launch-token fp from /proc. FAIL-SOFT: any failure (no pid,
    unreadable, no token) -> None; NEVER raises. Seams injectable for tests."""
    find_pid = find_pid or _default_find_orch_pid
    read_environ = read_environ or _default_read_environ
    try:
        pid = find_pid()
        if not pid:
            return None
        return _hub_auth_fp_from_environ(read_environ(pid))
    except Exception:
        return None


def _write_hub_heartbeat(cur, fp):
    """Stamp the hub's agent_status heartbeat + auth_fp (COALESCE-retains a NULL fp).
    Caller MUST have set the identity GUC in the same txn (hardened identity trigger)."""
    cur.execute(_HUB_HB_SQL, (fp, HUB_AGENT))


def cmd_renew() -> int:
    """Hub heartbeat. Renews the lease, then (best-effort) stamps the hub's
    agent_status heartbeat + real auth_fp (#4b). Also self-stamps holder_host."""
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
    # #4b: the lease renewed (we ARE the hub holder) -> stamp the hub's hb + real
    # auth_fp so the console SHOWS the hub key. BEST-EFFORT, AFTER the renew already
    # committed above -> a fp/hb hiccup can never fail the renew (console's hard req).
    try:
        fp = _read_hub_auth_fp()
        with psycopg.connect(_dsn()) as c2, c2.cursor() as cur2:
            cur2.execute("SELECT set_config('app.current_agent_id',%s,true)", (HUB_AGENT,))
            _write_hub_heartbeat(cur2, fp)
            c2.commit()
        print(f"hub-hb stamped: auth_fp={fp or '(retained last-known)'}")
    except Exception as e:  # never let the hb write break the renew
        print(f"[orch_lease renew] hub-hb best-effort skipped: {e}")
    print(f"renewed: holder={row[0]} host={row[1]}")
    return 0


def cmd_take(reason: str, force: bool = False) -> int:
    """DR takeover. CAS succeeds if this host already holds it OR the lease is
    EXPIRED (or --force). A fresh, live holder is PROTECTED (mistaken-death
    guard) unless --force is given. LOUD on a real handover."""
    holder_id = _holder_id()
    host = _me()
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        row = _fetch_lease(cur)
        if row is None:
            print("no lease row — run scripts/apply_orch_lease.py first")
            return 1
        old = f"{row['holder']}@{row['holder_host']}"
        cur.execute(
            "UPDATE orch_lease "
            "SET holder=%(me)s, holder_host=%(host)s, acquired_at=now(), renewed_at=now(), "
            "    taken_over_from = CASE WHEN holder <> %(me)s OR holder_host IS DISTINCT FROM %(host)s "
            "        THEN holder || '@' || COALESCE(holder_host,'?') ELSE taken_over_from END, "
            "    takeover_reason = CASE WHEN holder <> %(me)s OR holder_host IS DISTINCT FROM %(host)s "
            "        THEN %(reason)s ELSE takeover_reason END "
            "WHERE lease_key=%(key)s "
            # Snapshot compare-and-swap (CAI-RESP-513): the row MUST be unchanged
            # since we read it — applies to EVERY path incl. --force. Two concurrent
            # `take --force` can no longer last-writer-win into a silent lost-update
            # (split-brain); the second CAS-fails into the return(3) re-read below.
            "    AND holder = %(snap_holder)s "
            "    AND holder_host IS NOT DISTINCT FROM %(snap_host)s "
            "    AND ( "
            "        (holder_host IS NULL OR holder_host=%(host)s) "            # already this body
            "        OR (renewed_at + make_interval(secs => ttl_seconds) < now()) " # expired
            "        OR %(force)s ) "                                           # --force: override FRESHNESS only
            "RETURNING holder, holder_host",
            {"me": holder_id, "host": host, "reason": reason, "key": LEASE_KEY,
             "force": force, "snap_holder": row["holder"], "snap_host": row["holder_host"]})
        got = cur.fetchone()
        conn.commit()
    if got is None:
        print(f"take FAILED — either a live holder still owns the lease (not expired), "
              f"or it CHANGED under us since we read it (concurrent takeover): {old}. "
              f"Refusing to clobber it (ORCH-TOPOLOGY-001 / snapshot-CAS). Re-read status; "
              f"if the holder is GENUINELY dead but not yet TTL-expired, re-run with "
              f"--force (break-glass, loud) — --force overrides the freshness guard but "
              f"still CAS-checks the row is unchanged.")
        return 3
    handover = row["holder"] != holder_id or row["holder_host"] != host
    if handover:
        print("=" * 68)
        print(f"🚨 DR LEASE TAKEOVER{' (--force break-glass)' if force else ''}: "
              f"{holder_id}@{host} now holds '{LEASE_KEY}'")
        print(f"   from: {old}   reason: {reason}")
        print("   Inherited: bus-drain, watchdog, tg-out, declarations, status.")
        print("   NOT inherited: tmux lanes hosted on the dead machine (re-home them).")
        print("   Set ORCH_BODY_ROLE=hub in .env; notify cai + the operator NOW.")
        print("=" * 68)
    else:
        print(f"take OK (already this body): holder={got[0]} host={got[1]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "status", "renew", "take"])
    ap.add_argument("--reason", help="required for take")
    ap.add_argument("--force", action="store_true",
                    help="break-glass: bypass the expiry guard and steal a fresh "
                         "holder (genuine DR against a dead-but-not-yet-expired hub)")
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
    return cmd_take(a.reason, force=a.force)


if __name__ == "__main__":
    sys.exit(main())
