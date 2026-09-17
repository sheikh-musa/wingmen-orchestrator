"""fc-v65 (op#20702 Stage E) — per-project governance registry on the console.

Locks:
  * the PURE validators in nervous_system/console/governance.py (operators must be
    positive USER ids, channels negative GROUP ids, reason mandatory, money ON needs
    the typed acknowledgement phrase, column allowlist);
  * GET /api/governance — server-side read via the vetted subprocess, 503 when
    unavailable (never an empty list), cached, ?fresh=1 bypasses the cache;
  * POST /api/governance-set — an ARMED action. Every FAILURE branch is proven to
    stop BEFORE the write subprocess is spawned: no bearer 401, wrong bearer 401,
    bearer unconfigured 503, bearer too short 503, R4 off 503, typed confirm
    missing/mismatched 400, reason missing 400, money ON without the ack 400,
    bad field / group-id operator 400; then the success path's exact argv;
  * CONSOLE_ARMED_BEARER min-length (>= 24): the unit state, the per-request 503
    (even when the caller presents the short key CORRECTLY), and the startup log
    line — with the key's VALUE never appearing in any log.
Hermetic: same stdlib-server idiom as test_app.py; subprocess + DB patched.
"""
import json
import logging
import threading
import time
from unittest.mock import MagicMock

import httpx
import pytest

from nervous_system.console import app as console_app
from nervous_system.console import auth, db, governance

KEY = "governance-armed-key-0123456789abcdef"     # 37 chars (>= 24)
SHORT = "short-key-12"                            # 12 chars (< 24)


# ============================================================================ pure validators
def test_operator_chat_id_must_be_positive_user_id():
    assert governance.is_user_chat_id("605271890") and governance.is_user_chat_id(1913044694)
    for bad in ("-5390372474", "0", "", None, "abc", "12.5", "1e3"):
        assert not governance.is_user_chat_id(bad), bad
    ok = governance.normalize_operators([{"name": "Shuq", "chat_id": 605271890, "internal": False}])
    assert ok == [{"name": "Shuq", "chat_id": "605271890", "internal": False}]
    with pytest.raises(governance.GovernanceError, match="group id can never authorize"):
        governance.normalize_operators([{"name": "group", "chat_id": "-5390372474", "internal": False}])
    with pytest.raises(governance.GovernanceError, match="name required"):
        governance.normalize_operators([{"name": "", "chat_id": "1", "internal": False}])
    with pytest.raises(governance.GovernanceError, match="duplicate"):
        governance.normalize_operators([{"name": "a", "chat_id": "1", "internal": True}, {"name": "b", "chat_id": "1", "internal": False}])
    with pytest.raises(governance.GovernanceError, match="internal must be"):
        governance.normalize_operators([{"name": "a", "chat_id": "1", "internal": "yes"}])
    with pytest.raises(governance.GovernanceError, match="must be an array"):
        governance.normalize_operators({"name": "a"})
    with pytest.raises(governance.GovernanceError, match="too many"):
        governance.normalize_operators([{"name": str(i), "chat_id": str(i + 1), "internal": True} for i in range(17)])
    # JSON-string form (what the CLI receives on argv)
    assert governance.normalize_operators('[{"name":"Wan","chat_id":"661212242","internal":false}]')[0]["name"] == "Wan"


def test_channels_must_be_negative_group_ids():
    assert governance.normalize_channels(["-5330147776", " -5390372474 "]) == ["-5330147776", "-5390372474"]
    assert governance.normalize_channels("-1, -2, -1") == ["-1", "-2"]        # comma form, deduped
    assert governance.normalize_channels("[]") == [] and governance.normalize_channels("") == []
    with pytest.raises(governance.GovernanceError, match="negative Telegram GROUP"):
        governance.normalize_channels(["605271890"])
    with pytest.raises(governance.GovernanceError, match="negative Telegram GROUP"):
        governance.normalize_channels(["x"])


