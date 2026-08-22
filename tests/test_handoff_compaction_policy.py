"""handoff_compaction_policy — SSOT for STAGED handoff auto-compaction at recycle time
(Nazim #31825, item-3 endgame). The tool (compact_handoff) is already proven+tested; this
covers the STAGING gate (which bodies fire) and the fail-loud wiring contract.

Staging (the ONLY knob is COMPACTION_HELD):
  - cai is HELD (tier 3 — last, after one clean fleet cycle); held on agent OR session.
  - every other body (console/nazim, coord, engineer/worker lanes, fleet-health) is ENABLED.
  - a handoff already <= cap is a NO-OP even when enabled (safe to land fleet-wide now).
Fail-loud: compact_handoff_file writes .bak first and refuses to write an empty/larger
result (original left intact). An I/O exception PROPAGATES so the caller aborts the recycle
rather than reset onto a half-written restore point.
"""
import sys
from pathlib import Path

_ORCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ORCH / "scripts" / "lib"))
import handoff_compaction_policy as hcp  # noqa: E402


# ---- should_compact: the staging gate --------------------------------------

def test_cai_held_by_agent():
    assert hcp.should_compact(agent="cai") is False

def test_cai_held_by_session():
    assert hcp.should_compact(session="cai") is False

def test_console_enabled():
    assert hcp.should_compact(agent="orch-console", session="nazim") is True

def test_worker_lane_enabled():
    assert hcp.should_compact(agent="cc-irsyad", session="cc-irsyad-1") is True

def test_fleet_health_enabled():
    assert hcp.should_compact(agent="cc-fleet-health", session="fleet-health") is True

def test_none_defaults_enabled():
    assert hcp.should_compact() is True

def test_held_wins_even_with_enabled_partner():
    # If EITHER identifier is held, hold (fail-closed).
    assert hcp.should_compact(agent="cai", session="nazim") is False


# ---- compact_if_enabled: the wiring ----------------------------------------

_TITLE = "# handoff\n\npreamble\n"

def _big(path: Path, n=40, body=3000):
    parts = [_TITLE]
    for i in range(n):
        parts.append(f"## SEC-{i} block {i}\n" + ("x" * body) + "\n")
    path.write_text("\n".join(parts), encoding="utf-8")

def _small(path: Path):
    path.write_text(_TITLE + "## ONLY current state\nshort\n", encoding="utf-8")


def test_held_body_does_not_touch_file(tmp_path):
    h = tmp_path / "cai-handoff-NOW.md"
    _big(h)
    before = h.read_text()
    r = hcp.compact_if_enabled(str(h), agent="cai", session="cai", stamp="T1")
    assert r["held"] == "cai"
    assert r["wrote"] is False and r["changed"] is False
    assert h.read_text() == before          # untouched
    assert not list(tmp_path.glob("*.bak"))  # no backup written

def test_enabled_undercap_is_noop(tmp_path):
    h = tmp_path / "nazim-handoff-NOW.md"
    _small(h)
    before = h.read_text()
    r = hcp.compact_if_enabled(str(h), agent="orch-console", session="nazim", stamp="T1")
    assert r.get("held") is None
    assert r["wrote"] is False and r["changed"] is False
    assert h.read_text() == before
    assert not list(tmp_path.glob("*.bak"))

def test_enabled_overcap_compacts_with_bak(tmp_path):
    h = tmp_path / "coord-handoff-NOW.md"
    _big(h)
    before = h.read_text()
    r = hcp.compact_if_enabled(str(h), agent="cc-irsyad-coord",
                               session="cc-irsyad-coord", stamp="T1")
    assert r["wrote"] is True and r["changed"] is True
    assert r["after_bytes"] < r["before_bytes"]
    baks = list(tmp_path.glob("*.bak"))
    assert len(baks) == 1
    assert baks[0].read_text() == before          # .bak = full original (reversible)
    # section[0] (current-state) kept verbatim
    assert "## SEC-0 block 0" in h.read_text()

def test_dry_run_never_writes(tmp_path):
    h = tmp_path / "coord-handoff-NOW.md"
    _big(h)
    before = h.read_text()
    r = hcp.compact_if_enabled(str(h), agent="cc-irsyad-coord",
                               session="cc-irsyad-coord", stamp="T1", dry_run=True)
    assert r["dry_run"] is True and r["wrote"] is False
    assert h.read_text() == before
    assert not list(tmp_path.glob("*.bak"))

def test_io_exception_propagates(tmp_path):
    # Missing file must RAISE (caller aborts recycle), not silently pass.
    import pytest
    with pytest.raises(OSError):
        hcp.compact_if_enabled(str(tmp_path / "nope.md"),
                               agent="cc-irsyad", session="cc-irsyad", stamp="T1")
