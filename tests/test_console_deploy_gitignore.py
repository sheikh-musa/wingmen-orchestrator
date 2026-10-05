"""The console-deploy gate's generated per-hash evidence (renders' raw data, the shadow
quality-gate verdict, render/test logs) is transient build output, not reviewable content --
it showed up as untracked dirt in the live checkout after every deploy (orch-console
#52093), including feeding the shadow quality-gate's G7/G10 false positives on uncommitted
files. These must be gitignored. cc-quality-review.md is the ACTUAL reviewable record the
gate exists to produce and must stay trackable (never ignored), or the op#12457 gate itself
would have nothing to commit.
"""
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

_IGNORED_BASENAMES = [
    "_fleet.json",
    "_tt.json",
    "_fleet-harness.html",
    "_lanes-harness.html",
    "_fleet-governance-harness.html",
    "quality-gate-verdict.json",
    "quality-gate.err",
    "pytest.log",
    "render.log",
    "fleet.png",
]


def _is_ignored(rel_path: str) -> bool:
    r = subprocess.run(
        ["git", "check-ignore", "-q", rel_path], cwd=str(_ROOT), capture_output=True
    )
    return r.returncode == 0


def test_generated_evidence_basenames_are_gitignored():
    sample_hash = "0123456789abcdef"
    for name in _IGNORED_BASENAMES:
        rel = f"reports/console-deploy/{sample_hash}/{name}"
        assert _is_ignored(rel), f"{rel} must be gitignored (transient build artifact)"


def test_cc_quality_review_is_never_gitignored():
    sample_hash = "0123456789abcdef"
    rel = f"reports/console-deploy/{sample_hash}/cc-quality-review.md"
    assert not _is_ignored(rel), (
        "cc-quality-review.md must stay trackable -- it is the actual reviewable record "
        "the op#12457 gate exists to produce, not transient build output"
    )


def test_deploy_log_is_never_gitignored():
    # The top-level append-only deploy history, not per-hash -- a deliberate record.
    rel = "reports/console-deploy/deploy-log.txt"
    assert not _is_ignored(rel), "deploy-log.txt is a deliberate tracked history, not dirt"
