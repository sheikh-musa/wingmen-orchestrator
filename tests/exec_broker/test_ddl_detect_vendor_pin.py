"""Self-check: exec_broker's vendored ddl_detect.py must never drift from
either its pinned upstream hash or the orchestrator's OTHER vendored copy
(scripts/hooks/ddl_detect.py) — two copies of the same module quietly
disagreeing is exactly the footgun both files' docstrings exist to prevent.
Mirrors the existing pattern in tests/test_secrets_transcript_guard.py's
test_vendored_ddl_detect_matches_its_own_pinned_hash."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VENDORED_PATH = REPO_ROOT / "scripts" / "lib" / "exec_broker" / "ddl_detect.py"
HOOKS_PATH = REPO_ROOT / "scripts" / "hooks" / "ddl_detect.py"

# the exact upstream hash both vendored copies are pinned to (ihsanos-irsyad
# main scripts/db/lib/ddl_detect.py as of PR #1168 squash 799b16c2).
UPSTREAM_PINNED_HASH = "02a4c047d7ad196dc408f3cce384dcc8da5388d6fb26bcd14f29a726c78c453e"


def test_hooks_copy_matches_its_own_pinned_hash():
    # sanity: the existing orchestrator vendored copy is still what we're pinning against
    assert hashlib.sha256(HOOKS_PATH.read_bytes()).hexdigest() == UPSTREAM_PINNED_HASH


def test_vendored_copy_cites_the_upstream_pin():
    src = VENDORED_PATH.read_text()
    m = re.search(r"sha256\s*\n?#?\s*([0-9a-f]{64})", src)
    assert m, "no sha256 pin found near the top of the vendored copy -- did the header move?"
    assert m.group(1) == UPSTREAM_PINNED_HASH


def test_vendored_copy_logic_matches_hooks_copy_verbatim():
    # Strip the exec_broker-specific header comment block (everything up to and
    # including the line starting the module's own """ docstring) from the
    # vendored copy, then the remainder must be byte-identical to the hooks
    # copy's own content from its """ docstring onward -- i.e. the two files
    # differ ONLY in which vendoring-provenance comment precedes the shared code.
    vendored_lines = VENDORED_PATH.read_text().splitlines(keepends=True)
    hooks_lines = HOOKS_PATH.read_text().splitlines(keepends=True)

    def _from_docstring(lines):
        for i, line in enumerate(lines):
            if line.startswith('"""'):
                return lines[i:]
        raise AssertionError("no docstring line found")

    assert _from_docstring(vendored_lines) == _from_docstring(hooks_lines)


def test_is_ddl_shaped_importable_and_matches_hooks_copy_behavior():
    import importlib.util
    import sys

    def _load(path, name):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    vendored = _load(VENDORED_PATH, "exec_broker_vendored_ddl_detect")
    hooks = _load(HOOKS_PATH, "hooks_vendored_ddl_detect")

    for sql in ("select 1", "create table t (id int)", "DO $$ BEGIN EXECUTE 'drop table t'; END $$;"):
        assert vendored.is_ddl_shaped(sql) == hooks.is_ddl_shaped(sql)
