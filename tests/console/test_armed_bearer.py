"""CAI-RESP-1434 (re-enable precondition) — a SECOND factor on the ARMED endpoints.

/api/apply-armed and /api/reset now require CONSOLE_ARMED_BEARER on top of the IP
allowlist: an ALLOWLISTED peer (e.g. a co-located process on the Mini, or this
loopback test client) with no key gets 401; an unset key gets 503 (fail-closed);
a wrong key gets 401; the right key proceeds to the endpoint's NEXT gate. The
check sits right after `_authed()` and before every other gate, and the real
handlers are provably NOT reached without it.

Also locks the fc-v64 payload shape (Musa op#20715/20716): every /api/fleet lane
AND coordinator carries `pool`, `model`, `model_src`, with the documented source
precedence (proc > boot string > registry) and no fp in the model field.
Same hermetic server idiom as test_app.py.
"""
import pathlib
import threading
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from nervous_system.console import app as console_app
from nervous_system.console import auth, db

_MUSA_FP = "68142948c003"
_MUSA2_FP = "e1dfa48eec85"
_SYED_FP = "582043088eae"
KEY = "armed-key-for-tests-0123456789"


def _lane(agent, base, sess, fp=None, task=None, reg=None, hb=12):
    return {"agent_id": agent, "base_agent_id": base, "status": "working",
            "current_task": task, "tmux_session": sess, "auth_fp": fp, "host": "Mini",
            "heartbeat_age_s": hb, "desired_state": "up", "lane": sess,
            "display_name": agent, "registry_model": reg, "activity": None,
            "activity_age_s": None}


def _coord(agent, short, sess, fp=None):
    return {"agent_id": agent, "short": short, "role_label": "r", "tmux_session": sess,
            "activity": None, "activity_age_s": None, "ctx_tokens": None, "ctx_age_s": None,
            "ctx_session_id": None, "ctx_current_session_id": None, "auth_fp": fp,
            "auth_account": None, "host": "Mini", "last_seen_s": 5}


