"""Pure-function proof for the gated-inbound digest (PR #174 follow-up, bus
#43647/#43879). No live DB — mirrors the discipline of test_model_precedence.py.

Uses the REAL gate (imports nervous_system.ingest.gate_allows + Channel) — no
mirror — and builds real Channel objects from fixture tuples, so the test exercises
the exact gate the digest uses in production. Proves: gated resolution via the real
gate, orphan-tag handling, the THREE buckets (service / operator-DM / genuine),
lead-with-actionable, empty-genuine→None (no spam), and the no-message-text invariant.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

from nervous_system.ingest import Channel  # noqa: E402  (the real gate's Channel)
from gated_inbound_digest import (  # noqa: E402
    row_gated, bucket_of, classify, build_digest_body,
    SERVICE, OPERATOR_DM, GENUINE,
)

MUSA = 286619815  # MUSA_TELEGRAM_ID (an operator id)


def _chan(key, tag, chat_ids, usernames, audience="operator", owner_lane=None,
          stage_file_to_agent=None):
    """Build a REAL Channel from a fixture tuple in Channel.COLS order
    (migration 085 added audience/owner_lane; migration 092 added
    stage_file_to_agent as the final column)."""
    return Channel((key, None, "agent-session", key, None, None,
                    chat_ids, usernames, None, tag, "substrate", 0,
                    audience, owner_lane, stage_file_to_agent))


# tag -> [Channel]; note operator-orch uses tag 'orch-channel' in prod
_CHANNELS = {
    "oeh": [_chan("oeh", "oeh", [-5585966657], [], audience="client", owner_lane="oeh")],
    "angullia": [_chan("angullia", "angullia", [-5449309564], [],
                        audience="client", owner_lane="angullia")],
    "orch-channel": [_chan("operator-orch", "orch-channel", [MUSA, -5319479270], [])],
}
_OPS = {MUSA}


# ── gated resolution via the REAL gate ───────────────────────────────────────
def test_allowed_sender_not_gated():
    assert row_gated({"tag": "oeh", "chat_id": "-5585966657", "from_username": None}, _CHANNELS) is False


def test_unknown_sender_on_known_channel_is_gated():
    assert row_gated({"tag": "oeh", "chat_id": "-5000000000", "from_username": "newperson"}, _CHANNELS) is True


def test_orphan_tag_not_flagged():
    assert row_gated({"tag": "removed", "chat_id": "42", "from_username": None}, _CHANNELS) is False


def test_none_chat_id_is_gated():
    assert row_gated({"tag": "oeh", "chat_id": None, "from_username": None}, _CHANNELS) is True


# ── the three buckets ────────────────────────────────────────────────────────
def test_bucket_service_when_no_chat():
    assert bucket_of({"tag": "oeh", "chat_id": None}, _OPS) == SERVICE


def test_bucket_operator_dm_for_known_operator_id():
    # Musa's private /start to @angullia_bot (chat 286619815) — bot setup, NOT actionable
    assert bucket_of({"tag": "angullia", "chat_id": str(MUSA)}, _OPS) == OPERATOR_DM


def test_bucket_genuine_for_unknown_sender():
    assert bucket_of({"tag": "oeh", "chat_id": "-5000000000"}, _OPS) == GENUINE


def test_classify_only_buckets_gated_rows_into_three():
    rows = [
        {"tag": "oeh", "chat_id": "-5585966657", "from_username": None, "from_name": "allowed"},   # not gated
        {"tag": "oeh", "chat_id": None, "from_username": None, "from_name": None},                  # gated -> service
        {"tag": "angullia", "chat_id": str(MUSA), "from_username": "haikusmesh", "from_name": "Musa"},  # gated -> operator_dm
        {"tag": "oeh", "chat_id": "-9999", "from_username": "stranger", "from_name": "Stranger"},   # gated -> genuine
    ]
    b = classify(rows, _CHANNELS, _OPS)
    assert len(b[GENUINE]) == 1 and len(b[OPERATOR_DM]) == 1 and len(b[SERVICE]) == 1


# ── digest body: lead-with-actionable, no-spam, privacy ──────────────────────
def test_no_genuine_returns_none_even_with_operator_and_service():
    b = {GENUINE: [], OPERATOR_DM: [{"tag": "angullia", "chat_id": str(MUSA), "from_name": "Musa", "from_username": "haikusmesh"}],
         SERVICE: [{"tag": "oeh", "chat_id": None, "from_name": None, "from_username": None}]}
    assert build_digest_body(b, _CHANNELS) is None  # pure no-action noise = no post


def test_body_leads_with_actionable_summarizes_rest_and_hides_text():
    b = {
        GENUINE: [
            {"tag": "oeh", "chat_id": "-9999", "from_username": "stranger", "from_name": "Real Contact", "text": "LEAKME hello"},
            {"tag": "oeh", "chat_id": "-9999", "from_username": "stranger", "from_name": "Real Contact", "text": "LEAKME again"},
        ],
        OPERATOR_DM: [{"tag": "angullia", "chat_id": str(MUSA), "from_name": "Musa", "from_username": "haikusmesh", "text": "SECRET /start"}],
        SERVICE: [{"tag": "oeh", "chat_id": None, "from_name": None, "from_username": None, "text": "my_chat_member"}],
    }
    body = build_digest_body(b, _CHANNELS)
    assert body is not None
    # privacy: no message text ever
    assert "LEAKME" not in body and "SECRET" not in body and "my_chat_member" not in body
    # leads with the actionable genuine sender
    assert "ACTIONABLE" in body and body.index("ACTIONABLE") < body.index("No action")
    assert "oeh: Real Contact @stranger (chat -9999) — 2 msg(s)" in body
    # operator DM summarized + explicit do-not-allowlist, service summarized
    assert "operator DM to a client bot" in body and "do NOT allowlist" in body
    assert "service updates, no sender" in body
