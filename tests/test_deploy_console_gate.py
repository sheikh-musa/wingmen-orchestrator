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

# The ORIGINAL 5 (op#12457) — gated from the start.
_ORIG5 = [
    "nervous_system/console/static/fleet.html",
    "nervous_system/console/static/fleet.js",
    "nervous_system/console/static/lanes.html",
    "nervous_system/console/static/lanes.js",
    "nervous_system/console/static/sw.js",
]
# Served static that used to SHIP UNREVIEWED (the twin blindspot) — now gated by Nazim #31865
# Option 1 (gate ALL served static). A representative spread: SPA logic, an alt page + its js, the
# PWA manifest, and a binary icon.
_SERVED_STATIC_EXTRA = [
    "nervous_system/console/static/app.js",        # 26KB SPA logic — the big one
    "nervous_system/console/static/index.html",
    "nervous_system/console/static/irsyad.html",
    "nervous_system/console/static/irsyad.js",
    "nervous_system/console/static/manifest.json",
    "nervous_system/console/static/icons/favicon.ico",
]
_STATIC_REL = _ORIG5 + _SERVED_STATIC_EXTRA
# Deploy-provenance artifact — gated per Nazim #31865 (defines the VPS-portable container).
_DOCKERFILE = "nervous_system/console/Dockerfile"


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
    """A synthetic console tree: the FULL served-static set (incl. previously-ungated app.js /
    index.html / irsyad.* / manifest.json / a binary icon), a flat backend module, a nested
    subpackage module (recursion), the Dockerfile, and a __pycache__ artifact that must be
    ignored."""
    root = tmp_path
    (root / "nervous_system/console/static/icons").mkdir(parents=True)
    for rel in _STATIC_REL:
        if rel.endswith(".ico"):
            (root / rel).write_bytes(b"\x00ICO\x00v1")   # binary asset — cat/hash must handle it
        else:
            (root / rel).write_text(f"// {Path(rel).name} v1\n")
    pkg = root / "nervous_system/console"
    (pkg / "app.py").write_text("APP = 'v1'\n")
    (pkg / "db.py").write_text("DB = 1\n")
    (root / _DOCKERFILE).write_text("FROM python:3.11-slim\n# v1\n")
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


def test_file_list_includes_all_served_static_and_recursive_backend(tree):
    files = _files(tree)
    for rel in _STATIC_REL:
        assert rel in files, f"served static NOT gated: {rel}"
    assert "nervous_system/console/app.py" in files, "backend app.py NOT gated — the blindspot"
    assert "nervous_system/console/db.py" in files
    assert "nervous_system/console/hosted/view.py" in files, \
        "recursive: a subpackage module must be gated (Nazim #31843 no-sub-blindspot)"


def test_previously_ungated_served_static_is_now_gated(tree):
    """Nazim #31865 Option 1: the twin blindspot — app.js/index/irsyad/manifest/icons shipped
    UNREVIEWED. They must ALL be gated now, not just the original 5."""
    files = _files(tree)
    for rel in _SERVED_STATIC_EXTRA:
        assert rel in files, f"twin blindspot still open — served static ungated: {rel}"


def test_dockerfile_is_gated(tree):
    """Nazim #31865: gate the Dockerfile (deploy-provenance — defines the deployed artifact)."""
    assert _DOCKERFILE in _files(tree), "Dockerfile NOT gated (deploy-provenance blindspot)"


def test_manifest_works_without_a_dockerfile(tree):
    """Robustness: a checkout that lacks the Dockerfile must NOT make the seam exit non-zero
    (the `[ -f ] &&` idiom did, and pipefail propagated it). The gate must still compute."""
    (tree / _DOCKERFILE).unlink()
    files = _files(tree)                                   # _files asserts returncode == 0
    assert _DOCKERFILE not in files
    assert "nervous_system/console/app.py" in files       # rest of the gate still intact
    assert _hash(tree)                                     # hash still computes (non-empty, rc 0)


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


def test_app_js_edit_changes_the_hash(tree):
    """THE twin blindspot's dead-man's-switch: app.js (26KB SPA logic) used to ship UNREVIEWED.
    A change to it must now move the review hash."""
    before = _hash(tree)
    (tree / "nervous_system/console/static/app.js").write_text("// SPA logic changed\n")
    assert _hash(tree) != before, (
        "app.js edit did not move the hash — the twin static blindspot is still OPEN"
    )


def test_icon_edit_changes_the_hash(tree):
    """A binary asset (favicon) is console/design content per Nazim #31865 — its change is gated."""
    before = _hash(tree)
    (tree / "nervous_system/console/static/icons/favicon.ico").write_bytes(b"\x00ICO\x00v2")
    assert _hash(tree) != before, "a binary icon edit must invalidate the review hash"


def test_dockerfile_edit_changes_the_hash(tree):
    """Deploy-provenance dead-man's-switch (Nazim #31865): a Dockerfile change alters what ships."""
    before = _hash(tree)
    (tree / _DOCKERFILE).write_text("FROM python:3.11-slim\n# v2 — base image bumped\n")
    assert _hash(tree) != before, "a Dockerfile change must invalidate the review hash"


def test_unchanged_tree_hash_is_stable(tree):
    assert _hash(tree) == _hash(tree), "identical content must hash identically"


def test_cross_file_content_move_changes_the_hash(tmp_path):
    """cc-quality #31859 LOW: without a per-file boundary, moving content across an adjacent-file
    seam leaves the manifest AND the back-to-back byte stream identical -> a hash collision that
    misses a real change. Build two adjacent backend files and move a line from the top of the
    second to the bottom of the first: the concatenated bytes are byte-identical, so ONLY the
    per-file delimiter can make the hash move."""
    root = tmp_path
    for rel in _STATIC_REL:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")                     # empty static — isolate the backend seam
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


def test_real_served_static_and_dockerfile_are_covered():
    """Nazim #31865: on the REAL repo, the previously-ungated served static and the Dockerfile are
    now in the gate (not just a synthetic tree)."""
    files = _files(_ROOT)
    for rel in ("nervous_system/console/static/app.js",
                "nervous_system/console/static/index.html",
                "nervous_system/console/static/irsyad.html",
                "nervous_system/console/static/irsyad.js",
                "nervous_system/console/static/docs.js",
                "nervous_system/console/static/media.js",
                "nervous_system/console/static/manifest.json",
                "nervous_system/console/static/icons/icon-192.png",
                "nervous_system/console/Dockerfile"):
        assert rel in files, f"real served content/provenance not gated: {rel}"
