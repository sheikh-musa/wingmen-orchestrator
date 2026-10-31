"""Terminal media_status ('unrecoverable') — held_commitment #964, bus #85768.

A Telegram attachment can fail to download for two very different reasons:

  * transient  — Errno 54 reset, timeout, DNS, 5xx, a truncated JSON body.
                 Retrying is exactly right; the media_reaper exists for this.
  * permanent  — 400 "file is too big" (the Bot API cannot download anything
                 over 20 MB), 400 "invalid file_id" / "wrong file identifier",
                 an expired file reference, or a 404. NO retry can ever fix
                 these, so the old behaviour was pure noise: the row stayed
                 'failed', the reaper retried it every tick, and re-paged the
                 console every ESCALATE_AFTER (2h) forever.

The fix is a TERMINAL status ('unrecoverable', migration 140) plus a single
shared classifier, `ingest._unrecoverable_reason`, used by BOTH the ingest
first-attempt path and the reaper — so the two can never disagree about what
is retryable.

These tests pin the direction of the classifier's mistakes, which is the part
that actually matters: calling a transient failure TERMINAL would silently
stop the reaper on a file that could still be recovered, so any doubt must
fall back to None (-> 'failed' -> keeps retrying). Pure functions, no network,
no DB.
"""
from __future__ import annotations

import json
import urllib.error
from io import BytesIO

import pytest

from nervous_system import ingest, media_reaper


def _http_error(code: int, description: str, body: bytes | None = None) -> urllib.error.HTTPError:
    """Telegram's real shape: HTTPError whose body is the JSON API answer."""
    if body is None:
        body = json.dumps({"ok": False, "error_code": code,
                           "description": description}).encode()
    return urllib.error.HTTPError(
        "https://api.telegram.org/botTOK/getFile", code, description, {}, BytesIO(body))


# ── permanent: must classify TERMINAL ───────────────────────────────────────

@pytest.mark.parametrize("description", [
    "Bad Request: file is too big",
    "Bad Request: FILE_TOO_BIG",
    "Bad Request: invalid file_id",
    "Bad Request: wrong file identifier/HTTP URL specified",
    "Bad Request: file reference expired",
    "Bad Request: file is not found",
])
def test_permanent_getfile_answers_are_terminal(description):
    reason = ingest._unrecoverable_reason(_http_error(400, description))
    assert reason, f"{description!r} must be TERMINAL, not retryable"
    assert description.split(": ")[-1].lower()[:12] in reason.lower()


def test_too_big_reason_names_the_20mb_limit():
    """The page tells the operator to 'resend smaller' — it must say WHY, or
    the ask reads as arbitrary."""
    reason = ingest._unrecoverable_reason(_http_error(400, "Bad Request: file is too big"))
    assert "20 MB" in reason


def test_404_is_terminal_even_with_an_empty_body():
    """A 404 with no readable body still means the file is gone; the status
    code alone is enough evidence."""
    err = _http_error(404, "Not Found", body=b"")
    assert ingest._unrecoverable_reason(err)


# ── transient: must stay retryable ──────────────────────────────────────────

@pytest.mark.parametrize("description", [
    "Bad Request: message to delete not found",   # 400, but unrelated to the file
    "Bad Request: chat not found",
    "Too Many Requests: retry after 5",
])
def test_other_4xx_answers_stay_retryable(description):
    assert ingest._unrecoverable_reason(_http_error(429, description)) is None


def test_5xx_stays_retryable():
    """Telegram 5xx is by definition transient."""
    assert ingest._unrecoverable_reason(_http_error(500, "Internal Server Error")) is None
    assert ingest._unrecoverable_reason(_http_error(502, "Bad Gateway")) is None


def test_oserror_is_never_terminal():
    """THE critical negative: an Errno 54 reset is the exact failure that lost
    the operator's photos twice. If this ever classifies as terminal, the
    reaper stops on a file that a retry would have recovered."""
    assert ingest._unrecoverable_reason(ConnectionResetError(54, "Connection reset by peer")) is None
    assert ingest._unrecoverable_reason(urllib.error.URLError("timed out")) is None
    assert ingest._unrecoverable_reason(TimeoutError("timed out")) is None


