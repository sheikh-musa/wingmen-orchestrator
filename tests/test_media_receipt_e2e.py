"""bus #74111's non-negotiable deliverable: a REAL (non-mocked) failure-then-
recovery proof. Not a test of mocked business logic — this drives the ACTUAL
nervous_system.ingest.process_update() and the ACTUAL
nervous_system.media_reaper.reap() against a real ephemeral Postgres (the
shared pg_dsn/cycle_dsn harness, same one test_ingest_token_group_routing.py
already proves process_update against). The only thing stubbed is the
network edge itself (urllib.request.urlopen) — every line of ingest.py's
_download_media/_tg_download_file retry+backoff and media_reaper.py's
reap() loop runs for real, including real bytes landing on real disk (a
tmp_path standing in for logs/tg_media, never the real one).

Phase 1 (failure): Telegram's getFile succeeds (as it does in the real
Errno-54-mid-download incidents this guards against — bus #74111's header
comment, reference_bridge_media_download_retry), but every file-CDN fetch
is reset. ingest.py's own 4 in-line retries exhaust; the row MUST still
land with file_id/file_unique_id/media_kind set and media_status='failed'
(migration 115's whole point — this is the identity that used to be
silently discarded).

Phase 2 (recovery): the CDN heals. media_reaper.reap(fire=True) — the real
daemon entrypoint, not a stub of it — re-fetches using nothing but the
columns migration 115 added, and the row flips to media_status='ok' with
real bytes on disk.
"""
from __future__ import annotations

import os

import psycopg
import pytest

from nervous_system import ingest
from nervous_system import media_reaper
from tests.test_ingest_token_group_routing import cycle_dsn, _seed_channel  # noqa: F401  (fixture reuse)


class _FakeResp:
    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            chunk, self._pos = self._data[self._pos:], len(self._data)
            return chunk
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_REAL_PHOTO_BYTES = b"\xff\xd8\xff\xe0 not a real jpeg but real bytes on real disk"


def _photo_update(upd_id: int, chat_id: int) -> dict:
    return {
        "update_id": upd_id,
        "message": {
            "message_id": upd_id,
            "date": 1_700_000_000,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": 42, "first_name": "Tester", "username": "tester"},
            "photo": [{"file_id": "PHOTOFILE1", "file_unique_id": "PHOTOUNIQ1",
                       "width": 100, "height": 100}],
        },
    }


@pytest.fixture
def media_env(cycle_dsn, monkeypatch, tmp_path):
    """Real DSN (cycle_dsn) + a real-but-sandboxed media directory, never
    the repo's own logs/tg_media."""
    monkeypatch.setenv("DATABASE_URL", cycle_dsn)
    monkeypatch.setenv("E2E_BOT_TOKEN", "tok-e2e")
    monkeypatch.setattr(ingest, "_MEDIA_DIR", str(tmp_path))
    # Real retry COUNT stays exercised; only the real-world backoff WAIT is
    # skipped, so this proof runs in under a second instead of ~7s.
    monkeypatch.setattr(ingest.time, "sleep", lambda *_a, **_k: None)
    _seed_channel(cycle_dsn, "e2e", "E2E_BOT_TOKEN", chat_ids=(), usernames=())
    return cycle_dsn


def test_failed_download_then_reaper_recovery_is_real_end_to_end(media_env, monkeypatch):
    dsn = media_env
    calls = {"file_attempts": 0, "getfile_calls": 0}

    def fake_urlopen_failing(url, timeout=None):
        if "/getFile" in url:
            calls["getfile_calls"] += 1
            return _FakeResp(b'{"ok":true,"result":{"file_path":"photos/file_1.jpg"}}')
        assert "/file/" in url
        calls["file_attempts"] += 1
        raise ConnectionResetError("[Errno 54] Connection reset by peer")

    monkeypatch.setattr(ingest.urllib.request, "urlopen", fake_urlopen_failing)

    # chat_id=1 is never in this channel's allowed_chat_ids -> process_update's
    # LOG step still runs in full (the durable write this whole feature is
    # about), it just returns True right after instead of reaching ROUTE/ACK
    # — exactly the _UNLISTED_CHAT_ID pattern tests/test_personal_routing_
    # identity_leak.py already relies on, so no outbound sendMessage/network
    # call needs faking here.
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {ingest.Channel.COLS} FROM bot_channels WHERE channel_key='e2e'")
        ch = ingest.Channel(cur.fetchone())

    upd = _photo_update(upd_id=5001, chat_id=1)
    with psycopg.connect(dsn) as conn:
        won = ingest.process_update(conn, ch, upd)
        conn.commit()
    assert won is True

    # Real retry+backoff ran 4 real times against the real CDN-fetch call site.
    assert calls["file_attempts"] == 4
    assert calls["getfile_calls"] == 1

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, text, file_id, file_unique_id, media_kind, media_status "
                    "FROM operator_messages WHERE file_unique_id='PHOTOUNIQ1'")
        row = cur.fetchone()
    assert row is not None, "migration 115's whole point: the row must exist even on total download failure"
    msg_id, text, file_id, file_unique_id, media_kind, media_status = row
    assert file_id == "PHOTOFILE1"
    assert file_unique_id == "PHOTOUNIQ1"
    assert media_kind == "photo"
    assert media_status == "failed"
    assert "download failed" in text
    assert not os.listdir(str(ingest._MEDIA_DIR)), "a failed download must leave no partial file on disk"

    # ── Phase 2: the real daemon entrypoint, CDN healed ──────────────────────
    def fake_urlopen_healed(url, timeout=None):
        if "/getFile" in url:
            return _FakeResp(b'{"ok":true,"result":{"file_path":"photos/file_1.jpg"}}')
        assert "/file/" in url
        return _FakeResp(_REAL_PHOTO_BYTES)

    monkeypatch.setattr(ingest.urllib.request, "urlopen", fake_urlopen_healed)

    rc = media_reaper.reap(fire=True)
    assert rc == 0

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT text, media_status FROM operator_messages WHERE id=%s", (msg_id,))
        text_after, status_after = cur.fetchone()
    assert status_after == "ok"
    assert "RECOVERED by media-reaper" in text_after

    written = os.listdir(str(ingest._MEDIA_DIR))
    assert len(written) == 1, f"expected exactly one real file on disk, got {written}"
    with open(os.path.join(str(ingest._MEDIA_DIR), written[0]), "rb") as f:
        assert f.read() == _REAL_PHOTO_BYTES, "recovered bytes must be the real content, not a placeholder"
