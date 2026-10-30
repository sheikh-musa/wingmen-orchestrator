"""Tests for scripts.lib.auto_agent_id — GOVERNANCE-CLEANUP-001 Step 3."""
import json
import os
import subprocess
import sys

import pytest
from dotenv import load_dotenv

from scripts.lib import auto_agent_id

load_dotenv()
DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")

pytestmark_integration = pytest.mark.skipif(
    not DSN,
    reason="DATABASE_URL not set — skipping Supabase integration tests",
)


@pytest.fixture(autouse=True)
def _clean_test_family_rows():
    """Unconditional DELETE of cc-test-family-% rows before AND after each integration test.
    Makes teardown crash-safe — prior aborted runs don't leak state.

    BUG-024 Phase 1B: also ensures 'cc-test-family' exists in agents — required
    because agent_status.base_agent_id is an FK to agents(id) post-migration.
    Idempotent INSERT — leaves other agent rows untouched."""
    if not DSN:
        yield
        return
    import psycopg
    def _purge():
        with psycopg.connect(DSN, autocommit=True) as c:
            with c.cursor() as cur:
                cur.execute("DELETE FROM agent_status WHERE agent_id LIKE 'cc-test-family-%%'")
                # #57914 follow-up: allocation now also auto-registers the instance
                # id in `agents` (agent_messages.to_agent FK) — purge those too.
                cur.execute("DELETE FROM agents WHERE id LIKE 'cc-test-family-%%'")
    def _ensure_family():
        with psycopg.connect(DSN, autocommit=True) as c:
            with c.cursor() as cur:
                cur.execute(
                    "INSERT INTO agents (id, display_name) VALUES ('cc-test-family', 'cc-test-family') "
                    "ON CONFLICT (id) DO NOTHING"
                )
    _ensure_family()
    _purge()
    yield
    _purge()


@pytestmark_integration
class TestLoadFamilyMap:
    def test_returns_all_cc_families_canonicalized(self):
        m = auto_agent_id.load_family_map(DSN)
        # Post CAI-AGENTS-002: cc-ihsanos narrowed to ['ihsanos'];
        # cc-orchestrator owns ['wingmen-orchestrator'] (→ 'orchestrator').
        assert m["ihsanos"] == "cc-ihsanos"
        assert m["orchestrator"] == "cc-orchestrator"  # post-AGENTS-002
        assert m["ai-scholar"] == "cc-scholar"
        assert m["hifz-companion"] == "cc-scholar"
        assert m["dookana"] == "cc-web"
        assert m["wordpress-sites"] == "cc-web"
        # cosem family split into distinct identities (adcda + tdu) so the two
        # repos no longer share one bus inbox — each maps to its own base.
        assert m["cosem-tdu"] == "cc-cosem-tdu"
        assert m["cosem-adcda"] == "cc-cosem-adcda"

    def test_duplicate_claim_raises(self):
        # Can't easily test in integration without mutating agents table.
        # Unit-style test with monkeypatched psycopg instead.
        import types
        fake_rows = [("cc-a", ["x"]), ("cc-b", ["x"])]

        class _FakeCur:
            def __enter__(self_): return self_
            def __exit__(self_, *a): pass
            def execute(self_, *a, **k): pass
            def fetchall(self_): return fake_rows
        class _FakeConn:
            def __enter__(self_): return self_
            def __exit__(self_, *a): pass
            def cursor(self_): return _FakeCur()

        import psycopg
        orig = psycopg.connect
        psycopg.connect = lambda *a, **k: _FakeConn()
        try:
            with pytest.raises(ValueError, match="claimed by both"):
                auto_agent_id.load_family_map("dummy-dsn")
        finally:
            psycopg.connect = orig


# Fixture-style map matching live agents table post-CAI-AGENTS-002.
# cc-ihsanos narrowed to ['ihsanos']; cc-orchestrator owns 'orchestrator'.
FAKE_MAP = {
    "ihsanos": "cc-ihsanos",
    "orchestrator": "cc-orchestrator",
    "ai-scholar": "cc-scholar",
    "hifz-companion": "cc-scholar",
    "dookana": "cc-web",
    "wordpress-sites": "cc-web",
    "cosem-tdu": "cc-cosem",
    "cosem-adcda": "cc-cosem",
}


class TestStripWorktreeSuffix:
    def test_dash_uppercase_stripped(self):
        assert auto_agent_id.strip_worktree_suffix("orchestrator-LEDGER") == "orchestrator"

    def test_dot_wt_stripped(self):
        assert auto_agent_id.strip_worktree_suffix("orchestrator.wt-qurban") == "orchestrator"

    def test_dash_lowercase_preserved(self):
        # This is a legit repo name, not a worktree suffix.
        assert auto_agent_id.strip_worktree_suffix("hifz-companion") == "hifz-companion"

    def test_dash_lowercase_multi_preserved(self):
        assert auto_agent_id.strip_worktree_suffix("cosem-tdu") == "cosem-tdu"

    def test_no_suffix_unchanged(self):
        assert auto_agent_id.strip_worktree_suffix("orchestrator") == "orchestrator"


