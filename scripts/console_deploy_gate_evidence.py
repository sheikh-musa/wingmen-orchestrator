#!/usr/bin/env python3
"""console_deploy_gate_evidence.py <content-hash> <deploy-dir>

Builds the evidence dict for nervous_system/quality_gate.py's shadow evaluation of a
fleet-console deploy (bus #43108 proposal, GO'd bus #43109 2026-09-24, condition #1:
map evidence to what the `deploy-prod` manifest class ACTUALLY requires; a field
deploy_console.sh cannot attest to is left OUT of "checks" so quality_gate.py scores
it UNPROVEN — never fabricated as "pass"). See scripts/console_deploy_quality_gate_shadow.sh
for the wrapper that calls this (and that deploy_console.sh actually invokes).

Reads ONLY deploy_console.sh's own artifacts for this content hash (pytest.log, the
rendered PNGs, cc-quality-review.md) plus read-only git state of the current directory
(deploy_console.sh cd's into the live checkout before calling this). Prints the evidence
dict as JSON to stdout. Never raises past main(): any error goes to stderr and a valid
(if empty) evidence dict is still printed, so a pipe into quality_gate.py always gets
parseable JSON.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

DEPLOY_TRUNK = "origin/fable/substrate-safe-fixes"


def _git(*args: str) -> Optional[str]:
    try:
        out = subprocess.run(["git", *args], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def _is_ancestor(rev_a: str, rev_b: str) -> bool:
    try:
        return subprocess.run(
            ["git", "merge-base", "--is-ancestor", rev_a, rev_b],
            capture_output=True, timeout=10,
        ).returncode == 0
    except Exception:
        return False


def build_evidence(content_hash: str, deploy_dir: Path) -> dict:
    checks: dict = {}

    # G1 (CI green): only this gate's own unit-test slice actually runs here —
    # typecheck/lint/boundaries/e2e-tests are NOT run by deploy_console.sh, so they
    # stay absent (quality_gate.py scores a missing deterministic check UNPROVEN).
    pytest_log = deploy_dir / "pytest.log"
    if pytest_log.is_file():
        text = pytest_log.read_text(errors="replace")
        # orch-console review (#45683): a missing/truncated/collection-error log has
        # neither "FAILED" nor " failed" in it either, so the old "fail only if a
        # failure marker is present" logic scored an EMPTY log "pass" — require the
        # positive "N passed" signal too, not just the absence of a negative one.
        ran_ok = re.search(r"\b\d+\s+passed\b", text) is not None
        blew_up = "failed" in text.lower() or "error" in text.lower()
        checks["unit-tests"] = "pass" if (ran_ok and not blew_up) else "fail"

    # G3 (mobile + desktop eyeball): render_console_pages.sh captures fleet.png +
    # lanes.png via Playwright "iPhone 13" emulation (390 CSS px) — a real 390px
    # capture, honest to mark. There is no 1440 (desktop) capture and no
    # console-error capture in the render step, so both stay absent.
    fleet_png = deploy_dir / "fleet.png"
    lanes_png = deploy_dir / "lanes.png"
    if fleet_png.is_file() and lanes_png.is_file():
        checks["screenshot-manifest-390"] = (
            "pass" if fleet_png.stat().st_size > 0 and lanes_png.stat().st_size > 0 else "fail"
        )

    # G7 (deployed == GitHub): deploy_console.sh ships whatever is checked out
    # locally via `launchctl kickstart`, not a tagged-SHA promotion with server
    # provenance headers — deploy-provenance-verified stays absent. Whether the
    # local tree actually IS the deploy trunk tip, uncommitted-free, is genuinely
    # checkable though (this repo's deployed trunk is fable/substrate-safe-fixes,
    # not "main" — see reference_fable_is_deployed_trunk_land_durability_not_main).
    head = _git("rev-parse", "HEAD")
    trunk = _git("rev-parse", DEPLOY_TRUNK)
    status = _git("status", "--porcelain")
    dirty = bool(status)
    if head and trunk:
        checks["ancestor-of-origin-main"] = "pass" if _is_ancestor(head, trunk) else "fail"
        checks["deploy-sha-equals-main"] = "pass" if (head == trunk and not dirty) else "fail"

    # G10 (reproducible + tracked).
    if status is not None:
        checks["committed-on-branch"] = "pass" if not dirty else "fail"
    review = deploy_dir / "cc-quality-review.md"
    checks["evidence-bundle-present"] = (
        "pass" if review.is_file() and review.stat().st_size > 0 else "fail"
    )

    # G5 (scoping/security) mandatory review arm: the cc-quality-review.md gate 4
    # requires is a general design/quality review of the diff, not a DEDICATED
    # security review of secrets/PII/residency (no-secret-leak, no-pii-leak,
    # deny-by-default-preserved, residency-verified are not scanned at all here
    # either, so they stay absent too) — mark the reviewer arm honestly absent,
    # not True. This is the headline gap shadow mode exists to surface for
    # deploy-prod.
    reviews = {"G5": False}

    return {"sha": content_hash, "checks": checks, "reviews": reviews}


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        print("usage: console_deploy_gate_evidence.py <content-hash> <deploy-dir>", file=sys.stderr)
        print(json.dumps({"sha": "", "checks": {}, "reviews": {}}))
        return 0
    content_hash, deploy_dir = argv[0], Path(argv[1])
    try:
        evidence = build_evidence(content_hash, deploy_dir)
    except Exception as exc:  # this must never crash the caller's pipe
        print(f"console_deploy_gate_evidence: {exc}", file=sys.stderr)
        evidence = {"sha": content_hash, "checks": {}, "reviews": {}}
    print(json.dumps(evidence))
    return 0


if __name__ == "__main__":
    sys.exit(main())
