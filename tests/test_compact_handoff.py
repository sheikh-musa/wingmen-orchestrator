"""Handoff-hygiene compaction (Nazim #31753): collapse an append-forever handoff to a
size a fresh body can actually read whole, WITHOUT losing the current state.

Recovery-critical: a handoff is what a recycled body boots from, so the compactor must
NEVER drop the authoritative state — the top FINAL-STATE/SUPERSEDES block (nazim
convention) OR the most-recent DELTA sections (coord convention). Middle/superseded
sections collapse to a 1-line pointer. Idempotent; cap yields to safety (never truncate
kept-verbatim current state just to hit the cap)."""
from scripts import compact_handoff as ch

TITLE = "# lane handoff\n\nintro preamble line\n"


def _handoff(n_sections, body_bytes=2000, prefix="DELTA"):
    parts = [TITLE]
    for i in range(n_sections):
        parts.append(f"## {prefix}-{i} section {i}\n" + ("x" * body_bytes) + "\n")
    return "\n".join(parts)


def test_under_cap_is_unchanged_idempotent():
    h = TITLE + "## only section\nshort body\n"
    assert ch.compact_handoff(h, cap_bytes=60000) == h


def test_over_cap_shrinks_to_at_or_below_cap():
    h = _handoff(60, 2000)  # ~120KB
    out = ch.compact_handoff(h, cap_bytes=60000, keep_recent=8)
    assert len(out.encode("utf-8")) <= 60000


def test_keeps_title_and_most_recent_sections_verbatim():
    h = _handoff(60, 2000)
    out = ch.compact_handoff(h, cap_bytes=60000, keep_recent=8)
    assert "# lane handoff" in out                 # title/preamble kept
    assert "## DELTA-59 section 59" in out          # most-recent header kept
    assert "## DELTA-58 section 58" in out
    assert ("x" * 2000) in out                      # at least one full recent body verbatim


def test_never_drops_the_single_most_recent_section_even_if_huge():
    # the last section alone is bigger than the cap -> we still keep it (safety > cap).
    h = TITLE + "## DELTA-0 old\n" + ("o" * 3000) + "\n## DELTA-1 latest\n" + ("L" * 80000) + "\n"
    out = ch.compact_handoff(h, cap_bytes=60000, keep_recent=1)
    assert "## DELTA-1 latest" in out and ("L" * 80000) in out   # current state never lost


def test_middle_sections_collapse_to_one_line_pointers():
    h = _handoff(60, 2000)
    out = ch.compact_handoff(h, cap_bytes=60000, keep_recent=8)
    # an early (superseded) section's HEADER survives as a pointer, but not its full body
    assert "DELTA-0" in out
    assert "collapsed" in out.lower()
    # the collapsed body is gone: far fewer than 60 full 2000-byte bodies remain
    assert out.count("x" * 2000) < 60


def test_final_state_supersedes_block_kept_verbatim():
    # nazim convention: a top block flagged FINAL STATE / SUPERSEDES must be kept verbatim.
    h = (TITLE + "## FINAL STATE (READ FIRST; SUPERSEDES EVERYTHING BELOW)\n"
         + ("F" * 4000) + "\n" + _handoff(40, 2000))
    out = ch.compact_handoff(h, cap_bytes=60000, keep_recent=5)
    assert "## FINAL STATE (READ FIRST; SUPERSEDES EVERYTHING BELOW)" in out
    assert ("F" * 4000) in out                      # FINAL STATE body kept verbatim
    assert len(out.encode("utf-8")) <= 60000


def test_collapsed_runs_are_summarized_not_one_pointer_per_section():
    # 464-section coord handoffs made ~462 one-line pointers (~83KB) — pointers must
    # collapse in RUNS so the collapse itself stays tiny.
    h = _handoff(200, 1500)
    out = ch.compact_handoff(h, cap_bytes=60000, keep_recent=8)
    assert out.count("[collapsed") <= 3          # a few run-summaries, not ~190 lines
    assert len(out.encode("utf-8")) <= 60000


def test_compaction_is_idempotent_on_its_own_output():
    h = _handoff(60, 2000)
    once = ch.compact_handoff(h, cap_bytes=60000, keep_recent=8)
    twice = ch.compact_handoff(once, cap_bytes=60000, keep_recent=8)
    assert once == twice


# ── cc-quality FINDING-1: non-keyword section[0] (coord identity) must survive ──
def test_nonkeyword_section0_identity_kept_verbatim():
    # coord convention: section[0] = "## 0. IDENTITY/DIRECTIVES" (NO keyword). It is
    # current state; an over-cap compaction must NOT collapse its body (recovery data-loss).
    h = (TITLE + "## 0. IDENTITY + OPERATOR DIRECTIVES\n" + ("I" * 3000) + "\n"
         + _handoff(60, 2000))
    out = ch.compact_handoff(h, cap_bytes=60000, keep_recent=5)
    assert "## 0. IDENTITY + OPERATOR DIRECTIVES" in out
    assert ("I" * 3000) in out                      # identity BODY verbatim, not a pointer
    assert len(out.encode("utf-8")) <= 60000


