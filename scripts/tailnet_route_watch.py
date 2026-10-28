#!/usr/bin/env python3
"""tailnet_route_watch.py — page when the Mini's tailnet route (100.64/10 -> utun) is gone.

WHY (console #79806, 2026-10-28): the Mini lost its 100.64.0.0/10 -> utun5 route at about
08:36Z and nobody noticed for ~1.5h. `tailscale ping` still answered over DERP, outbound
internet and the substrate were fine, so every heartbeat looked green. The only symptoms were
"cubeasht ssh drop" and then "gzb unreachable", both misread as REMOTE inbound drops.
The real signal is local and cheap: `route -n get <peer tailnet ip>` resolves to en0
via the LAN gateway instead of a utun interface.

WHAT: each run asks the kernel which interface carries each known peer's tailnet IP.
  * any peer NOT on utun*  -> 'tailnet-route-missing'   (P1, requires_response)
  * a probe that fails     -> 'tailnet-route-unmeasured' (P2; never reported as healthy)
  * recovered after a page -> one P3 'recovered' note, dedup key cleared
Paging is deduped via a JSON state file, COOLDOWN_MIN per key.

Fail LOUD: an exception in the monitor itself (state file, paging) propagates and exits
non-zero — a failed page is never swallowed. Read-only: it never touches Tailscale or the
routing table (Mini network config is console's pen).

Usage: tailnet_route_watch.py [--dry-run] [--state PATH]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

FROM_AGENT = "cc-fleet-health"
TO_AGENT = "orch-console"
COOLDOWN_MIN = 60
ROUTE_TIMEOUT_S = 10
DEFAULT_STATE_FILE = Path.home() / "wingmen" / "fleet-health" / "state" / "tailnet_route_watch.json"

# Stable tailnet peers the Mini must reach. One is enough to see the 100.64/10 route; three
# guard against a single peer's host route being the odd one out.
PEERS = {
    "gzbai": "100.77.251.8",
    "mac-studio": "100.104.36.27",
    "cubeasht": "100.64.131.9",
}

SCRIPTS_DIR = Path(__file__).resolve().parent


class MonitorError(RuntimeError):
    """A failure of the monitor itself — must exit non-zero, loudly."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_interface(text: str) -> str:
    m = re.search(r"^\s*interface:\s*(\S+)", text, re.MULTILINE)
    if not m:
        raise MonitorError(f"no 'interface:' line in route output: {text.strip()[:200]!r}")
    return m.group(1)


def probe(ip: str) -> Optional[str]:
    """Interface the kernel would use for `ip`, or None if it could not be measured."""
    try:
        cp = subprocess.run(["/sbin/route", "-n", "get", ip], capture_output=True,
                            text=True, timeout=ROUTE_TIMEOUT_S)
        if cp.returncode != 0:
            return None
        return parse_interface(cp.stdout)
    except (OSError, subprocess.TimeoutExpired, MonitorError):
        return None


def evaluate(ifaces: dict) -> Optional[dict]:
    """{peer: interface|None} -> the finding, or None when every peer routes via utun."""
    bad = {p: i for p, i in ifaces.items() if i is not None and not i.startswith("utun")}
    unmeasured = sorted(p for p, i in ifaces.items() if i is None)
    if bad:
        return {"key": "tailnet-route-missing", "bad": bad, "unmeasured": unmeasured}
    if unmeasured:
        return {"key": "tailnet-route-unmeasured", "bad": {}, "unmeasured": unmeasured}
    return None


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise MonitorError(f"state {path} unreadable/corrupt: {e} — fix or move it aside") from e
    if not isinstance(state, dict):
        raise MonitorError(f"state {path} is not a JSON object")
    return state


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def plan(finding: Optional[dict], state: dict, now: datetime) -> "tuple[Optional[str], dict]":
    """-> ('alert' | 'recovered' | None, new state). Keys of resolved findings are cleared."""
    if finding is None:
        return ("recovered" if state else None), {}
    key = finding["key"]
    new_state = {k: v for k, v in state.items() if k == key}
    last = new_state.get(key)
    if last and now - datetime.fromisoformat(last) < timedelta(minutes=COOLDOWN_MIN):
        return None, new_state
    return "alert", new_state