def test_download_loop_runtime_error_stays_retryable():
    """KNOWN LIMIT, pinned on purpose: _tg_download_file wraps its 4 attempts
    in a RuntimeError, so a CDN failure AFTER a successful getFile can't be
    inspected here — it stays 'failed' and keeps retrying. That is the safe
    direction (recoverable things keep recovering); this test makes the limit
    explicit instead of letting a future reader assume 404s are all caught."""
    assert ingest._unrecoverable_reason(
        RuntimeError("telegram file download failed after 4 attempts: HTTP Error 404")) is None


def test_unparseable_body_does_not_raise_and_stays_retryable():
    """A proxy/truncated body must degrade to 'unknown', never crash the
    classifier and never be guessed terminal."""
    err = _http_error(400, "Bad Request", body=b"<html>502 Bad Gateway</html>")
    assert ingest._unrecoverable_reason(err) is None


def test_classifier_never_raises_on_a_broken_exception():
    class _Exploding(urllib.error.HTTPError):
        def read(self, *a, **kw):
            raise OSError("body already consumed")

    err = _Exploding("u", 400, "Bad Request: file is too big", {}, None)
    assert ingest._unrecoverable_reason(err) is None


# ── the classifier is SHARED: ingest and the reaper cannot disagree ─────────

def test_reaper_uses_the_ingest_classifier():
    assert media_reaper._ingest is ingest
    assert media_reaper._ingest._unrecoverable_reason is ingest._unrecoverable_reason


def test_unrecoverable_is_not_in_the_reapers_retry_set():
    """'unrecoverable' must not be selectable by the reaper, or the terminal
    state would not actually terminate anything."""
    assert "unrecoverable" not in media_reaper.RETRYABLE_STATUSES
    assert set(media_reaper.RETRYABLE_STATUSES) == {"pending", "failed"}


# ── propagation: the flag reaches the caller that writes the row ────────────

def _channel(key="operator-orch", token_env="TOK_FOR_TEST"):
    return ingest.Channel((key, token_env, "inject", None, "", None, [], [], {},
                           f"{key}-tag", None, None, "operator", None, None))


def _photo_msg():
    return {"photo": [{"file_id": "F1", "file_unique_id": "U1"}]}


def test_media_meta_carries_the_terminal_reason(monkeypatch):
    monkeypatch.setenv("TOK_FOR_TEST", "TOKEN")
    monkeypatch.setattr(ingest, "_log_line", lambda *a, **k: None)

    def _boom(*a, **kw):
        raise _http_error(400, "Bad Request: file is too big")
    monkeypatch.setattr(ingest, "_download_media", _boom)

    content, meta = ingest._media_content(_channel(), _photo_msg(), 440382277, "")
    assert "download failed" in content
    assert meta is not None and meta["ok"] is False
    assert "20 MB" in meta["unrecoverable"]


def test_media_meta_is_clean_on_a_transient_failure(monkeypatch):
    monkeypatch.setenv("TOK_FOR_TEST", "TOKEN")
    monkeypatch.setattr(ingest, "_log_line", lambda *a, **k: None)

    def _boom(*a, **kw):
        raise ConnectionResetError(54, "Connection reset by peer")
    monkeypatch.setattr(ingest, "_download_media", _boom)

    _content, meta = ingest._media_content(_channel(), _photo_msg(), 440382277, "")
    assert meta["ok"] is False
    assert meta["unrecoverable"] is None, "transient must stay retryable"


def test_media_meta_is_clean_on_success(monkeypatch):
    monkeypatch.setenv("TOK_FOR_TEST", "TOKEN")
    monkeypatch.setattr(ingest, "_download_media", lambda *a, **kw: "/tmp/x.jpg")

    content, meta = ingest._media_content(_channel(), _photo_msg(), 1, "")
    assert meta == {"file_id": "F1", "file_unique_id": "U1", "kind": "photo",
                    "ok": True, "unrecoverable": None}
    assert "/tmp/x.jpg" in content
