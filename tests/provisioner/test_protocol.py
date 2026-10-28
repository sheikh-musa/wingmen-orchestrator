"""provisioner wire protocol — one newline-JSON request per connection, one
newline-JSON response. Mirrors exec_broker's frozen §1-§3 shape with the
provisioner's own five request fields (gate_id is REQUIRED and non-null here:
both verbs are DDL — there is no gateless path, unlike exec_broker's DML).
Pure, no I/O.
"""
import json
import sys
from pathlib import Path

import pytest

ORCH_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ORCH_DIR / "scripts" / "lib"))

from provisioner import protocol  # noqa: E402


def _req(**overrides):
    base = dict(verb="provision", client_key="bariq", gate_id="78463",
                reason="client onboarding", caller="orch-console")
    base.update(overrides)
    return json.dumps(base)


def test_parse_happy_path():
    req = protocol.parse_request(_req())
    assert req.verb == "provision"
    assert req.client_key == "bariq"
    assert req.gate_id == "78463"
    assert req.caller == "orch-console"


def test_gate_id_is_required_non_null_and_non_empty():
    # both verbs are DDL — no gateless path exists on this broker
    for bad in (None, "", 78463):
        with pytest.raises(protocol.MalformedRequest):
            protocol.parse_request(_req(gate_id=bad))
    d = json.loads(_req())
    del d["gate_id"]
    with pytest.raises(protocol.MalformedRequest):
        protocol.parse_request(json.dumps(d))


def test_parse_rejects_shape_errors():
    for bad_line in ("not json", "[1,2]", _req(verb=""), _req(client_key=42),
                     _req(caller="")):
        with pytest.raises(protocol.MalformedRequest):
            protocol.parse_request(bad_line)
    d = json.loads(_req())
    del d["reason"]  # reason key must be present (may be empty string)
    with pytest.raises(protocol.MalformedRequest):
        protocol.parse_request(json.dumps(d))
    assert protocol.parse_request(_req(reason="")).reason == ""


def test_error_codes_are_exactly_six():
    codes = {c.value for c in protocol.ErrorCode}
    assert codes == {"ACL_DENIED", "BAD_VERB", "SPEC_INVALID", "GATE_INVALID",
                     "UPSTREAM_ERROR", "INTERNAL_ERROR"}


def test_encode_error_never_echoes_dsn_shaped_text():
    # leak discipline (exec_broker §3): a raw upstream connection error can
    # carry host/user/password — the wire response must carry only the code
    # and a sanitized message, never the raw text
    raw = "connection to postgresql://bariq_editor.dbref@host:5432 failed"
    out = json.loads(protocol.encode_error(protocol.ErrorCode.UPSTREAM_ERROR, raw))
    assert out["status"] == "error"
    assert out["error"]["code"] == "UPSTREAM_ERROR"
    assert "postgresql://" not in json.dumps(out)


def test_encode_ok_carries_the_artifact_identity():
    out = json.loads(protocol.encode_ok(client_key="bariq", verb="provision",
                                        bundle_sha256="a" * 64))
    assert out == {"status": "ok", "client_key": "bariq", "verb": "provision",
                   "bundle_sha256": "a" * 64}
    assert protocol.encode_ok(client_key="b", verb="provision", bundle_sha256="c").endswith(b"\n")
