"""App-level integration: routing + auth gate + read-only API shape.

Spins up the real stdlib threaded server on an ephemeral port and drives it with
httpx. The DB layer is monkeypatched so tests are hermetic (no live Supabase).
"""
import pathlib
import threading
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from nervous_system.console import app as console_app
from nervous_system.console import db


@pytest.fixture
def server(monkeypatch, tmp_path):
    # The test client always connects from loopback (127.0.0.1), so the
    # allowlist here deliberately does NOT include it — that forces every
    # request through the breakglass path below, exercising the
    # Bearer-token-shaped tests exactly like the old CONSOLE_TOKEN did.
    # test_loopback_allowlisted_ip_needs_no_token (below) separately proves
    # the real IP-allowlist path end to end.
    monkeypatch.setenv("CONSOLE_ALLOWED_IPS", "203.0.113.9")
    monkeypatch.setenv("CONSOLE_BREAKGLASS_TOKEN", "test-console-token")
    # Isolate the access-audit log: without this it defaults to the real repo's
    # logs/console_access.log — which, since the LIVE console also runs from
    # this same checkout, means test runs silently write fake entries into
    # the production audit trail (found live 2026-07-04, mixed in with real
    # phone traffic).
    monkeypatch.setenv("CONSOLE_ACCESS_LOG", str(tmp_path / "console_access.log"))
    # Hermetic DB stubs.
    monkeypatch.setattr(
        db, "fetch_messages",
        lambda limit=50, thread=None, agent=None: [
            {"id": 1, "thread_id": "t1", "from_agent": "cc-cai",
             "to_agent": "cc-orchestrator", "message_type": "ruling",
             "subject": "hi", "body": "email a@b.com here", "priority": "P2",
             "requires_response": False, "created_at": "2026-06-17T00:00:00Z"},
        ],
    )
    monkeypatch.setattr(
        db, "fetch_lanes",
        lambda: [
            {"agent_id": "cc-ihsanos-1", "base_agent_id": "cc-ihsanos",
             "status": "working", "current_task": "build",
             "heartbeat_age_s": 12, "desired_state": "up",
             "display_name": "Ihsanos lane"},
        ],
    )
    srv = console_app.make_server(host="127.0.0.1", port=0)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    # Wait for liveness.
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            httpx.get(base + "/healthz", timeout=1.0)
            break
        except Exception:
            time.sleep(0.05)
    yield base
    srv.shutdown()


def H(tok="test-console-token"):
    return {"Authorization": f"Bearer {tok}"}


def test_healthz_open_no_auth(server):
    r = httpx.get(server + "/healthz", timeout=5)
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_api_requires_auth(server):
    r = httpx.get(server + "/api/messages", timeout=5)
    assert r.status_code == 401


def test_api_rejects_token_in_query(server):
    r = httpx.get(server + "/api/messages?token=test-console-token", timeout=5)
    assert r.status_code == 401


def test_api_messages_with_valid_token(server):
    r = httpx.get(server + "/api/messages", headers=H(), timeout=5)
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list)
    assert rows[0]["id"] == 1
    # PII redaction applied in-view.
    assert "a@b.com" not in rows[0]["body"]


def test_api_messages_wrong_token_401(server):
    r = httpx.get(server + "/api/messages", headers=H("nope"), timeout=5)
    assert r.status_code == 401


def test_api_lanes(server):
    r = httpx.get(server + "/api/lanes", headers=H(), timeout=5)
    assert r.status_code == 200
    rows = r.json()
    assert rows[0]["base_agent_id"] == "cc-ihsanos"
    assert rows[0]["heartbeat_age_s"] == 12


def test_no_service_key_in_any_response(server):
    for path in ("/healthz", "/api/messages", "/api/lanes"):
        r = httpx.get(server + path, headers=H(), timeout=5)
        assert "SUPABASE_SERVICE_KEY" not in r.text
        assert "service_role" not in r.text.lower()


