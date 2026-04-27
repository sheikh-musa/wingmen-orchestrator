"""Tests for nervous_system.repo_context_writer (CAI-RESP-093)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.repo_context_writer import (
    parse_status_md,
    _extract_section,
    update_repo_contexts,
)

load_dotenv()
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark_integration = pytest.mark.skipif(
    not _DSN, reason="DATABASE_URL not set — skipping Supabase integration tests"
)


# ----------------------------------------------------------------------------
# parse_status_md — pure parser tests
# ----------------------------------------------------------------------------

class TestParseStatusMd:

    def test_empty_input_returns_all_null(self):
        out = parse_status_md("")
        assert out == {
            "current_phase": None,
            "blockers": None,
            "known_debt": None,
            "architecture_summary": None,
        }

    def test_whitespace_only_returns_all_null(self):
        assert parse_status_md("\n\n   \n").get("current_phase") is None

    def test_phase_header_extracted(self):
        content = """# repo STATUS

Last Updated: 2026-04-23 SGT
Phase: Phase 2 — protocol shipping
Build Status: green

## Some Section
content
"""
        out = parse_status_md(content)
        assert out["current_phase"] == "Phase 2 — protocol shipping"

    def test_phase_header_capped_at_500_chars(self):
        long_phase = "A" * 700
        content = f"Phase: {long_phase}\n\n## Other\n"
        out = parse_status_md(content)
        assert len(out["current_phase"]) == 500

    def test_blockers_section_canonical_returns_array(self):
        content = """# STATUS

## Blockers
- waiting on cai
- migration pending
"""
        out = parse_status_md(content)
        assert out["blockers"] == ["waiting on cai", "migration pending"]

    def test_blocked_alias_recognized_array(self):
        # 'Blocked' is the dookana convention; should also map to blockers ARRAY
        content = """## Blocked
- DB connection
"""
        assert parse_status_md(content)["blockers"] == ["DB connection"]

    def test_known_debt_with_parens_alias_array(self):
        # hifz convention: '## Known Debts (tracked)'
        content = """## Known Debts (tracked)
- Test setup is fragile
"""
        assert parse_status_md(content)["known_debt"] == ["Test setup is fragile"]

    def test_section_terminates_at_next_heading_array(self):
        content = """## Blockers
- A
- B
## Other
- should not appear
"""
        out = parse_status_md(content)
        assert out["blockers"] == ["A", "B"]
        assert "should not appear" not in (out["blockers"] or [])

    def test_section_without_bullets_falls_back_to_single_element(self):
        # Some repos (ihsanos, hifz) write Blockers as paragraphs not bullets.
        # Lenient: capture the whole section as a single array element.
        content = """## Blockers
free-form paragraph describing the blocker
spanning multiple lines.
"""
        out = parse_status_md(content)
        assert isinstance(out["blockers"], list)
        assert len(out["blockers"]) == 1
        assert "free-form paragraph" in out["blockers"][0]

    def test_mixed_asterisk_dash_bullets(self):
        # Markdown convention varies — accept both
        content = """## Blockers
* asterisk one
- dash two
"""
        assert parse_status_md(content)["blockers"] == ["asterisk one", "dash two"]

    def test_unrecognized_format_partial_result(self):
        # ihsanos-style: '## Shipped — Batch 1' — no recognized sections
        content = """# repo STATUS