def mark(state: dict, key: str, now: datetime) -> dict:
    s = dict(state)
    s[key] = now.isoformat()
    return s


def render(f: dict) -> "tuple[str, str]":
    bad = ", ".join(f"{p} -> {i}" for p, i in sorted(f["bad"].items()))
    um = ", ".join(f["unmeasured"]) or "none"
    if f["key"] == "tailnet-route-missing":
        subj = "[tailnet-route] Mini lost its 100.64/10 -> utun route: tailnet traffic is leaking to the LAN"
        body = (
            "TL;DR: The Mini can no longer reach any tailnet peer over IP (ssh, deploys, the "
            "cubeasht probe). Outbound internet and the substrate still look fine, so this "
            "breaks silently.\n\n"
            f"Evidence: `route -n get <peer>` resolves to a non-utun interface: {bad}. "
            f"Unmeasured: {um}.\n"
            "Not a remote problem: `tailscale ping` still answers over DERP while TCP/ICMP fail.\n\n"
            "Fix (console's pen, Mini network): restart Tailscale on the Mini, e.g. `tailscale down` "
            "then `tailscale up` with the Mini's exact non-default flags (accept-dns=false, "
            "accept-routes=false). Verify `route -n get 100.77.251.8` shows utun and `ssh gzb true` works.\n"
            "Ref: TAILNET-ROUTE-WATCH (console #79806)."
        )
    else:
        subj = "[tailnet-route] could not measure the Mini's tailnet route"
        body = (
            "TL;DR: The tailnet-route check could not measure its route probe, so the "
            "result is unknown. This is NOT a healthy reading.\n\n"
            f"Unmeasured peers: {um}. `/sbin/route -n get <ip>` failed or timed out.\n"
            "Ref: TAILNET-ROUTE-WATCH."
        )
    return subj, body


def page_priority(f: dict) -> "tuple[str, bool]":
    return ("P1", True) if f["key"] == "tailnet-route-missing" else ("P2", False)


def _send(subj: str, body: str, prio: str, req: bool) -> int:
    sys.path.insert(0, str(SCRIPTS_DIR.parent))
    from scripts.bus_send import send  # lazy: DB only on the live paging path

    row_id, _thread = send(from_agent=FROM_AGENT, to=TO_AGENT, mtype="update",
                           subject=subj, body=body, priority=prio, req=req)
    return row_id


def run(state_path: Path, now: datetime, peers: dict = PEERS, dry_run: bool = False) -> int:
    """One check. Returns 0 healthy, 1 finding. Raises on monitor failure (fail LOUD)."""
    ifaces = {p: probe(ip) for p, ip in peers.items()}
    finding = evaluate(ifaces)
    print(f"[{now.isoformat(timespec='seconds')}] tailnet-route "
          + " ".join(f"{p}={i}" for p, i in sorted(ifaces.items()))
          + f" -> {finding['key'] if finding else 'ok'}")
    state = load_state(state_path)
    action, new_state = plan(finding, state, now)
    if action == "alert":
        subj, body = render(finding)
        prio, req = page_priority(finding)
        if dry_run:
            print(f"  DRY-RUN would page {prio}{' rr' if req else ''}: {subj}")
        else:
            row = _send(subj, body, prio, req)
            print(f"  paged {TO_AGENT} #{row}")
            new_state = mark(new_state, finding["key"], now)
    elif action == "recovered":
        subj = "[tailnet-route] recovered: the Mini's tailnet traffic routes via utun again"
        body = ("TL;DR: Resolved, no action needed. Every probed peer now routes via utun: "
                + ", ".join(f"{p}={i}" for p, i in sorted(ifaces.items())) + ".")
        if dry_run:
            print(f"  DRY-RUN would send recovered note: {subj}")
        else:
            row = _send(subj, body, "P3", False)
            print(f"  recovered note #{row}")
    if not dry_run:
        save_state(state_path, new_state)
    return 1 if finding else 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="probe + print; never page or write state")
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE_FILE)
    a = ap.parse_args(argv)
    run(a.state, _now(), dry_run=a.dry_run)
    return 0  # a finding is reported by paging; non-zero is reserved for monitor failure


if __name__ == "__main__":
    sys.exit(main())
