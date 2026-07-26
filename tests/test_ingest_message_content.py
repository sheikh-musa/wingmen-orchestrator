"""message_content type coverage + the diagnosable unknown-shape fallback.

Regression cover for the 2026-07-26 01:36:49Z nazim-console loss: an operator
message landed as the bare `[non-text update 440376558]` (with `from_name` set,
so it WAS his), its content gone and — because getUpdates consumes the raw
update — unreconstructable after the fact.

Two obligations tested here:
  1. every message type that CARRIES content no longer falls through to the
     marker (video / video_note / animation / sticker / poll / venue /
     location / contact / dice);
  2. a shape we still have no handler for records its KEY LIST (never values —
     the row is durable and widely read), so the next unknown is diagnosable.

Pure-function tests: no DB, and `_download_media` is stubbed — nothing here
touches api.telegram.org.
"""
import pytest

from nervous_system import ingest


class _Ch:
    """Minimal stand-in for ingest.Channel — message_content only reads these."""
    key = "nazim-console"
    token = "TESTTOKEN"


@pytest.fixture
def ch():
    return _Ch()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Every download resolves to a deterministic fake path — never the network."""
    monkeypatch.setattr(ingest, "_download_media",
                        lambda token, file_id, name=None: f"/media/{name or file_id}")


@pytest.fixture
def logged(monkeypatch):
    lines = []
    monkeypatch.setattr(ingest, "_log_line", lines.append)
    return lines


def _msg(**kw):
    base = {"message_id": 1, "date": 0, "chat": {"id": -100}, "from": {"id": 7}}
    base.update(kw)
    return base


# ── types that carry content must no longer be discarded ─────────────────────

def test_sticker_logs_emoji_and_set_without_downloading(ch, monkeypatch):
    monkeypatch.setattr(ingest, "_download_media",
                        lambda *a, **k: pytest.fail("stickers must not be downloaded"))
    out = ingest.message_content(
        ch, _msg(sticker={"file_id": "S1", "emoji": "👍", "set_name": "HotCherry"}), 1)
    assert out == "sent a STICKER 👍 (set: HotCherry)"


def test_video_with_caption_downloads_and_keeps_caption(ch):
    out = ingest.message_content(
        ch, _msg(video={"file_id": "V1", "duration": 12, "file_name": "screen.mp4"},
                 caption="here is the bug"), 2)
    assert out == "sent a VIDEO (12s) → /media/screen.mp4  | caption: here is the bug"


def test_poll_logs_question_and_options(ch):
    out = ingest.message_content(
        ch, _msg(poll={"question": "ship tonight?",
                       "options": [{"text": "yes"}, {"text": "no"}]}), 3)
    assert out == 'sent a POLL: "ship tonight?" — options: yes | no'


def test_location_logs_coordinates(ch):
    out = ingest.message_content(
        ch, _msg(location={"latitude": 24.4539, "longitude": 54.3773}), 4)
    assert out == "shared a LOCATION → 24.4539, 54.3773"


def test_video_note_animation_contact_dice_all_captured(ch):
    assert ingest.message_content(
        ch, _msg(video_note={"file_id": "N1234567890AB", "duration": 5}), 5) \
        == "sent a VIDEO NOTE (5s) → /media/videonote_N1234567890A.mp4"
    # animation must win over the backward-compat `document` Telegram also sets
    assert ingest.message_content(
        ch, _msg(animation={"file_id": "A1", "file_name": "cat.mp4"},
                 document={"file_id": "A1", "file_name": "cat.mp4"}), 6) \
        == "sent an ANIMATION/GIF → /media/cat.mp4"
    assert ingest.message_content(
        ch, _msg(contact={"first_name": "Hafiz", "phone_number": "+6591234567"}), 7) \
        == "shared a CONTACT: Hafiz — +6591234567"
    assert ingest.message_content(ch, _msg(dice={"emoji": "🎲", "value": 4}), 8) \
        == "sent a DICE 🎲 → 4"


def test_venue_beats_location_and_keeps_the_title(ch):
    out = ingest.message_content(
        ch, _msg(venue={"title": "Masjid", "address": "Corniche",
                        "location": {"latitude": 24.5, "longitude": 54.4}},
                 location={"latitude": 24.5, "longitude": 54.4}), 9)
    assert out == "shared a VENUE: Masjid — Corniche (24.5, 54.4)"


# ── the unknown shape must be diagnosable, keys only ─────────────────────────

def test_unknown_type_records_the_key_shape_not_values(ch, logged):
    out = ingest.message_content(
        ch, _msg(story={"id": 99, "chat": {"id": -100}}), 440376558)
    assert out == ("[non-text update 440376558 — keys: "
                   "chat,date,from,message_id,story]")
    # keys ONLY — no value from the update may appear in the durable row
    assert "99" not in out and "-100" not in out
    # and the next occurrence is visible in logs/nazim-ingest.log
    assert len(logged) == 1
    assert logged[0].startswith("nazim-console: WARNING unhandled message shape "
                                "on update 440376558 — keys: chat,date,from,message_id,story")


def test_unknown_key_list_is_capped(ch, logged):
    out = ingest.message_content(ch, {f"k{i:03d}": i for i in range(200)}, 12)
    keys = out.split("keys: ", 1)[1].rstrip("]")
    assert len(keys) == ingest.SHAPE_KEYS_MAX


def test_text_still_wins_over_the_marker(ch, logged):
    assert ingest.message_content(ch, _msg(text="plain words", story={}), 13) == "plain words"
    assert logged == []          # a handled message must not warn


# ── the durable log write must survive a broken extractor ────────────────────

def test_extraction_failure_degrades_to_marker_never_raises(ch, monkeypatch, logged):
    def boom(*a, **k):
        raise RuntimeError("branch bug")
    monkeypatch.setattr(ingest, "_media_content", boom)
    out = ingest.message_content(ch, _msg(video={"file_id": "V1"}), 14)
    assert out == "[non-text update 14]"
    assert "content extraction raised on update 14 (RuntimeError: branch bug)" in logged[0]


def test_reply_context_still_prefixes_the_new_types(ch):
    out = ingest.message_content(
        ch, _msg(sticker={"file_id": "S1", "emoji": "👍"},
                 reply_to_message={"text": "did it deploy?"}), 15)
    assert out == '↩️ re "did it deploy?": sent a STICKER 👍'
