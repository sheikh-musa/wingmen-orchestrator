"""memory_recall — the option-3 stopgap (op#34635/34637, bus #77584 sharpening E):
keyword/BM25-style ranked recall over a lane's existing memory/*.md files, so an agent
that doesn't know the exact grep term can still surface a semantically-related memory,
without any new infra (no DB, no embedding service, no new dependency). Files stay the
SSOT; this only ranks and points at them.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts import memory_recall as mr  # noqa: E402


def _write(mem_dir, stem, *, name=None, description="", mtype="project", body=""):
    mem_dir.mkdir(parents=True, exist_ok=True)
    (mem_dir / f"{stem}.md").write_text(
        "---\n"
        f"name: {name or stem}\n"
        f'description: "{description}"\n'
        "metadata:\n"
        f"  type: {mtype}\n"
        "---\n"
        f"{body}\n"
    )


def test_exact_keyword_match_ranks_first(tmp_path):
    _write(tmp_path, "pg17-bootstrap-order", description="bootstrap replays migrations in filename order")
    _write(tmp_path, "unrelated-topic", description="something about Obsidian vault sync")
    hits = mr.recall(tmp_path, "bootstrap order", top_n=5)
    assert hits[0]["file"] == "pg17-bootstrap-order.md"


def test_query_term_absent_from_every_file_returns_nothing(tmp_path):
    _write(tmp_path, "a", description="apples and oranges")
    hits = mr.recall(tmp_path, "zzz_nonexistent_term_zzz", top_n=5)
    assert hits == []


def test_description_match_outranks_body_only_match(tmp_path):
    # "migration" appears once, buried in a's body; it's the HEADLINE of b's description.
    _write(tmp_path, "a", description="unrelated", body="a passing mention of migration somewhere")
    _write(tmp_path, "b", description="migration renumbering collision fix")
    hits = mr.recall(tmp_path, "migration", top_n=5)
    assert hits[0]["file"] == "b.md"


def test_multi_term_query_prefers_file_matching_both_terms(tmp_path):
    _write(tmp_path, "both", description="bus writer gate enforcement migration")
    _write(tmp_path, "one", description="bus message priority levels")
    hits = mr.recall(tmp_path, "bus writer migration", top_n=5)
    assert hits[0]["file"] == "both.md"


def test_memory_index_files_are_excluded_from_the_corpus(tmp_path):
    _write(tmp_path, "real-memory", description="bus writer gate")
    (tmp_path / "MEMORY.md").write_text("# index\n- bus writer gate stuff\n")
    (tmp_path / "MEMORY-INDEX.md").write_text("# overflow\n- bus writer gate stuff\n")
    hits = mr.recall(tmp_path, "bus writer gate", top_n=5)
    assert [h["file"] for h in hits] == ["real-memory.md"]


def test_top_n_caps_the_result_count(tmp_path):
    for i in range(10):
        _write(tmp_path, f"m{i}", description="shared keyword topic")
    hits = mr.recall(tmp_path, "shared keyword topic", top_n=3)
    assert len(hits) == 3


def test_empty_memory_dir_returns_nothing(tmp_path):
    assert mr.recall(tmp_path, "anything", top_n=5) == []


def test_each_hit_reports_score_file_type_and_description(tmp_path):
    _write(tmp_path, "x", description="a thing worth finding", mtype="feedback")
    hits = mr.recall(tmp_path, "thing worth finding", top_n=5)
    hit = hits[0]
    assert hit["file"] == "x.md"
    assert hit["type"] == "feedback"
    assert hit["description"] == "a thing worth finding"
    assert isinstance(hit["score"], float) and hit["score"] > 0


def test_lane_dir_for_resolves_the_same_slug_scheme_the_harness_uses(tmp_path, monkeypatch):
    monkeypatch.setattr(mr, "PROJECTS", tmp_path)
    cwd = "/Users/sheikhmusa/wingmen/projects/paperclip-spike"
    resolved = mr.lane_dir_for(cwd)
    assert resolved == tmp_path / "-Users-sheikhmusa-wingmen-projects-paperclip-spike" / "memory"


def test_cli_lane_flag_accepts_a_plain_working_directory_path(tmp_path, monkeypatch, capsys):
    # Regression: --lane "/Users/.../paperclip-spike" (a real cwd, not already a
    # ~/.claude/projects/<slug>/memory path) must resolve through the same slug scheme
    # as the no-flag default, not be treated as a literal memory dir and 404.
    monkeypatch.setattr(mr, "PROJECTS", tmp_path)
    cwd = "/Users/sheikhmusa/wingmen/projects/paperclip-spike"
    lane_mem = mr.lane_dir_for(cwd)
    _write(lane_mem, "findable", description="a thing the CLI should surface")
    rc = mr.main(["findable thing", "--lane", cwd, "--top", "3"])
    assert rc == 0
    assert "findable.md" in capsys.readouterr().out
