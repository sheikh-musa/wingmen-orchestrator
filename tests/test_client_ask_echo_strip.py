"""Client-channel classification must ignore OUR OWN words echoed back (bus #89013).

Raised by cc-cosem-exams-1 (#89005) and ordered by orch-console (#89013): a client
replying in-thread quotes our list back and appends his answer, and the token
classifier filed the quoted half as a brand-new client ask with a live 24h chase.

The texts below are the REAL operator_messages bodies from 2026-10-31, copied
verbatim rather than paraphrased -- a fixture written from a summary would pass
while the live text kept misfiling. Ids in the docstrings are the operator_asks
rows that were opened from them, so the ledger can be checked against this file.

Both directions are asserted everywhere, because a filter that stops filing asks
is worse than the noise it removes: every "no longer an ask" case here is paired
with a genuine ask that must still be filed (orch-console: fail toward capturing).
"""
import importlib
import os
from datetime import datetime, timezone

import psycopg
import pytest

ol = importlib.import_module("nervous_system.operator_log")


# ── the real texts (operator_messages) ───────────────────────────────────────
OUR_OUTBOUND_36351 = (
    "Thanks Hariz — three of the four zips came through. The first one did not "
    "download on our side; the server refused it, which usually means it is large, "
    "so please resend that one, and if it is big sending it as two smaller zips works."
    "\n\nTwo short things still needed with the files:"
    "\n- your one-line confirmation that the sheets are BLANK templates — no trainee "
    "names, IDs, photos or marks on them. That is the one point we must have before "
    "we load them."
    "\n- which set is which: a name for each zip, and whether it belongs to "
    "Wednesday's Product test or Thursday's theory paper."
    "\n\nOn what else to follow up, the rest of yesterday's list is still the answer:"
    "\n- the Product incident scenarios, and the correct answer set for them"
    "\n- the Arabic names for the equipment and PPE checklist, and which of those "
    "items must count as critical"
    "\n- the final theory questions"
    "\n- the Arabic name for sheet SS7-2 (now axe, chainsaw and hoseline)"
    "\n\nAnything that reaches us after Monday morning is at real risk of not making "
    "Wednesday."
)

# operator_asks 6029 — our own ingest notice, \berror\b matched "HTTP Error 400".
MSG_6029 = "sent a file (download failed: HTTP Error 400: Bad Request)"

# operator_asks 6037 — quotes our "Two short things" bullet, answers "-YES, BLANK TEMPLATE".
MSG_6037 = (
    "wo short things still needed with the files:"
    "\n- your one-line confirmation that the sheets are BLANK templates — no trainee "
    "names, IDs, photos or marks on them. That is the one point we must have before "
    "we load them. -YES, BLANK TEMPLATE"
    "\n- which set is which: a name for each zip, and whether it belongs to "
    "Wednesday's Product test or Thursday's theory paper. 2. RS - Skill Sheets (HM)"
)

# operator_asks 6041 — restates our list item by item with his own status.
MSG_6041 = (
    "On what else to follow up, the rest of yesterday's list is still the answer:"
    "\n- the Product incident scenarios, and the correct answer set for them - will "
    "update on this"
    "\n- the Arabic names for the equipment and PPE checklist, and which of those "
    "items must count as critical -its already updated in the new skill sheets"
    "\n- the final theory questions -i will place the question bank shortly"
    "\n- the Arabic name for sheet SS7-2 (now axe, chainsaw and hoseline) -part of "
    "recent upload"
)

# operator_asks 6043 — our first bullet handed straight back. Note the CURLY
# apostrophe in "Wednesday’s": the match must survive smart quotes, or a client
# whose keyboard differs from ours would defeat the filter.
MSG_6043 = (
    "- which set is which: a name for each zip, and whether it belongs to "
    "Wednesday’s Product test or Thursday’s theory paper. For the practical to use "
    "skill sheets from 2. RS - Skill Sheets (HM). The theory set send it soon"
)

# operator_asks 6034 — a GENUINE ask ("let me know what other follow up should i
# do"), answered 21 seconds later. Must keep filing.
MSG_6034 = "let me know what other follow up should i do"

