"""Tests for nervous_system.paused_jobs_retry_policy (PAUSED-JOBS-RETRY-POLICY-001)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system import paused_jobs_retry_policy as policy
from nervous_system.paused_jobs_retry_policy import (
    _classify, _already_auto_retried, run_paused_jobs_retry_policy,
    _AUTO_RETRY_MARKER, _DISPATCHER_CLAIM_STATUS,
)


# ----------------------------------------------------------------------------
# Pure-unit: _classify
# ----------------------------------------------------------------------------

class TestClassify:

    def test_pgrst204_is_stale_error(self):
        s = ("{'message': \"Could not find the 'gate1_result' column of "
             "'work_outputs' in the schema cache\", 'code': 'PGRST204'}")
        assert _classify(s) == "stale_error"

    def test_pgrst204_code_alone_is_stale_error(self):
        assert _classify("Random text PGRST204 in middle") == "stale_error"

    def test_repos_json_alias_gap_is_stale_error(self):
        assert _classify("Repo 'hifz' not found in REPOS.json") == "stale_error"

    def test_repos_json_with_extra_text_is_stale_error(self):
        # Stale annotation appended doesn't break the match
        s = "Repo 'hifz' not found in REPOS.json [reset by cc-orchestrator 2026-04-29]"
        assert _classify(s) == "stale_error"

    def test_connection_timeout_is_stale_error(self):
        assert _classify("connection timed out after 30s") == "stale_error"

    def test_connection_refused_is_stale_error(self):
        assert _classify("OSError: [Errno 111] connection refused") == "stale_error"

    def test_ghost_success_prevented_is_permanent(self):
        assert _classify(
            "No commit produced — ghost success prevented (clarifying question or no-op response)"
        ) == "permanent"

    def test_unknown_failure_classified_other(self):
        assert _classify("ValueError: bad spec — line 42") == "other"

    def test_none_summary_classified_other(self):
        assert _classify(None) == "other"

    def test_permanent_takes_precedence_over_stale(self):
        # If a row contains both patterns somehow, permanent wins (safer default).
        s = "No commit produced — ghost success prevented; PGRST204 also seen"
        assert _classify(s) == "permanent"


# ----------------------------------------------------------------------------
# Pure-unit: _already_auto_retried
# ----------------------------------------------------------------------------

class TestAlreadyAutoRetried:

    def test_unmarked_summary_not_yet_retried(self):
        assert _already_auto_retried("Repo 'hifz' not found in REPOS.json") is False

    def test_marked_summary_detected(self):
        marked = (f"Repo 'hifz' not found in REPOS.json {_AUTO_RETRY_MARKER} "
                  f"2026-04-30T10:00:00 — stale-error class]")
        assert _already_auto_retried(marked) is True

    def test_none_summary_not_yet_retried(self):
        assert _already_auto_retried(None) is False


# ----------------------------------------------------------------------------
# Mocked-supabase: run_paused_jobs_retry_policy
# ----------------------------------------------------------------------------

def _supabase_with_query_result(rows, update_returning=None):
    """Build a supabase mock for the .table().select()....execute() chain
    used by the policy + the .table().update().eq().eq().execute() chain."""
    sb = MagicMock()
    sb.table.return_value = sb
    sb.select.return_value = sb
    sb.update.return_value = sb
    sb.eq.return_value = sb
    sb.gte.return_value = sb
    sb.lt.return_value = sb
    sb.execute = AsyncMock(side_effect=[
        # Heartbeat call (CC-LONG-CALLER-REGISTRY-001 Phase A heartbeat at function start).
        # The result is discarded by heartbeat() except for `data` truthiness check; an
        # empty list signals "caller not registered" but the test's outer try/except
        # absorbs that without affecting downstream work.
        MagicMock(data=[]),
        MagicMock(data=rows),                            # main paused-jobs query
        *[MagicMock(data=update_returning or [{"id": r["id"], "status": _DISPATCHER_CLAIM_STATUS}])
          for r in rows]                                  # one UPDATE per row
    ])
    return sb


class TestRunPausedJobsRetryPolicy:

    @pytest.mark.asyncio
    async def test_no_paused_jobs_no_action(self):
        sb = _supabase_with_query_result([])
        counts = await run_paused_jobs_retry_policy(sb)
        assert counts["considered"] == 0
        assert counts["retried"] == 0

    @pytest.mark.asyncio
    async def test_stale_error_auto_retried(self):
        rows = [{
            "id": 117, "repo_name": "hifz", "status": "paused", "fail_count": 3,
            "result_summary": "Repo 'hifz' not found in REPOS.json",
            "updated_at": "2026-04-23T18:13:00+00:00",
        }]
        sb = _supabase_with_query_result(rows)
        counts = await run_paused_jobs_retry_policy(sb)
        assert counts["considered"] == 1
        assert counts["retried"] == 1
        assert counts["skipped_permanent"] == 0
        assert counts["skipped_already_retried"] == 0

    @pytest.mark.asyncio
    async def test_permanent_pause_never_retried(self):
        rows = [{
            "id": 91, "repo_name": "ihsanos", "status": "paused", "fail_count": 3,
            "result_summary": "No commit produced — ghost success prevented",
            "updated_at": "2026-04-16T18:07:00+00:00",
        }]
        sb = _supabase_with_query_result(rows, update_returning=[])
        counts = await run_paused_jobs_retry_policy(sb)
        assert counts["retried"] == 0
        assert counts["skipped_permanent"] == 1

    @pytest.mark.asyncio
    async def test_already_retried_skipped(self):
        marked = (f"Repo 'hifz' not found in REPOS.json {_AUTO_RETRY_MARKER} "
                  f"2026-04-30T01:00:00 — stale-error class]")
        rows = [{
            "id": 117, "repo_name": "hifz", "status": "paused", "fail_count": 3,
            "result_summary": marked,
            "updated_at": "2026-04-30T01:30:00+00:00",
        }]
        sb = _supabase_with_query_result(rows, update_returning=[])
        counts = await run_paused_jobs_retry_policy(sb)
        assert counts["retried"] == 0
        assert counts["skipped_already_retried"] == 1

    @pytest.mark.asyncio
    async def test_unknown_failure_skipped_other(self):
        rows = [{
            "id": 200, "repo_name": "x", "status": "paused", "fail_count": 3,
            "result_summary": "ValueError: surprise from line 42",
            "updated_at": "2026-04-29T00:00:00+00:00",
        }]
        sb = _supabase_with_query_result(rows, update_returning=[])
        counts = await run_paused_jobs_retry_policy(sb)
        assert counts["retried"] == 0
        assert counts["skipped_other"] == 1

    @pytest.mark.asyncio
    async def test_status_assertion_catches_wrong_target(self):
        """AC (iv) self-audit: if the UPDATE returns status != 'queued',
        fire error_tracker + count as error, do NOT count as retried."""
        rows = [{
            "id": 117, "repo_name": "hifz", "status": "paused", "fail_count": 3,
            "result_summary": "PGRST204 schema cache",
            "updated_at": "2026-04-29T00:00:00+00:00",
        }]
        # Inject a poisoned update result where status=='pending' (the
        # CAI-RESP-105 mistake) instead of 'queued' (dispatcher filter)
        sb = _supabase_with_query_result(
            rows, update_returning=[{"id": 117, "status": "pending"}]
        )
        counts = await run_paused_jobs_retry_policy(sb)
        assert counts["retried"] == 0
        assert counts["errors"] == 1

    @pytest.mark.asyncio
    async def test_query_failure_returns_error_count(self):
        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.eq.return_value = sb
        sb.gte.return_value = sb
        sb.lt.return_value = sb
        sb.execute = AsyncMock(side_effect=RuntimeError("simulated DB outage"))
        counts = await run_paused_jobs_retry_policy(sb)
        assert counts["errors"] == 1
        assert counts["retried"] == 0
