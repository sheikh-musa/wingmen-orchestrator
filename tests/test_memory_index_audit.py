"""memory_index_audit — NO-INDEX must mean SILENT LOSS, not merely "no MEMORY.md".

The audit's job is to catch memory that is silently lost: an ORPHANED file (exists, nothing
indexes it) or an OVER-LIMIT index (tail truncates on read). A dir with NO MEMORY.md and NO
files loses nothing — there is nothing to index and nothing to forget. Flagging it NO-INDEX is
noise, and the --alert path then PAGES that noise to the SRE (bus 38069, a false positive on
-Users-sheikhmusa-wingmen-quality-audit-drain which is an empty dir). Nazim 37868 already ruled
files=0 EMPTY dirs are ok/empty, not "needs attention". This locks that: NO-INDEX fires only when
there are FILES that a missing index would orphan.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts import memory_index_audit as mia  # noqa: E402


def test_empty_dir_no_index_is_not_a_loss(tmp_path):
    # no MEMORY.md, no files — the quality-audit-drain case. audit() reports missing_index but
    # zero files, and that must NOT be a lossy NO-INDEX condition.
    r = mia.audit(tmp_path)
    assert r["missing_index"] is True and r["files"] == 0
    assert mia.missing_index_is_lossy(r) is False


def test_files_without_index_IS_a_loss(tmp_path):
    # real silent loss: memory files exist but no MEMORY.md points at them -> orphaned from birth.
    (tmp_path / "some-fact.md").write_text("---\nname: some-fact\ndescription: x\n---\nbody\n")
    (tmp_path / "another.md").write_text("---\nname: another\ndescription: y\n---\nbody\n")
    r = mia.audit(tmp_path)
    assert r["missing_index"] is True and r["files"] == 2
    assert mia.missing_index_is_lossy(r) is True


def test_indexed_dir_is_not_missing_index(tmp_path):
    (tmp_path / "some-fact.md").write_text("---\nname: some-fact\ndescription: x\n---\nbody\n")
    (tmp_path / "MEMORY.md").write_text("# index\n- [some-fact](some-fact.md) - x\n")
    r = mia.audit(tmp_path)
    assert r["missing_index"] is False
    assert mia.missing_index_is_lossy(r) is False
