"""hc#95 exec-broker wire protocol (contract §1-3, ihsanos-irsyad main
reports/hc95-exec-broker-contract.md, squash 5053826e)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "lib"))

from exec_broker.protocol import (  # noqa: E402
    ErrorCode,
    MalformedRequest,
    encode_error,
    encode_ok,
    parse_request,
)


def test_parse_request_valid_dml_no_gate():
    line = (
        '{"project_ref":"oovskwogvefnznfowjky","guarded_sql":"select 1",'
        '"reason":"r","gate_id":null,"caller":"cc-irsyad-2","exec_ledger_id":"abc-123"}'
    )
    req = parse_request(line)
    assert req.gate_id is None
    assert req.project_ref == "oovskwogvefnznfowjky"
    assert req.guarded_sql == "select 1"
    assert req.caller == "cc-irsyad-2"
    assert req.exec_ledger_id == "abc-123"


def test_parse_request_valid_ddl_with_gate():
    line = (
        '{"project_ref":"goumlynecruxrlmzlntp","guarded_sql":"alter table t add column x int",'
        '"reason":"r","gate_id":"56485","caller":"cc-irsyad-2","exec_ledger_id":"abc-124"}'
    )
    assert parse_request(line).gate_id == "56485"


def test_parse_request_missing_gate_id_key_raises():
    # the key itself is absent -- distinct from gate_id: null (DML, no gate needed)
    line = '{"project_ref":"x","guarded_sql":"select 1","reason":"r","caller":"c","exec_ledger_id":"e"}'
    with pytest.raises(MalformedRequest):
        parse_request(line)


def test_parse_request_missing_caller_raises():
    line = '{"project_ref":"x","guarded_sql":"select 1","reason":"r","gate_id":null,"exec_ledger_id":"e"}'
    with pytest.raises(MalformedRequest):
        parse_request(line)


def test_parse_request_missing_project_ref_raises():
    line = '{"guarded_sql":"select 1","reason":"r","gate_id":null,"caller":"c","exec_ledger_id":"e"}'
    with pytest.raises(MalformedRequest):
        parse_request(line)


def test_parse_request_empty_project_ref_raises():
    line = '{"project_ref":"","guarded_sql":"select 1","reason":"r","gate_id":null,"caller":"c","exec_ledger_id":"e"}'
    with pytest.raises(MalformedRequest):
        parse_request(line)


def test_parse_request_empty_gate_id_string_raises():
    # gate_id must be null or a non-empty string -- "" is neither
    line = '{"project_ref":"x","guarded_sql":"select 1","reason":"r","gate_id":"","caller":"c","exec_ledger_id":"e"}'
    with pytest.raises(MalformedRequest):
        parse_request(line)


def test_parse_request_bad_json_raises():
    with pytest.raises(MalformedRequest):
        parse_request("not json")


def test_parse_request_non_object_json_raises():
    with pytest.raises(MalformedRequest):
        parse_request("[1, 2, 3]")


def test_encode_ok_roundtrip():
    payload = json.loads(encode_ok([{"a": 1}]).decode("utf-8"))
    assert payload == {"status": "ok", "rows": [{"a": 1}]}


def test_encode_ok_ends_with_newline():
    assert encode_ok([]).endswith(b"\n")


def test_encode_error_roundtrip():
    payload = json.loads(encode_error(ErrorCode.BAD_PROJECT_REF, "nope").decode("utf-8"))
    assert payload == {"status": "error", "error": {"code": "BAD_PROJECT_REF", "message": "nope"}}


def test_encode_error_ends_with_newline():
    assert encode_error(ErrorCode.INTERNAL_ERROR, "x").endswith(b"\n")


def test_parse_request_migration_and_pr_absent_default_to_none():
    # Part A (rollout step (c), bus #79024/#79465): migration/pr are OPTIONAL
    # keys -- absent means None, same as an explicit null, and must NOT raise
    # MalformedRequest. This is the deploy-safety property the whole optional-
    # key design rests on: an old 6-key caller must keep working unchanged.
    line = (
        '{"project_ref":"oovskwogvefnznfowjky","guarded_sql":"select 1",'
        '"reason":"r","gate_id":null,"caller":"cc-irsyad-2","exec_ledger_id":"abc-123"}'
    )
    req = parse_request(line)
    assert req.migration is None
    assert req.pr is None


def test_parse_request_migration_and_pr_explicit_null():
    line = (
        '{"project_ref":"oovskwogvefnznfowjky","guarded_sql":"select 1",'
        '"reason":"r","gate_id":null,"caller":"cc-irsyad-2","exec_ledger_id":"abc-123",'
        '"migration":null,"pr":null}'
    )
    req = parse_request(line)
    assert req.migration is None
    assert req.pr is None


def test_parse_request_migration_and_pr_present():
    line = (
        '{"project_ref":"goumlynecruxrlmzlntp","guarded_sql":"alter table t add column x int",'
        '"reason":"r","gate_id":"56485","caller":"cc-irsyad-2","exec_ledger_id":"abc-124",'
        '"migration":"481","pr":"1072"}'
    )
    req = parse_request(line)
    assert req.migration == "481"
    assert req.pr == "1072"


def test_parse_request_migration_empty_string_raises():
    # mirrors gate_id's rule: present but "" is neither null nor non-empty
    line = (
        '{"project_ref":"x","guarded_sql":"select 1","reason":"r","gate_id":null,'
        '"caller":"c","exec_ledger_id":"e","migration":""}'
    )
    with pytest.raises(MalformedRequest):
        parse_request(line)


def test_parse_request_pr_empty_string_raises():
    line = (
        '{"project_ref":"x","guarded_sql":"select 1","reason":"r","gate_id":null,'
        '"caller":"c","exec_ledger_id":"e","pr":""}'
    )
    with pytest.raises(MalformedRequest):
        parse_request(line)


def test_parse_request_migration_non_string_raises():
    line = (
        '{"project_ref":"x","guarded_sql":"select 1","reason":"r","gate_id":null,'
        '"caller":"c","exec_ledger_id":"e","migration":481}'
    )
    with pytest.raises(MalformedRequest):
        parse_request(line)


def test_parse_request_pr_non_string_raises():
    line = (
        '{"project_ref":"x","guarded_sql":"select 1","reason":"r","gate_id":null,'
        '"caller":"c","exec_ledger_id":"e","pr":1072}'
    )
    with pytest.raises(MalformedRequest):
        parse_request(line)


def test_read_only_override_error_code_exists_and_encodes():
    # Additive seventh §5 code (orch-console GO #78974, assignment bus #79024):
    # while the bounded ref override is live, a non-plain-read statement is
    # refused with this code. Response shape unchanged; callers must tolerate
    # an unknown code string.
    assert ErrorCode.READ_ONLY_OVERRIDE.value == "READ_ONLY_OVERRIDE"
    payload = json.loads(encode_error(ErrorCode.READ_ONLY_OVERRIDE, "broker is read-only").decode("utf-8"))
    assert payload == {"status": "error", "error": {"code": "READ_ONLY_OVERRIDE", "message": "broker is read-only"}}


def test_gate_mismatch_error_code_exists_and_encodes():
    # Additive eighth §5 code (orch-console ruling bus #79477, backing the
    # byte-binding check for lexically hazardous SQL): the executed bytes'
    # sha12 does not equal the sha12 the gate cites. Additive exactly like
    # READ_ONLY_OVERRIDE -- response shape unchanged, and a caller that does
    # not know the code yet must still parse the response.
    assert ErrorCode.GATE_MISMATCH.value == "GATE_MISMATCH"
    payload = json.loads(encode_error(ErrorCode.GATE_MISMATCH, "bytes differ").decode("utf-8"))
    assert payload == {"status": "error", "error": {"code": "GATE_MISMATCH", "message": "bytes differ"}}
