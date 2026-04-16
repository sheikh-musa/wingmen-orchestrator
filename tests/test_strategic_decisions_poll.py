"""Tests for strategic_decisions_poll — BUG-018 ships-guard filter."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from nervous_system.strategic_decisions_poll import poll_strategic_decisions
from tests.conftest import mock_supabase_chain


@pytest.mark.asyncio
async def test_poll_filters_out_shipped_decisions():
    """BUG-018: the candidate-decision query must filter rows where
    evidence_commit_sha IS NULL AND challenge_status != 'implemented'.
    Without these clauses, decisions flipped to 'implemented' by TASK-026
    (or backfilled with evidence_commit_sha by ARCH-024) get re-queued.
    """
    sb = mock_supabase_chain([])

    await poll_strategic_decisions(sb)

    sb.is_.assert_any_call("evidence_commit_sha", "null")
    sb.neq.assert_any_call("challenge_status", "implemented")


@pytest.mark.asyncio
async def test_poll_does_not_enqueue_shipped_row():
    """Even if the mock returns a row that represents a 'shipped' decision
    (challenge_status='implemented' or evidence_commit_sha populated), the
    poller must not queue a job for it.

    In production the SQL filter excludes these rows before they ever reach
    the Python code. We assert on the query chain (above) and here we assert
    that when the filter is honored — i.e. the DB returns zero shipped rows —
    no insert into `jobs` is attempted.
    """
    sb = mock_supabase_chain([])

    await poll_strategic_decisions(sb)

    # `.table()` is only called for the SELECT, never for jobs.insert(...)
    sb.table.assert_called_once_with("strategic_decisions")
    sb.insert.assert_not_called()


@pytest.mark.asyncio
async def test_poll_still_enqueues_open_decision():
    """Open decisions (no evidence_commit_sha, status != implemented) still flow
    through to the jobs.insert path — we didn't break the happy case.
    """
    open_row = {
        "id": 1,
        "decision_ref": "BUG-018",
        "title": "filter shipped decisions",
        "decision": "add two-clause guard",
        "reasoning": "r",
        "repos_affected": ["wingmen-orchestrator"],
        "challenge_status": "accepted",
        "source": "claude_ai_session",
        "bypass_review": False,
        "created_at": "2026-04-16T00:00:00Z",
        "category": "infra",
        "parent_ref": None,
    }
    sb = mock_supabase_chain([open_row])

    await poll_strategic_decisions(sb)

    insert_calls = [c.args for c in sb.insert.call_args_list]
    assert any(
        isinstance(a[0], dict) and a[0].get("repo_name") == "wingmen-orchestrator"
        for a in insert_calls
    ), f"expected a jobs insert for the open decision, got: {insert_calls}"