def test_validate_write_rules():
    assert governance.validate_write("irsyad", "cai_enabled", "true", "op#1") == ("cai_enabled", True, "op#1")
    assert governance.validate_write("irsyad", "cai_enabled", False, " r ") == ("cai_enabled", False, "r")
    with pytest.raises(governance.GovernanceError, match="bad project"):
        governance.validate_write("Irsyad;drop", "cai_enabled", True, "r")
    with pytest.raises(governance.GovernanceError, match="unknown field"):
        governance.validate_write("irsyad", "residency_ack", True, "r")
    with pytest.raises(governance.GovernanceError, match="unknown field"):
        governance.validate_write("irsyad", "updated_by", "x", "r")
    with pytest.raises(governance.GovernanceError, match="true or false"):
        governance.validate_write("irsyad", "cai_enabled", "yes", "r")
    with pytest.raises(governance.GovernanceError, match="reason is required"):
        governance.validate_write("irsyad", "cai_enabled", True, "   ")
    with pytest.raises(governance.GovernanceError, match="reason too long"):
        governance.validate_write("irsyad", "cai_enabled", True, "x" * 501)
    # money ON: the ack phrase is REQUIRED; OFF never needs it
    with pytest.raises(governance.GovernanceError, match="ENABLE MONEY CLEARANCE"):
        governance.validate_write("irsyad", "money_clearance_enabled", True, "r")
    with pytest.raises(governance.GovernanceError, match="ENABLE MONEY CLEARANCE"):
        governance.validate_write("irsyad", "money_clearance_enabled", True, "r", money_ack="enable money clearance")
    assert governance.validate_write("irsyad", "money_clearance_enabled", True, "r", money_ack=governance.MONEY_ACK_PHRASE)[1] is True
    assert governance.validate_write("irsyad", "money_clearance_enabled", False, "r")[1] is False


def test_audit_changed_fields():
    assert governance.audit_changed_fields(None, {"cai_enabled": True}) == ["created"]
    b = {"cai_enabled": True, "money_clearance_enabled": False, "operators": [], "channels": []}
    a = dict(b, money_clearance_enabled=True)
    assert governance.audit_changed_fields(b, a) == ["money_clearance_enabled"]
    assert governance.audit_changed_fields(b, dict(b)) == []


def test_cli_exit_codes_and_no_dsn_leak(monkeypatch, capsys):
    monkeypatch.setattr(governance, "_dsn", lambda: "postgresql://user:SECRET@host/db")
    monkeypatch.setattr(governance, "apply_set", lambda *a, **k: (_ for _ in ()).throw(governance.NotFound("no row")))
    assert governance.main(["set", "--project", "ghost", "--field", "cai_enabled", "--value", "true",
                            "--updated-by", "t", "--reason", "r"]) == governance.EXIT_NOT_FOUND
    monkeypatch.setattr(governance, "apply_set", lambda *a, **k: (_ for _ in ()).throw(governance.GovernanceError("bad")))
    assert governance.main(["set", "--project", "x", "--field", "cai_enabled", "--value", "true",
                            "--updated-by", "t", "--reason", "r"]) == governance.EXIT_INVALID
    monkeypatch.setattr(governance, "apply_set", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("connection to postgresql://user:SECRET@host failed")))
    rc = governance.main(["set", "--project", "x", "--field", "cai_enabled", "--value", "true", "--updated-by", "t", "--reason", "r"])
    assert rc == 1
    out = capsys.readouterr()
    assert "SECRET" not in out.out and "SECRET" not in out.err and "<dsn>" in out.err   # DSN never printed, on either stream
    with pytest.raises(SystemExit):
        governance.main(["set", "--project", "x", "--field", "updated_by", "--value", "t", "--updated-by", "t", "--reason", "r"])


