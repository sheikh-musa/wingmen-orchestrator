"""merge_gate — the pure green-CI decision behind scripts/safe_merge.sh (orch-console #48411).

Given a PR's checks (from `gh pr checks <PR> --json name,state`) decide whether it is
safe to merge: EVERY check must be SUCCESS (or SKIPPED only when explicitly allowed).
Refuses fail-closed on pending / failure / neutral / cancelled / unknown / zero checks —
so a merge can never race an in_progress or red build (the 2026-10-01 PR#243 hole).

CLI: reads the checks JSON on stdin, `--allow-skipped <name>` may repeat; prints the
decision and exits 0 (safe) or 1 (refuse). Importable too: evaluate_checks(checks, allow).
"""
import json
import sys


def evaluate_checks(checks, allow_skipped=None):
    """Return (ok: bool, reason: str). Only SUCCESS passes; SKIPPED passes iff allowed."""
    allow_skipped = set(allow_skipped or [])
    if not checks:
        return False, "REFUSE: no checks found (fail-closed — a PR with zero checks is not provably green)"
    bad = []
    for c in checks:
        name = c.get("name") or c.get("workflow") or c.get("context") or "?"
        state = (c.get("state") or c.get("conclusion") or c.get("status") or "").upper()
        if state == "SUCCESS":
            continue
        if state == "SKIPPED":
            if name in allow_skipped:
                continue
            bad.append("%s=SKIPPED(not allowed)" % name)
            continue
        # PENDING / IN_PROGRESS / QUEUED / FAILURE / NEUTRAL / CANCELLED / ERROR / "" -> refuse
        bad.append("%s=%s" % (name, state or "UNKNOWN"))
    if bad:
        return False, "REFUSE: not all checks green: " + ", ".join(bad)
    return True, "OK: all %d checks green" % len(checks)


def _main(argv):
    allow = []
    i = 0
    while i < len(argv):
        if argv[i] == "--allow-skipped" and i + 1 < len(argv):
            allow.append(argv[i + 1]); i += 2
        else:
            i += 1
    try:
        data = json.load(sys.stdin)
    except Exception as e:
        print("REFUSE: could not parse checks JSON (%s)" % e); return 1
    checks = data.get("checks", data) if isinstance(data, dict) else data
    ok, reason = evaluate_checks(checks, allow)
    print(reason)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
