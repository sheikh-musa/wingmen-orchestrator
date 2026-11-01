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


# ── F1 (cc-quality on PR#747): two hooks must not emit the same block twice ──

def test_global_hook_defers_when_the_project_emits_facts_itself(fleet_db, tmp_path):
    """The orchestrator checkout runs BOTH hooks: its project-level
    session_start_reconstitute.py (which emits facts for lanes/console) and
    this global one. Emitting identical facts twice wastes boot context every
    single session, so the global hook stands down where the project already
    covers it.

    Deferral is read from the project's own .claude/settings.json rather than
    from a path: worktrees live outside ~/wingmen/orchestrator and each
    carries its own copy, so a path check would miss them.
    """
    proj = tmp_path / "an-orchestrator-checkout"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "settings.json").write_text(
        '{"hooks": {"SessionStart": [{"hooks": [{"type": "command",'
        ' "command": "scripts/session_start_reconstitute.py"}]}]}}',
        encoding="utf-8",
    )
    proc, elapsed = _boot(fleet_db, cwd=proj, env_extra={"CLAUDE_PROJECT_DIR": str(proj)})
    assert proc.returncode == 0
    assert proc.stdout.strip() == "", "facts were emitted by both hooks"
    assert elapsed < BUDGET_S


def test_global_hook_still_emits_where_the_project_does_not(fleet_db, tmp_path):
    """The deferral must be narrow: a repo with no such hook still gets facts,
    which is the whole reason the global hook exists."""
    proj = tmp_path / "second-brain"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "settings.json").write_text(
        '{"hooks": {"PreToolUse": []}}', encoding="utf-8")
    proc, _ = _boot(fleet_db, cwd=proj, env_extra={"CLAUDE_PROJECT_DIR": str(proj)})
    assert "DeepSeek console login done" in _context(proc)


# ── H1 (cc-quality on PR#747): the stand-down cannot be blind to the source ──

def _orchestrator_shaped_project(tmp_path):
    proj = tmp_path / "an-orchestrator-checkout"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "settings.json").write_text(
        '{"hooks": {"SessionStart": [{"hooks": [{"type": "command",'
        ' "command": "scripts/session_start_reconstitute.py"}]}]}}',
        encoding="utf-8",
    )
    return proj


@pytest.mark.parametrize("source", ["resume", "compact"])
def test_global_hook_emits_on_resume_and_compact_in_an_orchestrator_project(
        fleet_db, tmp_path, source):
    """H1: reconstitute returns early on resume/compact (it only re-injects on
    startup/clear), but the global hook used to stand down for the project on
    EVERY source. Result: zero facts on a resumed or post-compact boot -- the
    worst moment to lose them, and precisely what PR B exists to prevent.

    Standing down is only correct where the project will actually emit."""
    proj = _orchestrator_shaped_project(tmp_path)
    proc, _ = _boot(fleet_db, cwd=proj, env_extra={"CLAUDE_PROJECT_DIR": str(proj)},
                    stdin_payload={"source": source})
    assert proc.returncode == 0
    assert "DeepSeek console login done" in _context(proc)


def test_global_hook_still_defers_on_clear_in_an_orchestrator_project(fleet_db, tmp_path):
    """The source gate must not undo F1: startup and clear are exactly where
    reconstitute does emit, so the stand-down still applies there."""
    proj = _orchestrator_shaped_project(tmp_path)
    proc, _ = _boot(fleet_db, cwd=proj, env_extra={"CLAUDE_PROJECT_DIR": str(proj)},
                    stdin_payload={"source": "clear"})
    assert proc.stdout.strip() == "", "facts were emitted by both hooks"


def test_reconstitute_emits_facts_for_a_plain_orchestrator_session(fleet_db, tmp_path):
    """The other half of H1: a session in the orchestrator checkout that is
    neither a lane nor the console body used to get NOTHING from reconstitute,
    while the global hook stood down. It now gets the facts -- and only the
    facts: this branch must never claim an identity (op#29128 / bus #63372)."""
    proj = _orchestrator_shaped_project(tmp_path)
    proc, _ = _boot(fleet_db, cwd=proj, script=RECONSTITUTE_HOOK)
    assert proc.returncode == 0
    ctx = _context(proc)
    assert "DeepSeek console login done" in ctx
    assert "console body" not in ctx
    assert "NOT the consol" not in ctx


def test_reconstitute_emits_facts_when_the_handoff_cannot_be_read(fleet_db, tmp_path):
    """cc-quality's residual on H1: the console branch returns 0 when the
    handoff open raises, and by then the global hook has already stood down.
    An unreadable handoff must cost the session the handoff, not its memory."""
    orch = tmp_path / "orch-root"
    (orch / "reports").mkdir(parents=True)
    os.symlink(ROOT / "scripts", orch / "scripts")
    handoff = orch / "reports" / "nazim-handoff-20260101.md"
    handoff.write_text("unreadable on purpose", encoding="utf-8")
    handoff.chmod(0o000)
    try:
        proc, _ = _boot(fleet_db, cwd=orch, script=RECONSTITUTE_HOOK,
                        env_extra={"WINGMEN_ORCH_DIR": str(orch),
                                   "ORCH_BODY_ROLE": "console"})
        assert proc.returncode == 0
        assert "DeepSeek console login done" in _context(proc)
    finally:
        handoff.chmod(0o600)


def test_the_two_hooks_agree_on_which_sources_emit(fleet_db):
    """The global hook carries its own copy of the source set so it never has
    to import the identity-bearing hook. That copy can drift; this catches it
    the moment either side changes."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("reconstitute_probe", RECONSTITUTE_HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    facts_hook = importlib.util.spec_from_file_location("facts_probe", FACTS_HOOK)
    facts_mod = importlib.util.module_from_spec(facts_hook)
    facts_hook.loader.exec_module(facts_mod)
    assert set(facts_mod._RECONSTITUTE_SOURCES) == set(mod.RECONSTITUTE_SOURCES)
