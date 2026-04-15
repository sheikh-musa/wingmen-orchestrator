"""Drill 1 — broken migration should be caught by schema_gate, not shipped.

Setup injects a synthetic bad DDL line into a temp repo's schema.sql + stages
it as a commit. Run calls nervous_system.schema_gate.extract_schema_ddl to
confirm it surfaces as DDL (i.e. schema_gate would block a job that pushed
this). Assert: non-empty DDL list returned, contains the injected marker.
Cleanup: remove the temp repo.

This is the "the gate fires on the real thing" check — complement to the
unit tests that mock the DDL detection.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Any

from scripts.fire_drills.base import Drill


INJECTED_MARKER = "ALTER TABLE fire_drill_canary ADD COLUMN injected_by_drill TEXT"


class BrokenMigrationDrill(Drill):
    NAME = "fire_drill_migration"
    GATE_REF = "fire_drill_migration"

    async def setup(self) -> dict[str, Any]:
        # Isolated throwaway git repo with schema.sql.
        repo_dir = tempfile.mkdtemp(prefix="ihsan_drill_migration_")
        repo_path = Path(repo_dir)
        schema = repo_path / "schema.sql"

        await self._git(repo_path, "init", "-q")
        await self._git(repo_path, "config", "user.email", "drill@wingmen.test")
        await self._git(repo_path, "config", "user.name", "drill")

        schema.write_text("-- baseline schema\n")
        await self._git(repo_path, "add", "schema.sql")
        await self._git(repo_path, "commit", "-q", "-m", "baseline")

        # Inject bad DDL and commit — this is the failure mode the gate should detect.
        schema.write_text(
            schema.read_text()
            + f"\n{INJECTED_MARKER};\ncreate index idx_drill_canary on fire_drill_canary(injected_by_drill);\n"
        )
        await self._git(repo_path, "add", "schema.sql")
        await self._git(repo_path, "commit", "-q", "-m", "DRILL: inject unapplied DDL")

        return {"repo_path": repo_dir}

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        from nervous_system.schema_gate import extract_schema_ddl

        ddl = await extract_schema_ddl(state["repo_path"])
        return {"ddl_lines": ddl}

    async def assert_outcome(self, state: dict[str, Any], run_output: dict[str, Any]) -> None:
        ddl = run_output.get("ddl_lines") or []
        assert len(ddl) >= 1, (
            f"schema_gate.extract_schema_ddl returned {ddl} — expected to detect "
            f"at least the ALTER TABLE line injected by the drill"
        )
        assert any(INJECTED_MARKER.lower() in line.lower() for line in ddl), (
            f"injected marker not found in extracted DDL: {ddl}"
        )

    async def cleanup(self, state: dict[str, Any]) -> None:
        repo_path = state.get("repo_path")
        if repo_path and Path(repo_path).exists():
            shutil.rmtree(repo_path, ignore_errors=True)

    async def _git(self, repo_path: Path, *args: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=str(repo_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed: {err.decode(errors='replace')}")


if __name__ == "__main__":
    import sys
    from scripts.fire_drills.base import run_drill_sync
    dry = "--dry-run" in sys.argv
    result = run_drill_sync(BrokenMigrationDrill(), dry_run=dry)
    print(result)
    sys.exit(0 if result.passed else 1)
