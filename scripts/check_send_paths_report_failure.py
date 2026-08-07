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
# DISCOVERED, NOT ENUMERATED. The first version of this file carried a hardcoded list of six
# wrappers — and MISSED tg_send_file.sh and irsyad_support_send_file.sh, the operator and CLIENT
# file-send paths, both carrying the identical defect. cc-orchestrator found them by sweeping the
# tree instead of inheriting my list (4c79f3e). The count went 3 -> 4 -> 6, each step someone
# refusing to inherit the previous one's scope.
# A checker whose coverage is a literal is a checker that certifies exactly what its author
# happened to remember. So: find every script that writes to the durable message log, and hold
# ALL of them to the rule.
# The rule applies to scripts that SEND *and then* LOG. A script that only writes to the log
# (log_console_msg, the boot/reset wrappers recording an event) has no send to branch on, and
# flagging it would make this checker cry wolf on 11 scripts — which is how a checker gets
# ignored, and then the real one hides among the noise. Precision here IS the safety property.
_SENDS = ("_tg_chunked_send", "api.telegram.org", "sendMessage", "sendDocument", "sendPhoto")


def discover_wrappers() -> list[pathlib.Path]:
    out = []
    for p in sorted((ORCH / "scripts").glob("*.sh")):
        src = p.read_text(errors="ignore")
        logs = "operator_log" in src or "INSERT INTO operator_messages" in src.upper()
        sends = any(m in src for m in _SENDS)
        if logs and sends:
            out.append(p)
    return out


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
    wrappers = discover_wrappers()
    for p in wrappers:
        name = p.name
        src = p.read_text(errors="ignore")
        logs = "operator_log" in src or "INSERT INTO operator_messages" in src.upper()
        if not logs:
            continue
        # A logger is guarded three ways, and all three are legitimate:
        #   1. it passes --undelivered on the failure path;
        #   2. it sets `delivered` explicitly in a direct INSERT;
        #   3. it EXITS on send failure BEFORE reaching the log — control flow, not a flag.
        # (3) matters: nazim_say.sh does exactly that, and demanding a redundant --undelivered
        # branch there would have someone "fix" correct code. A checker that cannot tell a real
        # defect from a different correct shape trains people to ignore it.
        log_pos = src.find("operator_log")
        exits_first = bool(re.search(r"\|\|\s*\{[^}]*exit 1", src[:log_pos])) if log_pos > 0 else False
        guarded = (("--undelivered" in src)
                   or re.search(r"delivered\s*[,)]|%s\)?\s*,\s*delivered", src)
                   or exits_first)
        if not guarded:
            problems.append(f"{name}: logs to operator_log with NO failure branch — a failed send "
                            f"will record delivered=true")
    return (not problems), problems, [p.name for p in wrappers]


def main() -> int:
    ok_b, detail = behavioural_check()
    print(f"[behavioural] {'PASS' if ok_b else 'FAIL'} — {detail}")
    ok_s, problems, names = structural_check()
    print(f"[structural ] {'PASS' if ok_s else 'FAIL'} — {len(names)} log-writing scripts DISCOVERED: {', '.join(names)}")
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
