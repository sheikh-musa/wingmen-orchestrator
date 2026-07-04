#!/usr/bin/env python3
"""irsyad_residency_purge.py — GUARDED runner for the irreversible irsyad
residency PII purge (permanent delete of ~2763 stale pre-silo irsyad rows from
the ihsanos multi-tenant DB `ceayjeamtmcyzzvqflus`).

This script exists to make the 2026-07-03 near-miss STRUCTURALLY impossible to
repeat: the purge cannot run past the enforced authorization gate, and even a
passing gate is not enough — a deliberate operator+cai UNFREEZE sentinel must
also be present. Both are fail-closed.

Execution pre-conditions (ALL required):
  1. GATE: verified_authorization(...) returns ok — a BRIDGE-VERIFIED operator
     "YES PURGE" (inbound telegram, operator's real chat, references the op,
     created AFTER --request-ts). An in-console/tmux YES is NEVER sufficient.
  2. UNFREEZE SENTINEL: the file `.irsyad_purge_UNFROZEN` exists at the repo root
     (created deliberately by operator+cai when lifting the freeze — NOT by any
     agent as a side effect).
  3. Explicit `--execute` flag.

Default behaviour is a DRY authorization check: it reports whether a valid
authorization artifact exists and does NOT delete anything.

NOTE (2026-07-03): the purge is intentionally DOUBLE-FROZEN. This runner is the
plumbing/gate — it does not carry a live DELETE. The exact DELETE predicate is
owned by operator+cai and must be filled into `_execute_purge()` at unfreeze
time (see docs/session-checkpoint-2026-07-03.md). Do NOT unfreeze here.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

ORCH = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ORCH))

from scripts.lib.require_verified_authorization import verified_authorization  # noqa: E402

OP_ID = "irsyad-residency-purge"
APPROVAL_PHRASES = ["YES PURGE"]
# The op-identifying token must be the DISTINGUISHING SUBJECT ("irsyad"), NOT the
# verb ("purge") — otherwise the approval phrase would trivially self-satisfy the
# "references the specific op" requirement and a "YES PURGE the cosem cache" YES
# would wrongly authorize the irsyad delete.
OP_TOKENS = ["irsyad"]
UNFREEZE_SENTINEL = ORCH / ".irsyad_purge_UNFROZEN"


def authorized_to_purge(request_ts: str):
    """The enforced gate call. Returns an AuthResult (ok, reason, row)."""
    return verified_authorization(
        OP_ID,
        after=request_ts,
        approval_phrases=APPROVAL_PHRASES,
        op_tokens=OP_TOKENS,
    )


def _execute_purge(auth_row: dict) -> int:
    """The irreversible DELETE. Deliberately NOT implemented while frozen.

    Only ever reached after: gate passed + UNFREEZE sentinel present + --execute.
    The exact DELETE (table, predicate, batch size) is an operator+cai decision
    at unfreeze time — filling it in is the act of unfreezing and MUST be paired
    with a fresh bridge-verified authorization. Left as a hard stop on purpose."""
    raise NotImplementedError(
        f"purge body intentionally absent while frozen; authorized by "
        f"operator_messages id={auth_row.get('id')} — fill in the DELETE only at a "
        f"deliberate operator+cai unfreeze")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Guarded irsyad residency PII purge (fail-closed).")
    ap.add_argument("--request-ts", required=True,
                    help="ISO-8601 time the purge was REQUESTED; the operator YES must be newer")
    ap.add_argument("--execute", action="store_true",
                    help="attempt the irreversible delete (still requires gate + unfreeze sentinel)")
    a = ap.parse_args(argv)

    res = authorized_to_purge(a.request_ts)
    if not res.ok:
        print(f"REFUSED — {res.reason}", file=sys.stderr)
        return 3  # fail-closed: no verified authorization

    print(f"AUTHORIZATION VALID — {res.reason}")
    if not a.execute:
        print("dry run (no --execute): authorization present but nothing deleted.")
        return 0

    if not UNFREEZE_SENTINEL.exists():
        print(f"REFUSED — purge is FROZEN: unfreeze sentinel {UNFREEZE_SENTINEL} absent. "
              f"Lifting the freeze is a deliberate operator+cai act.", file=sys.stderr)
        return 4  # fail-closed: frozen

    return _execute_purge(res.row)


if __name__ == "__main__":
    sys.exit(main())