def test_static_index_served(server):
    r = httpx.get(server + "/", timeout=5)
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_x_forwarded_for_spoof_does_not_grant_access(server):
    """Regression test for the exact bug this change removes: the test
    client's real peer IP (127.0.0.1) is never in the allowlist here, and a
    spoofed X-Forwarded-For claiming to BE an allowed IP must not matter —
    prove the server ignores that header entirely for auth."""
    r = httpx.get(
        server + "/api/messages",
        headers={"X-Forwarded-For": "203.0.113.9"},
        timeout=5,
    )
    assert r.status_code == 401


@pytest.fixture
def server_ip_allowed(monkeypatch, tmp_path):
    """The real IP-allowlist path, end to end: the test client connects from
    127.0.0.1, and 127.0.0.1 IS the allowlist — no token/header needed at all."""
    monkeypatch.setenv("CONSOLE_ALLOWED_IPS", "127.0.0.1")
    monkeypatch.delenv("CONSOLE_BREAKGLASS_TOKEN", raising=False)
    monkeypatch.setenv("CONSOLE_ACCESS_LOG", str(tmp_path / "console_access.log"))
    monkeypatch.setattr(
        db, "fetch_messages", lambda limit=50, thread=None, agent=None: []
    )
    srv = console_app.make_server(host="127.0.0.1", port=0)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            httpx.get(base + "/healthz", timeout=1.0)
            break
        except Exception:
            time.sleep(0.05)
    yield base
    srv.shutdown()


def test_loopback_allowlisted_ip_needs_no_token(server_ip_allowed):
    r = httpx.get(server_ip_allowed + "/api/messages", timeout=5)
    assert r.status_code == 200


def test_pane_endpoint_requires_auth(server):
    """Same gate as everything else under /api/* — no new exposure."""
    r = httpx.get(server + "/api/lanes/cosem-tdu/pane", timeout=5)
    assert r.status_code == 401


def test_pane_endpoint_404s_for_a_non_live_session(server):
    with patch("nervous_system.console.panes.live_sessions", return_value=["orch"]):
        r = httpx.get(server + "/api/lanes/not-a-real-session/pane", headers=H(), timeout=5)
    assert r.status_code == 404