# ============================================================================ server fixture
@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSOLE_ALLOWED_IPS", "203.0.113.9")
    monkeypatch.setenv("CONSOLE_BREAKGLASS_TOKEN", "test-console-token")
    monkeypatch.setenv("CONSOLE_ARMED_BEARER", KEY)
    monkeypatch.setenv("CONSOLE_ACCESS_LOG", str(tmp_path / "console_access.log"))
    monkeypatch.setattr(db, "fetch_messages", lambda limit=50, thread=None, agent=None: [])
    monkeypatch.setattr(db, "fetch_lanes", lambda: [])
    monkeypatch.setattr(console_app, "_R4_ENABLED", True)
    console_app._governance_cache_clear()
    run = MagicMock(name="_governance_run")
    run.return_value = MagicMock(returncode=0, stdout=json.dumps(LIST), stderr="")
    monkeypatch.setattr(console_app, "_governance_run", run)
    srv = console_app.make_server(host="127.0.0.1", port=0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for _ in range(50):
        try:
            httpx.get(base + "/healthz", timeout=1.0)
            break
        except Exception:
            time.sleep(0.05)
    yield base, run, tmp_path
    srv.shutdown()
    console_app._governance_cache_clear()


LIST = {"projects": [
    {"project": "irsyad", "cai_enabled": False, "operators": [{"name": "Shuq", "chat_id": "605271890", "internal": False}],
     "channels": ["-5330147776"], "money_clearance_enabled": False, "residency_ack_on_file": False,
     "updated_by": "migration-063-seed", "reason": None, "updated_at": "2026-09-16T15:41:21+00:00"},
    {"project": "substrate", "cai_enabled": True, "operators": [], "channels": [], "money_clearance_enabled": False,
     "residency_ack_on_file": False, "updated_by": "migration-063-seed", "reason": None, "updated_at": "2026-09-16T15:41:21+00:00"},
], "audit": [{"id": 2, "project": "irsyad", "changed_by": "migration-063-seed", "reason": None,
              "changed_at": "2026-09-16T15:41:21+00:00", "changed": ["created"], "money_clearance_enabled": False}],
    "fields": list(governance.FIELDS), "money_ack_phrase": governance.MONEY_ACK_PHRASE}


def H():
    return {"Authorization": "Bearer test-console-token"}


def HA(key=KEY):
    return {**H(), "X-Armed-Bearer": key}


def GOOD(**over):
    b = {"project": "irsyad", "field": "cai_enabled", "value": True, "confirm": "irsyad", "reason": "op#20702 test"}
    b.update(over)
    return b


def _audit(tmp_path):
    p = tmp_path / "console_access.log"
    return p.read_text() if p.exists() else ""


# ============================================================================ GET /api/governance
def test_get_governance_requires_auth_and_returns_registry(server):
    base, run, _ = server
    assert httpx.get(base + "/api/governance", timeout=5).status_code == 401
    r = httpx.get(base + "/api/governance", headers=H(), timeout=5)
    assert r.status_code == 200
    d = r.json()
    assert [p["project"] for p in d["projects"]] == ["irsyad", "substrate"]
    assert d["projects"][0]["operators"][0]["chat_id"] == "605271890" and d["audit"][0]["changed"] == ["created"]
    assert run.call_args.args[0] == ["list"]
    # DSN / key never in the payload
    assert "postgres" not in r.text and KEY not in r.text


def test_get_governance_is_cached_and_fresh_bypasses(server):
    base, run, _ = server
    httpx.get(base + "/api/governance", headers=H(), timeout=5)
    httpx.get(base + "/api/governance", headers=H(), timeout=5)
    assert run.call_count == 1
    httpx.get(base + "/api/governance?fresh=1", headers=H(), timeout=5)
    assert run.call_count == 2


def test_get_governance_unavailable_is_503_not_empty(server):
    base, run, tmp_path = server
    run.return_value = MagicMock(returncode=1, stdout="", stderr='{"error":"DATABASE_URL not set"}')
    r = httpx.get(base + "/api/governance", headers=H(), timeout=5)
    assert r.status_code == 503 and r.json() == {"error": "governance registry unavailable"}
    run.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
    assert httpx.get(base + "/api/governance?fresh=1", headers=H(), timeout=5).status_code == 503
    assert "/api/governance\t503" in _audit(tmp_path)


# ============================================================================ POST failure branches (no spawn)
def _post(base, headers, body):
    return httpx.post(base + "/api/governance-set", headers=headers, json=body, timeout=5)


def test_set_without_bearer_401_no_spawn(server):
    base, run, tmp_path = server
    r = _post(base, H(), GOOD())
    assert r.status_code == 401 and r.json() == {"error": "armed bearer required"}
    run.assert_not_called()
    assert "/api/governance-set\t401-bearer" in _audit(tmp_path)


def test_set_wrong_bearer_401_no_spawn(server):
    base, run, _ = server
    r = _post(base, HA("wrong-key-but-long-enough-0123456789"), GOOD())
    assert r.status_code == 401 and r.json()["error"] == "armed bearer required"
    run.assert_not_called()


def test_set_bearer_unconfigured_503_no_spawn(server, monkeypatch):
    base, run, tmp_path = server
    monkeypatch.delenv("CONSOLE_ARMED_BEARER")
    r = _post(base, HA(), GOOD())
    assert r.status_code == 503 and r.json() == {"error": "armed bearer not configured"}
    run.assert_not_called()
    assert "503-bearer-unconfigured" in _audit(tmp_path)


def test_set_bearer_too_short_503_even_when_presented_correctly(server, monkeypatch, caplog):
    """The min-length rule (cc-quality LOW): a short key is a MISCONFIGURATION —
    refused with 503 even when the caller presents that exact short key."""
    base, run, tmp_path = server
    monkeypatch.setenv("CONSOLE_ARMED_BEARER", SHORT)
    with caplog.at_level(logging.ERROR, logger="wingmen.console.app"):
        r = _post(base, HA(SHORT), GOOD())
    assert r.status_code == 503 and r.json() == {"error": "armed bearer misconfigured (too short)"}
    run.assert_not_called()
    assert "503-bearer-too-short" in _audit(tmp_path)
    assert any("CONSOLE_ARMED_BEARER too short (<24)" in rec.getMessage() for rec in caplog.records)
    assert SHORT not in caplog.text and SHORT not in _audit(tmp_path)         # value never logged
    # the same rule guards the two older armed routes
    for path in ("/api/apply-armed", "/api/reset"):
        r = httpx.post(base + path, headers=HA(SHORT), json={"session": "x", "kind": "token", "confirm": "x", "body": "cai"}, timeout=5)
        assert r.status_code == 503 and "too short" in r.json()["error"], path


def test_set_r4_off_503_no_spawn(server, monkeypatch):
    base, run, tmp_path = server
    monkeypatch.setattr(console_app, "_R4_ENABLED", False)
    r = _post(base, HA(), GOOD())
    assert r.status_code == 503 and "CONSOLE_R4_ENABLED off" in r.json()["error"]
    run.assert_not_called()
    assert "/api/governance-set\t503" in _audit(tmp_path)


@pytest.mark.parametrize("confirm", ["", "Irsyad", "substrate", "irsyad2"])   # (trailing whitespace is stripped, as on apply-armed)
def test_set_confirm_missing_or_mismatch_400_no_spawn(server, confirm):
    base, run, _ = server
    r = _post(base, HA(), GOOD(confirm=confirm))
    assert r.status_code == 400 and r.json()["error"] == "type the exact project name to confirm"
    run.assert_not_called()


def test_set_reason_required_400_no_spawn(server):
    base, run, _ = server
    r = _post(base, HA(), GOOD(reason="  "))
    assert r.status_code == 400 and "reason is required" in r.json()["error"]
    run.assert_not_called()


def test_set_money_on_without_ack_400_no_spawn(server):
    base, run, _ = server
    r = _post(base, HA(), GOOD(field="money_clearance_enabled", value=True))
    assert r.status_code == 400 and "ENABLE MONEY CLEARANCE" in r.json()["error"]
    r = _post(base, HA(), GOOD(field="money_clearance_enabled", value=True, money_ack="yes"))
    assert r.status_code == 400
    run.assert_not_called()


@pytest.mark.parametrize("body", [
    GOOD(field="updated_by", value="x"),
    GOOD(field="residency_ack", value={}),
    GOOD(project="irsyad;--", confirm="irsyad;--"),
    GOOD(field="operators", value=[{"name": "grp", "chat_id": "-5390372474", "internal": False}]),
    GOOD(field="channels", value=["605271890"]),
    GOOD(value="maybe"),
    "not-an-object",
])
def test_set_bad_field_or_value_400_no_spawn(server, body):
    base, run, _ = server
    r = _post(base, HA(), body)
    assert r.status_code == 400
    run.assert_not_called()


# ============================================================================ POST success + rc mapping
def test_set_success_spawns_exact_argv_and_returns_audit_id(server):
    base, run, tmp_path = server
    run.return_value = MagicMock(returncode=0, stderr="",
                                 stdout=json.dumps({"ok": True, "project": "irsyad", "field": "cai_enabled",
                                                    "before": False, "after": True, "audit_id": 7}))
    r = _post(base, HA(), GOOD())
    assert r.status_code == 200 and r.json() == {"ok": True, "project": "irsyad", "field": "cai_enabled",
                                                 "value": True, "before": False, "audit_id": 7}
    argv = run.call_args.args[0]
    assert argv[:7] == ["set", "--project", "irsyad", "--field", "cai_enabled", "--value", "true"]
    i = argv.index("--updated-by")
    assert argv[i + 1].startswith("console:")                      # server-stamped, never from the body
    assert argv[argv.index("--reason") + 1] == "op#20702 test"
    assert "--money-ack" not in argv
    assert "governance-set:irsyad:cai_enabled:audit#7\t200" in _audit(tmp_path)


def test_set_money_on_with_ack_passes_ack_to_module(server):
    base, run, _ = server
    run.return_value = MagicMock(returncode=0, stderr="", stdout=json.dumps({"ok": True, "after": True, "audit_id": 8}))
    r = _post(base, HA(), GOOD(field="money_clearance_enabled", value=True, money_ack=governance.MONEY_ACK_PHRASE))
    assert r.status_code == 200 and r.json()["audit_id"] == 8
    argv = run.call_args.args[0]
    assert argv[argv.index("--money-ack") + 1] == governance.MONEY_ACK_PHRASE
    assert argv[argv.index("--field") + 1] == "money_clearance_enabled" and argv[argv.index("--value") + 1] == "true"


def test_set_operators_value_is_normalized_json_on_argv(server):
    base, run, _ = server
    run.return_value = MagicMock(returncode=0, stderr="", stdout=json.dumps({"ok": True, "audit_id": 9}))
    ops = [{"name": " Wan ", "chat_id": 661212242, "internal": False}]
    r = _post(base, HA(), GOOD(field="operators", value=ops))
    assert r.status_code == 200
    argv = run.call_args.args[0]
    assert json.loads(argv[argv.index("--value") + 1]) == [{"name": "Wan", "chat_id": "661212242", "internal": False}]


def test_set_rc_mapping_404_400_500_and_cache_cleared(server):
    base, run, _ = server
    httpx.get(base + "/api/governance", headers=H(), timeout=5)         # prime the cache
    run.return_value = MagicMock(returncode=2, stdout="", stderr='{"ok": false, "error": "no project_governance row for \'ghost\'"}')
    r = _post(base, HA(), GOOD(project="ghost", confirm="ghost"))
    assert r.status_code == 404 and "ghost" in r.json()["error"]
    run.return_value = MagicMock(returncode=3, stdout="", stderr='{"ok": false, "error": "bad"}')
    assert _post(base, HA(), GOOD()).status_code == 400
    run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
    r = _post(base, HA(), GOOD())
    assert r.status_code == 500 and r.json()["error"] == "governance write failed"
    # a write invalidates the read cache -> the next GET re-reads
    run.return_value = MagicMock(returncode=0, stdout=json.dumps(LIST), stderr="")
    n = run.call_count
    httpx.get(base + "/api/governance", headers=H(), timeout=5)
    assert run.call_count == n + 1 and run.call_args.args[0] == ["list"]


def test_set_handler_never_reached_without_bearer(server, monkeypatch):
    """The bearer gate sits IN FRONT of the handler (not inside it): with the
    handler replaced by a mock, a keyless POST still 401s and the mock never runs;
    with the key it is exactly what runs next."""
    base, run, _ = server
    from unittest.mock import MagicMock as _MM
    handler_cls = None
    # find the live handler class through a throwaway server instance's class attr
    srv_cls = console_app._make_handler(console_app._FeedLoop(fetch_since=None))
    handler_cls = srv_cls
    mock = _MM(name="_handle_governance_set")
    monkeypatch.setattr(handler_cls, "_handle_governance_set", mock)
    r = _post(base, H(), GOOD())
    assert r.status_code == 401
    mock.assert_not_called()
    run.assert_not_called()


# ============================================================================ min-length: unit + startup
def test_check_armed_bearer_too_short_state(monkeypatch):
    monkeypatch.setenv("CONSOLE_ARMED_BEARER", SHORT)
    assert auth.armed_bearer_state() == "too-short"
    assert auth.check_armed_bearer({"X-Armed-Bearer": SHORT}) == (False, "too-short")
    assert auth.check_armed_bearer({"Authorization": "Bearer " + SHORT}) == (False, "too-short")
    assert auth.check_armed_bearer({}) == (False, "too-short")                 # state wins over 'missing'
    monkeypatch.setenv("CONSOLE_ARMED_BEARER", "x" * 23)
    assert auth.check_armed_bearer({"X-Armed-Bearer": "x" * 23}) == (False, "too-short")
    monkeypatch.setenv("CONSOLE_ARMED_BEARER", "x" * 24)
    assert auth.armed_bearer_state() == "ok"
    assert auth.check_armed_bearer({"X-Armed-Bearer": "x" * 24}) == (True, "bearer")
    monkeypatch.delenv("CONSOLE_ARMED_BEARER")
    assert auth.armed_bearer_state() == "unconfigured"


def test_startup_check_logs_too_short_without_the_value(monkeypatch, caplog):
    monkeypatch.setenv("CONSOLE_ARMED_BEARER", SHORT)
    with caplog.at_level(logging.INFO, logger="wingmen.console.auth"):
        assert auth.log_armed_bearer_startup_state() == "too-short"
    assert any(r.levelno == logging.ERROR and "CONSOLE_ARMED_BEARER too short (<24)" in r.getMessage() for r in caplog.records)
    assert SHORT not in caplog.text
    caplog.clear()
    monkeypatch.setenv("CONSOLE_ARMED_BEARER", KEY)
    with caplog.at_level(logging.INFO, logger="wingmen.console.auth"):
        assert auth.log_armed_bearer_startup_state() == "ok"
    assert KEY not in caplog.text and "too short" not in caplog.text


def test_run_calls_startup_check(monkeypatch):
    """app.run() performs the check at server start (before make_server)."""
    called = []
    monkeypatch.setattr(auth, "log_armed_bearer_startup_state", lambda: called.append(1) or "ok")
    monkeypatch.setattr(console_app, "_resolve_host", lambda c: "127.0.0.1")

    class _Srv:
        def serve_forever(self):
            raise KeyboardInterrupt

        def shutdown(self):
            pass
    monkeypatch.setattr(console_app, "make_server", lambda h, p: _Srv())
    monkeypatch.setenv("CONSOLE_PORT", "0")
    console_app.run()
    assert called == [1]


def test_fleet_static_carries_the_governance_surface():
    import pathlib
    static = pathlib.Path(__file__).resolve().parents[2] / "nervous_system" / "console" / "static"
    html = (static / "fleet.html").read_text()
    js = (static / "fleet.js").read_text()
    assert 'id="govSec"' in html and 'id="govConfirm"' in html and 'id="govArmedKey"' in html
    assert 'fetch("/api/governance"' in js and 'fetch("/api/governance-set"' in js
    assert "ENABLE MONEY CLEARANCE" in js and "armedHeaders()" in js
    # both consoles share the same static files: nothing host-specific in the section
    assert "100.83.21.34" not in js
