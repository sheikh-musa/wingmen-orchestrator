"""The pre-push console gate must hash the SAME content the deploy gate reviews.

WHY THIS EXISTS (substrate audit 2026-09-05 #2). scripts/git-hooks/pre-push is the
belt to deploy_console.sh's op#12457 gate: it blocks pushing console content that
has no cc-quality review for THAT exact content. Both sides key the review on a
content hash — but they computed DIFFERENT hashes. The gate uses the SSOT manifest
(scripts/lib/console_deploy_manifest.sh :: console_content_hash), which covers ~31
files (all served static, every backend *.py, the Dockerfile). The hook hand-rolled
a shasum over ONLY five static files. So the hook's hash could NEVER equal the gate's:
its review path reports/console-deploy/<hook_hash>/cc-quality-review.md pointed at a
directory the gate never creates, so the hook blocked every push and was routinely
bypassed with --no-verify — the belt was dead. It also inspected only HEAD~1..HEAD
(one commit) and only the static dir, missing multi-commit pushes and backend edits.

The fix makes the hook SOURCE the manifest and use console_content_hash — so its hash
equals the gate's by construction — and read the real pushed range from stdin. These
tests are the dead-man's-switch: if the hook ever re-hand-rolls a hash or drops the
manifest, they go RED. Prod-clean: subprocess + bash only, no DB.
"""
import re
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HOOK = _ROOT / "scripts" / "git-hooks" / "pre-push"
_MANIFEST = _ROOT / "scripts" / "lib" / "console_deploy_manifest.sh"


def _hook_text() -> str:
    return _HOOK.read_text()


def _code_only(text: str) -> str:
    """Comment lines blanked (length-preserving) so a mention inside the rationale
    header does not satisfy a code assertion."""
    return "\n".join("" if ln.lstrip().startswith("#") else ln for ln in text.splitlines())


def test_hook_and_manifest_exist():
    assert _HOOK.is_file(), f"missing {_HOOK}"
    assert _MANIFEST.is_file(), f"missing {_MANIFEST} (the SSOT the hook must reuse)"


def test_hook_sources_the_manifest_ssot():
    code = _code_only(_hook_text())
    assert "console_deploy_manifest.sh" in code, (
        "pre-push must SOURCE the SSOT manifest, not hand-roll a hash"
    )
    assert "console_content_hash" in code, (
        "pre-push must compute its content hash via console_content_hash so it EQUALS "
        "the deploy gate's hash by construction"
    )


def test_hook_no_longer_hand_rolls_a_shasum():
    # Regression guard for the exact bug: the old hook piped `cat <5 files> | shasum`.
    # Hashing now lives entirely in the manifest, so the hook must not call shasum itself.
    code = _code_only(_hook_text())
    assert "shasum" not in code, (
        "pre-push still hand-rolls a shasum — that is the 5-file hash that could never "
        "match the gate. Delegate hashing to console_content_hash."
    )
    # And it must not enumerate the old five static files for hashing.
    assert not re.search(r"fleet\.html.*fleet\.js", code, re.DOTALL) or "console_content_hash" in code


def test_hook_reads_pushed_range_from_stdin_not_head_1():
    code = _code_only(_hook_text())
    assert "HEAD~1 HEAD" not in code, (
        "pre-push still inspects only HEAD~1..HEAD — it misses multi-commit pushes. "
        "Read the pushed range (local_sha/remote_sha) from stdin."
    )
    assert re.search(r"\bread\b.*(sha|ref)", code, re.IGNORECASE) or "while read" in code, (
        "pre-push must read the pushed refs/shas from stdin (git streams "
        "'<lref> <lsha> <rref> <rsha>' per ref)"
    )


def _sh(func_and_args: str):
    script = f'set -euo pipefail; source "{_MANIFEST}"; {func_and_args}'
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd=str(_ROOT))


def test_hook_hash_equals_gate_content_hash_for_the_same_tree():
    # The value the deploy gate keys its review on (console_content_hash of this tree).
    r = _sh('console_content_hash "$PWD"')
    assert r.returncode == 0, f"console_content_hash failed: {r.stderr}"
    gate_hash = r.stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{16}", gate_hash), f"not a 16-hex content hash: {gate_hash!r}"
    # The hook computes its hash the SAME way (sourced console_content_hash) and builds the
    # review path from it — so hook_hash == gate_hash by construction. Assert that wiring.
    code = _code_only(_hook_text())
    assert re.search(r"console_content_hash\b", code)
    assert "console-deploy/" in code and "cc-quality-review.md" in code, (
        "the hook must look for the review at reports/console-deploy/<hash>/cc-quality-review.md "
        "(the same path deploy_console.sh writes)"
    )


def test_hook_parses_clean():
    r = subprocess.run(["bash", "-n", str(_HOOK)], capture_output=True, text=True)
    assert r.returncode == 0, f"pre-push has a syntax error: {r.stderr}"


def test_blocked_message_prints_absolute_review_path():
    # orch-console #52089/#52091/#52093: a bare "save the review to
    # reports/console-deploy/<hash>/..." left it ambiguous WHICH checkout, so a reviewer
    # following the message from a worktree saved it into the live checkout instead -- an
    # untracked file that collided with the PR's own tracked copy on the next merge+pull.
    # The message must print the path PREFIXED with $ROOT (git rev-parse --show-toplevel,
    # i.e. THIS checkout/worktree), never the bare relative form alone.
    code = _code_only(_hook_text())
    assert "$ROOT/$REVIEW" in code, (
        "the blocked-message must print an ABSOLUTE review path ($ROOT/$REVIEW), not a "
        "bare relative one -- see orch-console #52089 (a worktree-based save landed in "
        "the live checkout by mistake because the old message didn't say which checkout)"
    )
    # And it must warn that deploy_console.sh itself always targets the live checkout --
    # the OTHER half of the same bug class (its own remediation suggestion was misleading
    # from a worktree).
    assert "always operates on" in code and "live checkout" in code, (
        "the blocked-message must warn that scripts/deploy_console.sh always operates on "
        "the live checkout, so running it from a worktree does not help"
    )
