"""The deploy_console gate must review the console BACKEND, not just its static files.

WHY THIS EXISTS (item-4b, Nazim #31843). deploy_console.sh's whole purpose (op#12457) is that
a console build cannot ship without a cc-quality review of *exactly what is shipping* — the
review is keyed to a content-hash so a stale review of a different diff can't slip through.
But the hash was computed over ONLY the five STATIC frontend files
(fleet/lanes .html/.js + sw.js). The console is actually run as `python -m nervous_system.console`
— the entire backend package (app.py 162KB, db.py, auth.py, panes.py, hosted_view.py, ...). So a
BACKEND-ONLY change left the hash UNCHANGED, the old review still "matched", and the backend
shipped UNREVIEWED. That is the exact blindspot this closes.

The fix folds every *.py under nervous_system/console/ (RECURSIVE + sorted, so a future
subpackage module can't be a sub-blindspot; __pycache__ excluded) into the SAME content-hash via
an SSOT seam (scripts/lib/console_deploy_manifest.sh). These tests are the dead-man's-switch: if
a backend edit ever stops changing the hash, they go RED.

COVERAGE BOUNDARY (documented, per Nazim #31843): the hash covers the console PACKAGE. Shared
libs imported from outside it (e.g. scripts/lib) are NOT gated — a bounded cut, widen only if a
console-serving shared module becomes material.
"""
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_GATE = _ROOT / "scripts" / "deploy_console.sh"
_MANIFEST = _ROOT / "scripts" / "lib" / "console_deploy_manifest.sh"

_STATIC_REL = [
    "nervous_system/console/static/fleet.html",
    "nervous_system/console/static/fleet.js",
    "nervous_system/console/static/lanes.html",
    "nervous_system/console/static/lanes.js",
    "nervous_system/console/static/sw.js",
]


def _sh(func_and_args, root):
    """Source the manifest seam and invoke one of its functions against <root>."""
    script = f'set -euo pipefail; source "{_MANIFEST}"; {func_and_args}'
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd=str(root))


def _files(root):
    out = _sh(f'console_deploy_files_rel "{root}"', root)
    assert out.returncode == 0, out.stderr
    return [l for l in out.stdout.splitlines() if l.strip()]


def _hash(root):
    out = _sh(f'console_content_hash "{root}"', root)
    assert out.returncode == 0, out.stderr
    h = out.stdout.strip()
    assert h, ("empty hash", out.stderr)
    return h


@pytest.fixture()
def tree(tmp_path):
    """A synthetic console tree: the 5 static files, a flat backend module, AND a nested
    subpackage module — so recursion is actually exercised, not just asserted."""
    root = tmp_path
    static = root / "nervous_system/console/static"
    static.mkdir(parents=True)
    for rel in _STATIC_REL:
        (root / rel).write_text(f"// {Path(rel).name}\n")
    pkg = root / "nervous_system/console"
    (pkg / "app.py").write_text("APP = 'v1'\n")
    (pkg / "db.py").write_text("DB = 1\n")
    sub = pkg / "hosted"
    sub.mkdir()
    (sub / "__init__.py").write_text("")
    (sub / "view.py").write_text("VIEW = 'v1'\n")
    # a __pycache__ artifact that must be IGNORED
    pyc = pkg / "__pycache__"
    pyc.mkdir()
    (pyc / "app.cpython-39.pyc").write_bytes(b"\x00\x01junk")
    return root


# ── the seam covers the backend, recursively, deterministically ──────────────

def test_manifest_seam_exists():
    assert _MANIFEST.is_file(), f"SSOT seam missing: {_MANIFEST}"


def test_file_list_includes_static_and_recursive_backend(tree):
    files = _files(tree)
    for rel in _STATIC_REL:
        assert rel in files, f"static file dropped from gate: {rel}"
    assert "nervous_system/console/app.py" in files, "backend app.py NOT gated — the blindspot"
    assert "nervous_system/console/db.py" in files
    assert "nervous_system/console/hosted/view.py" in files, \
        "recursive: a subpackage module must be gated (Nazim #31843 no-sub-blindspot)"


def test_pycache_is_excluded(tree):
    files = _files(tree)
    assert not any("__pycache__" in f for f in files), f"__pycache__ leaked into gate: {files}"


def test_file_list_is_sorted_and_deterministic(tree):
    a = _files(tree)
    b = _files(tree)
    assert a == b, "file list must be deterministic across runs"
    backend = [f for f in a if f.endswith(".py")]
    assert backend == sorted(backend), "backend files must be sorted for a stable hash"