class TestResolveBaseAgentId:
    def test_orchestrator_maps_to_cc_orchestrator(self, monkeypatch):
        # Post-AGENTS-002: orchestrator repo belongs to cc-orchestrator family.
        monkeypatch.setattr(auto_agent_id, "_git_toplevel",
                            lambda pwd: "/Users/sheikhmusa/wingmen/orchestrator")
        assert auto_agent_id.resolve_base_agent_id(
            "/Users/sheikhmusa/wingmen/orchestrator", FAKE_MAP
        ) == "cc-orchestrator"

    def test_orchestrator_worktree_LEDGER_maps(self, monkeypatch):
        # Worktree: git rev-parse --show-toplevel returns the worktree path.
        monkeypatch.setattr(auto_agent_id, "_git_toplevel",
                            lambda pwd: "/Users/sheikhmusa/wingmen/orchestrator-LEDGER")
        assert auto_agent_id.resolve_base_agent_id(
            "/Users/sheikhmusa/wingmen/orchestrator-LEDGER", FAKE_MAP
        ) == "cc-orchestrator"

    def test_orchestrator_worktree_dot_wt_maps(self, monkeypatch):
        monkeypatch.setattr(auto_agent_id, "_git_toplevel",
                            lambda pwd: "/Users/sheikhmusa/wingmen/orchestrator.wt-qurban")
        assert auto_agent_id.resolve_base_agent_id(
            "/Users/sheikhmusa/wingmen/orchestrator.wt-qurban", FAKE_MAP
        ) == "cc-orchestrator"

    def test_hifz_companion_hyphen_preserved(self, monkeypatch):
        monkeypatch.setattr(auto_agent_id, "_git_toplevel",
                            lambda pwd: "/Users/sheikhmusa/wingmen/projects/hifz-companion")
        assert auto_agent_id.resolve_base_agent_id(
            "/Users/sheikhmusa/wingmen/projects/hifz-companion", FAKE_MAP
        ) == "cc-scholar"

    def test_cosem_tdu_maps_to_cc_cosem(self, monkeypatch):
        # New family post-CAI-AGENTS-001.
        monkeypatch.setattr(auto_agent_id, "_git_toplevel",
                            lambda pwd: "/Users/sheikhmusa/wingmen/projects/cosem-tdu")
        assert auto_agent_id.resolve_base_agent_id(
            "/Users/sheikhmusa/wingmen/projects/cosem-tdu", FAKE_MAP
        ) == "cc-cosem"

    def test_subdirectory_falls_back_to_walk(self, monkeypatch):
        # User in dookana/src/components — git-toplevel resolves, basename dookana.
        monkeypatch.setattr(auto_agent_id, "_git_toplevel",
                            lambda pwd: "/Users/sheikhmusa/wingmen/projects/dookana")
        assert auto_agent_id.resolve_base_agent_id(
            "/Users/sheikhmusa/wingmen/projects/dookana/src/components", FAKE_MAP
        ) == "cc-web"

    def test_no_git_walks_pwd_components(self, monkeypatch):
        # Fallback when outside a git repo.
        monkeypatch.setattr(auto_agent_id, "_git_toplevel", lambda pwd: None)
        assert auto_agent_id.resolve_base_agent_id(
            "/Users/sheikhmusa/wingmen/projects/dookana/src", FAKE_MAP
        ) == "cc-web"

    def test_unrecognized_raises(self, monkeypatch):
        monkeypatch.setattr(auto_agent_id, "_git_toplevel",
                            lambda pwd: "/Users/sheikhmusa/wingmen/projects/unregistered-repo")
        with pytest.raises(auto_agent_id.UnknownRepoError):
            auto_agent_id.resolve_base_agent_id(
                "/Users/sheikhmusa/wingmen/projects/unregistered-repo", FAKE_MAP
            )

    def test_outside_wingmen_raises(self, monkeypatch):
        monkeypatch.setattr(auto_agent_id, "_git_toplevel", lambda pwd: None)
        with pytest.raises(auto_agent_id.UnknownRepoError):
            auto_agent_id.resolve_base_agent_id("/tmp/foo", FAKE_MAP)


class TestPickSubTag:
    def test_empty_active_picks_one(self):
        assert auto_agent_id.pick_sub_tag("cc-ihsanos", []) == "cc-ihsanos-1"

    def test_contiguous_picks_next(self):
        assert auto_agent_id.pick_sub_tag(
            "cc-ihsanos", ["cc-ihsanos-1", "cc-ihsanos-2"]
        ) == "cc-ihsanos-3"

    def test_gap_fills_first_gap(self):
        assert auto_agent_id.pick_sub_tag(
            "cc-ihsanos", ["cc-ihsanos-1", "cc-ihsanos-3"]
        ) == "cc-ihsanos-2"

    def test_foreign_family_ignored(self):
        assert auto_agent_id.pick_sub_tag(
            "cc-ihsanos",
            ["cc-web-1", "cc-scholar-5", "cc-ihsanos-1"],
        ) == "cc-ihsanos-2"

    def test_duplicate_active_deduped(self):
        assert auto_agent_id.pick_sub_tag(
            "cc-ihsanos", ["cc-ihsanos-1", "cc-ihsanos-1"]
        ) == "cc-ihsanos-2"

    def test_base_matching_entry_ignored(self):
        # "cc-ihsanos" (no -N suffix) is the legacy base row, not a sub-tag
        assert auto_agent_id.pick_sub_tag(
            "cc-ihsanos", ["cc-ihsanos", "cc-ihsanos-1"]
        ) == "cc-ihsanos-2"

    def test_non_integer_suffix_ignored(self):
        # Robust to unexpected suffixes like "cc-ihsanos-test"
        assert auto_agent_id.pick_sub_tag(
            "cc-ihsanos", ["cc-ihsanos-test", "cc-ihsanos-1"]
        ) == "cc-ihsanos-2"


