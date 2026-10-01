"""Collision-proof media filenames (bus #47469).

Regression cover for a real cross-client overwrite: two different bots
(channels) each returned a Telegram getFile `file_path` with the SAME
per-bot-numbered basename (file_path numbering is PER BOT, e.g.
"photos/file_6.jpg"). The old naming scheme used only that basename, so the
second bot's download silently clobbered the first bot's already-downloaded
client media under `open(dest, "wb")` -- no error, no trace, the earlier
client's bytes just gone. Proof in production: operator_messages rows 23614
(angullia) and 23643 (cosem-caai) both logged logs/tg_media/photos_file_6.jpg;
after the second download, that path held the cosem image and angullia's
original was unrecoverable (file_id is never persisted, so it can't be
re-fetched after the fact).

The fix keys the local filename on <channel>_<update_id>_<file_unique_id>,
all Telegram/caller-supplied values that are unique per actual file -- two
different bots can return the same file_path basename and still land at two
distinct local files. `_tg_download_file` additionally opens with O_EXCL
('xb') so even a same-keyed re-download (idempotent re-process) is a no-op
short-circuit rather than a truncate-and-overwrite.

Pure-function tests: urllib is monkeypatched, nothing touches the network.
"""
import json
import os
from io import BytesIO
from urllib.error import URLError

import pytest

from nervous_system import ingest


class _FakeResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _fake_urlopen_factory(file_path_by_token: dict, content_by_token: dict):
    """Routes getFile -> its configured file_path, and the file-CDN fetch ->
    that token's configured bytes. Keyed by token so two different "bots"
    (tokens) can return the SAME file_path with DIFFERENT bytes, exactly like
    the production collision."""
    def _urlopen(url, timeout=None):
        for token, fp in file_path_by_token.items():
            if url == f"https://api.telegram.org/bot{token}/getFile?file_id=F1":
                return _FakeResponse(json.dumps({"result": {"file_path": fp}}).encode())
            if url == f"https://api.telegram.org/file/bot{token}/{fp}":
                return _FakeResponse(content_by_token[token])
        raise AssertionError(f"unexpected urlopen: {url}")
    return _urlopen


@pytest.fixture
def media_dir(tmp_path, monkeypatch):
    d = tmp_path / "tg_media"
    monkeypatch.setattr(ingest, "_MEDIA_DIR", str(d))
    return d


def test_two_bots_same_file_path_do_not_collide(media_dir, monkeypatch):
    """The exact production shape: two channels, two tokens, identical
    Telegram file_path -- must land at two different files, both intact."""
    monkeypatch.setattr(
        ingest.urllib.request, "urlopen",
        _fake_urlopen_factory(
            file_path_by_token={"TOKEN_ANGULLIA": "photos/file_6.jpg",
                                 "TOKEN_COSEM": "photos/file_6.jpg"},
            content_by_token={"TOKEN_ANGULLIA": b"angullia-bytes",
                               "TOKEN_COSEM": b"cosem-bytes"},
        ))

    path_a = ingest._download_media("TOKEN_ANGULLIA", "F1", "UNIQUE_A", "angullia", 23614)
    path_b = ingest._download_media("TOKEN_COSEM", "F1", "UNIQUE_B", "cosem-caai", 23643)

    assert path_a != path_b
    assert open(path_a, "rb").read() == b"angullia-bytes"
    assert open(path_b, "rb").read() == b"cosem-bytes"
    # the angullia file must still exist and be untouched after the cosem download
    assert os.path.exists(path_a)
    assert open(path_a, "rb").read() == b"angullia-bytes"


def test_media_dest_name_keys_on_channel_update_and_unique_id():
    same_fp = "photos/file_6.jpg"
    name_a = ingest._media_dest_name("angullia", 23614, "UNIQUE_A", same_fp, None)
    name_b = ingest._media_dest_name("cosem-caai", 23643, "UNIQUE_B", same_fp, None)
    assert name_a != name_b
    assert name_a == "angullia_23614_UNIQUE_A.jpg"
    assert name_b == "cosem-caai_23643_UNIQUE_B.jpg"


def test_repeat_download_of_same_update_is_idempotent_not_overwritten(media_dir, monkeypatch):
    """Re-processing the SAME update (at-least-once redelivery) must short-
    circuit on the existing file, never re-open it with a truncating write."""
    urlopen = _fake_urlopen_factory(
        file_path_by_token={"TOKEN": "documents/file_9.pdf"},
        content_by_token={"TOKEN": b"original-bytes"},
    )
    monkeypatch.setattr(ingest.urllib.request, "urlopen", urlopen)
    path = ingest._download_media("TOKEN", "F1", "UNIQUE_X", "irsyad", 100, "invoice.pdf")
    assert open(path, "rb").read() == b"original-bytes"

    # second call for the identical (channel, update, file_unique_id): even if
    # urlopen would now serve different bytes, the existing file must win.
    urlopen2 = _fake_urlopen_factory(
        file_path_by_token={"TOKEN": "documents/file_9.pdf"},
        content_by_token={"TOKEN": b"DIFFERENT-bytes-would-be-a-bug"},
    )
    monkeypatch.setattr(ingest.urllib.request, "urlopen", urlopen2)
    path2 = ingest._download_media("TOKEN", "F1", "UNIQUE_X", "irsyad", 100, "invoice.pdf")
    assert path2 == path
    assert open(path2, "rb").read() == b"original-bytes"


def test_tg_download_file_o_excl_never_truncates_existing_bytes(media_dir, monkeypatch):
    """Direct O_EXCL check at the _tg_download_file layer: a dest that
    already holds bytes is returned as-is, the network response is never
    written over it."""
    dest = os.path.join(str(media_dir), "already_there.jpg")
    os.makedirs(media_dir, exist_ok=True)
    with open(dest, "wb") as f:
        f.write(b"pre-existing-bytes")

    def _urlopen(url, timeout=None):
        return _FakeResponse(b"network-bytes-must-not-land-here")
    monkeypatch.setattr(ingest.urllib.request, "urlopen", _urlopen)

    out = ingest._tg_download_file("TOKEN", "whatever/path.jpg", dest)
    assert out == dest
    assert open(dest, "rb").read() == b"pre-existing-bytes"