# ── the dead-man's-switch: a backend-only edit CHANGES the hash ──────────────

def test_backend_only_edit_changes_the_hash(tree):
    """THE point of the whole change. A stale review keyed to the old hash must no longer match
    after a backend-only edit."""
    before = _hash(tree)
    (tree / "nervous_system/console/app.py").write_text("APP = 'v2'  # backend-only change\n")
    after = _hash(tree)
    assert before != after, (
        "DEAD-MAN'S-SWITCH FAILED: a backend-only edit did not change the review hash — "
        "backend would ship UNREVIEWED, the exact bug item-4b closes"
    )


def test_nested_backend_edit_changes_the_hash(tree):
    """Recursion is real, not cosmetic: editing a subpackage module must also move the hash."""
    before = _hash(tree)
    (tree / "nervous_system/console/hosted/view.py").write_text("VIEW = 'v2'\n")
    assert _hash(tree) != before, "a nested backend edit must invalidate the review hash"


def test_new_backend_file_changes_the_hash(tree):
    """Adding a backend module (even trivial) must force a fresh review, not inherit an old one."""
    before = _hash(tree)
    (tree / "nervous_system/console/newmod.py").write_text("X = 1\n")
    assert _hash(tree) != before, "a newly-added backend module must invalidate the review hash"


def test_static_edit_still_changes_the_hash(tree):
    """Regression: folding in the backend must not stop static changes from being gated."""
    before = _hash(tree)
    (tree / "nervous_system/console/static/fleet.js").write_text("// changed\n")
    assert _hash(tree) != before, "a static edit must still invalidate the review hash"


def test_unchanged_tree_hash_is_stable(tree):
    assert _hash(tree) == _hash(tree), "identical content must hash identically"


def test_cross_file_content_move_changes_the_hash(tmp_path):
    """cc-quality #31859 LOW: without a per-file boundary, moving content across an adjacent-file
    seam leaves the manifest AND the back-to-back byte stream identical -> a hash collision that
    misses a real change. Build two adjacent backend files and move a line from the top of the
    second to the bottom of the first: the concatenated bytes are byte-identical, so ONLY the
    per-file delimiter can make the hash move."""
    root = tmp_path
    static = root / "nervous_system/console/static"
    static.mkdir(parents=True)
    for rel in _STATIC_REL:
        (root / rel).write_text("")            # empty static — isolate the backend seam
    pkg = root / "nervous_system/console"
    a = pkg / "aa_mod.py"                        # sorts immediately before ab_mod.py, nothing between
    b = pkg / "ab_mod.py"
    # state 1: the moved line X lives at the BOTTOM of aa_mod
    a.write_text("AAA\nX\n")
    b.write_text("BBB\n")
    before = _hash(root)
    # state 2: X moved to the TOP of ab_mod — concatenation "AAA\nX\nBBB\n" is byte-identical
    a.write_text("AAA\n")
    b.write_text("X\nBBB\n")
    after = _hash(root)
    assert before != after, (
        "cross-file content move was NOT detected — the per-file boundary delimiter is missing "
        "or ineffective; a real change could ship under a stale review hash"
    )


# ── the gate script actually USES the seam (not a dead parallel copy) ────────

def test_gate_sources_the_manifest_and_uses_the_hash():
    src = _GATE.read_text()
    assert "console_deploy_manifest.sh" in src, "deploy_console.sh must source the SSOT seam"
    assert "console_content_hash" in src, \
        "deploy_console.sh must key its review HASH off the seam, not an inline static-only cat"


def test_gate_documents_the_coverage_boundary():
    """Nazim #31843: a KNOWN, WRITTEN boundary is fine; a silent one is what we're killing."""
    src = (_GATE.read_text() + _MANIFEST.read_text()).lower()
    assert "boundary" in src or "shared lib" in src or "scripts/lib" in src, \
        "the package-only coverage boundary must be documented in the gate/seam"


# ── the real repo backend is genuinely covered (not just a synthetic tree) ───

def test_real_console_backend_is_covered():
    files = _files(_ROOT)
    for rel in ("nervous_system/console/app.py",
                "nervous_system/console/db.py",
                "nervous_system/console/auth.py",
                "nervous_system/console/panes.py",
                "nervous_system/console/hosted_view.py"):
        assert rel in files, f"real console backend not gated: {rel}"
