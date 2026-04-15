"""Tests for fire drill 1 — broken migration. Verifies the drill scaffolding
itself works + the dry-run mode returns pass without side effects.

Full live-run assertion (inject + run + observe real schema_gate) is the
drill's own job — this test suite covers the harness logic.
"""

import pytest

from scripts.fire_drills.drill_broken_migration import BrokenMigrationDrill, INJECTED_MARKER


@pytest.mark.asyncio
async def test_drill_dry_run_passes_without_side_effects():
    """Dry-run does setup + cleanup but skips the live run + assert."""
    drill = BrokenMigrationDrill()
    result = await drill.execute(dry_run=True)
    assert result.name == "fire_drill_migration"
    assert result.passed is True, f"dry-run should always pass, got error={result.error}"
    assert result.run_output == {"dry_run": True}


@pytest.mark.asyncio
async def test_drill_full_run_detects_injected_ddl():
    """Live-run drill: inject bad DDL, confirm schema_gate.extract_schema_ddl surfaces it.
    This is the real end-to-end drill — it writes to a throwaway git repo and
    invokes the actual extraction logic. No mocks."""
    drill = BrokenMigrationDrill()
    result = await drill.execute(dry_run=False)
    assert result.passed, f"drill failed: {result.error}"
    ddl = result.run_output.get("ddl_lines", [])
    assert any(INJECTED_MARKER.lower() in line.lower() for line in ddl), (
        f"drill reported pass but ddl_lines missing marker: {ddl}"
    )


@pytest.mark.asyncio
async def test_drill_cleanup_removes_temp_repo(tmp_path):
    """After execute(), the temp repo must be gone — drill leaves no turds."""
    import os

    drill = BrokenMigrationDrill()
    result = await drill.execute(dry_run=False)
    repo_path = result.setup_state.get("repo_path")
    assert repo_path is not None, "setup must return repo_path"
    assert not os.path.exists(repo_path), (
        f"drill left temp repo behind at {repo_path} — cleanup failed"
    )


@pytest.mark.asyncio
async def test_drill_reports_failure_when_extraction_broken(monkeypatch):
    """If extract_schema_ddl is made to return empty, drill must report FAIL
    (not silently pass)."""
    drill = BrokenMigrationDrill()

    async def fake_extract(_repo_path: str):
        return []

    monkeypatch.setattr(
        "nervous_system.schema_gate.extract_schema_ddl", fake_extract
    )
    result = await drill.execute(dry_run=False)
    assert result.passed is False
    assert result.error is not None and "expected to detect" in result.error
