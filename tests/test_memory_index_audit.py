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
import re
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


# --- frontmatter schema audit (op#34635/34637, bus #77584 sharpening + #77641: report-first,
# extend this checker rather than a new one-off script; fix_frontmatter is append-only, same
# philosophy as fix() above -- it never invents a historical fact or restructures a legacy
# shape, only backfills a field whose correct value is unambiguous) -----------------------

def _fm(stem, *, name=None, description="d", mtype="project", extra_meta="", body="body\n"):
    meta = f"  type: {mtype}\n{extra_meta}" if mtype is not None else extra_meta
    return (
        "---\n"
        f"name: {name if name is not None else stem}\n"
        f"description: {description}\n"
        "metadata:\n"
        f"{meta}"
        "---\n"
        f"{body}"
    )


def test_clean_file_has_no_frontmatter_issues(tmp_path):
    (tmp_path / "ok.md").write_text(_fm(
        "ok", extra_meta="  type: project\n  node_type: memory\n"
                         "  originSessionId: abc-123\n  modified: 2026-10-27T00:00:00Z\n",
        mtype=None,
    ))
    r = mia.audit_frontmatter(tmp_path)
    assert r["dirty"] == []
    assert r["files"] == 1


def test_missing_node_type_is_flagged_and_fixable(tmp_path):
    (tmp_path / "x.md").write_text(_fm(
        "x", extra_meta="  type: project\n  originSessionId: abc\n  modified: 2026-10-27T00:00:00Z\n",
        mtype=None,
    ))
    r = mia.audit_frontmatter(tmp_path)
    assert r["dirty"][0]["file"] == "x.md"
    assert r["dirty"][0]["missing_node_type"] is True


def test_missing_modified_is_flagged_and_fixable(tmp_path):
    (tmp_path / "x.md").write_text(_fm(
        "x", extra_meta="  type: project\n  node_type: memory\n  originSessionId: abc\n",
        mtype=None,
    ))
    r = mia.audit_frontmatter(tmp_path)
    assert r["dirty"][0]["missing_modified"] is True


def test_missing_origin_session_id_is_flagged_but_never_auto_fixable(tmp_path):
    (tmp_path / "x.md").write_text(_fm(
        "x", extra_meta="  type: project\n  node_type: memory\n  modified: 2026-10-27T00:00:00Z\n",
        mtype=None,
    ))
    r = mia.audit_frontmatter(tmp_path)
    assert r["dirty"][0]["missing_origin_session_id"] is True
    # fix_frontmatter must never fabricate a historical session id
    mia.fix_frontmatter(tmp_path, r)
    text = (tmp_path / "x.md").read_text()
    assert "originSessionId" not in text


def test_missing_type_is_inferred_from_known_filename_prefix(tmp_path):
    (tmp_path / "feedback_something.md").write_text(_fm(
        "feedback_something",
        extra_meta="  node_type: memory\n  originSessionId: abc\n  modified: 2026-10-27T00:00:00Z\n",
        mtype=None,
    ))
    r = mia.audit_frontmatter(tmp_path)
    d = r["dirty"][0]
    assert d["missing_type"] is True
    assert d["inferred_type"] == "feedback"


def test_missing_type_with_no_recognized_prefix_is_flagged_but_not_inferred(tmp_path):
    (tmp_path / "unprefixed.md").write_text(_fm(
        "unprefixed",
        extra_meta="  node_type: memory\n  originSessionId: abc\n  modified: 2026-10-27T00:00:00Z\n",
        mtype=None,
    ))
    r = mia.audit_frontmatter(tmp_path)
    d = r["dirty"][0]
    assert d["missing_type"] is True
    assert d.get("inferred_type") is None


def test_legacy_schema_no_metadata_block_is_flagged_but_not_auto_fixed(tmp_path):
    (tmp_path / "legacy.md").write_text(
        "---\nname: a full sentence used as a name\ndescription: d\ntype: feedback\n---\nbody\n"
    )
    r = mia.audit_frontmatter(tmp_path)
    d = r["dirty"][0]
    assert d["missing_metadata_block"] is True
    assert d["type_at_top_level"] is True
    assert d["name_is_sentence"] is True
    before = (tmp_path / "legacy.md").read_text()
    mia.fix_frontmatter(tmp_path, r)
    assert (tmp_path / "legacy.md").read_text() == before  # untouched -- needs restructuring, not appending


def test_empty_name_is_flagged_and_not_auto_fixed(tmp_path):
    (tmp_path / "x.md").write_text(_fm(
        "x", name='""',
        extra_meta="  type: project\n  node_type: memory\n  originSessionId: abc\n  modified: 2026-10-27T00:00:00Z\n",
        mtype=None,
    ))
    r = mia.audit_frontmatter(tmp_path)
    assert r["dirty"][0]["empty_name"] is True


def test_missing_description_is_flagged(tmp_path):
    text = (
        "---\nname: x\nmetadata:\n  type: project\n  node_type: memory\n"
        "  originSessionId: abc\n  modified: 2026-10-27T00:00:00Z\n---\nbody\n"
    )
    (tmp_path / "x.md").write_text(text)
    r = mia.audit_frontmatter(tmp_path)
    assert r["dirty"][0]["missing_description"] is True


def test_memory_index_files_excluded_from_frontmatter_audit(tmp_path):
    (tmp_path / "MEMORY.md").write_text("# index\n")
    (tmp_path / "MEMORY-INDEX.md").write_text("# overflow\n")
    r = mia.audit_frontmatter(tmp_path)
    assert r["files"] == 0
    assert r["dirty"] == []


def test_fix_frontmatter_appends_only_the_unambiguous_fixable_fields(tmp_path):
    (tmp_path / "x.md").write_text(_fm("x", extra_meta="  type: project\n", mtype=None))
    r = mia.audit_frontmatter(tmp_path)
    added = mia.fix_frontmatter(tmp_path, r)
    assert added == 2  # node_type + modified; originSessionId never fabricated
    text = (tmp_path / "x.md").read_text()
    assert "node_type: memory" in text
    assert re.search(r"modified: \S+", text)
    assert "originSessionId" not in text
    # re-auditing the fixed file must show those two fields clean now
    r2 = mia.audit_frontmatter(tmp_path)
    d2 = r2["dirty"][0]
    assert d2.get("missing_node_type") is None
    assert d2.get("missing_modified") is None
    assert d2["missing_origin_session_id"] is True


def test_fix_frontmatter_on_a_clean_file_is_a_noop(tmp_path):
    (tmp_path / "ok.md").write_text(_fm(
        "ok", extra_meta="  type: project\n  node_type: memory\n"
                         "  originSessionId: abc-123\n  modified: 2026-10-27T00:00:00Z\n",
        mtype=None,
    ))
    r = mia.audit_frontmatter(tmp_path)
    assert mia.fix_frontmatter(tmp_path, r) == 0


def test_malformed_frontmatter_is_flagged_not_crashed_on(tmp_path):
    (tmp_path / "broken.md").write_text("---\nname: x\ndescription: \"unterminated\nbody\n")
    r = mia.audit_frontmatter(tmp_path)
    assert r["dirty"][0]["malformed"] is True
