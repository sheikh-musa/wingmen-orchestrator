"""safe_merge.py — fail-closed PR merge wrapper. Merge ONLY when every check on
the PR has completed and succeeded; refuse, loudly, on anything else.

WHY THIS EXISTS
---------------
2026-08-18: a lane merged PR #332 — a CLIENT-PROD change on sheikh-musa/ihsanos —
with `gh pr merge --auto`. `--auto` gates only on the repo's REQUIRED checks. But
sheikh-musa/ihsanos is PRIVATE on a free plan, so GitHub does not offer branch
protection at all (can't mark `unit-tests` required). With zero required checks,
`--auto` merged before the `unit-tests` job finished. It came out green by luck,
not design. The obvious fix (mark the check required) is unavailable on that repo,
so the gate must live in the TOOL — and work identically on a private-free repo
and a public one.

THE RULE (fail-closed)
----------------------
Enumerate ALL checks on the PR (not just required ones). Merge ONLY if EVERY check
has status COMPLETED and conclusion SUCCESS. Refuse if ANY check is:
  - still pending / in-progress / queued,
  - failed (FAILURE/CANCELLED/TIMED_OUT/ACTION_REQUIRED/STALE/STARTUP_FAILURE/ERROR),
  - completed but not SUCCESS — SKIPPED and NEUTRAL count as NOT-succeeded (a
    skipped test job is a way tests "didn't run"),
  - an unrecognised shape.
Refuse — fail-closed — on everything ambiguous: cannot enumerate checks, gh/API
error, PR not open, or ZERO checks reported (a repo with no CI must NOT read as
"all green"). There is no "the required ones passed" shortcut — that is the exact
hole this closes. Every refusal prints precisely what it saw and why.

SKIPPED-BY-NAME EXCEPTION (--allow-skipped, ruled 2026-08-18)
------------------------------------------------------------
Measured reality: 6/6 recent ihsanos PRs skip the `e2e-tests` job BY DESIGN. A
pure-strict rule would therefore refuse 100% of ihsanos merges — on the exact repo
the incident happened on — and lanes would fall straight back to `gh pr merge
--auto`, i.e. a gate that guarantees its own bypass. An unusable gate is worse than
no gate because it looks like protection.

So a SKIPPED check may be permitted, but ONLY when named explicitly:
`--allow-skipped e2e-tests` (repeatable). A named check reporting SKIPPED is
permitted and PRINTED as an allowed skip ("ALLOWING SKIPPED: e2e-tests"). ANY
other check reporting SKIPPED still refuses, loudly, naming it — so a
mis-path-filtered `unit-tests` that silently SKIPPED (the #332 failure mode in
disguise) still stops the merge. There is NO blanket `--allow-skipped`; NEUTRAL is
never covered by this flag; with no flag the default is exactly strict. The
operator names the one skip they accept, once, deliberately, and it shows in the
output every time so the waiver is reviewable.

SCOPE: a merge-safety wrapper, nothing more. It does not touch branch protection
or repo settings, and it is not a general CI tool. It does NOT use `--auto` or
wait/poll: it checks the state NOW and either merges NOW or refuses NOW.

USAGE
-----
    scripts/safe_merge.sh --repo sheikh-musa/ihsanos 332 --allow-skipped e2e-tests
    python -m scripts.lib.safe_merge --repo o/r 332 --method merge
Exit 0 = merged; non-zero = refused (nothing merged) or the merge command failed.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Iterable

if __package__ in (None, ""):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

PASS, PENDING, FAIL, PERMITTED_SKIP = "pass", "pending", "fail", "permitted_skip"

# CheckRun conclusions that are NOT a pass and are NOT skipped. SUCCESS is the only
# pass; SKIPPED is handled separately (may be permitted by name); NEUTRAL and
# everything below fail closed and are never covered by --allow-skipped.
_NON_SUCCESS_CONCLUSIONS = {
    "FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STALE",
    "STARTUP_FAILURE", "NEUTRAL",
}


@dataclass
class MergeDecision:
    ok: bool
    reason: str
    passed: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    permitted_skips: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"{'MERGE-OK' if self.ok else 'REFUSE'}: {self.reason}"]
        # Permitted skips are printed FIRST and always — a waiver nobody can see is
        # a waiver nobody reviews.
        for it in self.permitted_skips:
            lines.append(f"  [ALLOWING SKIPPED] {it}")
        for label, items in (("PASSED", self.passed), ("PENDING", self.pending), ("FAILED/OTHER", self.failed)):
            for it in items:
                lines.append(f"  [{label}] {it}")
        return "\n".join(lines)


def classify_check(check: dict, allow_skipped: Iterable[str] = ()) -> tuple[str, str]:
    """PURE. Return (verdict, human label) for ONE statusCheckRollup entry.

    verdict in {pass, pending, fail, permitted_skip}. Anything not clearly pass,
    pending, or an explicitly-permitted skip is fail (fail-closed) — including
    unrecognised shapes. `allow_skipped` is the set of check names permitted to
    report SKIPPED (only SKIPPED — never NEUTRAL or a failure).
    """
    allow = {str(n) for n in allow_skipped}
    if not isinstance(check, dict):
        return FAIL, f"UNRECOGNISED check (not an object): {check!r}"
    typ = check.get("__typename") or ""
    name = check.get("name") or check.get("context") or "<unnamed>"

    # GitHub Actions / check-run: has status + conclusion.
    if typ == "CheckRun" or ("status" in check and "conclusion" in check):
        status = (check.get("status") or "").upper()
        concl = (check.get("conclusion") or "").upper()
        if status != "COMPLETED":
            return PENDING, f"{name}: {status or 'NO_STATUS'} (not completed)"
        if concl == "SUCCESS":
            return PASS, f"{name}: SUCCESS"
        if concl == "SKIPPED":
            if name in allow:
                return PERMITTED_SKIP, f"{name}: SKIPPED (explicitly permitted via --allow-skipped)"
            return FAIL, (f"{name}: SKIPPED (not permitted — a skipped check has not run; "
                          f"name it in --allow-skipped only if it skips by design)")
        if concl in _NON_SUCCESS_CONCLUSIONS:
            return FAIL, f"{name}: {concl} (completed, not SUCCESS)"
        return FAIL, f"{name}: {concl or 'NO_CONCLUSION'} (completed, not SUCCESS — treated as fail)"

    # Legacy commit-status context: has state (no SKIPPED concept).
    if typ == "StatusContext" or "state" in check:
        state = (check.get("state") or "").upper()
        if state == "SUCCESS":
            return PASS, f"{name}: SUCCESS"
        if state in ("PENDING", "EXPECTED"):
            return PENDING, f"{name}: {state}"
        return FAIL, f"{name}: {state or 'NO_STATE'} (not SUCCESS)"

    return FAIL, f"{name}: UNRECOGNISED check shape (__typename={typ or 'none'})"


def evaluate_checks(checks, allow_skipped: Iterable[str] = ()) -> MergeDecision:
    """PURE. Decide whether the full rollup permits a merge. Fail-closed.

    A merge is permitted iff no check is pending and no check failed. Explicitly
    permitted skips (via `allow_skipped`) do not block, but are surfaced.
    """
    if not isinstance(checks, list):
        return MergeDecision(False, f"checks rollup is not a list ({type(checks).__name__}) — fail-closed")
    if len(checks) == 0:
        return MergeDecision(
            False,
            "ZERO checks reported — a PR/repo with no CI must not read as 'all green' "
            "(fail-closed). If this repo genuinely has no CI, merge by hand deliberately.",
        )
    passed, pending, failed, permitted = [], [], [], []
    for c in checks:
        verdict, label = classify_check(c, allow_skipped)
        {PASS: passed, PENDING: pending, FAIL: failed, PERMITTED_SKIP: permitted}[verdict].append(label)

    if pending or failed:
        bits = []
        if pending:
            bits.append(f"{len(pending)} still pending")
        if failed:
            bits.append(f"{len(failed)} failed/not-succeeded")
        return MergeDecision(
            False,
            f"NOT all checks completed+succeeded ({', '.join(bits)}; {len(passed)} passed"
            + (f", {len(permitted)} permitted-skip" if permitted else "") + ").",
            passed, pending, failed, permitted,
        )
    tail = f" ({len(permitted)} skip permitted by name)" if permitted else ""
    return MergeDecision(True, f"all {len(passed)} checks completed and succeeded{tail}.",
                         passed, [], [], permitted)


# ── gh plumbing (injectable for tests) ───────────────────────────────────────

def _gh_fetch(repo: str, pr: int) -> dict:
    """Fetch the PR state + FULL check rollup via gh. Raises on any error."""
    out = subprocess.run(
        ["gh", "pr", "view", str(pr), "--repo", repo, "--json", "state,statusCheckRollup,number"],
        capture_output=True, text=True, timeout=60,
    )
    if out.returncode != 0:
        raise RuntimeError(f"gh pr view failed (rc={out.returncode}): {out.stderr.strip() or out.stdout.strip()}")
    return json.loads(out.stdout)


def _gh_merge(repo: str, pr: int, method: str) -> int:
    """Perform the merge (NO --auto). Returns gh's exit code."""
    flag = {"squash": "--squash", "merge": "--merge", "rebase": "--rebase"}[method]
    out = subprocess.run(
        ["gh", "pr", "merge", str(pr), "--repo", repo, flag],
        capture_output=True, text=True, timeout=120,
    )
    if out.stdout.strip():
        print(out.stdout.strip())
    if out.returncode != 0 and out.stderr.strip():
        print(out.stderr.strip())
    return out.returncode


