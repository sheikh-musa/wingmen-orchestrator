"""media_reaper — retries a Telegram media download that landed in
operator_messages with media_status IN ('pending','failed') until bytes land
and the row flips to 'ok' (bus #74111 part 2/3).

DB-integration tests against the shared ephemeral `pg_dsn` fixture
(tests/conftest.py) — real INSERT/UPDATE/SELECT, no DB mocking. The network
call (_ingest._download_media) is stubbed; nothing here touches
api.telegram.org.
"""
import datetime as _dt
import pathlib

import psycopg
import pytest
from psycopg.rows import dict_row

from nervous_system import ingest as _ingest
from nervous_system import media_reaper

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"

_DDL = (
    """CREATE TABLE agents (
         id TEXT PRIMARY KEY, display_name TEXT, status TEXT
       )""",
    """CREATE TABLE agent_messages (
         id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
         from_agent TEXT NOT NULL REFERENCES agents(id),
         to_agent TEXT NOT NULL,
         message_type TEXT NOT NULL, subject TEXT, body TEXT,
         requires_response BOOLEAN NOT NULL DEFAULT false,
         priority TEXT NOT NULL DEFAULT 'P2',
         thread_id UUID NOT NULL DEFAULT gen_random_uuid(),
         sub_tag TEXT,
         is_test BOOLEAN NOT NULL DEFAULT false,
         posted_by_identity TEXT,
         created_at TIMESTAMPTZ NOT NULL DEFAULT now()
       )""",
    """CREATE TABLE bot_channels (
         channel_key TEXT PRIMARY KEY, token_env_key TEXT, channel_tag TEXT UNIQUE
       )""",
    """CREATE TABLE operator_messages (
         id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
         tag TEXT, text TEXT,
         file_id TEXT, file_unique_id TEXT, media_kind TEXT, media_status TEXT,
         created_at TIMESTAMPTZ NOT NULL DEFAULT now()
       )""",
)


@pytest.fixture
def reaper_dsn(pg_dsn, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    # Default scope covers every channel_key the existing tests in this file
    # seed ("nazim-console", "client-x"); a test proving the scope FILTER
    # itself (test_out_of_scope_tag_is_skipped_even_with_a_valid_token)
    # narrows this with its own monkeypatch.setenv override.
    monkeypatch.setenv("MEDIA_REAPER_CHANNELS", "nazim-console,client-x")
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname='service_role'")
        if cur.fetchone() is None:
            cur.execute("CREATE ROLE service_role")
        for ddl in _DDL:
            cur.execute(ddl)
        cur.execute("INSERT INTO agents (id, display_name, status) "
                    "VALUES ('media-reaper', 'reaper', 'active'), "
                    "('orch-console', 'console', 'active')")
        # _notify() now posts via bus_send.send() -> post_agent_message() (backlog#124).
        cur.execute((MIGRATIONS_DIR / "117_durable_inbox_ack_additive.sql").read_text())
        cur.execute((MIGRATIONS_DIR / "120_post_agent_message_optional_params.sql").read_text())
    return pg_dsn


def _seed_channel(dsn, channel_key, tag, token_env_key="FAKE_BOT_TOKEN"):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO bot_channels (channel_key, token_env_key, channel_tag) VALUES (%s,%s,%s)",
            (channel_key, token_env_key, tag))


def _seed_message(dsn, *, tag, text, file_id, file_unique_id, media_kind,
                   media_status, age=_dt.timedelta(0)):
    created_at = _dt.datetime.now(_dt.timezone.utc) - age
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_messages (tag, text, file_id, file_unique_id, "
            " media_kind, media_status, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s) "
            "RETURNING id",
            (tag, text, file_id, file_unique_id, media_kind, media_status, created_at))
        return cur.fetchone()[0]


