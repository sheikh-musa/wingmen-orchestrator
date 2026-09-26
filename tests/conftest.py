"""Shared fixtures for the Wingmen orchestrator test suite."""

import os
import shutil
import socket
import subprocess
import tempfile
import time

import psycopg
import pytest
from unittest.mock import AsyncMock, MagicMock


# ── Ephemeral throwaway PostgreSQL 17 (shared: migration wet-proofs + the PII
# containment floor proofs). Session-scoped; socket-only (never TCP-binds), C-locale,
# torn down per session. Promoted here from tests/migrations/conftest.py so tests
# outside tests/migrations/ can run their synthetic-stand-in proofs against it in CI
# instead of needing the live substrate (backlog#68, Nazim #43375). ──
_PG_BIN = os.environ.get("WINGMEN_PG17_BIN", "/usr/local/opt/postgresql@17/bin")

# Force a C locale for initdb/pg_ctl. On macOS a non-C locale can trip
# "FATAL: postmaster became multithreaded during startup" (bus #43331/#43345).
_PG_ENV = {**os.environ, "LC_ALL": "C", "LANG": "C"}


def _pg_free_port() -> str:
    """A currently-free TCP port. The cluster runs socket-only (listen_addresses=''),
    so this only discriminates the socket filename and avoids a fixed-port clash."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return str(s.getsockname()[1])


def _pg_bin(name: str) -> str:
    path = os.path.join(_PG_BIN, name)
    if not os.path.exists(path):
        pytest.skip(f"PG17 binary missing: {path} (set WINGMEN_PG17_BIN)")
    return path


@pytest.fixture(scope="session")
def pg_dsn():
    datadir = tempfile.mkdtemp(prefix="wingmen-pgtest-")
    shutil.rmtree(datadir)  # initdb wants to create it
    sockdir = tempfile.mkdtemp(prefix="wingmen-pgsock-")
    port = _pg_free_port()
    subprocess.run(
        [_pg_bin("initdb"), "-D", datadir, "-U", "postgres",
         "--auth=trust", "--locale=C", "--encoding=UTF8"],
        check=True, capture_output=True, env=_PG_ENV,
    )
    subprocess.run(
        [_pg_bin("pg_ctl"), "-D", datadir, "-l", os.path.join(datadir, "log"),
         "-o", f"-p {port} -k {sockdir} -c listen_addresses='' "
               f"-c timezone=UTC -c log_timezone=UTC",
         "-w", "start"],
        check=True, capture_output=True, env=_PG_ENV,
    )
    dsn = f"host={sockdir} port={port} user=postgres dbname=postgres"
    for _ in range(50):
        try:
            with psycopg.connect(dsn):
                break
        except psycopg.OperationalError:
            time.sleep(0.1)
    try:
        yield dsn
    finally:
        subprocess.run([_pg_bin("pg_ctl"), "-D", datadir, "-w", "stop"],
                       capture_output=True, env=_PG_ENV)
        shutil.rmtree(datadir, ignore_errors=True)
        shutil.rmtree(sockdir, ignore_errors=True)


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
