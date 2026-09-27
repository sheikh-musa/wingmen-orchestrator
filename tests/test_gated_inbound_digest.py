"""Pure-function proof for the gated-inbound digest classifier (PR #174 follow-up,
bus #43647). No DB/network — mirrors the discipline of test_model_precedence.py.

Proves: (1) is_gated is a faithful inverse of ingest.gate_allows (chat_id OR
normalized-username allowlist), (2) row_is_gated resolves a row's tag to its
channel allowlists and treats orphan tags as NOT gated, (3) build_digest_body
aggregates + never leaks message text, and returns None on empty (no spam).
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))

from gated_inbound_digest import (  # noqa: E402
    is_gated, row_is_gated, build_digest_body,
)


# ── is_gated: the inverse of gate_allows ─────────────────────────────────────
def test_allowed_chat_id_is_not_gated():
    assert is_gated(286619815, None, [286619815], []) is False
    # operator_messages stores chat_id as a STRING → must still match
    assert is_gated("286619815", None, [286619815], []) is False


def test_allowed_username_is_not_gated_normalized():
    # gate_allows lstrips '@' and lowercases both sides
    assert is_gated(999, "@Musa", [], ["musa"]) is False
    assert is_gated(999, "musa", [], ["@MUSA"]) is False


def test_unknown_sender_is_gated():
    assert is_gated(-5585966657, "stranger", [286619815], ["musa"]) is True


def test_empty_allowlists_gate_everything():
    # ingest: "empty allowlists accept NOTHING"
    assert is_gated(123, "someone", [], []) is True


def test_none_or_unparseable_chat_id_is_gated():
    assert is_gated(None, None, [123], []) is True
    assert is_gated("not-a-number", None, [123], []) is True


# ── row_is_gated: tag → channel allowlist resolution ─────────────────────────
_CHANNELS = {
    "oeh": [("oeh", [-5585966657], [])],
    "orch-channel": [("operator-orch", [286619815, -5319479270], [])],
}


def test_row_allowed_by_its_channel_is_not_gated():
    row = {"tag": "oeh", "chat_id": "-5585966657", "from_username": None, "from_name": "OEH bot admin"}
    assert row_is_gated(row, _CHANNELS) is False


def test_row_from_unknown_chat_on_known_channel_is_gated():
    row = {"tag": "oeh", "chat_id": "-5000000000", "from_username": "newperson", "from_name": "New Person"}
    assert row_is_gated(row, _CHANNELS) is True


def test_orphan_tag_is_not_flagged_gated():
    # a tag with no matching channel is an orphan, not a 'new contact on a known channel'
    row = {"tag": "removed-channel", "chat_id": "42", "from_username": None, "from_name": "x"}
    assert row_is_gated(row, _CHANNELS) is False


def test_row_allowed_by_any_matching_channel_wins():
    channels = {"shared": [("a", [111], []), ("b", [222], [])]}
    assert row_is_gated({"tag": "shared", "chat_id": "222", "from_username": None, "from_name": "y"}, channels) is False
    assert row_is_gated({"tag": "shared", "chat_id": "333", "from_username": None, "from_name": "z"}, channels) is True


# ── build_digest_body: aggregation, privacy, empty-is-None ───────────────────
def test_empty_returns_none_no_spam():
    assert build_digest_body([], _CHANNELS) is None


def test_body_aggregates_and_omits_text():
    rows = [
        {"tag": "oeh", "chat_id": "-5000000000", "from_username": "newperson", "from_name": "New Person",
         "text": "SECRET body that must never appear"},
        {"tag": "oeh", "chat_id": "-5000000000", "from_username": "newperson", "from_name": "New Person",
         "text": "another secret"},
        {"tag": "orch-channel", "chat_id": "-1", "from_username": None, "from_name": "Someone Else",
         "text": "leak me"},
    ]
    body = build_digest_body(rows, _CHANNELS)
    assert body is not None
    # never leak message text
    assert "SECRET" not in body and "secret" not in body and "leak" not in body
    # counts: 3 total, 2 senders
    assert "3 message(s)" in body and "2 " in body
    # channel_key resolved (orch-channel tag -> operator-orch key), sender + count present
    assert "oeh:" in body and "operator-orch:" in body
    assert "New Person" in body and "@newperson" in body and "2 msg(s)" in body
