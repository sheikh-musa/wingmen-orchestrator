"""Memory 2.0 Part 2 PR B (op#36768, backlog #127): the SessionStart hook
must hand a booting body the facts the fleet already recorded.

Console's hard limits (bus #87068, Option A approved):
  * the whole hook stays under 3s,
  * ANY error or a cold DB prints NOTHING and exits 0,
  * output stays a few hundred tokens -- facts for that session scope only,
  * it has to work from a NON-orchestrator repo, which is where lanes live.

The failure mode this closes: a body boots with no memory of a fact Musa
already gave, re-asks him, and the fleet looks amnesiac. The failure mode
this must NOT create: a boot that hangs, crashes, dumps the table into every
session's context -- or a global hook that tells every repo on the box it is
the console (op#29128 / bus #63372), which is why the global hook is its own
script (scripts/session_start_facts.py) and not the reconstitution hook.

Runs the real hooks as subprocesses against the ephemeral pg_dsn harness --
no DATABASE_URL, no live substrate.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psycopg
import pytest

from tests.migrations.test_139_fleet_memory_facts import (
    _ROLES,
    _ensure_role,
    _make_ledger_table,
    _make_prereqs,
)

ROOT = Path(__file__).resolve().parent.parent.parent
FACTS_HOOK = ROOT / "scripts" / "session_start_facts.py"
RECONSTITUTE_HOOK = ROOT / "scripts" / "session_start_reconstitute.py"
MIGRATION_135 = ROOT / "migrations" / "135_fleet_memory.sql"
MIGRATION_139 = ROOT / "migrations" / "139_fleet_memory_facts.sql"
SILO = "tscuymavysscrvoberrr"

BUDGET_S = 3.0


@pytest.fixture
def fleet_db(fresh_db):
    """135 + 139 applied to a throwaway database, plus one fact per scope."""
    dsn = f"{fresh_db} application_name={SILO}"
    created = [r for r in _ROLES if _ensure_role(dsn, r)]
    _make_ledger_table(dsn)
    _make_prereqs(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(MIGRATION_135.read_text())
        cur.execute(MIGRATION_139.read_text())
        for scope, key, fact, op in (
            ("fleet", None, "DeepSeek console login done 30 Oct, profile in place", "op#36768"),
            ("repo", "second-brain", "Vault deploy pipeline is green at 36/36 parity", "op#35246"),
            ("repo", "cosem-adcda", "Cosem ADC baseline locked at v4", "op#36000"),
            ("client", "irsyad", "Irsyad floor gate stays closed for the console", "op#29128"),
        ):
            cur.execute(
                "SELECT fleet_memory_insert(%s,%s,%s,%s,%s,%s,%s,%s)",
                (scope, key, fact, op, "cc-seed", None, None, None),
            )
    yield dsn
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("GRANT USAGE ON SCHEMA public TO public")
    for rolename in created:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(f'DROP ROLE IF EXISTS "{rolename}"')


def _boot(dsn, *, cwd, script=FACTS_HOOK, env_extra=None, stdin_payload=None):
    """Run a hook exactly as a harness would: JSON on stdin, JSON out."""
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": os.environ.get("HOME", ""),
        "DATABASE_URL": dsn,
        "WINGMEN_ORCH_DIR": str(ROOT),
    }
    env.update(env_extra or {})
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(stdin_payload or {"source": "startup"}),
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
        timeout=30,
    )
    elapsed = time.monotonic() - started
    return proc, elapsed


def _context(proc) -> str:
    payload = json.loads(proc.stdout)
    return payload["hookSpecificOutput"]["additionalContext"]


def test_global_hook_gives_a_non_orchestrator_session_scoped_facts(fleet_db, tmp_path):
    """The whole point: a session in ANOTHER repo boots knowing what is
    settled, scoped to its own repo -- not another lane's facts, not the
    client silo it never declared."""
    repo_dir = tmp_path / "second-brain"
    repo_dir.mkdir()
    proc, elapsed = _boot(fleet_db, cwd=repo_dir)

    assert proc.returncode == 0
    ctx = _context(proc)
    assert "DeepSeek console login done" in ctx           # fleet-wide
    assert "Vault deploy pipeline is green" in ctx         # this repo
    assert "Cosem ADC baseline" not in ctx                 # another repo
    assert "Irsyad floor gate" not in ctx                  # another client
    assert elapsed < BUDGET_S


def test_a_client_fact_appears_only_when_the_session_declares_that_client(fleet_db, tmp_path):
    repo_dir = tmp_path / "second-brain"
    repo_dir.mkdir()
    proc, _ = _boot(fleet_db, cwd=repo_dir, env_extra={"CC_CLIENT": "irsyad"})
    assert "Irsyad floor gate" in _context(proc)


def test_plain_session_never_claims_to_be_console(fleet_db, tmp_path):
    """The hook is GLOBAL, so it fires for sessions that are neither a lane
    nor the console. _body_role() falls back to the live checkout's .env,
    which carries ORCH_BODY_ROLE=console -- a session opened in an unrelated
    repo must still never be told it is Nazim (op#29128 / bus #63372)."""
    repo_dir = tmp_path / "some-random-repo"
    repo_dir.mkdir()
    proc, _ = _boot(fleet_db, cwd=repo_dir, env_extra={"ORCH_BODY_ROLE": "console"})
    ctx = _context(proc)
    assert "console body" not in ctx
    assert "CONSOLE RECONSTITUTION" not in ctx
    assert "DeepSeek console login done" in ctx  # facts still reach it


def test_cold_database_prints_nothing_and_exits_zero(fleet_db, tmp_path):
    """A dead or unreachable DB must cost the boot nothing: silence, not a
    traceback, and well inside the budget."""
    repo_dir = tmp_path / "second-brain"
    repo_dir.mkdir()
    # Nothing listens here; psycopg should fail fast rather than hang the boot.
    proc, elapsed = _boot("postgresql://127.0.0.1:1/nothing", cwd=repo_dir)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""
    assert "Traceback" not in proc.stderr
    assert elapsed < BUDGET_S


def test_output_stays_bounded_with_many_facts(fleet_db, tmp_path):
    """Boot context is not a dump: 60 facts must not become 60 lines."""
    with psycopg.connect(fleet_db, autocommit=True) as conn, conn.cursor() as cur:
        for i in range(60):
            cur.execute(
                "SELECT fleet_memory_insert(%s,%s,%s,%s,%s,%s,%s,%s)",
                ("fleet", None, f"padded fact {i} " + "x" * 80, "op#36768",
                 "cc-seed", None, None, None),
            )
    repo_dir = tmp_path / "second-brain"
    repo_dir.mkdir()
    proc, elapsed = _boot(fleet_db, cwd=repo_dir)
    ctx = _context(proc)
    assert elapsed < BUDGET_S
    assert len(ctx) < 4000, f"facts block leaked a dump: {len(ctx)} chars"
    assert "padded fact 0" in ctx
    assert "padded fact 59" not in ctx


def test_lane_boot_keeps_its_identity_notice_and_gains_facts(fleet_db, tmp_path):
    """The reconstitution hook already fires for lanes; adding facts must not
    cost the identity notice that stops a lane believing it is the console."""
    repo_dir = tmp_path / "second-brain"
    repo_dir.mkdir()
    proc, elapsed = _boot(
        fleet_db, cwd=repo_dir, script=RECONSTITUTE_HOOK,
        env_extra={"CC_BASE_AGENT_ID": "cc-second-brain", "CC_AGENT_ID": "cc-second-brain-1"},
    )
    assert proc.returncode == 0
    ctx = _context(proc)
    assert "cc-second-brain" in ctx
    assert "NOT the consol" in ctx
    assert "DeepSeek console login done" in ctx
    assert elapsed < BUDGET_S