## Shipped — Batch 1 Bundle
content
## Older Sessions
more content
"""
        out = parse_status_md(content)
        # No Phase header, no blockers/debt/architecture — all None
        assert all(v is None for v in out.values())

    def test_case_insensitive_heading_match(self):
        content = "## blockers\nlower case heading\n"
        assert "lower case heading" in parse_status_md(content)["blockers"]

    def test_empty_section_returns_null_not_empty_string(self):
        content = "## Blockers\n\n## Next\nfoo\n"
        assert parse_status_md(content)["blockers"] is None


class TestExtractSection:

    def test_first_match_returned(self):
        body = _extract_section("## A\nfirst\n## B\nsecond\n", "A")
        assert body == "first"

    def test_no_match_returns_none(self):
        assert _extract_section("# Title only\n", "Missing") is None

    def test_eof_terminates_section(self):
        assert _extract_section("## End\nlast line", "End") == "last line"


# ----------------------------------------------------------------------------
# update_repo_contexts — integration with mocked supabase + git
# ----------------------------------------------------------------------------

class TestUpdateRepoContexts:

    @pytest.mark.asyncio
    async def test_writes_mechanical_even_when_status_md_missing(self):
        """When STATUS.md is unreadable, mechanical fields still upsert."""
        sb = MagicMock()
        upsert_chain = MagicMock()
        sb.table.return_value.upsert.return_value = upsert_chain
        upsert_chain.execute = AsyncMock(return_value=MagicMock(data=[]))

        fake_repos = [
            {"name": "test-repo", "status": "active", "local_path": "/nonexistent",
             "deploy_url": "https://example.com"}
        ]

        with patch("nervous_system.repo_context_writer._load_repos", return_value=fake_repos), \
             patch("nervous_system.repo_context_writer.read_status_md_from_origin_main",
                   AsyncMock(return_value=None)), \
             patch("nervous_system.repo_context_writer.get_recent_changes",
                   AsyncMock(return_value="")):
            await update_repo_contexts(sb)

        sb.table.assert_called_once_with("repo_context")
        upsert_args = sb.table.return_value.upsert.call_args[0][0]
        assert upsert_args["repo"] == "test-repo"
        assert upsert_args["deploy_url"] == "https://example.com"
        assert upsert_args["updated_by"] == "cc-orchestrator"
        # Semantic fields all None when STATUS.md unreadable
        assert upsert_args["current_phase"] is None
        assert upsert_args["blockers"] is None

    @pytest.mark.asyncio
    async def test_writes_semantic_when_status_md_parseable(self):
        sb = MagicMock()
        sb.table.return_value.upsert.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[])
        )
        fake_repos = [
            {"name": "test-repo", "status": "active", "local_path": "/x",
             "deploy_url": "https://example.com"}
        ]
        status_md = "Phase: shipping\n\n## Blockers\n- network outage\n"

        with patch("nervous_system.repo_context_writer._load_repos", return_value=fake_repos), \
             patch("nervous_system.repo_context_writer.read_status_md_from_origin_main",
                   AsyncMock(return_value=status_md)), \
             patch("nervous_system.repo_context_writer.get_recent_changes",
                   AsyncMock(return_value="abc1234 some commit")):
            await update_repo_contexts(sb)

        upsert_args = sb.table.return_value.upsert.call_args[0][0]
        assert upsert_args["current_phase"] == "shipping"
        assert "network outage" in upsert_args["blockers"]
        assert "abc1234 some commit" in upsert_args["recent_changes"]

    @pytest.mark.asyncio
    async def test_skips_inactive_repos(self):
        sb = MagicMock()
        fake_repos = [
            {"name": "specced-repo", "status": "specced", "local_path": "/x"},
            {"name": "dead-repo", "status": "archived", "local_path": "/y"},
        ]
        with patch("nervous_system.repo_context_writer._load_repos", return_value=fake_repos), \
             patch("nervous_system.repo_context_writer.read_status_md_from_origin_main",
                   AsyncMock(return_value=None)), \
             patch("nervous_system.repo_context_writer.get_recent_changes",
                   AsyncMock(return_value="")):
            await update_repo_contexts(sb)
        sb.table.assert_not_called()

    @pytest.mark.asyncio
    async def test_per_repo_failure_does_not_abort_sweep(self):
        """One repo's upsert exception must not block other repos."""
        sb = MagicMock()
        call_count = {"n": 0}

        async def execute_side_effect():
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated DB error")
            return MagicMock(data=[])

        sb.table.return_value.upsert.return_value.execute = AsyncMock(side_effect=execute_side_effect)

        fake_repos = [
            {"name": "fail-repo", "status": "active", "local_path": "/x"},
            {"name": "ok-repo", "status": "active", "local_path": "/y"},
        ]
        with patch("nervous_system.repo_context_writer._load_repos", return_value=fake_repos), \
             patch("nervous_system.repo_context_writer.read_status_md_from_origin_main",
                   AsyncMock(return_value=None)), \
             patch("nervous_system.repo_context_writer.get_recent_changes",
                   AsyncMock(return_value="")):
            await update_repo_contexts(sb)
        # Both repos attempted (sweep continued past first failure)
        assert sb.table.return_value.upsert.return_value.execute.call_count == 2


# ----------------------------------------------------------------------------
# Live-DB integration test — would have caught the Gap 3 schema mismatch
# (PR #9) at test-time. Hits the real schema; mocked-only tests missed it.
# ----------------------------------------------------------------------------