# operator_asks 6212 — the required negative control: quotes us, then asks a real
# question. A blunt "quotes us => not an ask" rule kills this one.
MSG_6212_QUOTE_THEN_ASK = (
    '↩️ re "Update 9:00pm SGT ✅ Done since 6pm: • Admin accounts no longer see the '
    'Move and ": • Rows 4, 5 and 6 of your 7 names: do you want their extra '
    "fuller-name profiles joined too? If yes, I'll send you the names to check "
    "first.\n\nwhich one?"
)


# ── pure: our own ingest notices never classify as client speech ────────────
def test_download_failure_notice_is_not_an_ask():
    """6029: our own media-path error string (\berror\b) is not a client request."""
    assert ol.classify_client_ask(MSG_6029) == "not_an_ask"


def test_download_failure_notice_keeps_a_caption_the_client_typed():
    """Fail toward capturing: the notice is ours, the caption is his."""
    assert ol.classify_client_ask(
        MSG_6029 + " | please resend it as two smaller zips") == "ask"


def test_file_upload_notice_keeps_its_caption():
    assert ol.classify_client_ask(
        "sent a FILE → /tmp/ff01.zip  | caption: can you check set 2 today?") == "ask"


def test_a_caption_that_asks_nothing_is_still_not_an_ask():
    assert ol.classify_client_ask(
        "sent a FILE → /tmp/ff01.zip  | caption: these are the four sets") == "not_an_ask"


# ── pure: the min-span floor ────────────────────────────────────────────────
class _FakeCur:
    """strip_our_echo only needs execute()/fetchall() from its corpus query."""

    def __init__(self, outbound):
        self._outbound = outbound

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params

    def fetchall(self):
        return [(t,) for t in self._outbound]


def test_a_short_shared_run_does_not_erase_a_real_ask():
    """'please send the invoice' is 22 chars -- below the floor, so it stays and
    the surrounding question still classifies as an ask."""
    text = "please send the invoice for October"
    out = ol.strip_our_echo(text, "cosem-exams", _FakeCur(["please send the invoice"]))
    assert out == text
    assert ol.classify_client_ask(out) == "ask"


def test_a_long_shared_run_is_removed():
    shared = "which set is which: a name for each zip, and whether it belongs"
    text = shared + " -- answered below"
    out = ol.strip_our_echo(text, "cosem-exams", _FakeCur([shared]))
    assert shared not in out
    assert "answered below" in out


def test_without_a_tag_nothing_is_stripped():
    """The backfill / any tagless caller keeps the old behaviour."""
    assert ol.strip_our_echo(MSG_6043, None, _FakeCur([MSG_6043])) == MSG_6043


# ── end to end against the ephemeral ledger schema ──────────────────────────
@pytest.fixture
def db(operator_ledger_db):
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        with conn.cursor() as cur:
            yield cur


def _msg(cur, direction, text, at, tag="cosem-exams"):
    cur.execute(
        "INSERT INTO operator_messages (direction, channel, tag, text, created_at) "
        "VALUES (%s,'telegram',%s,%s,%s) RETURNING id",
        (direction, tag, text, at),
    )
    return cur.fetchone()[0]


def _track(cur, mid, text, tag="cosem-exams"):
    rid = ol.maybe_track_client_ask(mid, text, "cosem-exams",
                                    tag=tag, chase_hours=24)
    cur.execute("SELECT triage_state, chase_by FROM operator_asks WHERE id=%s", (rid,))
    return cur.fetchone()


@pytest.mark.parametrize("text,at", [
    (MSG_6037, "2026-10-31 02:54:51+00"),
    (MSG_6041, "2026-10-31 03:00:39+00"),
    (MSG_6043, "2026-10-31 03:18:28+00"),
])
def test_echoed_client_replies_no_longer_open_asks(db, text, at):
    """6037 / 6041 / 6043 -- the three echo misfires, on their real timings."""
    _msg(db, "outbound", OUR_OUTBOUND_36351, "2026-10-31 02:38:23+00")
    mid = _msg(db, "inbound", text, at)
    state, chase_by = _track(db, mid, text)
    assert state == "not_an_ask"
    assert chase_by is None, "a not_an_ask row must never carry a live chase deadline"