def test_pane_endpoint_returns_captured_text_for_a_live_session(server):
    with patch("nervous_system.console.panes.live_sessions", return_value=["cosem-tdu"]), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        # Blank-separated so the soft-wrap reflow (op#3729) keeps them as two
        # distinct logical lines rather than rejoining the prose run into one.
        mock_run.return_value.stdout = "line1\n\nline2\n"
        r = httpx.get(server + "/api/lanes/cosem-tdu/pane", headers=H(), timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert body["session"] == "cosem-tdu"
    assert body["text"] == "line1\nline2"
    # read-only: capture-pane only, never send-keys or any other subcommand
    call_args = mock_run.call_args[0][0]
    assert call_args[1] == "capture-pane"


def test_pane_endpoint_rejects_crafted_session_name_without_shelling_out(server):
    """A session name isn't in live_sessions() -> 404 before any subprocess
    call, regardless of what characters it contains."""
    with patch("nervous_system.console.panes.live_sessions", return_value=["cosem-tdu"]), \
         patch("subprocess.run") as mock_run:
        r = httpx.get(server + "/api/lanes/%3B%20rm%20-rf%20%2F/pane", headers=H(), timeout=5)
    assert r.status_code == 404
    mock_run.assert_not_called()


def test_manifest_served_unauthenticated(server):
    """PWA regression (orch probe, 2026-07-03): the manifest and SW must be
    reachable pre-auth, or the browser can never install/register them even
    though the app otherwise renders fine."""
    r = httpx.get(server + "/manifest.json", timeout=5)
    assert r.status_code == 200
    assert r.headers.get("content-type") == "application/manifest+json"
    body = r.json()
    assert body["name"] == "Fleet Console"
    assert body["display"] == "standalone"


def test_service_worker_served_unauthenticated(server):
    r = httpx.get(server + "/sw.js", timeout=5)
    assert r.status_code == 200
    assert "javascript" in r.headers.get("content-type", "")
    assert r.headers.get("cache-control") == "no-cache"
    assert "skipWaiting" in r.text
    assert "clients.claim" in r.text


def test_icons_served_unauthenticated(server):
    r = httpx.get(server + "/static/icons/icon-192.png", timeout=5)
    assert r.status_code == 200
    assert r.headers.get("content-type") == "image/png"


# --- Bulk re-token: /api/switch-group + /api/switch-all -----------------------
# These re-token MANY live sessions, so the tests pin the safety contract: dry-run
# default-safe (fires NOTHING), singletons/remote/SELF excluded, unknown token
# fail-closed, already-on-target skipped, family scoping, and partial-failure
# surfaced (ok:false, itemised). The token resolve + fingerprint are stubbed so the
# tests are hermetic (no real key files) and token_ground_truth is mocked so lane
# discovery is deterministic (no live `ps`).
_SYED_FP = "582043088eae"   # the target account fp in these tests
_MUSA_FP = "68142948c003"   # a DIFFERENT account (lanes here start on this)


def _row(session, fp=_MUSA_FP, verified=True, metered=False, host="Mini"):
    return {"session": session, "account": "acct", "fp": fp, "metered": metered,
            "model": None, "host": host, "verified": verified,
            "expected": None, "expected_fp": None, "mismatch": False}


def _batch_patches(rows):
    """Patch the three seams the bulk switch reads: token-file resolve, its
    fingerprint (-> target account), and the live-lane ground truth. gazzabyte
    stays fail-closed upstream (LANE_TOKEN_FILES), so 'syed' resolves here."""
    return (
        patch.object(console_app, "_resolve_lane_token_file",
                     return_value=pathlib.Path("/fake/syed-oauth-token")),
        patch.object(console_app, "_fp_of_token_file", return_value=_SYED_FP),
        patch.object(console_app.panes, "token_ground_truth",
                     return_value={"rows": rows, "summary": {}}),
    )


def _by_lane(targets):
    return {t["lane"]: t for t in targets}


def test_switch_all_dry_run_plans_and_fires_nothing(server):
    """DEFAULT dry-run (dry_run key ABSENT): resolves the full plan with exclusions
    applied and shells out to NOTHING."""
    rows = [
        _row("cosem-tdu"), _row("irsyad"),
        _row("cai"), _row("fleet-health"), _row("nazim"),
        _row("cc-orchestrator", host="VPS", verified=False, fp=None),
        _row("ihsanos-1", fp=_SYED_FP),   # already on the syed target
    ]
    p1, p2, p3 = _batch_patches(rows)
    with p1, p2, p3, patch("subprocess.run") as mock_run:
        r = httpx.post(server + "/api/switch-all", headers=H(),
                       json={"token_name": "syed"}, timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["dry_run"] is True
    assert body["token_name"] == "syed"
    tg = _by_lane(body["targets"])
    assert tg["cosem-tdu"]["action"] == "switch"
    assert tg["irsyad"]["action"] == "switch"
    assert tg["ihsanos-1"]["action"] == "skipped:already"
    # DRY-RUN fires NOTHING.
    mock_run.assert_not_called()


def test_switch_all_excludes_singletons_and_remote(server):
    """cai, fleet-health (SELF), nazim, cc-orchestrator, and remote bodies are
    NEVER switch targets — each is itemised skipped:excluded, never 'switch'."""
    rows = [
        _row("cosem-tdu"),
        _row("cai"), _row("fleet-health"), _row("nazim"),
        _row("cc-orchestrator", host="VPS", verified=False, fp=None),
    ]
    p1, p2, p3 = _batch_patches(rows)
    with p1, p2, p3, patch("subprocess.run"):
        r = httpx.post(server + "/api/switch-all", headers=H(),
                       json={"token_name": "syed", "dry_run": True}, timeout=10)
    tg = _by_lane(r.json()["targets"])
    for excluded in ("cai", "fleet-health", "nazim", "cc-orchestrator"):
        assert tg[excluded]["action"] == "skipped:excluded", excluded
    assert tg["cosem-tdu"]["action"] == "switch"


def test_bulk_switch_excludes_held_lane_server_side(server):
    """A HELD lane (irsyad-import) is NEVER a switch target — the server-side
    _HELD_LANES guard, independent of the operator exclude[] list, in BOTH
    switch-all and switch-group (so a direct API call can't hit it either)."""
    rows = [_row("cosem-tdu"), _row("irsyad"), _row("irsyad-import")]
    # switch-ALL: held lane excluded WITHOUT any operator exclude
    p1, p2, p3 = _batch_patches(rows)
    with p1, p2, p3, patch("subprocess.run"):
        r = httpx.post(server + "/api/switch-all", headers=H(),
                       json={"token_name": "syed", "dry_run": True}, timeout=10)
    tg = _by_lane(r.json()["targets"])
    assert tg["irsyad-import"]["action"] == "skipped:excluded"
    assert "HELD" in tg["irsyad-import"]["detail"]
    # switch-GROUP on family 'irsyad': the held in-family lane is STILL excluded,
    # its non-held siblings still switch.
    p1, p2, p3 = _batch_patches(rows)
    with p1, p2, p3, patch("subprocess.run"):
        r = httpx.post(server + "/api/switch-group", headers=H(),
                       json={"family": "irsyad", "token_name": "syed", "dry_run": True}, timeout=10)
    tg = _by_lane(r.json()["targets"])
    assert tg["irsyad-import"]["action"] == "skipped:excluded"
    assert tg["irsyad"]["action"] == "switch"


def test_switch_all_operator_exclude_list(server):
    """A session named in the request `exclude` list is skipped:excluded."""
    rows = [_row("cosem-tdu"), _row("irsyad")]
    p1, p2, p3 = _batch_patches(rows)
    with p1, p2, p3, patch("subprocess.run"):
        r = httpx.post(server + "/api/switch-all", headers=H(),
                       json={"token_name": "syed", "exclude": ["irsyad"]}, timeout=10)
    tg = _by_lane(r.json()["targets"])
    assert tg["cosem-tdu"]["action"] == "switch"
    assert tg["irsyad"]["action"] == "skipped:excluded"


def test_switch_all_unknown_token_400(server):
    """Unknown/forbidden token_name fails the WHOLE request closed."""
    with patch("subprocess.run") as mock_run:
        r = httpx.post(server + "/api/switch-all", headers=H(),
                       json={"token_name": "gazzabyte"}, timeout=10)
    assert r.status_code == 400
    mock_run.assert_not_called()


def test_switch_all_already_on_target_skipped(server):
    """A lane already on the target account -> skipped:already (no restart)."""
    rows = [_row("cosem-tdu", fp=_SYED_FP)]
    p1, p2, p3 = _batch_patches(rows)
    with p1, p2, p3, patch("subprocess.run") as mock_run:
        r = httpx.post(server + "/api/switch-all", headers=H(),
                       json={"token_name": "syed", "dry_run": True}, timeout=10)
    tg = _by_lane(r.json()["targets"])
    assert tg["cosem-tdu"]["action"] == "skipped:already"
    mock_run.assert_not_called()


def test_switch_group_scopes_to_named_family_only(server):
    """switch-group targets ONLY the named family; other lanes are not listed."""
    rows = [_row("cosem-tdu"), _row("cosem-exams"), _row("irsyad"), _row("cai")]
    p1, p2, p3 = _batch_patches(rows)
    with p1, p2, p3, patch("subprocess.run"):
        r = httpx.post(server + "/api/switch-group", headers=H(),
                       json={"family": "cosem", "token_name": "syed"}, timeout=10)
    tg = _by_lane(r.json()["targets"])
    assert set(tg) == {"cosem-tdu", "cosem-exams"}
    assert tg["cosem-tdu"]["action"] == "switch"
    assert tg["cosem-exams"]["action"] == "switch"


def test_switch_all_partial_failure_is_ok_false_and_itemised(server, monkeypatch):
    """dry_run=false actually fires; if ANY lane fails, ok=false and the failing
    lane is itemised with its script tail (never swallowed)."""
    # Clear + neutralise the shared per-lane guard so these lanes fire. (The
    # cooldown sentinel is get(lane, 0.0); in a FRESH test process monotonic() is
    # still < the 30s cooldown, which would false-trip — a non-issue for the
    # long-lived real console. See report note.)
    console_app._SWITCH_LAST_RUN.clear()
    console_app._SWITCH_INFLIGHT.clear()
    monkeypatch.setattr(console_app, "_SWITCH_COOLDOWN_S", 0.0)
    rows = [_row("laneA"), _row("laneB")]
    ok_res = MagicMock(returncode=0, stdout="switched to syed\n", stderr="")
    bad_res = MagicMock(returncode=1, stdout="", stderr="BOOM relaunch failed\n")
    p1, p2, p3 = _batch_patches(rows)
    with p1, p2, p3, patch("subprocess.run", side_effect=[ok_res, bad_res]):
        r = httpx.post(server + "/api/switch-all", headers=H(),
                       json={"token_name": "syed", "dry_run": False}, timeout=10)
    assert r.status_code == 500
    body = r.json()
    assert body["ok"] is False and body["dry_run"] is False
    tg = _by_lane(body["targets"])
    assert tg["laneA"]["action"] == "switch"
    assert tg["laneB"]["action"] == "failed"
    assert "BOOM" in tg["laneB"]["detail"]
    assert body["summary"] == {"switched": 1, "skipped": 0, "failed": 1}


def test_switch_all_busy_lane_skipped_not_forced(server, monkeypatch):
    """A BUSY lane (script rc 5) is skipped:busy — NEVER --force'd, never a failure
    of the whole batch. Assert no --force in any argv."""
    console_app._SWITCH_LAST_RUN.clear()
    console_app._SWITCH_INFLIGHT.clear()
    monkeypatch.setattr(console_app, "_SWITCH_COOLDOWN_S", 0.0)
    rows = [_row("laneBusy"), _row("laneOk")]
    busy_res = MagicMock(returncode=5, stdout="", stderr="'laneBusy' is BUSY\n")
    ok_res = MagicMock(returncode=0, stdout="switched to syed\n", stderr="")
    p1, p2, p3 = _batch_patches(rows)
    with p1, p2, p3, patch("subprocess.run", side_effect=[busy_res, ok_res]) as mock_run:
        r = httpx.post(server + "/api/switch-all", headers=H(),
                       json={"token_name": "syed", "dry_run": False}, timeout=10)
    body = r.json()
    assert body["ok"] is True   # a busy skip is not a failure
    tg = _by_lane(body["targets"])
    assert tg["laneBusy"]["action"] == "skipped:busy"
    assert tg["laneOk"]["action"] == "switch"
    for call in mock_run.call_args_list:
        assert "--force" not in call[0][0]


def test_switch_requires_auth(server):
    """Same auth gate as /api/reset — no new unauthenticated surface."""
    r1 = httpx.post(server + "/api/switch-all", json={"token_name": "syed"}, timeout=5)
    r2 = httpx.post(server + "/api/switch-group",
                    json={"family": "cosem", "token_name": "syed"}, timeout=5)
    assert r1.status_code == 401 and r2.status_code == 401