@pytestmark_integration
class TestAllocateSubTagAndRegister:
    """SAVEPOINT-rolled integration tests against the real Supabase project.
    Mirrors verify_governance_hygiene_batch.py SAVEPOINT/ROLLBACK harness."""

    def _fresh_conn(self):
        import psycopg
        return psycopg.connect(DSN, autocommit=False)

    def test_empty_family_allocates_one(self):
        # Roll in SAVEPOINT so we don't pollute real agent_status.
        import psycopg
        with self._fresh_conn() as setup_conn:
            with setup_conn.cursor() as cur:
                cur.execute("SAVEPOINT test_alloc")
                # Delete any sub-tagged rows in test family so we start clean.
                cur.execute("SELECT set_config('app.current_agent_id', %s, true)",
                            ("cc-test-family",))
                cur.execute(
                    "DELETE FROM agent_status WHERE agent_id LIKE 'cc-test-family-%'"
                )
            setup_conn.commit()

        try:
            result = auto_agent_id.allocate_sub_tag_and_register(
                base="cc-test-family",
                dsn=DSN,
                repo="orchestrator",
            )
            assert result.sub_tag == "cc-test-family-1"
            assert result.siblings == []

            # Verify row landed
            with self._fresh_conn() as verify_conn:
                with verify_conn.cursor() as cur:
                    cur.execute(
                        "SELECT status, current_task, scope_repos, base_agent_id "
                        "FROM agent_status WHERE agent_id = %s",
                        (result.sub_tag,),
                    )
                    row = cur.fetchone()
                    assert row is not None
                    assert row[0] == "working"
                    assert row[1] == "session-launch"
                    assert row[2] == ["orchestrator"]
                    # BUG-024 Phase 1B: base_agent_id FK populated from `base` arg.
                    assert row[3] == "cc-test-family"
        finally:
            # Cleanup
            with self._fresh_conn() as clean_conn:
                with clean_conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.current_agent_id', %s, true)",
                                ("cc-test-family-1",))
                    cur.execute(
                        "DELETE FROM agent_status WHERE agent_id LIKE 'cc-test-family-%'"
                    )
                clean_conn.commit()

    def test_stale_row_is_reclaimed(self):
        # Insert a row with heartbeat 2 hours old — allocator should skip it
        # (and its N becomes available).
        with self._fresh_conn() as setup_conn:
            with setup_conn.cursor() as cur:
                cur.execute("SELECT set_config('app.current_agent_id', %s, true)",
                            ("cc-test-family-1",))
                cur.execute(
                    "INSERT INTO agent_status "
                    "(agent_id, base_agent_id, status, last_heartbeat, updated_at) "
                    "VALUES (%s, %s, 'working', now() - interval '2 hours', now() - interval '2 hours') "
                    "ON CONFLICT (agent_id) DO UPDATE SET "
                    "last_heartbeat = EXCLUDED.last_heartbeat",
                    ("cc-test-family-1", "cc-test-family"),
                )
            setup_conn.commit()

        try:
            # N=1 is stale, so allocator should reclaim it (pick N=1 again).
            result = auto_agent_id.allocate_sub_tag_and_register(
                base="cc-test-family",
                dsn=DSN,
                repo="orchestrator",
            )
            assert result.sub_tag == "cc-test-family-1"
            # The previously-stale row should now have fresh heartbeat (UPSERT overwrote).
        finally:
            with self._fresh_conn() as clean_conn:
                with clean_conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.current_agent_id', %s, true)",
                                ("cc-test-family-1",))
                    cur.execute(
                        "DELETE FROM agent_status WHERE agent_id LIKE 'cc-test-family-%'"
                    )
                clean_conn.commit()

    def test_active_sibling_bumps_n(self):
        # Pre-populate with a fresh sibling; new allocation should pick N=2.
        with self._fresh_conn() as setup_conn:
            with setup_conn.cursor() as cur:
                cur.execute("SELECT set_config('app.current_agent_id', %s, true)",
                            ("cc-test-family-1",))
                cur.execute(
                    "INSERT INTO agent_status "
                    "(agent_id, base_agent_id, status, last_heartbeat, updated_at) "
                    "VALUES (%s, %s, 'working', now(), now()) "
                    "ON CONFLICT (agent_id) DO UPDATE SET "
                    "last_heartbeat = now()",
                    ("cc-test-family-1", "cc-test-family"),
                )
            setup_conn.commit()

        result = auto_agent_id.allocate_sub_tag_and_register(
            base="cc-test-family",
            dsn=DSN,
            repo="orchestrator",
        )
        assert result.sub_tag == "cc-test-family-2"
        assert "cc-test-family-1" in result.siblings

    def test_allocate_sub_tag_registers_fresh_base_agent_without_existing_rows(self):
        """BUG-033 AC-BUG033-2: fresh-family INSERT codepath populates base_agent_id.

        Regression: prior to BUG-033 fix, INSERT column list omitted base_agent_id.
        Existing families (ihsanos/scholar/cosem) had pre-backfilled rows so UPSERT
        branch fired and succeeded. First-spawn of a never-seen family hit the
        INSERT branch with base_agent_id=NULL — violated NOT NULL pre-degradation,
        silently inserted NULL post-degradation.
        """
        # Teardown via autouse fixture already DELETEd cc-test-family-% rows.
        # Double-check: fresh-family state means zero sibling rows.
        with self._fresh_conn() as verify_conn:
            with verify_conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM agent_status WHERE agent_id LIKE 'cc-test-family-%'"
                )
                assert cur.fetchone()[0] == 0, "precondition: fresh family has zero rows"

        result = auto_agent_id.allocate_sub_tag_and_register(
            base="cc-test-family",
            dsn=DSN,
            repo="orchestrator",
        )
        assert result.sub_tag == "cc-test-family-1"

        with self._fresh_conn() as verify_conn:
            with verify_conn.cursor() as cur:
                cur.execute(
                    "SELECT agent_id, base_agent_id, scope_repos "
                    "FROM agent_status WHERE agent_id = %s",
                    (result.sub_tag,),
                )
                row = cur.fetchone()
                assert row is not None, "new agent_status row must exist"
                assert row[0] == "cc-test-family-1"
                assert row[1] == "cc-test-family", (
                    f"base_agent_id must be populated on fresh INSERT; got {row[1]!r}"
                )
                assert row[2] == ["orchestrator"]

    def test_allocate_respects_base_agent_id_prefix_check_constraint(self):
        """BUG-033 AC-BUG033-3: CHECK fires on base_agent_id prefix mismatch.

        Direct INSERT with agent_id='cc-test-family-1' but base_agent_id='cc-ihsanos'
        must be rejected by agent_status_base_agent_id_prefix_chk CHECK constraint.
        This is defense-in-depth for the fix: a future regression that writes a
        garbage base_agent_id gets caught at the DB layer, not just the Python layer.
        """
        import psycopg
        with self._fresh_conn() as setup_conn:
            with setup_conn.cursor() as cur:
                cur.execute("SELECT set_config('app.current_agent_id', %s, true)",
                            ("cc-test-family-1",))
                with pytest.raises(psycopg.errors.CheckViolation) as exc:
                    cur.execute(
                        "INSERT INTO agent_status "
                        "(agent_id, base_agent_id, status, scope_repos, "
                        " last_heartbeat, updated_at) "
                        "VALUES (%s, %s, 'working', ARRAY['orchestrator']::text[], "
                        "        now(), now())",
                        ("cc-test-family-1", "cc-ihsanos"),  # prefix mismatch
                    )
                assert "prefix" in str(exc.value).lower() or "check" in str(exc.value).lower()
            setup_conn.rollback()

    def test_lock_timeout_diagnostic_query_survives_real_contention(self):
        """bus #85138 (cc-loop, found live under genuine 8-way launch contention):
        the diagnostic query fired when the advisory lock is NOT acquired within
        the retry budget selected a bare `pid` against a two-table join (pg_locks
        AS l JOIN pg_stat_activity AS a ON a.pid = l.pid) -- ambiguous between
        l.pid and a.pid, so psycopg raised UndefinedColumn/AmbiguousColumn instead
        of the intended LockTimeoutError, killing identity allocation outright
        under real contention. Reproduced here with GENUINE lock contention (a
        second connection actually holding the advisory lock across the whole
        5s retry ceiling), not a mock -- a mocked cursor can't exercise the SQL
        parser at all, which is exactly how this shipped unnoticed."""
        import threading
        import psycopg

        holder_ready = threading.Event()
        release_holder = threading.Event()

        def _hold_lock():
            with psycopg.connect(DSN, autocommit=False) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_xact_lock(%s)", (auto_agent_id._ALLOC_LOCK_ID,))
                holder_ready.set()
                release_holder.wait(timeout=30)
                conn.rollback()  # releases the advisory lock

        t = threading.Thread(target=_hold_lock, daemon=True)
        t.start()
        try:
            assert holder_ready.wait(timeout=5), "lock-holder thread never acquired the lock"
            with pytest.raises(auto_agent_id.LockTimeoutError) as exc:
                auto_agent_id.allocate_sub_tag_and_register(
                    base="cc-test-family", dsn=DSN, repo="orchestrator",
                )
            # proves the diagnostic SELECT ran to completion (not an UndefinedColumn/
            # AmbiguousColumn instead) and found the real contending holder's pid.
            assert "Holders: [(" in str(exc.value), str(exc.value)
        finally:
            release_holder.set()
            t.join(timeout=5)


