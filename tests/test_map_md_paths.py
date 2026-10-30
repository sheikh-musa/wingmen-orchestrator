"""MAP.md path-existence check (op#35990): every path in MAP.md's path
manifest must exist, so the map can't silently rot as the repo moves on.
No DB, no network — pure filesystem check."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAP_MD = REPO_ROOT / "MAP.md"


def _manifest_paths() -> list[str]:
    text = MAP_MD.read_text()
    section = text.split("## Path manifest", 1)[1]
    fence = re.search(r"```\n(.*?)\n```", section, re.S)
    assert fence, "MAP.md: no fenced code block found under '## Path manifest'"
    return [line for line in fence.group(1).splitlines() if line.strip()]


def test_map_md_exists():
    assert MAP_MD.is_file(), "MAP.md is missing from the repo root"


def test_map_md_has_a_path_manifest():
    assert _manifest_paths(), "MAP.md's path manifest is empty"


def test_map_md_paths_all_exist():
    missing = [p for p in _manifest_paths() if not (REPO_ROOT / p).exists()]
    assert not missing, (
        "MAP.md references path(s) that no longer exist -- fix the path in "
        "MAP.md (manifest and prose) or restore it:\n  " + "\n  ".join(missing)
    )