def _row(dsn, msg_id):
    with psycopg.connect(dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM operator_messages WHERE id=%s", (msg_id,))
        return cur.fetchone()


def test_dry_run_changes_nothing(reaper_dsn, monkeypatch):
    _seed_channel(reaper_dsn, "nazim-console", "nazim-console")
    monkeypatch.setenv("FAKE_BOT_TOKEN", "tok")
    msg_id = _seed_message(
        reaper_dsn, tag="nazim-console", text="sent a photo (download failed: boom)",
        file_id="F1", file_unique_id="U1", media_kind="photo", media_status="failed")
    monkeypatch.setattr(_ingest, "_download_media",
                        lambda *a, **k: pytest.fail("dry-run must not touch the network"))

    assert media_reaper.reap(fire=False) == 0

    row = _row(reaper_dsn, msg_id)
    assert row["media_status"] == "failed"


def test_successful_retry_flips_to_ok_and_appends_recovered_marker(reaper_dsn, monkeypatch):
    _seed_channel(reaper_dsn, "nazim-console", "nazim-console")
    monkeypatch.setenv("FAKE_BOT_TOKEN", "tok")
    msg_id = _seed_message(
        reaper_dsn, tag="nazim-console", text="sent a photo (download failed: Errno 54)",
        file_id="F1", file_unique_id="U1", media_kind="photo", media_status="failed")
    monkeypatch.setattr(_ingest, "_download_media",
                        lambda token, file_id, file_unique_id, channel_key, upd_id, name=None:
                            f"/media/{file_unique_id}")

    assert media_reaper.reap(fire=True) == 0

    row = _row(reaper_dsn, msg_id)
    assert row["media_status"] == "ok"
    assert "RECOVERED by media-reaper" in row["text"]
    assert "/media/U1" in row["text"]
    assert "download failed" in row["text"]        # original failure stays, for audit


def test_pending_row_is_also_retried(reaper_dsn, monkeypatch):
    """'pending' has no current producer in ingest.py, but the reaper must
    still treat it as retry-worthy (forward-compatible with a future
    decoupled write path) — same handling as 'failed'."""
    _seed_channel(reaper_dsn, "nazim-console", "nazim-console")
    monkeypatch.setenv("FAKE_BOT_TOKEN", "tok")
    msg_id = _seed_message(
        reaper_dsn, tag="nazim-console", text="sent a photo (downloading...)",
        file_id="F2", file_unique_id="U2", media_kind="photo", media_status="pending")
    monkeypatch.setattr(_ingest, "_download_media",
                        lambda token, file_id, file_unique_id, channel_key, upd_id, name=None:
                            f"/media/{file_unique_id}")

    assert media_reaper.reap(fire=True) == 0

    assert _row(reaper_dsn, msg_id)["media_status"] == "ok"


def test_repeated_failure_stays_failed_and_does_not_escalate_before_the_floor(reaper_dsn, monkeypatch):
    _seed_channel(reaper_dsn, "nazim-console", "nazim-console")
    monkeypatch.setenv("FAKE_BOT_TOKEN", "tok")
    msg_id = _seed_message(
        reaper_dsn, tag="nazim-console", text="sent a photo (download failed: boom)",
        file_id="F3", file_unique_id="U3", media_kind="photo", media_status="failed",
        age=_dt.timedelta(minutes=1))

    def _boom(*a, **k):
        raise RuntimeError("telegram file download failed after 4 attempts: Errno 54")
    monkeypatch.setattr(_ingest, "_download_media", _boom)

    assert media_reaper.reap(fire=True) == 0

    row = _row(reaper_dsn, msg_id)
    assert row["media_status"] == "failed"
    with psycopg.connect(reaper_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_messages WHERE to_agent='orch-console'")
        assert cur.fetchone()[0] == 0   # too fresh to escalate yet


def test_repeated_failure_past_escalate_floor_pages_console_once(reaper_dsn, monkeypatch):
    _seed_channel(reaper_dsn, "nazim-console", "nazim-console")
    monkeypatch.setenv("FAKE_BOT_TOKEN", "tok")
    _seed_message(
        reaper_dsn, tag="nazim-console", text="sent a photo (download failed: boom)",
        file_id="F4", file_unique_id="U4", media_kind="photo", media_status="failed",
        age=_dt.timedelta(hours=3))

    def _boom(*a, **k):
        raise RuntimeError("telegram file download failed after 4 attempts: Errno 54")
    monkeypatch.setattr(_ingest, "_download_media", _boom)

    assert media_reaper.reap(fire=True) == 0

    with psycopg.connect(reaper_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_messages WHERE to_agent='orch-console' "
                    "AND from_agent='media-reaper'")
        assert cur.fetchone()[0] == 1


def test_unresolvable_tag_is_skipped_not_crashed(reaper_dsn, monkeypatch):
    """No bot_channels row matches this tag at all (also outside the fixture's
    default MEDIA_REAPER_CHANNELS scope, same outcome either way) — must skip
    cleanly, not raise. nazim-console is seeded too so the run's OVERALL scope
    still resolves to a non-empty set (otherwise this would hit the separate
    "scope resolves to nothing" refusal instead of exercising the per-row
    unresolvable-tag skip this test is actually about)."""
    _seed_channel(reaper_dsn, "nazim-console", "nazim-console")
    monkeypatch.setenv("FAKE_BOT_TOKEN", "tok")
    msg_id = _seed_message(
        reaper_dsn, tag="some-unknown-tag", text="sent a photo (download failed: boom)",
        file_id="F5", file_unique_id="U5", media_kind="photo", media_status="failed")
    monkeypatch.setattr(_ingest, "_download_media",
                        lambda *a, **k: pytest.fail("must not even attempt the network"))

    assert media_reaper.reap(fire=True) == 0

    assert _row(reaper_dsn, msg_id)["media_status"] == "failed"


def test_missing_token_env_on_this_host_is_skipped_not_crashed(reaper_dsn, monkeypatch):
    """A channel exists, but ITS token env var isn't set on this host (e.g. a
    client bot token the reaper host doesn't carry) — skip, don't raise."""
    _seed_channel(reaper_dsn, "client-x", "client-x", token_env_key="SOME_UNSET_TOKEN")
    msg_id = _seed_message(
        reaper_dsn, tag="client-x", text="sent a photo (download failed: boom)",
        file_id="F6", file_unique_id="U6", media_kind="photo", media_status="failed")
    monkeypatch.setattr(_ingest, "_download_media",
                        lambda *a, **k: pytest.fail("must not even attempt the network"))

    assert media_reaper.reap(fire=True) == 0

    assert _row(reaper_dsn, msg_id)["media_status"] == "failed"


def test_missing_dsn_refuses_loudly(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    assert media_reaper.reap(fire=False) == 2
    assert "MISSING DSN" in capsys.readouterr().err


def test_out_of_scope_tag_is_skipped_even_with_a_valid_token(reaper_dsn, monkeypatch):
    """bus #74154: a tag with a REAL bot_channels row and a REAL token on this
    host, but whose channel_key is not in MEDIA_REAPER_CHANNELS, must still be
    left alone — the scope filter, not token resolution, is what's gating it
    (this is the exact cross-host-download-to-the-wrong-disk case the
    scoping exists to prevent)."""
    monkeypatch.setenv("MEDIA_REAPER_CHANNELS", "nazim-console")  # narrower than the fixture default
    _seed_channel(reaper_dsn, "nazim-console", "nazim-console")  # keeps the scope non-empty
    _seed_channel(reaper_dsn, "other-host-channel", "other-host-channel")
    monkeypatch.setenv("FAKE_BOT_TOKEN", "tok")
    msg_id = _seed_message(
        reaper_dsn, tag="other-host-channel", text="sent a photo (download failed: boom)",
        file_id="F7", file_unique_id="U7", media_kind="photo", media_status="failed")
    monkeypatch.setattr(_ingest, "_download_media",
                        lambda *a, **k: pytest.fail("must not even attempt the network"))

    assert media_reaper.reap(fire=True) == 0

    assert _row(reaper_dsn, msg_id)["media_status"] == "failed"


def test_missing_media_reaper_channels_refuses_loudly(reaper_dsn, monkeypatch, capsys):
    monkeypatch.delenv("MEDIA_REAPER_CHANNELS", raising=False)
    assert media_reaper.reap(fire=False) == 2
    assert "MEDIA_REAPER_CHANNELS" in capsys.readouterr().err


def test_media_reaper_channels_resolving_to_no_known_channel_refuses_loudly(reaper_dsn, monkeypatch, capsys):
    monkeypatch.setenv("MEDIA_REAPER_CHANNELS", "no-such-channel-key")
    assert media_reaper.reap(fire=False) == 2
    assert "MEDIA_REAPER_CHANNELS" in capsys.readouterr().err
