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


def test_real_tail_index_link_is_not_dangling(tmp_path):
    # A dir correctly following the split-index pattern links to its own tail file with real
    # markdown syntax: "[memory index overflow](MEMORY-INDEX.md)". MEMORY-INDEX.md is deliberately
    # excluded from the orphan-detection file set (it's infra, not a content memory) — but that
    # same exclusion must not make its own, correct, self-referential link register as DANGLING
    # ("points at a file that is gone") when the tail file is sitting right there on disk.
    # Regression for a false positive found live on -Users-sheikhmusa-wingmen-projects-cosem-adcda
    # (orch-console audit, bus#67292): DANGLING=1 reported with zero actual silent loss.
    (tmp_path / "MEMORY.md").write_text(
        "# index\n- [memory index overflow](MEMORY-INDEX.md) - overflow\n"
    )
    (tmp_path / "MEMORY-INDEX.md").write_text("- [bar](bar.md) - a real overflow entry\n")
    (tmp_path / "bar.md").write_text("---\nname: bar\ndescription: z\n---\nbody\n")
    r = mia.audit(tmp_path)
    assert r["split"] is True
    assert r["tail_unreachable"] is False
    assert r["dangling"] == []
    assert r["orphans"] == []


def test_tail_index_link_to_missing_file_is_still_dangling(tmp_path):
    # The inverse case the fix must not paper over: MEMORY.md names a tail index that does not
    # actually exist on disk. That really is a promise the index cannot keep.
    (tmp_path / "MEMORY.md").write_text(
        "# index\n- [memory index overflow](MEMORY-INDEX.md) - overflow\n"
    )
    r = mia.audit(tmp_path)
    assert r["split"] is False
    assert r["dangling"] == ["MEMORY-INDEX.md"]


def test_dangling_link_to_a_genuinely_missing_file_is_still_caught(tmp_path):
    # A real broken link to a plain content memory (not the tail index) must still be caught.
    (tmp_path / "MEMORY.md").write_text(
        "# index\n"
        "- [memory index overflow](MEMORY-INDEX.md) - overflow\n"
        "- [gone](gone.md) - this file was deleted but the link survived\n"
    )
    (tmp_path / "MEMORY-INDEX.md").write_text("no entries\n")
    r = mia.audit(tmp_path)
    assert r["dangling"] == ["gone.md"]


# ── fix() -- console ruling bus #85239, after cc-quality's #85235 (--fix used to re-append
# orphan lines straight into MEMORY.md, re-bloating an index an owner had just manually
# compacted by folding those same files into clustered lines elsewhere) ─────────────────────
def test_fix_appends_orphans_to_tail_not_hot_index(tmp_path):
    (tmp_path / "some-fact.md").write_text("---\nname: some-fact\ndescription: a fact\n---\nbody\n")
    (tmp_path / "MEMORY.md").write_text("# index\nnothing here\n")
    r = mia.audit(tmp_path)
    assert r["orphans"] == ["some-fact.md"]

    added = mia.fix(r)
    assert added == 1
    hot = (tmp_path / "MEMORY.md").read_text()
    tail = (tmp_path / "MEMORY-INDEX.md").read_text()
    assert "some-fact.md" not in hot.split("MEMORY-INDEX.md")[0]  # not appended before the pointer
    assert "some-fact.md" in tail

    r2 = mia.audit(tmp_path)
    assert r2["orphans"] == []  # the tail link is enough to resolve it


def test_fix_adds_a_pointer_to_the_tail_if_none_exists_yet(tmp_path):
    (tmp_path / "some-fact.md").write_text("---\nname: some-fact\ndescription: a fact\n---\nbody\n")
    (tmp_path / "MEMORY.md").write_text("# index\nnothing here\n")
    r = mia.audit(tmp_path)
    assert r["split"] is False

    mia.fix(r)

    hot = (tmp_path / "MEMORY.md").read_text()
    assert "MEMORY-INDEX.md" in hot
    r2 = mia.audit(tmp_path)
    assert r2["split"] is True
    assert r2["tail_unreachable"] is False


def test_fix_does_not_duplicate_the_pointer_on_repeated_runs(tmp_path):
    (tmp_path / "a.md").write_text("---\nname: a\ndescription: x\n---\nbody\n")
    (tmp_path / "MEMORY.md").write_text("# index\nnothing here\n")

    r1 = mia.audit(tmp_path)
    mia.fix(r1)  # first orphan (a.md) run -- adds the pointer

    # simulate a second, later --fix pass picking up a newly-written orphan (b.md)
    (tmp_path / "b.md").write_text("---\nname: b\ndescription: y\n---\nbody\n")
    r2 = mia.audit(tmp_path)
    assert r2["orphans"] == ["b.md"]
    mia.fix(r2)

    hot = (tmp_path / "MEMORY.md").read_text()
    pointer_lines = [ln for ln in hot.splitlines() if "MEMORY-INDEX.md" in ln]
    assert len(pointer_lines) == 1


def test_fix_appends_to_an_existing_tail_without_clobbering_it(tmp_path):
    (tmp_path / "some-fact.md").write_text("---\nname: some-fact\ndescription: a fact\n---\nbody\n")
    (tmp_path / "MEMORY.md").write_text(
        "# index\n> See [MEMORY-INDEX.md](MEMORY-INDEX.md) for more.\n"
    )
    (tmp_path / "MEMORY-INDEX.md").write_text("# tail\n- [existing](existing.md) - already here\n")
    (tmp_path / "existing.md").write_text("---\nname: existing\ndescription: z\n---\nbody\n")

    r = mia.audit(tmp_path)
    assert r["orphans"] == ["some-fact.md"]
    hot_before = (tmp_path / "MEMORY.md").read_text()
    mia.fix(r)

    tail = (tmp_path / "MEMORY-INDEX.md").read_text()
    assert "existing.md" in tail  # untouched
    assert "some-fact.md" in tail  # appended
    hot_after = (tmp_path / "MEMORY.md").read_text()
    assert hot_after == hot_before  # pointer already present -> MEMORY.md untouched
