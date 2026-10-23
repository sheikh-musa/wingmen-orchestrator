"""message_content_with_media() — the Telegram file_id identity survives a
download failure (bus #74111).

Before this change, a media download that exhausted all 4 retries degraded to
a text marker ("sent a photo (download failed: ...)") and the file_id that
could have re-fetched it was simply discarded — operator_messages had no
column for it and message_queue.file_id was dead legacy (last row 2026-04-16,
confirmed unused by this ingest). These tests pin the new contract: the media
identity (file_id/file_unique_id/kind/ok) is always returned alongside the
content string for a media update, REGARDLESS of whether the download itself
succeeded, so process_update can persist a re-fetchable pointer either way.

Pure-function tests: no DB, and `_download_media` is stubbed — nothing here
touches api.telegram.org.
"""
import pytest

from nervous_system import ingest


class _Ch:
    key = "nazim-console"
    token = "TESTTOKEN"


@pytest.fixture
def ch():
    return _Ch()


def _msg(**kw):
    base = {"message_id": 1, "date": 0, "chat": {"id": -100}, "from": {"id": 7}}
    base.update(kw)
    return base


def test_photo_success_returns_ok_media_meta(ch, monkeypatch):
    monkeypatch.setattr(
        ingest, "_download_media",
        lambda token, file_id, file_unique_id, channel_key, upd_id, name=None:
            f"/media/{file_unique_id}")
    content, meta = ingest.message_content_with_media(
        ch, _msg(photo=[{"file_id": "F1", "file_unique_id": "U1"}]), 1)
    assert "/media/U1" in content
    assert meta == {"file_id": "F1", "file_unique_id": "U1", "kind": "photo", "ok": True}


def test_photo_download_failure_still_returns_file_id(ch, monkeypatch):
    def _boom(token, file_id, file_unique_id, channel_key, upd_id, name=None):
        raise RuntimeError("telegram file download failed after 4 attempts: Errno 54")
    monkeypatch.setattr(ingest, "_download_media", _boom)
    monkeypatch.setattr(ingest, "_log_media_fail", lambda *a, **k: None)

    content, meta = ingest.message_content_with_media(
        ch, _msg(photo=[{"file_id": "F2", "file_unique_id": "U2"}]), 2)

    assert "download failed" in content
    # The whole point of bus #74111: the identity survives the failure.
    assert meta == {"file_id": "F2", "file_unique_id": "U2", "kind": "photo", "ok": False}


def test_document_download_failure_still_returns_file_id(ch, monkeypatch):
    def _boom(token, file_id, file_unique_id, channel_key, upd_id, name=None):
        raise RuntimeError("CDN reset")
    monkeypatch.setattr(ingest, "_download_media", _boom)
    monkeypatch.setattr(ingest, "_log_media_fail", lambda *a, **k: None)

    content, meta = ingest.message_content_with_media(
        ch, _msg(document={"file_id": "F3", "file_unique_id": "U3", "file_name": "a.pdf"}), 3)

    assert "download failed" in content
    assert meta == {"file_id": "F3", "file_unique_id": "U3", "kind": "document", "ok": False}


def test_text_only_has_no_media_meta(ch):
    content, meta = ingest.message_content_with_media(ch, _msg(text="hello"), 4)
    assert content == "hello"
    assert meta is None


def test_sticker_has_no_media_meta_and_is_not_downloaded(ch, monkeypatch):
    monkeypatch.setattr(ingest, "_download_media",
                        lambda *a, **k: pytest.fail("stickers must not be downloaded"))
    content, meta = ingest.message_content_with_media(
        ch, _msg(sticker={"emoji": "😀", "set_name": "pack"}), 5)
    assert "STICKER" in content
    assert meta is None


def test_message_content_wrapper_still_returns_bare_string(ch, monkeypatch):
    """The pre-existing public API (message_content) is unchanged — it must
    keep returning a bare str, never the tuple, so every existing caller/test
    with this exact signature stays untouched."""
    monkeypatch.setattr(
        ingest, "_download_media",
        lambda token, file_id, file_unique_id, channel_key, upd_id, name=None:
            f"/media/{file_unique_id}")
    out = ingest.message_content(
        ch, _msg(photo=[{"file_id": "F4", "file_unique_id": "U4"}]), 6)
    assert isinstance(out, str)
    assert "/media/U4" in out