@pytest.fixture
def server(monkeypatch, tmp_path):
    """Loopback IS allowlisted — every request passes check_access via 'ip' with NO
    Authorization header. That is exactly the caller the armed bearer must stop."""
    monkeypatch.setenv("CONSOLE_ALLOWED_IPS", "127.0.0.1")
    monkeypatch.delenv("CONSOLE_BREAKGLASS_TOKEN", raising=False)
    monkeypatch.delenv("CONSOLE_ARMED_BEARER", raising=False)
    monkeypatch.delenv("CONSOLE_R4_ENABLED", raising=False)
    monkeypatch.setenv("CONSOLE_ACCESS_LOG", str(tmp_path / "console_access.log"))
    # hermetic DB + tmux for /api/fleet
    monkeypatch.setattr(db, "fetch_lanes", lambda: [
        _lane("cc-hifz-1", "cc-hifz", "hifz", _MUSA_FP, "session-launch model=claude-sonnet-5 repo=hifz", "claude-opus-4-8"),
        _lane("cc-irsyad-2", "cc-irsyad", "irsyad-coord", _MUSA2_FP, "session-launch model=claude-opus-4-8 repo=x", "claude-sonnet-5"),
        _lane("cc-cosem-1", "cc-cosem", "cosem-tdu", _SYED_FP, "build", "claude-fable-5-1"),
        _lane("cc-x-1", "cc-x", "xlane", None, None, None),
    ])
    monkeypatch.setattr(db, "fetch_coordinators", lambda: [
        _coord("cai", "cai", "cai", _MUSA_FP),
        _coord("cc-orchestrator", "Hub", "orch", None),
    ])
    for name in ("fetch_deploys", "fetch_needs_you", "fetch_backlog", "fetch_pane_context",
                 "fetch_pool_usage", "fetch_queue", "fetch_inbox_backlog", "fetch_asks"):
        monkeypatch.setattr(db, name, lambda: [])
    monkeypatch.setattr(db, "fetch_messages", lambda limit=50, thread=None, agent=None: [])
    monkeypatch.setattr(console_app.panes, "live_sessions",
                        lambda: ["hifz", "irsyad-coord", "cosem-tdu", "xlane", "cai"])
    monkeypatch.setattr(console_app.panes, "capture",
                        lambda sess, live=None: ({"running": True, "state": "idle"}, ""))
    # process truth: hifz + cai are visible locally; irsyad-coord is off-box (gzb)
    monkeypatch.setattr(console_app.panes, "token_ground_truth", lambda include_remote=False: {
        "rows": [
            {"session": "hifz", "fp": _MUSA_FP, "model": "claude-opus-4-8", "host": "Mini"},
            {"session": "cai", "fp": _MUSA_FP, "model": "claude-opus-4-8", "host": "Mini"},
            {"session": "orch", "fp": None, "model": None, "host": "VPS"},
        ], "summary": {}})
    console_app._PROC_MODEL_CACHE["at"] = None   # the ~20s cache must not leak across tests
    srv = console_app.make_server(host="127.0.0.1", port=0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            httpx.get(base + "/healthz", timeout=1.0)
            break
        except Exception:
            time.sleep(0.05)
    yield base, srv
    srv.shutdown()


def _audit(tmp_path):
    p = tmp_path / "console_access.log"
    return p.read_text() if p.exists() else ""


# fc-v65: /api/governance-set (op#20702 Stage E) is the THIRD armed route — the same
# gate, the same failure branches, proven here by parametrization.
ARMED = ("/api/apply-armed", "/api/reset", "/api/governance-set")


# ---------------------------------------------------------------- the unit
def test_check_armed_bearer_states(monkeypatch):
    monkeypatch.delenv("CONSOLE_ARMED_BEARER", raising=False)
    assert auth.check_armed_bearer({"Authorization": "Bearer x"}) == (False, "unconfigured")
    monkeypatch.setenv("CONSOLE_ARMED_BEARER", KEY)
    assert auth.check_armed_bearer({}) == (False, "missing")
    assert auth.check_armed_bearer({"Authorization": "Basic abc"}) == (False, "missing")
    assert auth.check_armed_bearer({"Authorization": "Bearer nope"}) == (False, "mismatch")
    assert auth.check_armed_bearer({"X-Armed-Bearer": "nope"}) == (False, "mismatch")
    assert auth.check_armed_bearer({"Authorization": "Bearer " + KEY}) == (True, "bearer")
    assert auth.check_armed_bearer({"x-armed-bearer": KEY}) == (True, "bearer")          # case-insensitive
    assert auth.check_armed_bearer({"X-Armed-Bearer": KEY, "Authorization": "Bearer other"}) == (True, "bearer")
    assert auth.check_armed_bearer({"X-Armed-Bearer": "other", "Authorization": "Bearer " + KEY}) == (True, "bearer")
    # read at REQUEST time — a rotation is honoured without a restart
    monkeypatch.setenv("CONSOLE_ARMED_BEARER", "rotated-to-a-long-enough-key-01")
    assert auth.check_armed_bearer({"X-Armed-Bearer": KEY}) == (False, "mismatch")
    assert auth.check_armed_bearer({"X-Armed-Bearer": "rotated-to-a-long-enough-key-01"}) == (True, "bearer")
    # fc-v65: < 24 chars is a distinct fail-closed state, before any comparison
    monkeypatch.setenv("CONSOLE_ARMED_BEARER", "rotated")
    assert auth.check_armed_bearer({"X-Armed-Bearer": "rotated"}) == (False, "too-short")


# ---------------------------------------------------------------- (ii) env unset -> 503
@pytest.mark.parametrize("path", ARMED)
def test_unconfigured_key_503_fail_closed_even_from_allowlisted_ip(server, tmp_path, path):
    base, _ = server
    with patch.object(console_app.subprocess, "run") as run:
        r = httpx.post(base + path, json={"body": "cai", "confirm": "cai",
                                          "session": "hifz", "kind": "token"}, timeout=5)
    assert r.status_code == 503 and r.json() == {"error": "armed bearer not configured"}
    run.assert_not_called()
    assert f"{path}\t503-bearer-unconfigured" in _audit(tmp_path)


# ---------------------------------------------------------------- (i) allowlisted, no bearer -> 401
@pytest.mark.parametrize("path", ARMED)
def test_allowlisted_ip_without_bearer_401(server, monkeypatch, tmp_path, path):
    base, _ = server
    monkeypatch.setenv("CONSOLE_ARMED_BEARER", KEY)
    # sanity: this peer IS allowlisted (a read works with no header at all)
    assert httpx.get(base + "/api/messages", timeout=5).status_code == 200
    r = httpx.post(base + path, json={"body": "cai", "confirm": "cai", "session": "hifz", "kind": "token"}, timeout=5)
    assert r.status_code == 401 and r.json() == {"error": "armed bearer required"}
    assert f"{path}\t401-bearer" in _audit(tmp_path)


# ---------------------------------------------------------------- (iii) wrong bearer -> 401
@pytest.mark.parametrize("path", ARMED)
@pytest.mark.parametrize("hdr", [{"Authorization": "Bearer wrong"}, {"X-Armed-Bearer": "wrong"}])
def test_wrong_bearer_401(server, monkeypatch, path, hdr):
    base, _ = server
    monkeypatch.setenv("CONSOLE_ARMED_BEARER", KEY)
    r = httpx.post(base + path, headers=hdr, json={"body": "cai", "confirm": "cai", "session": "hifz", "kind": "token"}, timeout=5)
    assert r.status_code == 401 and r.json()["error"] == "armed bearer required"


# ---------------------------------------------------------------- (iv) right bearer -> NEXT gate
@pytest.mark.parametrize("hdr", [{"Authorization": "Bearer " + KEY}, {"X-Armed-Bearer": KEY}])
def test_correct_bearer_reaches_the_next_gate(server, monkeypatch, hdr):
    base, _ = server
    monkeypatch.setenv("CONSOLE_ARMED_BEARER", KEY)
    # apply-armed: the NEXT gate is the R4 feature flag (pinned OFF here — the live
    # Mini env may carry CONSOLE_R4_ENABLED=1) -> its 503, distinguishable from the
    # bearer 503 by its error text.
    monkeypatch.setattr(console_app, "_R4_ENABLED", False)
    r = httpx.post(base + "/api/apply-armed", headers=hdr, json={"session": "hifz", "kind": "token", "confirm": "hifz"}, timeout=5)
    assert r.status_code == 503 and "R4 armed apply is DISABLED" in r.json()["error"]
    # reset: the NEXT gate is body parsing -> its existing 400 on an empty/unknown body.
    with patch.object(console_app.subprocess, "run") as run:
        r = httpx.post(base + "/api/reset", headers=hdr, json={}, timeout=5)
    assert r.status_code == 400 and r.json()["error"] == "unknown body"
    run.assert_not_called()


def test_correct_bearer_governance_set_reaches_the_r4_gate(server, monkeypatch):
    """governance-set: the NEXT gate after the bearer is the R4 flag (503, distinct
    text); with R4 on, the next is the body/confirm parse (400) — never a write."""
    base, _ = server
    monkeypatch.setenv("CONSOLE_ARMED_BEARER", KEY)
    monkeypatch.setattr(console_app, "_R4_ENABLED", False)
    with patch.object(console_app, "_governance_run") as run:
        r = httpx.post(base + "/api/governance-set", headers={"X-Armed-Bearer": KEY},
                       json={"project": "irsyad", "field": "cai_enabled", "value": True, "confirm": "irsyad", "reason": "r"}, timeout=5)
        assert r.status_code == 503 and "CONSOLE_R4_ENABLED off" in r.json()["error"]
        monkeypatch.setattr(console_app, "_R4_ENABLED", True)
        r = httpx.post(base + "/api/governance-set", headers={"X-Armed-Bearer": KEY}, json={}, timeout=5)
        assert r.status_code == 400 and r.json()["error"] == "bad project/field"
        run.assert_not_called()


def test_too_short_bearer_503_on_every_armed_route(server, monkeypatch, tmp_path):
    """fc-v65 (cc-quality LOW): a key under 24 chars is refused as MISCONFIGURED on
    every armed route — even when presented correctly — and its value is never logged."""
    base, _ = server
    short = "only-twelve1"
    monkeypatch.setenv("CONSOLE_ARMED_BEARER", short)
    monkeypatch.setattr(console_app, "_R4_ENABLED", True)
    with patch.object(console_app.subprocess, "run") as run, patch.object(console_app, "_governance_run") as grun:
        for path in ARMED:
            r = httpx.post(base + path, headers={"X-Armed-Bearer": short},
                           json={"body": "cai", "confirm": "cai", "session": "hifz", "kind": "token",
                                 "project": "cai", "field": "cai_enabled", "value": True, "reason": "r"}, timeout=5)
            assert r.status_code == 503 and r.json() == {"error": "armed bearer misconfigured (too short)"}, path
            assert f"{path}\t503-bearer-too-short" in _audit(tmp_path)
    run.assert_not_called(); grun.assert_not_called()
    assert short not in _audit(tmp_path)
    # exactly 24 is accepted (boundary): reaches the next gate, not 503-too-short
    key24 = "k" * 24
    monkeypatch.setenv("CONSOLE_ARMED_BEARER", key24)
    monkeypatch.setattr(console_app, "_R4_ENABLED", False)
    r = httpx.post(base + "/api/apply-armed", headers={"X-Armed-Bearer": key24}, json={"session": "hifz", "kind": "token", "confirm": "hifz"}, timeout=5)
    assert r.status_code == 503 and "R4 armed apply is DISABLED" in r.json()["error"]


def test_correct_bearer_apply_armed_400_on_empty_body_when_r4_enabled(server, monkeypatch):
    base, _ = server
    monkeypatch.setenv("CONSOLE_ARMED_BEARER", KEY)
    monkeypatch.setattr(console_app, "_R4_ENABLED", True)
    r = httpx.post(base + "/api/apply-armed", headers={"X-Armed-Bearer": KEY}, json={}, timeout=5)
    assert r.status_code == 400 and r.json()["error"] == "bad session/kind"


# ---------------------------------------------------------------- (v) the handlers are NOT reached
def test_colocated_no_bearer_post_never_reaches_the_handlers(server, monkeypatch):
    """A co-located process on the Mini (loopback, allowlisted) POSTing with no key:
    the apply handler method and the reset subprocess are provably never invoked."""
    base, srv = server
    monkeypatch.setenv("CONSOLE_ARMED_BEARER", KEY)
    handler_cls = srv.RequestHandlerClass
    apply_mock = MagicMock(name="_handle_apply_armed")
    monkeypatch.setattr(handler_cls, "_handle_apply_armed", apply_mock)
    monkeypatch.setattr(console_app, "_R4_ENABLED", True)
    with patch.object(console_app.subprocess, "run") as run:
        r1 = httpx.post(base + "/api/apply-armed", json={"session": "hifz", "kind": "token", "confirm": "hifz"}, timeout=5)
        r2 = httpx.post(base + "/api/reset", json={"body": "cai", "confirm": "cai"}, timeout=5)
        r3 = httpx.post(base + "/api/reset", headers={"X-Armed-Bearer": "wrong"}, json={"body": "cai", "confirm": "cai"}, timeout=5)
    assert (r1.status_code, r2.status_code, r3.status_code) == (401, 401, 401)
    apply_mock.assert_not_called()
    run.assert_not_called()
    # and WITH the key the apply handler IS what runs next (the gate is in front of it, not instead of it)
    apply_mock.return_value = None
    def _ok(self):
        return self._json(200, {"ok": True, "reached": "handler"})
    monkeypatch.setattr(handler_cls, "_handle_apply_armed", _ok)
    r = httpx.post(base + "/api/apply-armed", headers={"X-Armed-Bearer": KEY}, json={"session": "hifz", "kind": "token", "confirm": "hifz"}, timeout=5)
    assert r.status_code == 200 and r.json()["reached"] == "handler"


def test_non_armed_posts_are_untouched_by_the_armed_gate(server, monkeypatch):
    """The second factor is scoped to the two ARMED routes: a dry-run POST from the
    same allowlisted peer still gets its own handler's answer with no key configured."""
    base, _ = server
    r = httpx.post(base + "/api/apply-dry-run", json={}, timeout=5)
    assert r.status_code != 503 or "armed bearer" not in r.text
    assert r.status_code in (400, 200)


# ---------------------------------------------------------------- fc-v64 payload shape (op#20715/20716)
def test_fleet_lanes_and_coordinators_carry_pool_model_model_src(server):
    base, _ = server
    d = httpx.get(base + "/api/fleet", timeout=10).json()
    lanes = {l["tmux_session"]: l for l in d["lanes"]}
    coords = {c["agent_id"]: c for c in d["coordinators"]}
    for row in list(lanes.values()) + list(coords.values()):
        assert set(("pool", "model", "model_src")) <= set(row), row.get("agent_id")
        assert row.get("model") != row.get("auth_fp") or row.get("model") is None
        assert "registry_model" not in row
    # (a) proc truth wins even when the boot string says otherwise
    assert (lanes["hifz"]["model"], lanes["hifz"]["model_src"]) == ("claude-opus-4-8", "proc")
    # (b) off-box lane: boot string beats the registry default
    assert (lanes["irsyad-coord"]["model"], lanes["irsyad-coord"]["model_src"]) == ("claude-opus-4-8", "boot")
    # (c) registry default as the last resort
    assert (lanes["cosem-tdu"]["model"], lanes["cosem-tdu"]["model_src"]) == ("claude-fable-5-1", "registry")
    # nothing known -> None, never invented / never "null"-ish
    assert (lanes["xlane"]["model"], lanes["xlane"]["model_src"]) == (None, None)
    assert lanes["hifz"]["pool"] == "Musa" and lanes["irsyad-coord"]["pool"] == "musa2" \
        and lanes["cosem-tdu"]["pool"] == "Syed" and lanes["xlane"]["pool"] == ""
    # coordinators: pool from auth_fp; model = proc truth only, else None (never invented)
    assert coords["cai"]["pool"] == "Musa" and (coords["cai"]["model"], coords["cai"]["model_src"]) == ("claude-opus-4-8", "proc")
    assert coords["cc-orchestrator"]["pool"] == "" and coords["cc-orchestrator"]["model"] is None \
        and coords["cc-orchestrator"]["model_src"] is None


def test_proc_model_read_is_cached_and_failure_safe(monkeypatch):
    calls = {"n": 0}
    def _tgt(include_remote=False):
        calls["n"] += 1
        return {"rows": [{"session": "hifz", "model": "claude-opus-4-8", "host": "Mini"},
                         {"session": "orch", "model": "claude-opus-4-8", "host": "VPS"}]}
    monkeypatch.setattr(console_app.panes, "token_ground_truth", _tgt)
    console_app._PROC_MODEL_CACHE["at"] = None
    a = console_app._proc_models(); b = console_app._proc_models()
    assert a == {"hifz": "claude-opus-4-8"} == b and calls["n"] == 1      # remote rows excluded; one shell-out
    assert console_app._proc_models().get("hifz") and calls["n"] == 1
    # a failure never raises into /api/fleet: last good map is returned
    console_app._PROC_MODEL_CACHE["at"] = None
    monkeypatch.setattr(console_app.panes, "token_ground_truth", lambda include_remote=False: (_ for _ in ()).throw(RuntimeError("ps died")))
    assert console_app._proc_models() == {"hifz": "claude-opus-4-8"}
    console_app._PROC_MODEL_CACHE["at"] = None


def test_lanes_query_selects_registry_model():
    sql, _ = db.build_lanes_query()
    assert "l.model AS registry_model" in sql and "SELECT desired_state, lane, model FROM fleet_lanes fl" in sql


def test_fleet_js_and_html_carry_the_fc_v64_surfaces():
    static = pathlib.Path(__file__).resolve().parents[2] / "nervous_system" / "console" / "static"
    js = (static / "fleet.js").read_text()
    html = (static / "fleet.html").read_text()
    assert "function poolRollup(lanes, coordinators, active)" in js
    assert "function mdlChip(" in js and "function routineSummary(" in js
    assert 'id="armedKey"' in html and 'type="password"' in html
    assert ".tok.mdl" in html and ".cchip.offpool" in html
