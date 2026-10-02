"""Regression coverage for bus #49740/#49763's mamadah wedge: write_personal_content()
must treat a duplicate-key (23505) insert conflict on (channel, chat_id,
tg_message_id) as idempotent success (the message is already safely stored,
most often a redelivered Telegram update_id for the same inner message),
not a retriable failure. Before this fix every retry rolled back the
caller's substrate envelope forever since the same conflict reproduces on
every attempt (ingest.py's C1 rollback-on-raise never clears).

No live network: urllib.request.urlopen is monkeypatched per-test.
"""
from __future__ import annotations

import importlib
import json
import urllib.error

import pytest

personal_routing = importlib.import_module("nervous_system.personal_routing")


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _personal_env(monkeypatch):
    monkeypatch.setenv("WINGMEN_PERSONAL_URL", "https://fake-personal.example")
    monkeypatch.setenv("WINGMEN_PERSONAL_SERVICE_KEY", "fake-key")


def test_duplicate_key_on_insert_returns_existing_row_id(monkeypatch):
    """POST 409/23505 -> idempotent success: the existing row's id is returned,
    no exception raised, and the caller (ingest.py) proceeds to commit the
    substrate envelope instead of rolling back."""
    import io

    calls = []

    def fake_urlopen(req, timeout=15):
        calls.append(req.full_url)
        if req.get_method() == "POST":
            body = json.dumps({
                "code": "23505",
                "message": 'duplicate key value violates unique constraint '
                           '"mamadah_messages_channel_chat_tgmsg_key"',
                "details": "Key (channel, chat_id, tg_message_id)=(mamadah, 999, 168) already exists.",
            }).encode()
            raise urllib.error.HTTPError(
                url="https://fake-personal.example/rest/v1/mamadah_messages",
                code=409, msg="Conflict", hdrs=None, fp=io.BytesIO(body),
            )
        # GET lookup
        return _FakeResponse([{"id": 24847}])

    monkeypatch.setattr(personal_routing.urllib.request, "urlopen", fake_urlopen)

    result_id = personal_routing.write_personal_content(
        123, direction="inbound", channel="mamadah", tag="mamadah",
        text="hello", chat_id=999, tg_message_id=168,
    )

    assert result_id == 24847
    assert any("tg_message_id=eq.168" in url for url in calls)


def test_non_duplicate_http_error_still_raises(monkeypatch):
    """A genuine server error (not a 23505 conflict) must still raise —
    this fix narrows the exception, it doesn't swallow failures broadly."""
    import io

    def fake_urlopen(req, timeout=15):
        err = urllib.error.HTTPError(
            url="https://fake-personal.example/rest/v1/mamadah_messages",
            code=500, msg="Internal Server Error", hdrs=None,
            fp=io.BytesIO(b'{"message": "internal error"}'),
        )
        raise err

    monkeypatch.setattr(personal_routing.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(personal_routing.PersonalRouteError):
        personal_routing.write_personal_content(
            123, direction="inbound", channel="mamadah", tag="mamadah",
            text="hello", chat_id=999, tg_message_id=168,
        )


def test_duplicate_key_without_tg_message_id_still_raises(monkeypatch):
    """The constraint this fix targets is keyed on tg_message_id; without
    one there's nothing to look up by, so a 23505 here still raises (fails
    safe rather than guessing)."""
    import io

    def fake_urlopen(req, timeout=15):
        err = urllib.error.HTTPError(
            url="https://fake-personal.example/rest/v1/mamadah_messages",
            code=409, msg="Conflict", hdrs=None,
            fp=io.BytesIO(b'{"code": "23505", "message": "duplicate key value violates unique constraint"}'),
        )
        raise err

    monkeypatch.setattr(personal_routing.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(personal_routing.PersonalRouteError):
        personal_routing.write_personal_content(
            123, direction="inbound", channel="mamadah", tag="mamadah",
            text="hello", chat_id=999, tg_message_id=None,
        )


def test_duplicate_key_but_lookup_finds_nothing_still_raises(monkeypatch):
    """If the dup-key error fires but the idempotent lookup can't confirm an
    existing row (0 or >1 matches, or the lookup itself errors), fall back
    to raising — never fabricate a success id."""
    import io

    def fake_urlopen(req, timeout=15):
        if req.get_method() == "POST":
            err = urllib.error.HTTPError(
                url="https://fake-personal.example/rest/v1/mamadah_messages",
                code=409, msg="Conflict", hdrs=None,
                fp=io.BytesIO(b'{"code": "23505", "message": "duplicate key value violates unique constraint"}'),
            )
            raise err
        return _FakeResponse([])  # lookup finds nothing

    monkeypatch.setattr(personal_routing.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(personal_routing.PersonalRouteError):
        personal_routing.write_personal_content(
            123, direction="inbound", channel="mamadah", tag="mamadah",
            text="hello", chat_id=999, tg_message_id=168,
        )