# ── cc-quality FINDING-2: older FINAL-STATE blocks collapse, newest (section[0]) kept ──
def test_older_final_state_blocks_collapse_and_get_under_cap():
    parts = [TITLE, "## FINAL STATE latest\n" + ("N" * 4000) + "\n"]
    for i in range(20):
        parts.append(f"## FINAL STATE v{i} (superseded)\n" + ("s" * 4000) + "\n")
    h = "\n".join(parts)                                            # ~88KB, over cap
    out = ch.compact_handoff(h, cap_bytes=60000, keep_recent=3)
    assert "## FINAL STATE latest" in out and ("N" * 4000) in out   # newest (section[0]) kept
    assert len(out.encode("utf-8")) <= 60000                        # under cap (was ~88KB)
    assert out.count("s" * 4000) < 20                               # older ones collapsed


# ── cc-quality: idempotence on a cap-YIELDED output (current state alone > cap) ──
def test_idempotent_when_cap_yields_to_safety():
    h = TITLE + "## 0. IDENTITY\n" + ("I" * 80000) + "\n## DELTA-1 latest\n" + ("L" * 2000) + "\n"
    once = ch.compact_handoff(h, cap_bytes=60000, keep_recent=1)
    assert len(once.encode("utf-8")) > 60000        # section[0] alone > cap -> cap yields
    assert ("I" * 80000) in once                     # current state kept whole (safety > cap)
    twice = ch.compact_handoff(once, cap_bytes=60000, keep_recent=1)
    assert once == twice


# ── cc-quality: the file wrapper (dry-run-default / .bak / refuse-larger) was untested ──
def test_file_wrapper_default_is_dry_run(tmp_path):
    p = tmp_path / "h.md"
    orig = _handoff(60, 2000)
    p.write_text(orig, encoding="utf-8")
    s = ch.compact_handoff_file(str(p))                 # no dry_run arg -> default True
    assert s["dry_run"] and not s["wrote"] and s["backup"] is None
    assert p.read_text(encoding="utf-8") == orig        # disk untouched


def test_file_wrapper_apply_writes_backup_then_compacts(tmp_path):
    import os
    p = tmp_path / "h.md"
    orig = _handoff(60, 2000)
    p.write_text(orig, encoding="utf-8")
    s = ch.compact_handoff_file(str(p), dry_run=False, stamp="TEST")
    assert s["wrote"] and s["backup"] and os.path.exists(s["backup"])
    assert open(s["backup"], encoding="utf-8").read() == orig            # .bak == original
    assert len(p.read_text(encoding="utf-8").encode("utf-8")) <= 60000   # compacted on disk


def test_file_wrapper_refuses_to_write_a_larger_result(tmp_path, monkeypatch):
    p = tmp_path / "h.md"
    orig = _handoff(60, 2000)
    p.write_text(orig, encoding="utf-8")
    monkeypatch.setattr(ch, "compact_handoff", lambda text, **k: text + ("Z" * 999999))
    s = ch.compact_handoff_file(str(p), dry_run=False)
    assert not s["wrote"] and "error" in s
    assert p.read_text(encoding="utf-8") == orig                         # never wrote a worse file


def test_file_wrapper_noop_when_under_cap(tmp_path):
    p = tmp_path / "small.md"
    small = TITLE + "## only\nshort body\n"
    p.write_text(small, encoding="utf-8")
    s = ch.compact_handoff_file(str(p), dry_run=False)
    assert not s["changed"] and not s["wrote"] and s["backup"] is None


# ── F3 (cc-quality): the in-place overwrite is ATOMIC (temp + os.replace), now load-bearing
# because compaction auto-fires at recycle time. ────────────────────────────────────────────

def test_apply_leaves_no_temp_file_on_success(tmp_path):
    p = tmp_path / "h.md"
    p.write_text(_handoff(60, 2000), encoding="utf-8")
    ch.compact_handoff_file(str(p), dry_run=False, stamp="TEST")
    leftovers = list(tmp_path.glob(".compact_*.tmp"))
    assert leftovers == []                       # temp swapped in, none left behind

def test_apply_atomic_abort_leaves_original_intact(tmp_path, monkeypatch):
    """If the atomic os.replace fails, the LIVE handoff must be untouched (the old bytes),
    the temp file cleaned up, and the exception propagated (so the caller aborts the recycle)."""
    import os
    import pytest
    p = tmp_path / "h.md"
    orig = _handoff(60, 2000)
    p.write_text(orig, encoding="utf-8")
    def _boom(*a, **k):
        raise OSError("replace boom")
    monkeypatch.setattr(os, "replace", _boom)   # the fn's `import os as _os` is the same module
    with pytest.raises(OSError):
        ch.compact_handoff_file(str(p), dry_run=False, stamp="TEST")
    assert p.read_text(encoding="utf-8") == orig           # live handoff = old bytes, never torn
    assert list(tmp_path.glob(".compact_*.tmp")) == []     # temp cleaned up
    # the .bak (written before the replace attempt) still preserves the original
    assert (tmp_path / "h.md.TEST.bak").read_text(encoding="utf-8") == orig
