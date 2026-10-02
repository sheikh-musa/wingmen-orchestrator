"""handoff_find — the ONE definition of "this lane's newest handoff file" (CAI-1392 C).

WHY THIS EXISTS. On 2026-10-02 cc-storefront wrote a fresh, complete handoff named
`cc-storefront-HANDOFF-20261002.md` and the worker fresh_handoff gate read False: its
default glob was `reports/storefront-handoff-*.md` — case-sensitive AND without the `cc-`
prefix — so it could never see the new file and instead found a 12-day-old
`storefront-handoff-NOW.md`. Fail-closed, so nothing was lost, but a gate that cannot see
the restore point the lane actually wrote blocks every correct recycle.

Second trap folded in (2026-09-27, self_recycle.sh): freshness was read off a SYMLINK's
own mtime, not its target's — a handoff written seconds earlier read as 25 days old.
"""
import os
import time
from pathlib import Path

import sys

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts" / "lib"))
sys.path.insert(0, str(_ROOT / "scripts"))
import handoff_find as hf  # noqa: E402
import sre_lane_recycle as slr  # noqa: E402


def _touch(p: Path, age_s: float = 0.0) -> Path:
    p.write_text("handoff\n")
    t = time.time() - age_s
    os.utime(p, (t, t))
    return p


def test_matches_cc_prefixed_uppercase_handoff(tmp_path):
    f = _touch(tmp_path / "cc-storefront-HANDOFF-20261002.md")
    assert hf.newest_handoff(tmp_path, "cc-storefront") == str(f.resolve())


def test_matches_short_lowercase_handoff(tmp_path):
    f = _touch(tmp_path / "storefront-handoff-NOW.md")
    assert hf.newest_handoff(tmp_path, "cc-storefront") == str(f.resolve())


def test_newest_wins_across_both_naming_forms(tmp_path):
    _touch(tmp_path / "storefront-handoff-NOW.md", age_s=12 * 86400)
    fresh = _touch(tmp_path / "cc-storefront-HANDOFF-20261002.md", age_s=60)
    assert hf.newest_handoff(tmp_path, "cc-storefront") == str(fresh.resolve())


@pytest.mark.parametrize("name", [
    "cc-storefront2-handoff-x.md",      # a different lane sharing a prefix
    "irsyad-storefront-handoff-x.md",   # another lane's name ENDING in the short name
    "cc-storefront-handoff-x.txt",      # not markdown
    "cc-storefront-boot.md",            # not a handoff
])
def test_does_not_match_other_lanes_or_files(tmp_path, name):
    _touch(tmp_path / name)
    assert hf.newest_handoff(tmp_path, "cc-storefront") is None


def test_none_when_reports_dir_missing(tmp_path):
    assert hf.newest_handoff(tmp_path / "nope", "cc-quality") is None


def test_symlink_freshness_follows_the_target(tmp_path):
    target_dir = tmp_path / "elsewhere"
    target_dir.mkdir()
    target = _touch(target_dir / "real.md", age_s=5)
    link = tmp_path / "cc-quality-handoff-NOW.md"
    link.symlink_to(target)
    old = time.time() - 30 * 86400
    os.utime(link, (old, old), follow_symlinks=False)  # the LINK itself is ancient
    path, mtime = hf.newest_handoff_with_mtime(tmp_path, "cc-quality")
    assert path is not None
    assert time.time() - mtime < 60, "freshness read off the symlink, not its target"


def test_broken_symlink_is_ignored_not_fatal(tmp_path):
    (tmp_path / "cc-finance-handoff-x.md").symlink_to(tmp_path / "gone.md")
    assert hf.newest_handoff(tmp_path, "cc-finance") is None


def test_sre_lane_recycle_default_glob_sees_cc_prefixed_handoff(tmp_path, monkeypatch):
    """The worker gate uses the shared finder by default (no handoff_glob override)."""
    (tmp_path / "reports").mkdir()
    f = _touch(tmp_path / "reports" / "cc-cosem-video-HANDOFF-20261002.md")
    monkeypatch.setattr(slr, "_ORCH_DIR", tmp_path)
    assert slr._newest_handoff_path("cc-cosem-video", None) == str(f.resolve())


def test_sre_lane_recycle_explicit_handoff_glob_override_still_wins(tmp_path, monkeypatch):
    (tmp_path / "reports").mkdir()
    _touch(tmp_path / "reports" / "cc-audit-cosem-handoff-a.md")
    want = _touch(tmp_path / "reports" / "audit-cosem-handoff-b.md")
    monkeypatch.setattr(slr, "_ORCH_DIR", tmp_path)
    got = slr._newest_handoff_path("cc-audit-cosem", "x handoff_glob=reports/audit-cosem-handoff-*.md y")
    assert got == os.path.abspath(want)


def test_cli_prints_path_and_integer_mtime(tmp_path, capsys):
    f = _touch(tmp_path / "cc-quality-HANDOFF-1.md")
    rc = hf.main(["--reports", str(tmp_path), "--base", "cc-quality"])
    out = capsys.readouterr().out.strip().split("\t")
    assert rc == 0 and out[0] == str(f.resolve()) and out[1].isdigit()


def test_cli_exit_1_when_none(tmp_path, capsys):
    assert hf.main(["--reports", str(tmp_path), "--base", "cc-quality"]) == 1
    assert capsys.readouterr().out == ""
