"""Cross-drill test suite. Each drill runs end-to-end (live, no mocks at
the drill boundary — only at the Supabase boundary where we have to)
and must pass. These ARE the fire drills that unlock Phase 0.

If any drill here regresses, Phase 0's 14-day clean clock must reset.
"""

import pytest

from scripts.fire_drills.drill_broken_migration import BrokenMigrationDrill
from scripts.fire_drills.drill_sigkill import SigkillDrill
from scripts.fire_drills.drill_concurrent_conflict import ConcurrentConflictDrill
from scripts.fire_drills.drill_cli_timeout import CliTimeoutDrill
from scripts.fire_drills.drill_missing_tool import MissingToolDrill


ALL_DRILLS = [
    BrokenMigrationDrill,
    SigkillDrill,
    ConcurrentConflictDrill,
    CliTimeoutDrill,
    MissingToolDrill,
]


@pytest.mark.parametrize("drill_cls", ALL_DRILLS)
@pytest.mark.asyncio
async def test_each_drill_passes_live(drill_cls):
    """Every fire drill must pass end-to-end. This is the assertion that
    unlocks the Phase 0 gates."""
    drill = drill_cls()
    result = await drill.execute(dry_run=False)
    assert result.passed, (
        f"{drill.NAME} failed: {result.error}\n"
        f"run_output={result.run_output}"
    )
    assert result.duration_seconds < 15, (
        f"{drill.NAME} took {result.duration_seconds}s — too slow for weekly CI"
    )


@pytest.mark.parametrize("drill_cls", ALL_DRILLS)
@pytest.mark.asyncio
async def test_each_drill_dry_run_passes(drill_cls):
    """Dry-run must always pass + must not leave side effects."""
    drill = drill_cls()
    result = await drill.execute(dry_run=True)
    assert result.passed is True
    assert result.run_output == {"dry_run": True}


@pytest.mark.asyncio
async def test_all_drills_have_unique_gate_refs():
    """Gate refs feed bug_pipeline_readiness — must be unique or we'd
    silently overwrite each other's status rows."""
    refs = [d.GATE_REF for d in ALL_DRILLS]
    assert len(refs) == len(set(refs)), f"duplicate gate_refs: {refs}"
    for ref in refs:
        assert ref, "every drill must set GATE_REF"


@pytest.mark.asyncio
async def test_every_gate_ref_exists_in_readiness_table():
    """Sanity: drill gate_refs must match bug_pipeline_readiness rows that
    the seed migration created. Any mismatch means a drill won't surface
    its result in the right row."""
    expected = {
        "fire_drill_migration",
        "fire_drill_sigkill",
        "fire_drill_conflict",
        "fire_drill_cli_timeout",
        "fire_drill_missing_tool",
    }
    actual = {d.GATE_REF for d in ALL_DRILLS}
    assert actual == expected, (
        f"drill gate_refs {actual} do not match bug_pipeline_readiness seed {expected}"
    )
