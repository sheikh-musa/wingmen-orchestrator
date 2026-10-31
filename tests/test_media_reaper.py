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
        # Migrations 117/122 GRANT to the Supabase web roles. CI's bootstrap
        # creates them; a bare local ephemeral cluster does not, and the whole
        # fixture used to die on `role "anon" does not exist` — which silently
        # cost this file ALL of its coverage outside CI.
        for role in ("anon", "authenticated", "service_role"):
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE ROLE "{role}" NOLOGIN')
        for ddl in _DDL:
            cur.execute(ddl)
        # migration 141's CHECK, so the reaper's UPDATE to 'unrecoverable' is
        # validated by the real constraint here (held_commitment #964).
        cur.execute((MIGRATIONS_DIR /
                     "141_operator_messages_media_status_unrecoverable.sql").read_text())
        cur.execute("INSERT INTO agents (id, display_name, status) "
                    "VALUES ('media-reaper', 'reaper', 'active'), "
                    "('orch-console', 'console', 'active')")
        # _notify() now posts via bus_send.send() -> post_agent_message() (backlog#124).
        cur.execute((MIGRATIONS_DIR / "117_durable_inbox_ack_additive.sql").read_text())
        cur.execute((MIGRATIONS_DIR / "122_post_agent_message_optional_params.sql").read_text())
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


# ── TERMINAL failures (held_commitment #964, migration 141) ─────────────────
# A getFile answer that no retry can fix must flip the row to the TERMINAL
# 'unrecoverable' (leaving RETRYABLE_STATUSES, so the loop stops) AND tell the
# operator ONCE (so "terminal" never means "silently dropped").

def _telegram_http_error(code, description):
    import json as _json
    from io import BytesIO
    from urllib.error import HTTPError
    body = _json.dumps({"ok": False, "error_code": code,
                        "description": description}).encode()
    return HTTPError("https://api.telegram.org/botTOK/getFile", code,
                     description, {}, BytesIO(body))


def _console_pages(dsn, subject_like="%"):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT subject, body FROM agent_messages "
                    "WHERE to_agent='orch-console' AND from_agent='media-reaper' "
                    "AND subject LIKE %s", (subject_like,))
        return cur.fetchall()


def test_permanent_getfile_error_marks_the_row_unrecoverable(reaper_dsn, monkeypatch):
    _seed_channel(reaper_dsn, "nazim-console", "nazim-console")
    monkeypatch.setenv("FAKE_BOT_TOKEN", "tok")
    msg_id = _seed_message(
        reaper_dsn, tag="nazim-console", text="sent a FILE (download failed: boom)",
        file_id="F8", file_unique_id="U8", media_kind="document", media_status="failed")

    def _too_big(*a, **k):
        raise _telegram_http_error(400, "Bad Request: file is too big")
    monkeypatch.setattr(_ingest, "_download_media", _too_big)

    assert media_reaper.reap(fire=True) == 0

    assert _row(reaper_dsn, msg_id)["media_status"] == "unrecoverable"
    pages = _console_pages(reaper_dsn, "%UNRECOVERABLE%")
    assert len(pages) == 1
    assert "resend" in pages[0][1].lower(), "the operator must be told WHAT to do"
    assert "20 MB" in pages[0][1]


def test_an_unrecoverable_row_is_never_selected_again(reaper_dsn, monkeypatch):
    """The whole point: the terminal state must actually terminate the loop.
    If 'unrecoverable' leaked back into the retry set, this daemon would keep
    hitting Telegram forever and re-paging every 2h."""
    _seed_channel(reaper_dsn, "nazim-console", "nazim-console")
    monkeypatch.setenv("FAKE_BOT_TOKEN", "tok")
    msg_id = _seed_message(
        reaper_dsn, tag="nazim-console", text="sent a FILE (download failed: boom)",
        file_id="F9", file_unique_id="U9", media_kind="document",
        media_status="failed", age=_dt.timedelta(hours=5))

    def _too_big(*a, **k):
        raise _telegram_http_error(400, "Bad Request: file is too big")
    monkeypatch.setattr(_ingest, "_download_media", _too_big)
    assert media_reaper.reap(fire=True) == 0
    assert _row(reaper_dsn, msg_id)["media_status"] == "unrecoverable"
    assert len(_console_pages(reaper_dsn)) == 1

    # Second tick: the row is out of scope. Any network attempt here is a bug.
    monkeypatch.setattr(_ingest, "_download_media",
                        lambda *a, **k: pytest.fail("terminal row must not be retried"))
    assert media_reaper.reap(fire=True) == 0

    assert _row(reaper_dsn, msg_id)["media_status"] == "unrecoverable"
    assert len(_console_pages(reaper_dsn)) == 1, "exactly one page, ever"


def test_a_transient_failure_is_NOT_marked_terminal(reaper_dsn, monkeypatch):
    """The expensive mistake is the opposite one: calling a recoverable file
    dead. An Errno 54 reset must keep its retry."""
    _seed_channel(reaper_dsn, "nazim-console", "nazim-console")
    monkeypatch.setenv("FAKE_BOT_TOKEN", "tok")
    msg_id = _seed_message(
        reaper_dsn, tag="nazim-console", text="sent a photo (download failed: boom)",
        file_id="F10", file_unique_id="U10", media_kind="photo", media_status="failed")

    def _reset(*a, **k):
        raise ConnectionResetError(54, "Connection reset by peer")
    monkeypatch.setattr(_ingest, "_download_media", _reset)

    assert media_reaper.reap(fire=True) == 0

    assert _row(reaper_dsn, msg_id)["media_status"] == "failed"
    assert _console_pages(reaper_dsn) == []      # too fresh for the 2h floor
