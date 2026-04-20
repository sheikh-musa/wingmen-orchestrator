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
    Makes teardown crash-safe — prior aborted runs don't leak state."""
    if not DSN:
        yield
        return
    import psycopg
    def _purge():
        with psycopg.connect(DSN, autocommit=True) as c:
            with c.cursor() as cur:
                cur.execute("DELETE FROM agent_status WHERE agent_id LIKE 'cc-test-family-%%'")
    _purge()
    yield
    _purge()


@pytestmark_integration
class TestLoadFamilyMap:
    def test_returns_all_four_cc_families_canonicalized(self):
        m = auto_agent_id.load_family_map(DSN)
        # 4 families live (post CAI-AGENTS-001 split).
        assert m["ihsanos"] == "cc-ihsanos"
        assert m["orchestrator"] == "cc-ihsanos"  # 'wingmen-' stripped
        assert m["ai-scholar"] == "cc-scholar"
        assert m["hifz-companion"] == "cc-scholar"
        assert m["dookana"] == "cc-web"
        assert m["wordpress-sites"] == "cc-web"
        assert m["cosem-tdu"] == "cc-cosem"
        assert m["cosem-adcda"] == "cc-cosem"

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


# Fixture-style map matching live agents table at delta-v2 time.
FAKE_MAP = {
    "ihsanos": "cc-ihsanos",
    "orchestrator": "cc-ihsanos",
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
    def test_orchestrator_maps_to_cc_ihsanos(self, monkeypatch):
        monkeypatch.setattr(auto_agent_id, "_git_toplevel",
                            lambda pwd: "/Users/sheikhmusa/wingmen/orchestrator")
        assert auto_agent_id.resolve_base_agent_id(
            "/Users/sheikhmusa/wingmen/orchestrator", FAKE_MAP
        ) == "cc-ihsanos"

    def test_orchestrator_worktree_LEDGER_maps(self, monkeypatch):
        # Worktree: git rev-parse --show-toplevel returns the worktree path.
        monkeypatch.setattr(auto_agent_id, "_git_toplevel",
                            lambda pwd: "/Users/sheikhmusa/wingmen/orchestrator-LEDGER")
        assert auto_agent_id.resolve_base_agent_id(
            "/Users/sheikhmusa/wingmen/orchestrator-LEDGER", FAKE_MAP
        ) == "cc-ihsanos"

    def test_orchestrator_worktree_dot_wt_maps(self, monkeypatch):
        monkeypatch.setattr(auto_agent_id, "_git_toplevel",
                            lambda pwd: "/Users/sheikhmusa/wingmen/orchestrator.wt-qurban")
        assert auto_agent_id.resolve_base_agent_id(
            "/Users/sheikhmusa/wingmen/orchestrator.wt-qurban", FAKE_MAP
        ) == "cc-ihsanos"

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
                        "SELECT status, current_task, scope_repos "
                        "FROM agent_status WHERE agent_id = %s",
                        (result.sub_tag,),
                    )
                    row = cur.fetchone()
                    assert row is not None
                    assert row[0] == "working"
                    assert row[1] == "session-launch"
                    assert row[2] == ["orchestrator"]
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
                    "(agent_id, status, last_heartbeat, updated_at) "
                    "VALUES (%s, 'working', now() - interval '2 hours', now() - interval '2 hours') "
                    "ON CONFLICT (agent_id) DO UPDATE SET "
                    "last_heartbeat = EXCLUDED.last_heartbeat",
                    ("cc-test-family-1",),
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
                    "(agent_id, status, last_heartbeat, updated_at) "
                    "VALUES (%s, 'working', now(), now()) "
                    "ON CONFLICT (agent_id) DO UPDATE SET "
                    "last_heartbeat = now()",
                    ("cc-test-family-1",),
                )
            setup_conn.commit()

        result = auto_agent_id.allocate_sub_tag_and_register(
            base="cc-test-family",
            dsn=DSN,
            repo="orchestrator",
        )
        assert result.sub_tag == "cc-test-family-2"
        assert "cc-test-family-1" in result.siblings


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
                      "(agent_id, status, scope_repos, last_heartbeat, updated_at) "
                      "VALUES (%s, 'working', ARRAY[%s]::text[], now(), now()) "
                      "ON CONFLICT (agent_id) DO UPDATE SET "
                      "scope_repos = EXCLUDED.scope_repos, "
                      "last_heartbeat = now()",
                      (f"cc-test-family-{n}", scope),
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
                  "(agent_id, status, scope_repos, last_heartbeat, updated_at) "
                  "VALUES (%s, 'working', ARRAY['dookana']::text[], now(), now()) "
                  "ON CONFLICT (agent_id) DO UPDATE SET "
                  "scope_repos = EXCLUDED.scope_repos, last_heartbeat = now()",
                  ("cc-test-family-1",),
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
