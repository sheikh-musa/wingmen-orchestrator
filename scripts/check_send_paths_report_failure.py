#!/usr/bin/env python3
"""check_send_paths_report_failure.py — can every operator-facing send path REPORT ITS OWN FAILURE?

WHY THIS EXISTS (2026-07-26). `operator_log` defaults delivered=TRUE, and four send wrappers called
it unconditionally — so a FAILED send was written down as delivered, on every channel, for the life
of the log. It surfaced only because a real client asked the same question three times while our log
showed every reply delivered.

cc-orchestrator fixed three wrappers (25701aa). cai filed the incident as "all three send scripts",
taking that scope from the report without checking it. There was a FOURTH — nazim_send.sh, the
console's own voice — and it was the very instrument cai had ruled should test the channel. Had it
not been fixed minutes before that test, a failed receipt check would have logged as delivered and
we would have concluded the operator was ignoring us: a false conclusion about a person, drawn from
a green light.

cai's own words: "the system caught it, but by diligence, not by design." This is the design half.

WHAT IT CHECKS
  1. BEHAVIOURAL (fault injection): drive the shared chunked sender with a deliberately invalid
     token and assert it exits NON-ZERO. If failure is not detectable at the chokepoint, nothing
     downstream can record it honestly.
  2. STRUCTURAL: assert every wrapper that logs to operator_log guards that call on the send result
     — i.e. passes --undelivered on the failure path. This half is INSPECTION, not behaviour, and
     is labelled as such: it proves the branch exists, not that it fires. It is here because it is
     the check that would have caught nazim_send.sh, and a weaker check that catches the real bug
     beats a stronger one nobody runs.

Exit 0 = every path can report its own failure. Exit 1 = at least one cannot.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ORCH = pathlib.Path(__file__).resolve().parent.parent
SENDER = ORCH / "scripts" / "_tg_chunked_send.py"
WRAPPERS = ["tg_send.sh", "cai_send.sh", "irsyad_support_send.sh", "nazim_send.sh",
            "dev_group_send.sh", "lane_reply.sh"]


def behavioural_check() -> tuple[bool, str]:
    """Fault-inject the chokepoint: an invalid token must produce a non-zero exit."""
    env = {"TG_TOK": "0000000000:INVALID_TOKEN_FOR_FAULT_INJECTION",
           "TG_CHAT": "286619815",
           "TG_TEXT": "fault-injection probe — this send is EXPECTED to fail and must not be logged as delivered",
           "TG_ALLOW_DUPLICATE": "1",
           "PATH": "/usr/bin:/bin"}
    r = subprocess.run([str(ORCH / ".venv/bin/python3"), str(SENDER)],
                       env=env, capture_output=True, text=True, timeout=90)
    if r.returncode == 0:
        return False, "chunked sender returned 0 for an INVALID token — failure is undetectable"
    return True, f"chunked sender correctly exits {r.returncode} on an invalid token"


def structural_check() -> tuple[bool, list[str]]:
    """Every wrapper that logs must branch on the send result. Inspection, not behaviour."""
    problems = []
    for name in WRAPPERS:
        p = ORCH / "scripts" / name
        if not p.exists():
            continue
        src = p.read_text()
        logs = "operator_log" in src or "INSERT INTO operator_messages" in src.upper()
        if not logs:
            continue
        # a guarded logger either passes --undelivered somewhere, or sets delivered explicitly
        guarded = ("--undelivered" in src) or re.search(r"delivered\s*[,)]|%s\)?\s*,\s*delivered", src)
        if not guarded:
            problems.append(f"{name}: logs to operator_log with NO failure branch — a failed send "
                            f"will record delivered=true")
    return (not problems), problems


def main() -> int:
    ok_b, detail = behavioural_check()
    print(f"[behavioural] {'PASS' if ok_b else 'FAIL'} — {detail}")
    ok_s, problems = structural_check()
    print(f"[structural ] {'PASS' if ok_s else 'FAIL'} — {len(WRAPPERS)} wrappers inspected")
    for p in problems:
        print(f"    {p}")
    if ok_b and ok_s:
        print("\nEvery operator-facing send path can report its own failure.")
        return 0
    print("\nAt least one send path cannot report its own failure. `delivered` is decoration "
          "until this passes.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