def test_our_own_download_notice_no_longer_opens_an_ask(db):
    """6029, with no outbound at all -- fixed by the exact-marker strip, not the echo."""
    mid = _msg(db, "inbound", MSG_6029, "2026-10-31 02:36:54+00")
    state, chase_by = _track(db, mid, MSG_6029)
    assert state == "not_an_ask" and chase_by is None


def test_a_genuine_ask_still_opens_an_ask(db):
    """6034: he IS asking us; the strip must not touch an unmatched message."""
    _msg(db, "outbound", OUR_OUTBOUND_36351, "2026-10-31 02:38:23+00")
    mid = _msg(db, "inbound", MSG_6034, "2026-10-31 02:37:57+00")
    state, chase_by = _track(db, mid, MSG_6034)
    assert state == "ask"
    assert chase_by is not None, "a real client ask must get its deadline"


def test_a_client_who_quotes_us_and_then_asks_still_opens_an_ask(db):
    """6212 -- the negative control orch-console required. Stripping only the
    quoted run leaves his new question, which must still file."""
    _msg(db, "outbound", "Update 9:00pm SGT ✅ Done since 6pm",
         "2026-10-31 13:00:00+00")
    mid = _msg(db, "inbound", MSG_6212_QUOTE_THEN_ASK, "2026-10-31 14:37:59+00")
    state, chase_by = _track(db, mid, MSG_6212_QUOTE_THEN_ASK)
    assert state == "ask"
    assert chase_by is not None


def test_the_echo_corpus_stops_at_the_inbounds_own_timestamp(db):
    """The corpus stops at the inbound. Our reply routinely restates the client's
    points back to him; counting it let OUR paraphrase delete HIS ask (found live:
    outbound 36373 at 03:19 restated 36363's status lines from 03:00).

    Asserted directly on the real SQL, in both directions, because an end-to-end
    case cannot isolate this: with our EARLIER outbound also seeded, the label is
    not_an_ask whether or not the bound holds."""
    later = "can you use the name axe chainsaw and hoseline for SS7-2"
    _msg(db, "outbound", later, "2026-10-31 03:19:38+00")
    before = datetime(2026, 10, 31, 3, 0, 39, tzinfo=timezone.utc)

    assert ol.strip_our_echo(later, "cosem-exams", db, before=before) == later, \
        "an outbound sent AFTER the inbound is not part of its echo corpus"

    unbounded = ol.strip_our_echo(later, "cosem-exams", db, before=None)
    assert later not in unbounded, "the bound is load-bearing, not decorative"
    assert ol.classify_client_ask(unbounded) == "not_an_ask", \
        "unbounded, our own later restatement erases a real ask"


def test_omitting_the_tag_keeps_the_old_behaviour(db):
    """Pins WHY ingest.py must pass tag=: without it the echo misfires return."""
    _msg(db, "outbound", OUR_OUTBOUND_36351, "2026-10-31 02:38:23+00")
    mid = _msg(db, "inbound", MSG_6043, "2026-10-31 03:18:28+00")
    state, _ = _track(db, mid, MSG_6043, tag=None)
    assert state == "ask"


def test_the_stored_ask_text_is_the_original_inbound(db):
    """The lane's human must see what the client actually wrote, not our de-echoed
    edition -- only the LABEL is computed from the stripped text."""
    _msg(db, "outbound", OUR_OUTBOUND_36351, "2026-10-31 02:38:23+00")
    mid = _msg(db, "inbound", MSG_6041, "2026-10-31 03:00:39+00")
    rid = ol.maybe_track_client_ask(mid, MSG_6041, "cosem-exams", tag="cosem-exams")
    db.execute("SELECT ask, source_msg_id FROM operator_asks WHERE id=%s", (rid,))
    assert db.fetchone() == (MSG_6041, mid)
