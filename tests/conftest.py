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


# ── Production-silo DSN guard (orch-console bus #44006, op#22741) ───────────
# A hand-rolled test _dsn()=os.environ.get("DATABASE_URL") on a lane (where
# DATABASE_URL IS the live substrate) wrote a fake operator message into the
# REAL operator_messages/operator_asks tables and it reached a body's inbox
# before cleanup ran. Any test connecting to a DSN it built itself (not the
# pg_dsn ephemeral fixture below) must run this DSN through the guard first.
PRODUCTION_SILO_REFS = (
    "tscuymavysscrvoberrr",  # orchestrator substrate (the monolith)
    "ceayjeamtmcyzzvqflus",  # ihsanos multi-tenant DB
    "goumlynecruxrlmzlntp",  # irsyad silo (goumlyne)
    "brrgastulcffamlbggyu",  # wingmen-personal
    "ywrpttpxwfcoodovxhsr",  # cosem-platform production store (CAI-RESP-1340)
)


def assert_dsn_is_not_production(dsn) -> None:
    """Fail LOUDLY (never skip, never swallow) if `dsn` names a real production
    silo ref. Call this before any test connects to a DSN it assembled itself."""
    dsn = dsn or ""
    for ref in PRODUCTION_SILO_REFS:
        if ref in dsn:
            pytest.fail(
                f"TEST DSN NAMES A PRODUCTION SILO ({ref}) — refusing to connect. "
                f"Use the ephemeral pg_dsn/operator_ledger_db fixtures, never a DSN "
                f"read from the real DATABASE_URL/SUPABASE_DB_URL environment "
                f"(orch-console bus #44006, op#22741)."
            )


@pytest.fixture
def operator_ledger_db(pg_dsn, monkeypatch):
    """Ephemeral Postgres carrying the real operator_messages/operator_asks
    schema (mirrors migrations 007/020/021/044/072/082/084/085) — op#22669 ledger
    tests must NEVER touch the live substrate (orch-console bus #44006,
    op#22741: a test using DATABASE_URL directly inserted + deleted rows in
    production and one fake operator message reached a body's live inbox
    before cleanup ran).

    Points DATABASE_URL at the ephemeral instance for the duration of the test
    (nervous_system/operator_log.py and scripts/asks_open.py both read
    DATABASE_URL/SUPABASE_DB_URL directly, with no DSN-injection seam) and wipes
    SUPABASE_DB_URL so it can never win the fallback. Schema is dropped and
    recreated per-test on the shared session-scoped cluster, same idiom as
    tests/test_ingest_pinned_channel_drift.py's drift_dsn fixture.
    """
    assert_dsn_is_not_production(pg_dsn)
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("""
            CREATE TABLE operator_messages (
                id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                direction     text NOT NULL CHECK (direction IN ('inbound','outbound')),
                channel       text NOT NULL DEFAULT 'telegram',
                chat_id       text,
                tag           text,
                text          text NOT NULL,
                delivered     boolean NOT NULL DEFAULT true,
                created_at    timestamptz NOT NULL DEFAULT now(),
                from_user_id  text,
                from_username text,
                from_name     text,
                cos_triage    jsonb,
                tg_message_id bigint
            )
        """)
        cur.execute("""
            CREATE TABLE operator_asks (
                id                  bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                ask                 text NOT NULL,
                source_msg_id       bigint,
                thread_id           uuid,
                delegated_to        text,
                created_at          timestamptz NOT NULL DEFAULT now(),
                confirmed_at        timestamptz,
                closed_at           timestamptz,
                closed_reason       text,
                waiting_on_operator boolean NOT NULL DEFAULT false,
                chase_by            timestamptz,
                outbound_msg_id     bigint,
                triage_state        text NOT NULL DEFAULT 'captured'
                                    CHECK (triage_state IN ('captured','ask','not_an_ask','done')),
                triage_summary      text,
                triage_evidence_ref text,
                triaged_at          timestamptz,
                triaged_by          text,
                ask_surface         text NOT NULL DEFAULT 'operator'
                                    CHECK (ask_surface IN ('operator','client-channel')),
                committed_date      timestamptz
                                    CHECK (committed_date IS NULL OR outbound_msg_id IS NOT NULL)
            )
        """)
        cur.execute("""
            CREATE UNIQUE INDEX operator_asks_source_ask_uniq
              ON operator_asks (source_msg_id, ask)
              WHERE source_msg_id IS NOT NULL
        """)
        cur.execute("""
            CREATE INDEX operator_asks_client_chase_idx
              ON operator_asks (chase_by)
              WHERE closed_at IS NULL AND ask_surface = 'client-channel'
        """)
        cur.execute("""
            CREATE TABLE bot_channels (
                id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                channel_key   text NOT NULL UNIQUE,
                enabled       boolean NOT NULL DEFAULT true,
                inject_target text,
                audience      text NOT NULL CHECK (audience IN ('operator','client','internal')),
                owner_lane    text
            )
        """)
    return pg_dsn


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