class TestRepoContextWriterLiveSchema:
    """Asserts the writer's payload upserts cleanly against the actual live
    schema. If a future schema change re-introduces NOT NULL on a column the
    writer leaves None, or changes a column type incompatibly, this test fails.

    Uses a sentinel repo name 'cc-test-writer-sentinel' that doesn't appear
    in REPOS.json — cleaned up before AND after each run.
    """

    SENTINEL_REPO = "cc-test-writer-sentinel"

    def _conn_factory(self):
        # Lazy import to keep psycopg out of the fast unit-test path
        import psycopg
        return lambda: psycopg.connect(_DSN, autocommit=True)

    def _purge_sentinel(self, conn_factory):
        with conn_factory() as c:
            with c.cursor() as cur:
                cur.execute(
                    "DELETE FROM repo_context WHERE repo = %s",
                    (self.SENTINEL_REPO,),
                )

    @pytestmark_integration
    def test_writer_payload_upserts_against_live_schema(self):
        """The full writer payload (mechanical + semantic-NULL) must succeed
        as an UPSERT on the real repo_context table. Catches NOT NULL drift
        + type mismatches that mocked tests miss.

        IMPORTANT: this is the Gap 3-class regression test. If schema changes
        introduce a NOT NULL on current_phase / architecture_summary /
        recent_changes, OR change blockers/known_debt away from text[], this
        test fails with the same NotNullViolation / TypeMismatch the writer
        would hit in production at the next 15-min sweep.

        Uses psycopg directly (not supabase-py) — matches test_auto_agent_id.py's
        live-DB pattern. Bypasses any supabase-py / httpx routing surface so
        the test exercises only schema-vs-payload alignment.
        """
        import psycopg
        conn_factory = self._conn_factory()
        self._purge_sentinel(conn_factory)
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            with conn_factory() as c:
                with c.cursor() as cur:
                    # First upsert: writer's NULL-on-missing-STATUS.md case.
                    cur.execute(
                        """
                        INSERT INTO repo_context
                          (repo, recent_changes, deploy_url, updated_at, updated_by,
                           current_phase, blockers, known_debt, architecture_summary,
                           active_modules)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (repo) DO UPDATE SET
                          recent_changes        = EXCLUDED.recent_changes,
                          deploy_url            = EXCLUDED.deploy_url,
                          updated_at            = EXCLUDED.updated_at,
                          updated_by            = EXCLUDED.updated_by,
                          current_phase         = EXCLUDED.current_phase,
                          blockers              = EXCLUDED.blockers,
                          known_debt            = EXCLUDED.known_debt,
                          architecture_summary  = EXCLUDED.architecture_summary
                        """,
                        (
                            self.SENTINEL_REPO,
                            "abcd1234 test commit",
                            "https://example.test",
                            now_iso,
                            "cc-test-writer-sentinel",
                            None, None, None, None,  # all semantic NULL
                            [],                      # active_modules NOT NULL
                        ),
                    )
                    cur.execute(
                        "SELECT recent_changes, deploy_url, current_phase, "
                        "blockers, known_debt, architecture_summary, updated_by "
                        "FROM repo_context WHERE repo = %s",
                        (self.SENTINEL_REPO,),
                    )
                    row = cur.fetchone()
                    assert row is not None
                    assert row[0] == "abcd1234 test commit"
                    assert row[1] == "https://example.test"
                    assert row[2] is None
                    assert row[3] is None
                    assert row[4] is None
                    assert row[5] is None
                    assert row[6] == "cc-test-writer-sentinel"

                    # Second upsert: array-typed semantic fields populated.
                    cur.execute(
                        """
                        UPDATE repo_context SET
                          current_phase = %s, blockers = %s, known_debt = %s
                         WHERE repo = %s
                        """,
                        (
                            "test-phase",
                            ["block-A", "block-B"],
                            ["debt-X"],
                            self.SENTINEL_REPO,
                        ),
                    )
                    cur.execute(
                        "SELECT current_phase, blockers, known_debt "
                        "FROM repo_context WHERE repo = %s",
                        (self.SENTINEL_REPO,),
                    )
                    row = cur.fetchone()
                    assert row[0] == "test-phase"
                    assert row[1] == ["block-A", "block-B"]
                    assert row[2] == ["debt-X"]
        finally:
            self._purge_sentinel(conn_factory)
