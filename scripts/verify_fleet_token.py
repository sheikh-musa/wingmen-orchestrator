#!/usr/bin/env python3
"""verify_fleet_token.py — POST-FLIP fleet auth verifier (op#11326, CAI-785 spend remit).

Read-ONLY. After the operator arms a fleet-wide Musa->Syed token flip, this answers
the one question that matters: is EVERY live body actually on the target account, and
did NONE silently fall back to metered API billing? A silent metered fallback on a
fleet-wide flip is the worst outcome (ref lane_env_key_forces_metered_api).

TWO facts, TWO reliable sources (this matters — an earlier draft used the live-process
env token fp and it was WRONG: `claude` ROTATES its OAuth access token at runtime, so
the live env token (a refreshed, shared, changing value) NEVER equals the account
token-FILE fp the launcher stamps):

  1. ACCOUNT — read agent_status.auth_fp (the launcher stamps sha256(token-file)[:12]
     at launch; stable + file-based). ENGINEER LANES stamp it; SINGLETONS currently do
     NOT (auth_fp NULL) -> reported UNVERIFIABLE until their boots stamp it (known gap).
  2. METERED LEAK — walk the tmux pane descendants and check for the PRESENCE of an
     ANTHROPIC_API_KEY in the process env. Presence is a boolean, so token rotation /
     ps truncation cannot corrupt it (unlike an fp). Absence of a locatable process ->
     reported UNKNOWN, never a false 'clean'.

Never prints a token — only fingerprints. Mini-host only; the VPS hub + any Studio
lanes verify on their own hosts.

Usage:
  scripts/verify_fleet_token.py                      # expect the Syed token (flip target)
  scripts/verify_fleet_token.py --expect musa        # expect Musa (pre-flip baseline)
  scripts/verify_fleet_token.py --expect-file <path>
  scripts/verify_fleet_token.py --json

Exit 0 = every VERIFIABLE body on target + no metered leak; 1 = a mismatch/leak, OR any
body left UNVERIFIABLE (fail-closed — an unverifiable singleton is not a pass); 2 = setup error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

_ORCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ORCH))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(_ORCH / ".env")

TM = os.environ.get("TM", "/usr/local/bin/tmux")
if not os.path.exists(TM):
    TM = "tmux"
KEYS = Path.home() / ".wingmen" / "keys"
EMPTY_HASH = "e3b0c44298fc"  # sha256("")[:12]

# Singleton node -> tmux session. These do NOT stamp agent_status.auth_fp today.
SINGLETONS = {
    "cc-fleet-health": "fleet-health",
    "cai": "cai",
    "orch-console": "nazim",
    "cc-quality": "quality",
}


def _fp_of_token_file(path: Path) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if raw.endswith(b"\n"):
        raw = raw[:-1]  # $(cat) strips one trailing newline; match the launchers
    return hashlib.sha256(raw).hexdigest()[:12]


def _resolve_expected(args) -> tuple[str, str]:
    if args.expect_file:
        p = Path(args.expect_file).expanduser()
        fp = _fp_of_token_file(p)
        if not fp or fp == EMPTY_HASH:
            sys.exit(f"ERROR: --expect-file {p} is empty/unreadable")
        return fp, p.name
    name = "musa" if args.expect == "musa" else "syed"
    fp = _fp_of_token_file(KEYS / f"{name}-oauth-token")
    if not fp:
        sys.exit(f"ERROR: cannot read {KEYS}/{name}-oauth-token")
    return fp, f"{name}-oauth-token"


def _descendants(pid: int) -> list[int]:
    acc = [pid]
    try:
        out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True, timeout=10)
        for line in out.stdout.split():
            acc.extend(_descendants(int(line)))
    except (subprocess.SubprocessError, ValueError):
        pass
    return acc


def _metered_state(session: str) -> str | None:
    """'metered' if any descendant of the pane carries a non-empty ANTHROPIC_API_KEY,
    'clean' if we located the process tree and none did, None if no process found."""
    try:
        out = subprocess.run([TM, "display-message", "-p", "-t", session, "#{pane_pid}"],
                             capture_output=True, text=True, timeout=10)
        ppid = int(out.stdout.strip()) if out.stdout.strip() else None
    except (subprocess.SubprocessError, ValueError):
        ppid = None
    if not ppid:
        return None
    found_proc = False
    for pid in _descendants(ppid):
        try:
            env = subprocess.run(["ps", "eww", "-p", str(pid)], capture_output=True, text=True, timeout=10).stdout
        except subprocess.SubprocessError:
            continue
        found_proc = True
        for tok in env.split():
            if tok.startswith("ANTHROPIC_API_KEY=") and len(tok) > len("ANTHROPIC_API_KEY="):
                return "metered"
    return "clean" if found_proc else None


def _live_rows() -> list[dict]:
    try:
        import psycopg
    except ImportError:
        sys.exit("ERROR: psycopg unavailable (use the orchestrator .venv)")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    rows = []
    with psycopg.connect(dsn, connect_timeout=15) as c, c.cursor() as cur:
        cur.execute(
            """SELECT agent_id, tmux_session, auth_fp
               FROM agent_status
               WHERE status IN ('working','active')
                 AND (host = 'Sheikhs-Mini' OR host IS NULL)
                 AND tmux_session IS NOT NULL
               ORDER BY agent_id""")
        for agent_id, sess, fp in cur.fetchall():
            rows.append({"agent_id": agent_id, "session": sess, "auth_fp": fp})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect", choices=["syed", "musa"], default="syed")
    ap.add_argument("--expect-file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    expected_fp, expected_label = _resolve_expected(args)
    results = []
    for r in _live_rows():
        is_singleton = r["agent_id"] in SINGLETONS
        fp = r["auth_fp"]
        metered = _metered_state(r["session"])
        if fp is None:
            # No account stamp. Singletons legitimately don't stamp today -> UNVERIFIABLE.
            account = "unverifiable"
            ok = False
        else:
            account = "target" if fp == expected_fp else "OTHER"
            ok = (fp == expected_fp) and (metered != "metered")
        results.append({
            "body": r["agent_id"], "kind": "singleton" if is_singleton else "lane",
            "session": r["session"], "auth_fp": fp, "account": account,
            "metered": metered, "pass": ok,
        })

    verifiable = [r for r in results if r["auth_fp"] is not None]
    failures = [r for r in results if not r["pass"]]
    unverifiable = [r for r in results if r["auth_fp"] is None]
    overall = "PASS" if results and not failures else "FAIL"

    if args.json:
        print(json.dumps({"expected_fp": expected_fp, "expected_label": expected_label,
                          "overall": overall, "results": results}, indent=2))
    else:
        print(f"Fleet token verify — expect {expected_label} (fp {expected_fp})   host=Sheikhs-Mini")
        print(f"{'body':22}{'kind':11}{'session':16}{'auth_fp':14}{'account':14}{'metered':9}{'verdict'}")
        for r in sorted(results, key=lambda x: (x["kind"], x["body"])):
            m = "?" if r["metered"] is None else r["metered"]
            v = "PASS" if r["pass"] else ("UNVERIFIABLE" if r["auth_fp"] is None else "FAIL")
            print(f"{r['body'][:21]:22}{r['kind']:11}{(r['session'] or '')[:15]:16}"
                  f"{(r['auth_fp'] or '—'):14}{r['account']:14}{m:9}{v}")
        print(f"\nOVERALL: {overall}   "
              f"({len(verifiable)-len([r for r in failures if r['auth_fp'] is not None])}/{len(verifiable)} "
              f"verifiable on target, {len(unverifiable)} UNVERIFIABLE (no auth_fp stamp))")
        if unverifiable:
            print("  UNVERIFIABLE (singletons don't stamp auth_fp — flip fires but can't be confirmed here):",
                  ", ".join(r["body"] for r in unverifiable))
        bad = [r for r in failures if r["auth_fp"] is not None]
        if bad:
            print("  LOUD — NOT on target / metering:",
                  ", ".join(f"{r['body']}({'METERED' if r['metered']=='metered' else r['account']})" for r in bad))

    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
