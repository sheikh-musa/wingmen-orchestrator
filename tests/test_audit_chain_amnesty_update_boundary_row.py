"""Pure-function tests for ops/audit-chain-amnesty/update_boundary_row.py (console #81726).

No database: the fingerprint/count checks, frozen-file validation, gate-row
validation, read-back assertion and the --apply/--gate argument guard are all
exercised without connecting anywhere."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "ops" / "audit-chain-amnesty" / "update_boundary_row.py"
_spec = importlib.util.spec_from_file_location("update_boundary_row", _PATH)
ubr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ubr)

ORG = "73339164-7c1f-40ba-a093-33f1f292dd4c"
HASH = "a" * 64
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def _doc(ids, **over):
    d = {
        "org_id": ORG,
        "amnesty_mechanism": "row-id-list",
        "amnesty_row_id_snapshot_at": "2026-10-29",
        "amnesty_snapshot_tip_id": max(ids) if ids else 1,
        "count": len(ids),
        "frozen_row_ids": list(ids),
    }
    d.update(over)
    return d


# ── fingerprint ──

def test_fingerprint_is_sha256_of_sorted_comma_joined_first8():
    ids = [30, 4, 100]
    assert ubr.fingerprint(ids) == hashlib.sha256(b"4,30,100").hexdigest()[:8]


def test_fingerprint_sorts_numerically_not_lexically():
    assert ubr.fingerprint([10, 9]) == hashlib.sha256(b"9,10").hexdigest()[:8]


def test_fingerprint_order_independent():
    assert ubr.fingerprint([3, 1, 2]) == ubr.fingerprint([1, 2, 3])


# ── frozen file validation ──

def test_parse_frozen_ok():
    f = ubr.parse_frozen(_doc([98, 99, 124428]))
    assert f["count"] == 3 and f["tip_id"] == 124428 and f["ids"] == [98, 99, 124428]
    assert f["fp"] == ubr.fingerprint([98, 99, 124428])


@pytest.mark.parametrize("bad", [
    {"frozen_row_ids": []},
    {"frozen_row_ids": [1, 1]},
    {"frozen_row_ids": [1, -2]},
    {"frozen_row_ids": [1, "2"]},
    {"frozen_row_ids": [1, True]},
    {"count": 99},
    {"org_id": "00000000-0000-0000-0000-000000000000"},
    {"amnesty_snapshot_tip_id": 1},          # ids above the tip
    {"amnesty_snapshot_tip_id": None},
    {"amnesty_row_id_snapshot_at": "yesterday"},
])
def test_parse_frozen_refuses(bad):
    with pytest.raises(ubr.Refuse):
        ubr.parse_frozen(_doc([5, 6, 7], **bad))


def test_check_expectations_fp_mismatch_refuses():
    f = ubr.parse_frozen(_doc([5, 6, 7]))
    with pytest.raises(ubr.Refuse, match="fingerprint"):
        ubr.check_expectations(f, expect_fp="66e57e02", expect_count=None)


def test_check_expectations_count_mismatch_refuses():
    f = ubr.parse_frozen(_doc([5, 6, 7]))
    with pytest.raises(ubr.Refuse, match="count"):
        ubr.check_expectations(f, expect_fp=f["fp"], expect_count=5035)


def test_check_expectations_pass_and_fp_case_insensitive():
    f = ubr.parse_frozen(_doc([5, 6, 7]))
    ubr.check_expectations(f, expect_fp=f["fp"].upper(), expect_count=3)


def test_tip_hash_validation():
    assert ubr.check_tip_hash(HASH.upper()) == HASH
    for bad in ("", "abc", "g" * 64, "a" * 63):
        with pytest.raises(ubr.Refuse):
            ubr.check_tip_hash(bad)


# ── gate row validation ──

def _gate(**over):
    row = {"id": 81800, "from_agent": "orch-console", "message_type": "decision",
           "body": "GO: update audit_chain_boundaries row 1 to fp 66e57e02",
           "created_at": NOW - timedelta(hours=1)}
    row.update(over)
    return (row["id"], row["from_agent"], row["message_type"], row["body"], row["created_at"])


def test_gate_ok_decision_and_agreed():
    assert ubr.validate_gate_row(_gate(), 81800, now=NOW)["id"] == 81800
    ubr.validate_gate_row(_gate(message_type="agreed"), 81800, now=NOW)


@pytest.mark.parametrize("over,match", [
    ({"from_agent": "cc-irsyad-coord"}, "not 'orch-console'"),
    ({"message_type": "update"}, "message_type"),
    ({"body": "GO on the amnesty refresh"}, "audit_chain_boundaries"),
    ({"body": None}, "audit_chain_boundaries"),
    ({"created_at": NOW - timedelta(days=15)}, "staleness"),
])
def test_gate_refusals(over, match):
    with pytest.raises(ubr.Refuse, match=match):
        ubr.validate_gate_row(_gate(**over), 81800, now=NOW)


def test_gate_missing_row_refuses():
    with pytest.raises(ubr.Refuse, match="does not exist"):
        ubr.validate_gate_row(None, 1, now=NOW)


# ── read-back assertion ──

def _stored(ids, tip, h, count=None):
    return {"amnesty_row_ids": ids, "amnesty_row_id_count": len(ids) if count is None else count,
            "amnesty_snapshot_tip_id": tip, "amnesty_snapshot_tip_hash": h}


def test_readback_ok():
    f = ubr.parse_frozen(_doc([5, 6, 7]))
    ubr.assert_readback(_stored([5, 6, 7], 7, HASH), f, HASH)


@pytest.mark.parametrize("stored", [
    _stored([5, 6], 7, HASH),
    _stored([5, 6, 8], 7, HASH),
    _stored([5, 6, 7], 8, HASH),
    _stored([5, 6, 7], 7, "b" * 64),
    _stored([5, 6, 7], 7, HASH, count=4),
    _stored([], 7, HASH),
])
def test_readback_mismatch_refuses(stored):
    f = ubr.parse_frozen(_doc([5, 6, 7]))
    with pytest.raises(ubr.Refuse, match="read-back"):
        ubr.assert_readback(stored, f, HASH)


def test_summarize_row_is_payload_free():
    s = ubr.summarize_row({"amnesty_row_ids": [1, 2], "amnesty_row_id_count": 2})
    assert "amnesty_row_ids" not in s and s["stored_ids_len"] == 2 and s["stored_ids_fp"] == ubr.fingerprint([1, 2])


def test_new_values_cover_only_amnesty_columns():
    f = ubr.parse_frozen(_doc([5, 6, 7]))
    nv = ubr.new_values(f, tip_hash=HASH, decision_ref="console #81726", rederivation_ref="r",
                        annotated_by="x", notes=None)
    assert set(nv) == set(ubr._COLS) and all(c.startswith("amnesty_") for c in nv)
    assert "3 ids" in nv["amnesty_notes"] and f["fp"] in nv["amnesty_notes"]


# ── CLI guards (no DB reached) ──

def test_main_apply_without_gate_refuses_before_connecting(tmp_path, monkeypatch, capsys):
    p = tmp_path / "f.json"
    p.write_text(json.dumps(_doc([5, 6, 7])))
    monkeypatch.setattr(ubr, "_connect", lambda: pytest.fail("must not connect"))
    rc = ubr.main(["--frozen-file", str(p), "--expect-fp", ubr.fingerprint([5, 6, 7]),
                   "--tip-hash", HASH, "--decision-ref", "d", "--rederivation-ref", "r", "--apply"])
    assert rc == 2 and "--gate" in capsys.readouterr().err


def test_main_wrong_fp_refuses_before_connecting(tmp_path, monkeypatch, capsys):
    p = tmp_path / "f.json"
    p.write_text(json.dumps(_doc([5, 6, 7])))
    monkeypatch.setattr(ubr, "_connect", lambda: pytest.fail("must not connect"))
    rc = ubr.main(["--frozen-file", str(p), "--expect-fp", "66e57e02",
                   "--tip-hash", HASH, "--decision-ref", "d", "--rederivation-ref", "r"])
    assert rc == 2 and "fingerprint" in capsys.readouterr().err