@pytestmark_integration
class TestScanOverlapSiblings:
  def _fresh_conn(self):
      import psycopg
      return psycopg.connect(DSN, autocommit=False)

  def test_returns_overlapping_active_sibling(self):
      # Seed two rows in cc-test-family: -1 scopes orchestrator, -2 scopes orchestrator too.
      with self._fresh_conn() as setup_conn:
          with setup_conn.cursor() as cur:
              for n, scope in [(1, "orchestrator"), (2, "orchestrator")]:
                  cur.execute("SELECT set_config('app.current_agent_id', %s, true)",
                              (f"cc-test-family-{n}",))
                  cur.execute(
                      "INSERT INTO agent_status "
                      "(agent_id, base_agent_id, status, scope_repos, last_heartbeat, updated_at) "
                      "VALUES (%s, %s, 'working', ARRAY[%s]::text[], now(), now()) "
                      "ON CONFLICT (agent_id) DO UPDATE SET "
                      "scope_repos = EXCLUDED.scope_repos, "
                      "last_heartbeat = now()",
                      (f"cc-test-family-{n}", "cc-test-family", scope),
                  )
          setup_conn.commit()

      overlaps = auto_agent_id.scan_overlap_siblings(
          base="cc-test-family",
          scope_repo="orchestrator",
          dsn=DSN,
          exclude_sub_tag="cc-test-family-2",
      )
      # delta-v2: return type is list[tuple[str, int]] — (agent_id, heartbeat_age_s).
      # Shape check + membership check by first element.
      assert all(isinstance(t, tuple) and len(t) == 2 for t in overlaps)
      assert all(isinstance(t[0], str) and isinstance(t[1], int) for t in overlaps)
      agent_ids = [t[0] for t in overlaps]
      assert "cc-test-family-1" in agent_ids
      assert "cc-test-family-2" not in agent_ids  # excluded self
      # heartbeat just-inserted → age should be tiny (< 60s).
      age_1 = next(age for (aid, age) in overlaps if aid == "cc-test-family-1")
      assert 0 <= age_1 < 60

  def test_non_overlapping_scope_excluded(self):
      with self._fresh_conn() as setup_conn:
          with setup_conn.cursor() as cur:
              cur.execute("SELECT set_config('app.current_agent_id', %s, true)",
                          ("cc-test-family-1",))
              cur.execute(
                  "INSERT INTO agent_status "
                  "(agent_id, base_agent_id, status, scope_repos, last_heartbeat, updated_at) "
                  "VALUES (%s, %s, 'working', ARRAY['dookana']::text[], now(), now()) "
                  "ON CONFLICT (agent_id) DO UPDATE SET "
                  "scope_repos = EXCLUDED.scope_repos, last_heartbeat = now()",
                  ("cc-test-family-1", "cc-test-family"),
              )
          setup_conn.commit()

      overlaps = auto_agent_id.scan_overlap_siblings(
          base="cc-test-family",
          scope_repo="orchestrator",  # different
          dsn=DSN,
          exclude_sub_tag="cc-test-family-2",
      )
      assert overlaps == []


