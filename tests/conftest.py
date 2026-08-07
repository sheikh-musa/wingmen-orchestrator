"""Shared fixtures for the Wingmen orchestrator test suite."""

import os

import pytest
from unittest.mock import AsyncMock, MagicMock


def mock_supabase_chain(final_data=None, *, count=None):
    """Build a MagicMock that mimics supabase chained query builder.

    supabase.table(...).select(...).eq(...) etc. are all sync (return self),
    only .execute() is async.
    """
    if final_data is None:
        final_data = []

    mock = MagicMock()
    mock.table.return_value = mock
    mock.select.return_value = mock
    mock.eq.return_value = mock
    mock.neq.return_value = mock
    mock.is_.return_value = mock
    mock.in_.return_value = mock
    mock.or_.return_value = mock
    mock.insert.return_value = mock
    mock.update.return_value = mock
    mock.gte.return_value = mock
    mock.lt.return_value = mock
    mock.order.return_value = mock
    mock.limit.return_value = mock
    mock.maybeSingle.return_value = mock
    mock.upsert.return_value = mock
    mock.delete.return_value = mock
    mock.not_ = mock

    result_mock = MagicMock(data=final_data, count=count)
    mock.execute = AsyncMock(return_value=result_mock)
    return mock


@pytest.fixture
def mock_supabase():
    return mock_supabase_chain()


@pytest.fixture
def sample_job():
    return {
        "id": 42,
        "repo_name": "ihsandms",
        "description": "Add login page",
        "status": "queued",
        "priority": 1,
        "fail_count": 0,
        "client_id": None,
        "triggered_by": None,
        "created_at": "2026-04-14T00:00:00Z",
        "updated_at": "2026-04-14T00:00:00Z",
        "result_summary": None,
    }


@pytest.fixture(autouse=True)
def _no_live_context_watchdog_seams(monkeypatch, tmp_path_factory):
    """Make every side-effecting seam of scripts.context_health_watchdog RAISE.

    WHY THIS EXISTS (2026-07-26 incident): an ordinary `pytest` run sent the
    operator two real Telegram pages claiming the hub had been cleared. The suite
    called the destructive reset routine directly and ONE test forgot to
    monkeypatch the paging seam — the suite was green the whole time, because
    "paged a human with a fabricated all-clear" was not a thing any assertion could
    see. `_in_pytest()` now no-ops the pages, but that is the last line of defence;
    this is the first: a test that reaches ANY live seam fails loudly instead of
    acting on the world.

    Opting in is explicit and per-test: a test that needs a seam monkeypatches it
    itself (which overrides the raiser for that test only). There is deliberately
    no blanket escape hatch — none of these seams has a legitimate un-stubbed use
    in a unit test, since each one drives tmux on a live singleton agent or the
    operator's phone.

    Also points the module's side-effect logs (pen_gate.log,
    context_health_preserved_input.log) at a per-run tmp dir, so test fixtures stop
    being written into the real audit trail as if they were real captures.
    """
    monkeypatch.setenv("CTX_WD_TEST_LOG_DIR",
                       str(tmp_path_factory.mktemp("ctx_wd_logs", numbered=True)))
    try:
        from scripts import context_health_watchdog as _w
    except Exception:  # pragma: no cover — module absent/unimportable: nothing to guard
        return

    def _forbid(name):
        def _raise(*args, **kwargs):
            raise AssertionError(
                f"TEST TOUCHED A LIVE SEAM: {name}() was called un-stubbed. This seam "
                f"acts on the real world (tmux send-keys into a live singleton agent, or "
                f"a Telegram page to the operator). Monkeypatch it in the test that needs "
                f"it — see tests/conftest.py::_no_live_context_watchdog_seams.")
        return _raise

    # _session_fingerprint is not destructive, but it opens a live substrate
    # connection: left un-stubbed it makes the reset-confirmation poll wait on a
    # real DB for its whole window (and reads production telemetry to decide a
    # test's outcome). Same rule — stub it or you do not get it.
    for seam in ("_page_loud", "_send_alert", "_send_literal", "_send_key",
                 "_capture_pane", "_tmux_run", "_session_fingerprint"):
        monkeypatch.setattr(_w, seam, _forbid(seam), raising=True)


@pytest.fixture(autouse=True)
def set_test_env(monkeypatch):
    env_vars = {
        "ANTHROPIC_API_KEY": "test-key",
        "MUSA_TELEGRAM_ID": "123456",
        "TELEGRAM_BOT_TOKEN": "test-bot-token",
        "VERCEL_TOKEN": "test-vercel-token",
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_SERVICE_KEY": "test-service-key",
        "CTO_GROUP_ID": "cto-group-999",
    }
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)
