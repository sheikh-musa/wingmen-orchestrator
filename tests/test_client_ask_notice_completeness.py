"""Every media download-failure notice must stop being read as client speech.

F1 on PR #796 (cc-quality verdict bus #89033): strip_system_notice covered the
`document` failure notice only, but ingest.py builds the SAME "(download failed:
{e})" tail at six sites -- photo (:292), animation (:304), document (:312), voice
note (:323), video (:337), video note (:348). Each one stores our own error string
as the inbound text, and \berror\b inside "HTTP Error 400: Bad Request" opens a
24h chase for a client who asked nothing. Same bug class as operator_asks 6029
("sent a file (download failed: HTTP Error 400: Bad Request)"), six times over.

The fix matches the shape the six generators share, not one sample, so a seventh
media kind cannot reopen the gap. The dangerous direction is unchanged and is
asserted here in both directions: a notice that carries a caption the client
typed still classifies, and only text we can prove is ours is removed.
"""
import importlib

import psycopg
import pytest

ol = importlib.import_module("nervous_system.operator_log")

# The live error string from the incident that opened operator_asks 6029.
HTTP_400 = "HTTP Error 400: Bad Request"

# (label, the exact prefix ingest.py emits, minus caption) -- copied from the six
# generator sites, not paraphrased, so a renamed kind fails here instead of
# silently returning to the noise pile.
NOTICES = [
    ("photo",      "sent a photo (download failed: {e})"),
    ("animation",  "sent an animation (download failed: {e})"),
    ("document",   "sent a file (download failed: {e})"),
    ("voice note", "sent a voice note (download failed: {e})"),
    ("video",      "sent a video (download failed: {e})"),
    ("video note", "sent a video note (download failed: {e})"),
]

REAL_ASKS = [
    "please resend it as two smaller zips",
    "can you check whether set 2 is the theory paper?",
    "let me know what other follow up should i do",
]


# ── pure: the whole domain of failure notices, not the one sample ───────────
@pytest.mark.parametrize("label,template", NOTICES, ids=[n[0] for n in NOTICES])
def test_every_download_failure_notice_is_not_an_ask(label, template):
    """No caption: the row is entirely ours, so there is nothing to chase."""
    text = template.format(e=HTTP_400)
    assert ol.strip_system_notice(text) == ""
    assert ol.classify_client_ask(text) == "not_an_ask", label


@pytest.mark.parametrize("label,template", NOTICES, ids=[n[0] for n in NOTICES])
@pytest.mark.parametrize("caption", REAL_ASKS)
def test_a_caption_the_client_typed_survives_on_every_kind(label, template, caption):
    """Fail toward capturing: the notice is ours, the caption is his."""
    text = template.format(e=HTTP_400) + " | " + caption
    assert ol.strip_system_notice(text) == caption
    assert ol.classify_client_ask(text) == "ask", label


@pytest.mark.parametrize("label,template", NOTICES, ids=[n[0] for n in NOTICES])
def test_a_caption_that_asks_nothing_is_still_not_an_ask(label, template):
    text = template.format(e=HTTP_400) + " | these are the four sets"
    assert ol.strip_system_notice(text) == "these are the four sets"
    assert ol.classify_client_ask(text) == "not_an_ask", label


def test_an_error_tail_containing_the_caption_separator_keeps_the_caption():
    """find() takes the FIRST ' | ' after the marker. An {e} that itself contains
    the separator can only ever OVER-return (error tail + caption), never drop
    the caption -- the same fail-toward-capture property the marker had."""
    text = ("sent a photo (download failed: HTTP Error 400 (got ') | ' upstream))"
            " | please resend")
    assert "please resend" in ol.strip_system_notice(text)


# ── pure: the anchor. This must never fire on the client's own sentences ────
@pytest.mark.parametrize("text", [
    "the upload you sent a photo (download failed: on) is fine, don't resend it",
    "I sent a file (download failed: earlier) but ignore that, use the new one",
    "sent a parcel by courier to the office address",
])
def test_a_client_sentence_that_mentions_the_phrase_is_left_alone(text):
    assert ol.strip_system_notice(text) == text
    assert ol.classify_client_ask(text) == "not_an_ask"


def test_a_genuine_ask_is_never_treated_as_a_notice():
    text = "can you send the final theory questions today?"
    assert ol.strip_system_notice(text) == text
    assert ol.classify_client_ask(text) == "ask"


# ── end to end against the ephemeral ledger schema ──────────────────────────
@pytest.fixture
def db(operator_ledger_db):
    with psycopg.connect(__import__("os").environ["DATABASE_URL"],
                         autocommit=True) as conn:
        with conn.cursor() as cur:
            yield cur


def _track(cur, text, tag="hk-editor"):
    cur.execute(
        "INSERT INTO operator_messages (direction, channel, tag, text, created_at) "
        "VALUES ('inbound','telegram',%s,%s, now()) RETURNING id",
        (tag, text),
    )
    mid = cur.fetchone()[0]
    rid = ol.maybe_track_client_ask(mid, text, "hk-editor", tag=tag, chase_hours=24)
    cur.execute("SELECT triage_state, chase_by FROM operator_asks WHERE id=%s", (rid,))
    return cur.fetchone()


@pytest.mark.parametrize("label,template", NOTICES, ids=[n[0] for n in NOTICES])
def test_a_non_document_failure_no_longer_opens_a_chase(db, label, template):
    """6029 was the document case. Without the shape match, these five still
    opened a live deadline -- that is the gap F1 named."""
    state, chase_by = _track(db, template.format(e=HTTP_400))
    assert state == "not_an_ask", label
    assert chase_by is None, f"{label}: a not_an_ask row must never carry a deadline"


def test_a_captioned_failure_still_opens_a_chase(db):
    """The negative control: silencing the notice must not silence the client."""
    text = NOTICES[0][1].format(e=HTTP_400) + " | " + REAL_ASKS[0]
    state, chase_by = _track(db, text)
    assert state == "ask"
    assert chase_by is not None