class TestCliEntrypoint:
    def test_bad_dsn_exits_1_with_clear_error(self):
        # Bad DSN → DatabaseError path (fail-loud, not silent-swallow).
        result = subprocess.run(
            [sys.executable, "-m", "scripts.lib.auto_agent_id",
             "--pwd", "/tmp/foo",
             "--repo", "unknown",
             "--dsn", "postgres://invalid"],
            capture_output=True, text=True,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "DatabaseError" in result.stderr, result.stderr

    @pytestmark_integration
    def test_unrecognized_repo_exits_1_with_clear_error(self):
        # Good DSN, unregistered pwd → UnknownRepoError path.
        result = subprocess.run(
            [sys.executable, "-m", "scripts.lib.auto_agent_id",
             "--pwd", "/tmp/foo",
             "--repo", "unknown",
             "--dsn", DSN],
            capture_output=True, text=True,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "UnknownRepoError" in result.stderr or "not a registered" in result.stderr

    @pytestmark_integration
    def test_recognized_repo_emits_json(self):
        # Autouse fixture handles before/after cleanup.
        env = {**os.environ, "DATABASE_URL": DSN}
        result = subprocess.run(
            [sys.executable, "-m", "scripts.lib.auto_agent_id",
             "--pwd", str(os.path.expanduser("~/wingmen/orchestrator")),
             "--repo", "orchestrator",
             "--dsn", DSN,
             "--base-override", "cc-test-family"],
            capture_output=True, text=True, env=env,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["base"] == "cc-test-family"
        assert payload["sub_tag"] == "cc-test-family-1"
        assert isinstance(payload["siblings"], list)
        assert isinstance(payload["overlap_warnings"], list)


# ---------------------------------------------------------------------------
# Step 3.5 additions — Task 15: G3 MAX_SUB_TAGS + A1 lock-namespace + A3 guard
# ---------------------------------------------------------------------------

import ast
from pathlib import Path

from scripts.lib.auto_agent_id import (
    pick_sub_tag,
    NamespaceExhaustedError,
    _MAX_SUB_TAGS_PER_BASE,
    _ALLOC_LOCK_ID,
)


def test_max_sub_tags_ceiling_is_20():
    assert _MAX_SUB_TAGS_PER_BASE == 20


def test_alloc_lock_id_is_registered_int():
    assert isinstance(_ALLOC_LOCK_ID, int)
    assert _ALLOC_LOCK_ID == 1001


def test_pick_sub_tag_raises_when_all_slots_taken():
    base = "cc-test-family"
    active = [f"{base}-{n}" for n in range(1, _MAX_SUB_TAGS_PER_BASE + 1)]
    with pytest.raises(NamespaceExhaustedError) as exc:
        pick_sub_tag(base, active)
    msg = str(exc.value)
    assert base in msg
    assert str(_MAX_SUB_TAGS_PER_BASE) in msg
    # The message must include the siblings list so the operator can spot the culprit.
    assert "cc-test-family-20" in msg


def test_pick_sub_tag_returns_first_free_below_ceiling():
    base = "cc-test-family"
    active = [f"{base}-{n}" for n in range(1, _MAX_SUB_TAGS_PER_BASE)]  # 1..19 taken
    assert pick_sub_tag(base, active) == f"{base}-{_MAX_SUB_TAGS_PER_BASE}"


def test_auto_agent_id_does_not_import_supabase_py():
    """A3 guard: allocate_sub_tag_and_register must stay on psycopg.
    supabase-py is PostgREST + pooled — incompatible with GUC."""
    module_src = Path("scripts/lib/auto_agent_id.py").read_text()
    tree = ast.parse(module_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "supabase" not in alias.name.lower(), f"found import {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").lower()
            assert "supabase" not in mod, f"found from-import {node.module}"


@pytestmark_integration
def test_allocate_sub_tag_populates_base_agent_id():
    """BUG-024 Phase 1B: allocate_sub_tag_and_register writes base_agent_id = family on agent_status.

    Regression test: verifies the Phase 1B UPSERT change in auto_agent_id.py
    populates the base_agent_id FK column. Uses cc-test-family (registered in
    agents + cleaned by autouse fixture) for safe isolation — the plan-verbatim
    cc-scholar would mutate a real family.
    """
    import psycopg

    # Autouse fixture already DELETEd cc-test-family-% rows + ensured agents row.
    result = auto_agent_id.allocate_sub_tag_and_register(
        base="cc-test-family",
        dsn=DSN,
        repo="orchestrator",
    )
    assert result.sub_tag == "cc-test-family-1"

    with psycopg.connect(DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT agent_id, base_agent_id FROM agent_status WHERE agent_id = %s",
            (result.sub_tag,),
        )
        row = cur.fetchone()
        assert row is not None, f"agent_status row not found for {result.sub_tag}"
        assert row[1] == "cc-test-family", (
            f"base_agent_id should be cc-test-family, got {row[1]}"
        )
    # Autouse fixture handles teardown.


# ── CC_BASE_OVERRIDE guardrail (CAI-RESP-258) ─────────────────────────────────
# Pure unit tests — no DB. The override lets spawn_reviewer.sh allocate
# cc-reviewer-N regardless of pwd; the guardrail must hard-refuse forging an
# authority/system identity and default-deny unknown / non-cc-* families.

_KNOWN = {"cc-ihsanos", "cc-cosem", "cc-reviewer"}


@pytest.mark.parametrize("authority", ["cai", "musa", "substrate", "broadcast"])
def test_base_override_refuses_authority_identity(authority):
    with pytest.raises(auto_agent_id.OverrideRefused):
        auto_agent_id.validate_base_override(authority, _KNOWN)


def test_base_override_refuses_non_cc_prefix():
    with pytest.raises(auto_agent_id.OverrideRefused):
        auto_agent_id.validate_base_override("random-family", _KNOWN)


def test_base_override_refuses_unknown_cc_family():
    with pytest.raises(auto_agent_id.OverrideRefused):
        auto_agent_id.validate_base_override("cc-ghost", _KNOWN)


def test_base_override_accepts_known_cc_family():
    assert auto_agent_id.validate_base_override("cc-reviewer", _KNOWN) == "cc-reviewer"


# ── Sticky instance ids per tmux session (orch-console bus #57914) ────────────
# Incident 2026-10-07: tmux session cosem-platform-adhoc was cc-cosem-platform-5,
# relaunched (claude --resume, SAME session) and was handed -3 (smallest free);
# cosem-platform-author then took -5. The resumed conversation still believed it was
# -5, so bus rows addressed to -5 went to the wrong lane. Allocation must be sticky
# per tmux session.

B = "cc-cosem-platform"


class TestChooseStickySubTag:
    def test_reuses_most_recent_session_row(self):
        # adhoc's last row was -5 (stale/offline after relaunch); nobody else holds -5.
        got = auto_agent_id.choose_sticky_sub_tag(
            B, "cosem-platform-adhoc",
            session_rows=[f"{B}-5", f"{B}-2"],
            live_rows=[(f"{B}-1", "cosem-port"), (f"{B}-3", "cosem-platform-calendar")],
        )
        assert got == f"{B}-5"

    def test_reuses_when_held_live_by_same_session(self):
        # Relaunch within the 30-min live window: the old row is still 'working' and
        # fresh, but it belongs to OUR session — that's us, reuse it.
        got = auto_agent_id.choose_sticky_sub_tag(
            B, "cosem-platform-adhoc",
            session_rows=[f"{B}-5"],
            live_rows=[(f"{B}-5", "cosem-platform-adhoc")],
        )
        assert got == f"{B}-5"

    def test_refused_when_held_live_by_different_session(self):
        got = auto_agent_id.choose_sticky_sub_tag(
            B, "cosem-platform-adhoc",
            session_rows=[f"{B}-5"],
            live_rows=[(f"{B}-5", "cosem-platform-author")],
        )
        assert got is None

    def test_refused_when_held_live_by_sessionless_row(self):
        # A live holder with NULL tmux_session cannot be proven to be us — refuse.
        got = auto_agent_id.choose_sticky_sub_tag(
            B, "cosem-platform-adhoc",
            session_rows=[f"{B}-5"],
            live_rows=[(f"{B}-5", None)],
        )
        assert got is None

    def test_no_session_rows_returns_none(self):
        assert auto_agent_id.choose_sticky_sub_tag(B, "s", [], []) is None

    def test_no_session_given_returns_none(self):
        assert auto_agent_id.choose_sticky_sub_tag(B, None, [f"{B}-5"], []) is None
        assert auto_agent_id.choose_sticky_sub_tag(B, "", [f"{B}-5"], []) is None

    def test_only_most_recent_parsable_row_considered(self):
        # Foreign-family / unparsable ids are skipped; the first PARSABLE row wins,
        # and if it is refused we do NOT fall through to an older row.
        got = auto_agent_id.choose_sticky_sub_tag(
            "cc-cosem", "s",
            session_rows=["cc-cosem-platform-3", "cc-cosem-7", "cc-cosem-2"],
            live_rows=[],
        )
        assert got == "cc-cosem-7"
        got = auto_agent_id.choose_sticky_sub_tag(
            B, "s",
            session_rows=[f"{B}-7", f"{B}-2"],
            live_rows=[(f"{B}-7", "other")],
        )
        assert got is None

    def test_out_of_namespace_n_refused(self):
        got = auto_agent_id.choose_sticky_sub_tag(B, "s", [f"{B}-0", f"{B}-21"], [])
        assert got is None


class TestAliveSessionClaims:
    def test_alive_other_session_claims_its_n(self):
        claimed = auto_agent_id.alive_session_claims(
            B,
            claim_rows=[(f"{B}-1", "cosem-port", "Sheikhs-Mini"),
                        (f"{B}-2", "dead-session", "Sheikhs-Mini")],
            tmux_session="new-lane", host="Sheikhs-Mini",
            alive_sessions={"cosem-port", "new-lane"},
        )
        assert claimed == [f"{B}-1"]

    def test_own_session_not_a_claim(self):
        claimed = auto_agent_id.alive_session_claims(
            B, [(f"{B}-1", "me", "h")], tmux_session="me", host="h", alive_sessions={"me"},
        )
        assert claimed == []

    def test_other_host_same_name_not_a_claim(self):
        # alive_sessions are THIS host's tmux sessions; a row on gzb with the same
        # session name is a different session.
        claimed = auto_agent_id.alive_session_claims(
            B, [(f"{B}-1", "lane", "gzb")], tmux_session="me", host="Sheikhs-Mini",
            alive_sessions={"lane"},
        )
        assert claimed == []

    def test_unknown_host_is_conservative(self):
        # Row host NULL, or our host unknown -> treat a live-named session as a claim.
        assert auto_agent_id.alive_session_claims(
            B, [(f"{B}-1", "lane", None)], "me", "Sheikhs-Mini", {"lane"}) == [f"{B}-1"]
        assert auto_agent_id.alive_session_claims(
            B, [(f"{B}-1", "lane", "gzb")], "me", None, {"lane"}) == [f"{B}-1"]

    def test_null_session_rows_never_claim(self):
        assert auto_agent_id.alive_session_claims(
            B, [(f"{B}-1", None, "h")], "me", "h", {"lane"}) == []

    def test_empty_alive_set_no_claims(self):
        assert auto_agent_id.alive_session_claims(
            B, [(f"{B}-1", "lane", "h")], "me", "h", set()) == []


class TestPickSubTagForSession:
    def test_sticky_reuse(self):
        sub, sticky = auto_agent_id.pick_sub_tag_for_session(
            B,
            live_rows=[(f"{B}-1", "cosem-port"), (f"{B}-2", "cal")],
            session_rows=[f"{B}-5"],
            claim_rows=[],
            tmux_session="cosem-platform-adhoc", host="Sheikhs-Mini", alive_sessions=set(),
        )
        assert (sub, sticky) == (f"{B}-5", True)

    def test_sticky_refused_falls_back_to_smallest_free(self):
        sub, sticky = auto_agent_id.pick_sub_tag_for_session(
            B,
            live_rows=[(f"{B}-1", "cosem-port"), (f"{B}-5", "cosem-platform-author")],
            session_rows=[f"{B}-5"],
            claim_rows=[],
            tmux_session="cosem-platform-adhoc", host="Sheikhs-Mini", alive_sessions=set(),
        )
        assert (sub, sticky) == (f"{B}-2", False)

    def test_fallback_skips_n_claimed_by_alive_other_session(self):
        # -2's row is stale (not live) but its tmux session is still alive on this
        # host — that lane still believes it is -2. Do NOT hand -2 out.
        sub, sticky = auto_agent_id.pick_sub_tag_for_session(
            B,
            live_rows=[(f"{B}-1", "cosem-port")],
            session_rows=[],
            claim_rows=[(f"{B}-1", "cosem-port", "Sheikhs-Mini"),
                        (f"{B}-2", "cosem-platform-calendar", "Sheikhs-Mini"),
                        (f"{B}-3", "long-dead", "Sheikhs-Mini")],
            tmux_session="brand-new", host="Sheikhs-Mini",
            alive_sessions={"cosem-port", "cosem-platform-calendar", "brand-new"},
        )
        assert (sub, sticky) == (f"{B}-3", False)

    def test_incident_replay(self):
        # 2026-10-07: adhoc held -5, author held -6. Both relaunch. Each must keep its N.
        claim = [(f"{B}-1", "cosem-port", "M"), (f"{B}-2", "cal", "M"),
                 (f"{B}-4", "cal2", "M"),
                 (f"{B}-5", "cosem-platform-adhoc", "M"),
                 (f"{B}-6", "cosem-platform-author", "M")]
        live = [(f"{B}-1", "cosem-port"), (f"{B}-2", "cal"), (f"{B}-4", "cal2")]
        alive = {"cosem-port", "cal", "cal2", "cosem-platform-adhoc", "cosem-platform-author"}
        a = auto_agent_id.pick_sub_tag_for_session(
            B, live, [f"{B}-5"], claim, "cosem-platform-adhoc", "M", alive)
        b = auto_agent_id.pick_sub_tag_for_session(
            B, live, [f"{B}-6"], claim, "cosem-platform-author", "M", alive)
        assert a == (f"{B}-5", True)
        assert b == (f"{B}-6", True)

    @pytest.mark.parametrize("live", [
        [], [f"{B}-1"], [f"{B}-1", f"{B}-2", f"{B}-4"], [f"{B}-2", "cc-other-1"],
    ])
    def test_no_session_identical_to_pick_sub_tag(self, live):
        rows = [(a, "some-session") for a in live]
        claims = [(f"{B}-{n}", "alive-sess", "M") for n in range(1, 6)]
        sub, sticky = auto_agent_id.pick_sub_tag_for_session(
            B, rows, [f"{B}-9"], claims, None, "M", {"alive-sess"})
        assert sub == auto_agent_id.pick_sub_tag(B, live)
        assert sticky is False

    # ── cross-host sticky-collision guard (hub bug, bus #73472/#73476) ──────
    # gz's cc-cosem-platform-3 was demonstrably alive, then clobbered by a
    # Mini-side process that ALSO sticky-claimed -3 because both hosts name
    # the role's tmux session identically ("cosem-platform-author"). Root
    # cause: choose_sticky_sub_tag's invalidation only compares session NAME,
    # blind to host, because live_rows carries no host field.
    def test_sticky_refused_when_same_named_session_is_a_different_host(self):
        live = [(f"{B}-3", "cosem-platform-author")]  # gz's row, globally live
        claim = [(f"{B}-3", "cosem-platform-author", "gzb")]  # gz's own host
        sub, sticky = auto_agent_id.pick_sub_tag_for_session(
            B, live, session_rows=[f"{B}-3"], claim_rows=claim,
            tmux_session="cosem-platform-author", host="Sheikhs-Mini",
            alive_sessions=set(),
        )
        # must NOT reuse -3 (that would clobber gz's live row) -- falls to smallest-free.
        assert sticky is False
        assert sub != f"{B}-3"

    def test_sticky_allowed_when_same_named_session_is_the_same_host(self):
        # The legitimate case this guard must NOT break: our own prior row, same
        # host, relaunching under the same session name.
        live = [(f"{B}-3", "cosem-platform-author")]
        claim = [(f"{B}-3", "cosem-platform-author", "Sheikhs-Mini")]
        sub, sticky = auto_agent_id.pick_sub_tag_for_session(
            B, live, session_rows=[f"{B}-3"], claim_rows=claim,
            tmux_session="cosem-platform-author", host="Sheikhs-Mini",
            alive_sessions=set(),
        )
        assert (sub, sticky) == (f"{B}-3", True)

    def test_sticky_refused_when_claim_row_host_is_unknown(self):
        # Our own host known, but the live claimant's host is NULL -- can't prove
        # it's foreign, but also can't prove it's ours; conservative: refuse,
        # same "unknown host on either side" rule as alive_session_claims.
        live = [(f"{B}-3", "cosem-platform-author")]
        claim = [(f"{B}-3", "cosem-platform-author", None)]
        sub, sticky = auto_agent_id.pick_sub_tag_for_session(
            B, live, session_rows=[f"{B}-3"], claim_rows=claim,
            tmux_session="cosem-platform-author", host="Sheikhs-Mini",
            alive_sessions=set(),
        )
        assert sticky is False
        assert sub != f"{B}-3"

    def test_sticky_refused_when_our_own_host_is_unknown(self):
        live = [(f"{B}-3", "cosem-platform-author")]
        claim = [(f"{B}-3", "cosem-platform-author", "Sheikhs-Mini")]
        sub, sticky = auto_agent_id.pick_sub_tag_for_session(
            B, live, session_rows=[f"{B}-3"], claim_rows=claim,
            tmux_session="cosem-platform-author", host=None,
            alive_sessions=set(),
        )
        assert sticky is False

    def test_sticky_allowed_when_candidate_not_live_anywhere(self):
        # candidate isn't in live_rows at all -- nothing to clobber, guard is a no-op
        # (choose_sticky_sub_tag's own logic already allows this: not-live is fine).
        claim = [(f"{B}-3", "cosem-platform-author", "gzb")]
        sub, sticky = auto_agent_id.pick_sub_tag_for_session(
            B, live_rows=[], session_rows=[f"{B}-3"], claim_rows=claim,
            tmux_session="cosem-platform-author", host="Sheikhs-Mini",
            alive_sessions=set(),
        )
        assert (sub, sticky) == (f"{B}-3", True)


# ── allocate_sub_tag_and_register SQL wiring (fake psycopg, no DB) ────────────

class _ScriptedCur:
    """Fake cursor: answers each SELECT by substring match, records every execute."""

    def __init__(self, answers):
        self.answers = answers  # list of (substring, rows)
        self.executed = []
        self._last = []

    def __enter__(self): return self
    def __exit__(self, *a): pass

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
        self._last = []
        for needle, rows in self.answers:
            if needle in sql:
                self._last = rows
                break

    def fetchone(self):
        return self._last[0] if self._last else None

    def fetchall(self):
        return list(self._last)


class _ScriptedConn:
    def __init__(self, cur): self.cur = cur; self.committed = False
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def cursor(self): return self.cur
    def commit(self): self.committed = True


def _run_alloc(monkeypatch, answers, **kw):
    import psycopg
    cur = _ScriptedCur([("pg_try_advisory_xact_lock", [(True,)])] + answers)
    conn = _ScriptedConn(cur)
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: conn)
    res = auto_agent_id.allocate_sub_tag_and_register(
        base=B, dsn="fake", repo="cosem-platform", **kw)
    assert conn.committed
    return res, cur


def test_alloc_registers_instance_in_agents_table(monkeypatch):
    """#57914 follow-up: cc-cosem-adcda-4 was allocated but never registered in
    `agents`, so every bus send to it hit agent_messages_to_agent_fkey. The
    allocation TX must INSERT the instance id into agents (idempotent)."""
    res, cur = _run_alloc(monkeypatch, [("AND status != 'offline'", [(f"{B}-1", "x")])])
    assert res.sub_tag == f"{B}-2"
    ins = [(s, p) for s, p in cur.executed if s.startswith("INSERT INTO agents")]
    assert len(ins) == 1
    sql, params = ins[0]
    assert "ON CONFLICT (id) DO NOTHING" in sql
    assert params[0] == f"{B}-2"
    assert params[1] == f"{B}-2 -- auto-registered instance (launcher)"
    assert "'active'" in sql
    # registered BEFORE the agent_status upsert, inside the same (locked) TX
    order = [s.split()[2] for s, _ in cur.executed if s.startswith("INSERT INTO")]
    assert order == ["agents", "agent_status"]


def test_alloc_without_session_issues_no_sticky_queries(monkeypatch):
    res, cur = _run_alloc(monkeypatch, [("AND status != 'offline'", [])])
    assert res.sub_tag == f"{B}-1"
    assert res.sticky is False
    assert not any("tmux_session =" in s for s, _ in cur.executed)


def test_alloc_with_session_reuses_sticky_n(monkeypatch):
    res, cur = _run_alloc(
        monkeypatch,
        [
            ("AND status != 'offline'", [(f"{B}-1", "cosem-port")]),
            ("AND tmux_session = %s", [(f"{B}-5",)]),
            ("AND tmux_session IS NOT NULL", [(f"{B}-5", "cosem-platform-adhoc", "M")]),
        ],
        tmux_session="cosem-platform-adhoc", host="M", alive_sessions={"cosem-platform-adhoc"},
    )
    assert res.sub_tag == f"{B}-5"
    assert res.sticky is True
    sticky_q = [(s, p) for s, p in cur.executed if "AND tmux_session = %s" in s]
    assert sticky_q and sticky_q[0][1][-1] == "M"  # host-scoped when host known
    assert "ORDER BY last_heartbeat DESC" in sticky_q[0][0]


def test_cli_passes_tmux_session_and_host(monkeypatch):
    seen = {}
    # (Non-singleton example family: a pwd-derived cc-orchestrator is now REFUSED, #77922.)
    monkeypatch.setattr(auto_agent_id, "load_family_map", lambda dsn: {"substrate": "cc-substrate"})
    monkeypatch.setattr(auto_agent_id, "resolve_base_agent_id", lambda pwd, m: "cc-substrate")
    monkeypatch.setattr(auto_agent_id, "singleton_base_ids", lambda dsn=None: frozenset({"cc-orchestrator"}))
    monkeypatch.setattr(auto_agent_id, "reap_stale_family", lambda base, dsn: None)
    monkeypatch.setattr(auto_agent_id, "scan_overlap_siblings", lambda **k: [])
    monkeypatch.setattr(auto_agent_id, "list_alive_tmux_sessions", lambda: {"a", "b"})

    def _alloc(**k):
        seen.update(k)
        return auto_agent_id.AllocResult(sub_tag="cc-substrate-3", siblings=[], sticky=True)
    monkeypatch.setattr(auto_agent_id, "allocate_sub_tag_and_register", _alloc)
    rc = auto_agent_id.main(["--pwd", "/x", "--repo", "orchestrator", "--dsn", "d",
                             "--tmux-session", "lane-a", "--host", "M"])
    assert rc == 0
    assert seen["tmux_session"] == "lane-a"
    assert seen["host"] == "M"
    assert seen["alive_sessions"] == {"a", "b"}


def test_cli_without_session_does_not_probe_tmux(monkeypatch):
    seen = {}
    monkeypatch.setattr(auto_agent_id, "load_family_map", lambda dsn: {})
    monkeypatch.setattr(auto_agent_id, "resolve_base_agent_id", lambda pwd, m: "cc-substrate")
    monkeypatch.setattr(auto_agent_id, "singleton_base_ids", lambda dsn=None: frozenset({"cc-orchestrator"}))
    monkeypatch.setattr(auto_agent_id, "reap_stale_family", lambda base, dsn: None)
    monkeypatch.setattr(auto_agent_id, "scan_overlap_siblings", lambda **k: [])

    def _boom():
        raise AssertionError("tmux probed without --tmux-session")
    monkeypatch.setattr(auto_agent_id, "list_alive_tmux_sessions", _boom)

    def _alloc(**k):
        seen.update(k)
        return auto_agent_id.AllocResult(sub_tag="cc-substrate-1", siblings=[])
    monkeypatch.setattr(auto_agent_id, "allocate_sub_tag_and_register", _alloc)
    assert auto_agent_id.main(["--pwd", "/x", "--repo", "o", "--dsn", "d"]) == 0
    assert seen.get("tmux_session") is None


def test_list_alive_tmux_sessions_fails_safe(monkeypatch):
    def _raise(*a, **k):
        raise FileNotFoundError("tmux")
    monkeypatch.setattr(auto_agent_id.subprocess, "run", _raise)
    assert auto_agent_id.list_alive_tmux_sessions() == set()

    class _R:
        returncode = 1
        stdout = ""
    monkeypatch.setattr(auto_agent_id.subprocess, "run", lambda *a, **k: _R())
    assert auto_agent_id.list_alive_tmux_sessions() == set()

    class _R2:
        returncode = 0
        stdout = "a\nb\n\n"
    monkeypatch.setattr(auto_agent_id.subprocess, "run", lambda *a, **k: _R2())
    assert auto_agent_id.list_alive_tmux_sessions() == {"a", "b"}


@pytestmark_integration
def test_integration_sticky_and_agents_registration():
    """Live-DB: a relaunch in the same tmux session gets its old N back, and the
    instance id lands in `agents`."""
    import psycopg
    r1 = auto_agent_id.allocate_sub_tag_and_register(
        base="cc-test-family", dsn=DSN, repo="orchestrator",
        tmux_session="test-sticky-A", host="test-host", alive_sessions=set())
    r2 = auto_agent_id.allocate_sub_tag_and_register(
        base="cc-test-family", dsn=DSN, repo="orchestrator",
        tmux_session="test-sticky-B", host="test-host", alive_sessions=set())
    assert (r1.sub_tag, r2.sub_tag) == ("cc-test-family-1", "cc-test-family-2")
    # stamp tmux_session/host the way agent_status_stamp.py would at boot
    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        for sub, sess in ((r1.sub_tag, "test-sticky-A"), (r2.sub_tag, "test-sticky-B")):
            cur.execute("SELECT set_config('app.current_agent_id', %s, false)", (sub,))
            cur.execute("UPDATE agent_status SET tmux_session=%s, host='test-host' "
                        "WHERE agent_id=%s", (sess, sub))
        # lane A goes offline (relaunch)
        cur.execute("SELECT set_config('app.current_agent_id', %s, false)", (r1.sub_tag,))
        cur.execute("UPDATE agent_status SET status='offline' WHERE agent_id=%s", (r1.sub_tag,))
    # A relaunches — must get -1 back even though smallest-free would also be -1;
    # make it non-trivial: B relaunching must get -2, not the free -1.
    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id', %s, false)", (r2.sub_tag,))
        cur.execute("UPDATE agent_status SET status='offline' WHERE agent_id=%s", (r2.sub_tag,))
    rb = auto_agent_id.allocate_sub_tag_and_register(
        base="cc-test-family", dsn=DSN, repo="orchestrator",
        tmux_session="test-sticky-B", host="test-host", alive_sessions=set())
    assert rb.sub_tag == "cc-test-family-2" and rb.sticky is True
    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute("SELECT id, status FROM agents WHERE id LIKE 'cc-test-family-%%' ORDER BY id")
        assert cur.fetchall() == [("cc-test-family-1", "active"), ("cc-test-family-2", "active")]


def test_launcher_resolves_session_and_host_before_allocation():
    """#57914: launch_dangerous_cc.sh must resolve the tmux session + host BEFORE the
    auto_agent_id call and pass them, and must not re-assign them afterwards."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "scripts" / "launch_dangerous_cc.sh").read_text()
    alloc = src.index("-m scripts.lib.auto_agent_id")
    assert src.index('_ALLOC_TMUX_SESSION="$(resolve_lane_session)"') < alloc
    assert src.index('CC_HOST="$(') < alloc
    assert '${_ALLOC_TMUX_SESSION:+--tmux-session "$_ALLOC_TMUX_SESSION"}' in src
    assert '${_ALLOC_HOST:+--host "$_ALLOC_HOST"}' in src
    # single assignment site each (reused later, never recomputed)
    assert src.count('CC_HOST="$(') == 1
    assert src.count("CC_TMUX_SESSION=\"$_ALLOC_TMUX_SESSION\"") == 1
    assert 'CC_TMUX_SESSION="${_BODY_MODEL_SESSION:-}"' not in src
    # the untargeted display-message fallback must NOT feed the allocator
    assert "_ALLOC_TMUX_SESSION=\"$(tmux display-message" not in src



# ── pwd-derived SINGLETON base is refused (orch-console #77922, 2026-10-27) ────────────────
# A manual launch in orchestrator.wt-cleanup derived 'cc-orchestrator' from pwd and minted
# 'cc-orchestrator-1' -- a cc-substrate worker impersonating the hub.
def _stub_alloc(monkeypatch, seen):
    monkeypatch.setattr(auto_agent_id, "singleton_base_ids", lambda dsn=None: frozenset(
        {"cc-orchestrator", "orch-console", "cai", "cc-fleet-health", "cc-quality"}))
    monkeypatch.setattr(auto_agent_id, "reap_stale_family", lambda base, dsn: None)
    monkeypatch.setattr(auto_agent_id, "scan_overlap_siblings", lambda **k: [])

    def _alloc(**k):
        seen.update(k)
        return auto_agent_id.AllocResult(sub_tag=f"{k['base']}-1", siblings=[])
    monkeypatch.setattr(auto_agent_id, "allocate_sub_tag_and_register", _alloc)


def test_pwd_derived_singleton_base_is_refused_and_never_allocates(monkeypatch, capsys):
    seen = {}
    _stub_alloc(monkeypatch, seen)
    monkeypatch.setattr(auto_agent_id, "load_family_map",
                        lambda dsn: {"orchestrator": "cc-orchestrator"})
    rc = auto_agent_id.main(["--pwd", "/Users/x/wingmen/projects/orchestrator.wt-cleanup",
                             "--repo", "orchestrator", "--dsn", "d"])
    assert rc == 1
    assert seen == {}, "a refused launch must not allocate or register anything"
    err = capsys.readouterr().err
    assert "PwdSingletonRefused" in err and "CC_BASE_OVERRIDE" in err and "lanes.sh up" in err


def test_lanes_sh_override_into_a_singleton_family_still_allocates(monkeypatch):
    # lanes.sh pins CC_BASE_OVERRIDE=<fleet_lanes.base_agent_id>; the 'quality' lane's base is
    # cc-quality (a singleton). An EXPLICIT override must keep working.
    seen = {}
    _stub_alloc(monkeypatch, seen)
    monkeypatch.setattr(auto_agent_id, "load_cc_families", lambda dsn: {"cc-quality", "cc-substrate"})
    monkeypatch.setattr(auto_agent_id, "load_family_map",
                        lambda dsn: pytest.fail("override must skip pwd resolution"))
    rc = auto_agent_id.main(["--pwd", "/Users/x/wingmen/projects/orchestrator.wt-anything",
                             "--repo", "orchestrator", "--dsn", "d", "--base-override", "cc-quality"])
    assert rc == 0 and seen["base"] == "cc-quality"


def test_lanes_sh_override_for_a_worker_lane_in_an_orchestrator_worktree_allocates(monkeypatch):
    # The exact lane that went wrong, launched the sanctioned way: lanes.sh up substrate-cleanup.
    seen = {}
    _stub_alloc(monkeypatch, seen)
    monkeypatch.setattr(auto_agent_id, "load_cc_families", lambda dsn: {"cc-substrate"})
    rc = auto_agent_id.main(["--pwd", "/Users/x/wingmen/projects/orchestrator.wt-cleanup",
                             "--repo", "orchestrator", "--dsn", "d", "--base-override", "cc-substrate"])
    assert rc == 0 and seen["base"] == "cc-substrate"


def test_pwd_derived_non_singleton_still_allocates(monkeypatch):
    seen = {}
    _stub_alloc(monkeypatch, seen)
    monkeypatch.setattr(auto_agent_id, "load_family_map", lambda dsn: {"ihsanos": "cc-ihsanos"})
    rc = auto_agent_id.main(["--pwd", "/Users/x/wingmen/projects/ihsanos.wt-fix",
                             "--repo", "ihsanos", "--dsn", "d"])
    assert rc == 0 and seen["base"] == "cc-ihsanos"