def safe_merge(
    repo: str,
    pr: int,
    *,
    method: str = "squash",
    allow_skipped: Iterable[str] = (),
    fetch: Callable[[str, int], dict] | None = None,
    do_merge: Callable[[str, int, str], int] | None = None,
) -> MergeDecision:
    """THE WRAPPER. Fetch checks, decide fail-closed, merge only if all green."""
    fetch = fetch or _gh_fetch
    do_merge = do_merge or _gh_merge

    try:
        info = fetch(repo, pr)
    except Exception as e:  # cannot enumerate -> refuse, never assume green
        return MergeDecision(False, f"could not enumerate checks for {repo}#{pr} "
                                    f"({type(e).__name__}: {e}) — fail-closed")
    if not isinstance(info, dict):
        return MergeDecision(False, f"unexpected gh payload for {repo}#{pr} — fail-closed")

    state = (info.get("state") or "").upper()
    if state != "OPEN":
        return MergeDecision(False, f"{repo}#{pr} is not OPEN (state={state or 'UNKNOWN'}) — nothing to merge.")

    decision = evaluate_checks(info.get("statusCheckRollup"), allow_skipped)
    if not decision.ok:
        return decision  # refusal; nothing merged

    rc = do_merge(repo, pr, method)
    if rc != 0:
        return MergeDecision(False, f"checks were green but the merge command failed (rc={rc}).",
                             decision.passed, [], [], decision.permitted_skips)
    return MergeDecision(True, f"{repo}#{pr} merged ({method}) — {decision.reason}",
                         decision.passed, [], [], decision.permitted_skips)


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Merge a PR ONLY when every check has completed and succeeded (fail-closed; "
                    "works where branch protection is unavailable). Does not use --auto."
    )
    ap.add_argument("pr", type=int, help="PR number")
    ap.add_argument("--repo", required=True, help="OWNER/REPO")
    ap.add_argument("--method", choices=["squash", "merge", "rebase"], default="squash")
    ap.add_argument("--allow-skipped", action="append", default=[], metavar="CHECK_NAME",
                    help="a check name permitted to report SKIPPED (repeatable). Any OTHER "
                         "skipped check still refuses; NEUTRAL is never covered.")
    a = ap.parse_args(argv)

    decision = safe_merge(a.repo, a.pr, method=a.method, allow_skipped=a.allow_skipped)
    print(decision.render())
    return 0 if decision.ok else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
