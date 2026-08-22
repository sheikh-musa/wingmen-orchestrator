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


# --- GAP-B: /api/set-group-pointer (pin/unpin a family, NO relaunch) ----------
# Mirrors the /api/set-pointer contract: reversible pointer write, gazzabyte/
# forbidden fail-closed, family charset-validated, attributable audit on pin+unpin.

@pytest.fixture
def repo_root(monkeypatch, tmp_path):
    """Redirect the console's pointer-file root to a temp dir so pointer writes in
    these tests NEVER touch the real checkout."""
    root = tmp_path / "orch"
    root.mkdir()
    monkeypatch.setattr(console_app, "_REPO_ROOT", root)
    return root


def _fake_token(tmp_path, name="syed"):
    """A readable fake key file + a patch making the registry resolve `name` to it
    (and reject anything else / forbidden)."""
    kf = tmp_path / (name + "-oauth-token")
    kf.write_text("SECRET-" + name + "\n")

    def _resolve(n):
        return kf if n == name else None

    return kf, patch.object(console_app, "_resolve_registry_token", side_effect=_resolve)


def test_set_group_pointer_pins_family(server, repo_root, tmp_path):
    kf, presolve = _fake_token(tmp_path, "syed")
    with presolve:
        r = httpx.post(server + "/api/set-group-pointer", headers=H(),
                       json={"family": "irsyad", "token_name": "syed"}, timeout=10)
    assert r.status_code == 200 and r.json()["ok"] is True
    pfile = repo_root / ".group_default_token.irsyad"
    assert pfile.exists()
    assert pfile.read_text().strip() == str(kf)


def test_set_group_pointer_clear_unpins(server, repo_root, tmp_path):
    pfile = repo_root / ".group_default_token.irsyad"
    pfile.write_text(str(tmp_path / "syed-oauth-token") + "\n")
    r = httpx.post(server + "/api/set-group-pointer", headers=H(),
                   json={"family": "irsyad", "clear": True}, timeout=10)
    assert r.status_code == 200 and r.json()["cleared"] is True
    assert not pfile.exists()


def test_set_group_pointer_clear_is_idempotent(server, repo_root):
    """Clearing an already-absent pin is a clean 200 (rm-reversible, no error)."""
    r = httpx.post(server + "/api/set-group-pointer", headers=H(),
                   json={"family": "nope", "clear": True}, timeout=10)
    assert r.status_code == 200 and r.json()["ok"] is True


def test_set_group_pointer_unknown_token_400_fail_closed(server, repo_root, tmp_path):
    """gazzabyte / any unknown token -> 400, NO pointer written (fail-closed)."""
    _kf, presolve = _fake_token(tmp_path, "syed")   # only 'syed' resolves
    with presolve:
        r = httpx.post(server + "/api/set-group-pointer", headers=H(),
                       json={"family": "irsyad", "token_name": "gazzabyte"}, timeout=10)
    assert r.status_code == 400
    assert not (repo_root / ".group_default_token.irsyad").exists()


def test_set_group_pointer_bad_family_400(server, repo_root):
    """A family with a path separator (or bad charset) is rejected before any write."""
    r = httpx.post(server + "/api/set-group-pointer", headers=H(),
                   json={"family": "../evil", "token_name": "syed"}, timeout=10)
    assert r.status_code == 400


def test_set_group_pointer_requires_auth(server):
    r = httpx.post(server + "/api/set-group-pointer",
                   json={"family": "irsyad", "token_name": "syed"}, timeout=5)
    assert r.status_code == 401


def test_set_group_pointer_audits_pin_and_unpin(server, repo_root, tmp_path):
    """Both a pin AND an unpin leave an attributable audit row (mirrors set-pointer)."""
    kf, presolve = _fake_token(tmp_path, "syed")
    with patch.object(console_app.auth, "audit") as maudit:
        with presolve:
            httpx.post(server + "/api/set-group-pointer", headers=H(),
                       json={"family": "irsyad", "token_name": "syed"}, timeout=10)
        httpx.post(server + "/api/set-group-pointer", headers=H(),
                   json={"family": "irsyad", "clear": True}, timeout=10)
    endpoints = [c.args[1] for c in maudit.call_args_list]
    assert any(e.startswith("/api/set-group-pointer:irsyad:syed") for e in endpoints)
    assert any(e.startswith("/api/set-group-pointer:irsyad:clear") for e in endpoints)


# --- GAP-B: /api/switch-group PERSISTS the group pointer ----------------------

def test_switch_group_persists_group_pointer(server, repo_root, monkeypatch):
    """A REAL (non-dry) group switch ALSO writes .group_default_token.<family> so
    the family stays pinned across relaunches (the fix for 'ran musa2 but pinned to
    a musa pointer')."""
    console_app._SWITCH_LAST_RUN.clear()
    console_app._SWITCH_INFLIGHT.clear()
    monkeypatch.setattr(console_app, "_SWITCH_COOLDOWN_S", 0.0)
    rows = [_row("irsyad-coord"), _row("irsyad-prog1")]
    ok_res = MagicMock(returncode=0, stdout="switched to syed\n", stderr="")
    p1, p2, p3 = _batch_patches(rows)
    with p1, p2, p3, patch("subprocess.run", return_value=ok_res):
        r = httpx.post(server + "/api/switch-group", headers=H(),
                       json={"family": "irsyad", "token_name": "syed", "dry_run": False},
                       timeout=10)
    assert r.status_code == 200
    assert r.json()["group_pinned"] == "irsyad"
    pfile = repo_root / ".group_default_token.irsyad"
    assert pfile.exists()
    assert pfile.read_text().strip() == "/fake/syed-oauth-token"


def test_switch_group_dry_run_does_not_persist(server, repo_root):
    """A DRY-RUN group switch NEVER writes the pointer (nothing is real yet)."""
    rows = [_row("irsyad-coord")]
    p1, p2, p3 = _batch_patches(rows)
    with p1, p2, p3, patch("subprocess.run"):
        r = httpx.post(server + "/api/switch-group", headers=H(),
                       json={"family": "irsyad", "token_name": "syed"}, timeout=10)
    assert r.json()["group_pinned"] is None
    assert not (repo_root / ".group_default_token.irsyad").exists()


def test_switch_all_does_not_persist_group_pointer(server, repo_root, monkeypatch):
    """switch-ALL is fleet-wide, not a family pin — it must NOT write a group file."""
    console_app._SWITCH_LAST_RUN.clear()
    console_app._SWITCH_INFLIGHT.clear()
    monkeypatch.setattr(console_app, "_SWITCH_COOLDOWN_S", 0.0)
    rows = [_row("irsyad-coord")]
    ok_res = MagicMock(returncode=0, stdout="switched\n", stderr="")
    p1, p2, p3 = _batch_patches(rows)
    with p1, p2, p3, patch("subprocess.run", return_value=ok_res):
        r = httpx.post(server + "/api/switch-all", headers=H(),
                       json={"token_name": "syed", "dry_run": False}, timeout=10)
    assert r.json().get("group_pinned") is None
    assert not list(repo_root.glob(".group_default_token.*"))


# --- GAP-B item 7: BREAK_GLASS only on a genuine gate-bypass (--force) ---------

def _switch_env_of(mock_run):
    """The env dict passed to switch_lane_token.sh in the (last) subprocess call."""
    return mock_run.call_args.kwargs["env"]


def _switch_argv_of(mock_run):
    return mock_run.call_args.args[0]


def test_switch_token_normal_is_not_break_glass(server, monkeypatch, tmp_path):
    """A normal single switch stamps BREAK_GLASS=0 (routine, P3 audit) and passes
    NO --force — it is not a gate-bypass (op#12486 f/u)."""
    console_app._SWITCH_LAST_RUN.clear()
    console_app._SWITCH_INFLIGHT.clear()
    monkeypatch.setattr(console_app, "_SWITCH_COOLDOWN_S", 0.0)
    kf = tmp_path / "syed-oauth-token"
    kf.write_text("SECRET\n")
    ok_res = MagicMock(returncode=0, stdout="switched\n", stderr="")
    with patch.object(console_app, "_resolve_lane_token_file", return_value=kf), \
         patch.object(console_app, "_lane_token_files", return_value={"syed": kf}), \
         patch("subprocess.run", return_value=ok_res) as mock_run:
        r = httpx.post(server + "/api/switch-token", headers=H(),
                       json={"lane": "irsyad-coord", "token_name": "syed"}, timeout=10)
    assert r.status_code == 200
    assert _switch_env_of(mock_run)["BREAK_GLASS"] == "0"
    assert "--force" not in _switch_argv_of(mock_run)


def test_switch_token_force_is_break_glass(server, monkeypatch, tmp_path):
    """{force:true} IS a genuine gate-bypass -> BREAK_GLASS=1 AND --force passed."""
    console_app._SWITCH_LAST_RUN.clear()
    console_app._SWITCH_INFLIGHT.clear()
    monkeypatch.setattr(console_app, "_SWITCH_COOLDOWN_S", 0.0)
    kf = tmp_path / "syed-oauth-token"
    kf.write_text("SECRET\n")
    ok_res = MagicMock(returncode=0, stdout="switched\n", stderr="")
    with patch.object(console_app, "_resolve_lane_token_file", return_value=kf), \
         patch.object(console_app, "_lane_token_files", return_value={"syed": kf}), \
         patch("subprocess.run", return_value=ok_res) as mock_run:
        r = httpx.post(server + "/api/switch-token", headers=H(),
                       json={"lane": "irsyad-coord", "token_name": "syed", "force": True},
                       timeout=10)
    assert r.status_code == 200
    assert _switch_env_of(mock_run)["BREAK_GLASS"] == "1"
    assert "--force" in _switch_argv_of(mock_run)


def test_bulk_switch_is_never_break_glass(server, monkeypatch):
    """The bulk path never forces, so it is never a break-glass event -> the env
    handed to switch_lane_token.sh carries BREAK_GLASS=0."""
    console_app._SWITCH_LAST_RUN.clear()
    console_app._SWITCH_INFLIGHT.clear()
    monkeypatch.setattr(console_app, "_SWITCH_COOLDOWN_S", 0.0)
    rows = [_row("cosem-tdu")]
    ok_res = MagicMock(returncode=0, stdout="switched\n", stderr="")
    p1, p2, p3 = _batch_patches(rows)
    with p1, p2, p3, patch("subprocess.run", return_value=ok_res) as mock_run:
        httpx.post(server + "/api/switch-all", headers=H(),
                   json={"token_name": "syed", "dry_run": False}, timeout=10)
    assert _switch_env_of(mock_run)["BREAK_GLASS"] == "0"


# --- GAP-B item 6 (server side): token_group surfaced for a group-pinned lane --

def test_enrich_marks_group_pinned_lane(monkeypatch, tmp_path):
    """_enrich_token_pointers sets token_group=<family> for a fleet-default lane
    whose family has a .group_default_token.<family> file (drives the lanes.js
    'Token · <family> group' label)."""
    root = tmp_path / "orch"
    root.mkdir()
    (root / ".group_default_token.irsyad").write_text("/fake/syed-oauth-token\n")
    monkeypatch.setattr(console_app, "_REPO_ROOT", root)
    payload = {"rows": [{"session": "irsyad-coord"}, {"session": "cosem-tdu"}]}
    out = console_app._enrich_token_pointers(payload)
    by = {r["session"]: r for r in out["rows"]}
    assert by["irsyad-coord"]["token_group"] == "irsyad"     # pinned family
    assert by["cosem-tdu"]["token_group"] is None            # unpinned family


# --- /api/set-pointer: fleet-default clobber guard (operator tripped it TWICE) --
# A per-LANE token pick on a WORKER lane fell through to the SHARED fleet default
# .lane_default_token; the write moved ALL other worker lanes off-account. These
# tests pin: LAYER-1 interim guard (refuse the worker-lane fleet-default write) and
# LAYER-2 group-aware routing (a worker pick lands in .group_default_token.<family>,
# the fleet default is UNTOUCHED). Singleton pointers (nazim/hub) stay unchanged.

def test_set_pointer_worker_lane_does_not_clobber_fleet_default(server, repo_root, tmp_path):
    """OPERATOR REPRO: a per-lane token pick on worker lane irsyad-import must NOT
    write the SHARED fleet default .lane_default_token. RED before the fix (the pick
    wrote it -> all lanes off-account); GREEN after (fleet default UNTOUCHED)."""
    _kf, presolve = _fake_token(tmp_path, "syed")
    fleet = repo_root / ".lane_default_token"
    with presolve:
        r = httpx.post(server + "/api/set-pointer", headers=H(),
                       json={"kind": "token", "session": "irsyad-import", "value": "syed"},
                       timeout=10)
    # The harmful all-lanes clobber never happens (the crux of the operator bug).
    assert not fleet.exists(), "per-lane pick must NEVER write the fleet default"


def test_set_pointer_worker_lane_clear_does_not_touch_fleet_default(server, repo_root):
    """A per-lane token CLEAR on a worker lane must not rm/rewrite the fleet default
    either — clearing the all-lanes default from one lane is the same footgun."""
    fleet = repo_root / ".lane_default_token"
    fleet.write_text("/fake/musa-oauth-token\n")   # a real fleet default in place
    r = httpx.post(server + "/api/set-pointer", headers=H(),
                   json={"kind": "token", "session": "irsyad-import", "clear": True},
                   timeout=10)
    # The pre-existing fleet default is left exactly as it was.
    assert fleet.exists() and fleet.read_text().strip() == "/fake/musa-oauth-token"


def test_set_pointer_singleton_token_still_writes_its_own_pointer(server, repo_root, tmp_path):
    """BACK-COMPAT: the guard/routing is worker-lane only — a singleton with its OWN
    pointer (nazim -> .nazim_default_token) still writes it, never the fleet default."""
    kf, presolve = _fake_token(tmp_path, "syed")
    with presolve:
        r = httpx.post(server + "/api/set-pointer", headers=H(),
                       json={"kind": "token", "session": "nazim", "value": "syed"},
                       timeout=10)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert (repo_root / ".nazim_default_token").read_text().strip() == str(kf)
    assert not (repo_root / ".lane_default_token").exists()


def test_set_pointer_worker_lane_routes_to_group(server, repo_root, tmp_path):
    """LAYER-2 group-aware routing: a per-lane token pick on a WORKER lane lands in
    its per-GROUP pointer .group_default_token.<family> — NOT the fleet default. The
    pick SUCCEEDS (no over-block), the group file gets the token, the fleet default
    stays absent, and the response reports where it landed. (Supersedes the LAYER-1
    refusal — routing is the durable fix; the guard remains as defense-in-depth.)"""
    kf, presolve = _fake_token(tmp_path, "syed")
    with presolve:
        r = httpx.post(server + "/api/set-pointer", headers=H(),
                       json={"kind": "token", "session": "irsyad-coord", "value": "syed"},
                       timeout=10)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["pointer"] == ".group_default_token.irsyad"
    grp = repo_root / ".group_default_token.irsyad"
    assert grp.exists() and grp.read_text().strip() == str(kf)
    assert not (repo_root / ".lane_default_token").exists()   # fleet default UNTOUCHED


def test_set_pointer_worker_lane_clear_targets_group_not_fleet(server, repo_root, tmp_path):
    """A per-lane CLEAR on a worker lane clears ONLY its group pointer; a pre-existing
    fleet default is left exactly as it was."""
    fleet = repo_root / ".lane_default_token"
    fleet.write_text("/fake/musa-oauth-token\n")
    grp = repo_root / ".group_default_token.irsyad"
    grp.write_text(str(tmp_path / "syed-oauth-token") + "\n")
    r = httpx.post(server + "/api/set-pointer", headers=H(),
                   json={"kind": "token", "session": "irsyad-coord", "clear": True},
                   timeout=10)
    assert r.status_code == 200 and r.json()["cleared"] is True
    assert not grp.exists()                                    # group pin removed
    assert fleet.read_text().strip() == "/fake/musa-oauth-token"   # fleet UNTOUCHED


def test_set_pointer_held_lane_refused(server, repo_root, tmp_path):
    """A HELD lane's token default must not be settable from a single-lane click
    (defense-in-depth beyond the bulk switch's _HELD_LANES guard). irsyad-import is
    the held lane in _HELD_LANES."""
    _kf, presolve = _fake_token(tmp_path, "syed")
    with patch.object(console_app, "_HELD_LANES", {"irsyad-import"}), presolve:
        r = httpx.post(server + "/api/set-pointer", headers=H(),
                       json={"kind": "token", "session": "irsyad-import", "value": "syed"},
                       timeout=10)
    assert r.status_code == 400 and "held lane" in r.json()["error"]
    assert not (repo_root / ".group_default_token.irsyad").exists()
    assert not (repo_root / ".lane_default_token").exists()


def test_enrich_preselects_group_pinned_token(monkeypatch, tmp_path):
    """display==boot: a group-pinned worker lane's preselected token reflects the
    GROUP pin (what it will boot on), so a per-lane pick shows as selected."""
    root = tmp_path / "orch"
    root.mkdir()
    kf = root / "syed-oauth-token"
    kf.write_text("SECRET-syed\n")
    (root / ".group_default_token.irsyad").write_text(str(kf) + "\n")
    monkeypatch.setattr(console_app, "_REPO_ROOT", root)
    fp = console_app._fp_of_token_file(str(kf))
    monkeypatch.setattr(console_app, "_token_name_for_fp",
                        lambda f: "syed" if f == fp else None)
    payload = {"rows": [{"session": "irsyad-coord"}]}
    out = console_app._enrich_token_pointers(payload)
    r = out["rows"][0]
    assert r["token_group"] == "irsyad"
    assert r["token_pointer_name"] == "syed"   # preselected == the group pin's token


def test_set_pointer_guard_does_not_block_bulk_switch_all(server, repo_root, monkeypatch):
    """DON'T over-block: the per-lane guard/routing lives ONLY in /api/set-pointer.
    An explicit fleet-wide switch-all still plans + fires unaffected (it re-tokens
    live lanes; it is the legitimate all-lanes path, never caught by the guard)."""
    console_app._SWITCH_LAST_RUN.clear()
    console_app._SWITCH_INFLIGHT.clear()
    monkeypatch.setattr(console_app, "_SWITCH_COOLDOWN_S", 0.0)
    rows = [_row("irsyad-coord")]
    ok_res = MagicMock(returncode=0, stdout="switched\n", stderr="")
    p1, p2, p3 = _batch_patches(rows)
    with p1, p2, p3, patch("subprocess.run", return_value=ok_res):
        r = httpx.post(server + "/api/switch-all", headers=H(),
                       json={"token_name": "syed", "dry_run": False}, timeout=10)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert _by_lane(r.json()["targets"])["irsyad-coord"]["action"] == "switch"
# --- op#12501: dedicated /irsyad page + /api/irsyad endpoint -----------------
# The endpoint is scoped to the irsyad lane-FAMILY (via _family_of) + the musa2
# weekly pool the family runs on. All seams are stubbed so the test is hermetic
# (no live tmux/ps, no Supabase): fetch_lanes/context_bloat/pool_usage + the
# panes live-pane + token ground-truth scan.
_MUSA2_FP = "e1dfa48eec85"   # the musa2 pool's OAuth fingerprint


def _irsyad_lane(session, base="cc-irsyad", lane=None):
    return {"agent_id": base + "-" + session, "base_agent_id": base,
            "display_name": "irsyad " + session, "status": "working",
            "current_task": "build", "tmux_session": session,
            "lane": lane if lane is not None else session,
            "auth_account": "musa", "auth_fp": _MUSA2_FP, "host": "Sheikhs-Mini",
            "heartbeat_age_s": 20, "desired_state": "up",
            "activity": "working on " + session, "activity_age_s": 40}


@pytest.fixture
def irsyad_server(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSOLE_ALLOWED_IPS", "203.0.113.9")
    monkeypatch.setenv("CONSOLE_BREAKGLASS_TOKEN", "test-console-token")
    monkeypatch.setenv("CONSOLE_ACCESS_LOG", str(tmp_path / "console_access.log"))
    # irsyad family (4 main + the import lane) PLUS a non-irsyad lane that MUST
    # be filtered out (proves the family scoping).
    monkeypatch.setattr(db, "fetch_lanes", lambda: [
        _irsyad_lane("irsyad"),
        _irsyad_lane("irsyad-coord"),
        _irsyad_lane("irsyad-prog1"),
        _irsyad_lane("irsyad-import", base="cc-irsyad-b", lane=None),
        _irsyad_lane("cosem-tdu", base="cc-cosem-platform"),   # NOT irsyad family
    ])
    # item-4 (op#31750): the irsyad view now reads PANE-TRUTH (fetch_pane_context +
    # _pane_bloat), NOT the cc_session_costs gauge that lies for idle workers. So the
    # per-lane ctx% comes from CC's `{N}% context used` pane signal: pct=50 -> 500000
    # tokens (50% of the 1M window), keyed by session==sub_tag for the (non-coord)
    # irsyad-coord worker lane.
    monkeypatch.setattr(db, "fetch_pane_context", lambda: [
        {"session": "irsyad-coord", "base": "cc-irsyad", "pct": 50, "pane_k": None,
         "age_s": 60, "idle_verdict": "IDLE_EMPTY"},
    ])
    monkeypatch.setattr(db, "fetch_pool_usage", lambda: [
        {"pool": "Musa", "pct_7d": "47", "pct_5h": "37", "resets_at": None,
         "status_7d": "allowed", "pace": "2.0", "projected_pct": "200",
         "runway_days": None, "updated_age_s": 100},
        {"pool": "musa2", "pct_7d": "9", "pct_5h": "31", "resets_at": None,
         "status_7d": "allowed", "pace": "0.31", "projected_pct": "31",
         "runway_days": None, "updated_age_s": 90},
    ])
    monkeypatch.setattr(console_app.panes, "live_sessions",
                        lambda: ["irsyad", "irsyad-coord", "irsyad-prog1",
                                 "irsyad-import", "cosem-tdu"])
    # import lane = working; the rest idle — proves live-pane bucketing.
    def _cap(sess, live=None):
        if sess == "irsyad-import":
            return {"running": True, "state": "working"}, ""
        return {"running": True, "state": "idle"}, ""
    monkeypatch.setattr(console_app.panes, "capture", _cap)
    monkeypatch.setattr(console_app.panes, "token_ground_truth", lambda include_remote=False: {
        "rows": [
            {"session": "irsyad-coord", "account": "musa2", "fp": _MUSA2_FP,
             "metered": False, "model": "claude-opus-4-8", "host": "Mini",
             "verified": True, "expected": "musa2", "expected_fp": _MUSA2_FP,
             "mismatch": False},
            {"session": "irsyad-import", "account": "Syed", "fp": _SYED_FP,
             "metered": False, "model": "claude-opus-4-8", "host": "Mini",
             "verified": True, "expected": "Musa", "expected_fp": _MUSA_FP,
             "mismatch": True},
        ],
        "summary": {},
    })
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


def test_irsyad_page_served_open(irsyad_server):
    """The /irsyad SPA shell is served OPEN (pre-auth), like /lanes — the page
    fetches /api/irsyad with the stored bearer token after it loads."""
    r = httpx.get(irsyad_server + "/irsyad", timeout=5)
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "Irsyad" in r.text


def test_api_irsyad_requires_auth(irsyad_server):
    r = httpx.get(irsyad_server + "/api/irsyad", timeout=5)
    assert r.status_code == 401


def test_api_irsyad_scopes_to_family_and_musa2_pool(irsyad_server):
    r = httpx.get(irsyad_server + "/api/irsyad", headers=H(), timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert body["family"] == "irsyad"
    sessions = {l["session"] for l in body["lanes"]}
    # the 4 irsyad-family lanes are present; the cosem lane is filtered OUT.
    assert sessions == {"irsyad", "irsyad-coord", "irsyad-prog1", "irsyad-import"}
    assert "cosem-tdu" not in sessions
    # the pool shown is musa2 (the pool this family runs on), never Musa.
    assert body["pool"]["pool"] == "musa2"
    assert body["pool"]["pct_7d"] == "9"
    # counts: import lane working, the other three idle.
    assert body["counts"]["working"] == 1
    assert body["counts"]["idle"] == 3


def test_api_irsyad_folds_token_model_and_context(irsyad_server):
    r = httpx.get(irsyad_server + "/api/irsyad", headers=H(), timeout=5)
    by = {l["session"]: l for l in r.json()["lanes"]}
    # token-truth folded per session: verified account + model.
    coord = by["irsyad-coord"]
    assert coord["bucket"] == "idle"
    assert coord["account"] == "musa2"
    assert coord["model"] == "claude-opus-4-8"
    assert coord["verified"] is True and coord["off_account"] is False
    # pane-truth ctx folded by sub_tag == tmux_session (pct=50 -> 500k / 1M = 50%).
    assert coord["ctx_pct"] == 50
    assert coord["ctx_tokens"] == 500000
    # the import lane is on a DIFFERENT account than expected -> off_account flag.
    imp = by["irsyad-import"]
    assert imp["bucket"] == "working"
    assert imp["off_account"] is True
    assert imp["expected"] == "Musa"
    # a lane with no context row surfaces null, not a fabricated number.
    assert by["irsyad-prog1"]["ctx_pct"] is None


# --- fc-v56 (#25436): context gauge — sub_tag=NULL fallback + downed→OFF -------
# Two display bugs made self-recycle look broken: (1) a sub_tag=NULL writer's row
# was DROPPED so its tile showed "—"; (2) a recycled/reset body kept drawing its
# FROZEN pre-reset high % because the old cost row lingered. The robust downed
# signal is SESSION SUPERSESSION (a newer session row exists for the identity),
# NOT staleness — a just-reset body's old row can still have a fresh ended_at.

def test_ctx_from_session_shows_real_pct_when_not_superseded():
    # same session (or no session info at all) -> the real reading, never a false OFF.
    assert console_app._ctx_from_session(900000, "s1", "s1") == (90, "red")
    assert console_app._ctx_from_session(500000, None, None) == (50, "green")


def test_ctx_from_session_off_when_session_superseded():
    # the id=2193 / session 36e398c5 live fixture: an old ~97% reading whose session
    # has been superseded by a newer one -> OFF (None), never the frozen 97%.
    assert console_app._ctx_from_session(970000, "36e398c5", "new-sess") is None


def test_ctx_from_session_staleness_alone_is_not_the_signal():
    # a reading that is FRESH by any staleness rule but from a superseded session is
    # STILL OFF — proving supersession, not age, is the downed signal.
    assert console_app._ctx_from_session(960000, "old-sess", "newer-sess") is None


def test_context_bloat_drops_superseded_body_keeps_live_high_rider():
    rows = [
        # a just-reset worker: old ~97% row, FRESH age (120s), session superseded.
        {"cc_identity": "cc-worker-a", "sub_tag": "lane-a", "ctx_tokens": 970000,
         "age_s": 120, "session_id": "36e398c5", "current_session_id": "new-sess"},
        # a real high-rider on its SAME live session -> kept, shows the real %.
        {"cc_identity": "cc-worker-b", "sub_tag": "lane-b", "ctx_tokens": 880000,
         "age_s": 60, "session_id": "s-live", "current_session_id": "s-live"},
    ]
    out = console_app._context_bloat(rows)
    by = {r["agent"]: r for r in out}
    assert "cc-worker-a" not in by            # superseded (reset) -> OFF, not 97%
    assert by["cc-worker-b"]["pct"] == 88     # live high-rider -> real %


def test_context_bloat_keeps_null_sub_tag_row():
    # a sub_tag=NULL writer is NOT dropped; it resolves via its cc_identity.
    rows = [{"cc_identity": "cc-irsyad-coord", "sub_tag": None, "ctx_tokens": 500000,
             "age_s": 30, "session_id": "s", "current_session_id": "s"}]
    out = console_app._context_bloat(rows)
    assert len(out) == 1
    assert out[0]["agent"] == "cc-irsyad-coord" and out[0]["sub_tag"] is None
    assert out[0]["pct"] == 50


def test_ctx_index_null_sub_tag_resolves_via_cc_identity():
    # a sub_tag=NULL row is keyed by cc_identity and reachable via the base fallback.
    idx = console_app._ctx_index([{"agent": "cc-irsyad", "sub_tag": None,
                                   "pct": 50, "level": "green"}])
    assert console_app._ctx_for_lane(idx, "irsyad-coord", "cc-irsyad")["pct"] == 50


def test_ctx_index_instance_reading_does_not_leak_to_siblings():
    # an instance (sub_tag) reading keys by session ONLY — a sibling lane with no
    # reading of its own truthfully gets {} (shows "—"), never the sibling's gauge.
    idx = console_app._ctx_index([{"agent": "cc-irsyad", "sub_tag": "irsyad-coord",
                                   "pct": 50, "level": "green"}])
    assert console_app._ctx_for_lane(idx, "irsyad-coord", "cc-irsyad")["pct"] == 50
    assert console_app._ctx_for_lane(idx, "irsyad-prog1", "cc-irsyad") == {}


def test_context_bloat_query_surfaces_session_supersession_columns():
    sql, params = db.build_context_bloat_query()
    assert params == []
    low = sql.lower()
    assert "session_id" in low and "current_session_id" in low
    # peer match must be sub_tag-aware (NULL == NULL) for the absolute-freshest session.
    assert "is not distinct from" in low


def test_coordinators_query_surfaces_session_supersession_columns():
    sql, params = db.build_coordinators_query()
    assert params == []
    low = sql.lower()
    assert "ctx_session_id" in low and "ctx_current_session_id" in low


# --- fc-v52: drain board (per-body inbox depth) + /api/assign -----------------
# The drain board reads each body's UNHANDLED bus inbox (agent_messages
# read_at IS NULL) + console-assigned items, grouped per body. /api/assign turns
# an operator ask into a REAL bus row to that body (via the vetted
# scripts/console_assign.py) so the body actually drains it — same read-only
# console / shell-out-to-write pattern as /api/reset + /api/backlog.

def test_build_inbox_backlog_query_shape():
    """The drain-board data source: unhandled (read_at IS NULL), non-test rows,
    excluding the operator pseudo-inboxes, grouped per to_agent. Flags the two
    signals the board badges: needs_response and console-assigned."""
    sql, params = db.build_inbox_backlog_query()
    assert params == []
    low = sql.lower()
    assert "from agent_messages" in low
    assert "read_at is null" in low
    assert "is_test is not true" in low
    assert "to_agent" in low
    # both badge signals derived in-query
    assert "responded_at is null" in low
    assert "orch-console" in low          # the `assigned` flag


def test_drain_board_groups_sorts_and_caps():
    """_drain_board groups flat unread rows per body, counts unread/needs_response/
    assigned, caps items with a `more` remainder, and floats reply-pending bodies
    to the top, then by depth."""
    rows = (
        [{"id": i, "to_agent": "cc-cosem", "from_agent": "cai",
          "subject": f"s{i}", "priority": "P2", "age_s": 10,
          "needs_response": False, "assigned": False} for i in range(7)]
        + [{"id": 100, "to_agent": "cc-irsyad", "from_agent": "orch-console",
            "subject": "do the thing", "priority": "P1", "age_s": 5,
            "needs_response": True, "assigned": True}]
    )
    board = console_app._drain_board(rows, max_items=5)
    by = {g["agent"]: g for g in board}
    # cosem: 7 unread, 5 items shown + 2 more
    assert by["cc-cosem"]["unread"] == 7
    assert len(by["cc-cosem"]["items"]) == 5
    assert by["cc-cosem"]["more"] == 2
    # irsyad: the console-assigned + reply-pending item is flagged both ways
    assert by["cc-irsyad"]["needs_response"] == 1
    assert by["cc-irsyad"]["assigned"] == 1
    # reply-pending body sorts FIRST even though it has fewer items
    assert board[0]["agent"] == "cc-irsyad"


def test_drain_board_empty_is_empty_list():
    assert console_app._drain_board([]) == []
    assert console_app._drain_board(None) == []


def test_api_assign_requires_auth(server):
    """Same gate as every mutating POST — no new unauthenticated surface."""
    r = httpx.post(server + "/api/assign",
                   json={"agent": "cc-irsyad", "ask": "do it"}, timeout=5)
    assert r.status_code == 401


def test_api_assign_bad_agent_400_no_subprocess(server):
    """A crafted / non-id agent value is rejected BEFORE any subprocess."""
    with patch("subprocess.run") as mock_run:
        r = httpx.post(server + "/api/assign", headers=H(),
                       json={"agent": "; rm -rf /", "ask": "do it"}, timeout=5)
    assert r.status_code == 400
    mock_run.assert_not_called()


def test_api_assign_empty_ask_400(server):
    with patch("subprocess.run") as mock_run:
        r = httpx.post(server + "/api/assign", headers=H(),
                       json={"agent": "cc-irsyad", "ask": "   "}, timeout=5)
    assert r.status_code == 400
    mock_run.assert_not_called()


def test_api_assign_inserts_bus_row_via_script(server):
    """A valid assign shells out to console_assign.py with (agent, ask, --priority)
    and returns the new bus-row id parsed from stdout."""
    ok = MagicMock(returncode=0, stdout="assigned agent_messages #4242 to cc-irsyad\n", stderr="")
    with patch("subprocess.run", return_value=ok) as mock_run:
        r = httpx.post(server + "/api/assign", headers=H(),
                       json={"agent": "cc-irsyad", "ask": "ship the thing", "priority": "P1"},
                       timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["id"] == 4242 and body["agent"] == "cc-irsyad"
    argv = mock_run.call_args.args[0]
    assert argv[-4:] == ["cc-irsyad", "ship the thing", "--priority", "P1"]
    assert argv[-5].endswith("console_assign.py")


def test_api_assign_unknown_agent_maps_to_400(server):
    """The script exits 2 for an agent not in `agents`; the endpoint returns 400
    so the UI can say 'unknown lane', not a generic 500."""
    bad = MagicMock(returncode=2, stdout="", stderr="unknown agent 'nope'\n")
    with patch("subprocess.run", return_value=bad):
        r = httpx.post(server + "/api/assign", headers=H(),
                       json={"agent": "nope", "ask": "do it"}, timeout=5)
    assert r.status_code == 400
    assert r.json()["ok"] is False


def test_api_assign_no_service_key_leak(server):
    """The assign response never leaks the service role / key."""
    ok = MagicMock(returncode=0, stdout="assigned agent_messages #1 to cc-irsyad\n", stderr="")
    with patch("subprocess.run", return_value=ok):
        r = httpx.post(server + "/api/assign", headers=H(),
                       json={"agent": "cc-irsyad", "ask": "x"}, timeout=5)
    assert "service_role" not in r.text.lower()
    assert "SUPABASE_SERVICE_KEY" not in r.text


# --- fc-v55: YOUR ASKS board (live-derived status) + assign→link + swipe-close --
# The "Your asks" board stores ONLY the link (operator_asks.thread_id →
# agent_messages) and derives status LIVE in SQL each poll, so it can never go
# stale (op#13250). console_assign.py writes the link row in the SAME transaction
# as the directive; the operator's swipe-to-confirm closes an ask via the vetted
# scripts/asks_close.py (read-only-console shell-out, like /api/backlog).

import importlib.util  # noqa: E402

_REPO = pathlib.Path(__file__).resolve().parents[2]


def test_build_asks_query_derives_every_status_live_in_sql():
    """Status is COMPUTED in SQL from the linked bus row — never a stored column.
    Assert the shipped query encodes ALL FIVE states, the exact needs_you
    condition (a reply BACK to orch-console, requires_response, unanswered), the
    open-only filter, and the needs_you-pinned ordering + live freshness ages."""
    sql, params = db.build_asks_query()
    assert params == []
    low = " ".join(sql.lower().split())
    # links, never stores status:
    assert "from operator_asks" in low
    assert "left join latest l on l.thread_id = a.thread_id" in low
    assert "from agent_messages" in low and "is_test is not true" in low
    assert "a.closed_at is null" in low            # only OPEN asks (delegate-reply ≠ done)
    # all five live-derived states present:
    for state in ("on_nazim", "needs_you", "delegate_done", "in_progress", "pending"):
        assert "'" + state + "'" in low, f"missing derived state {state}"
    # the needs_you condition, verbatim shape (bounced back, unanswered):
    assert "l.from_agent <> 'orch-console'" in low
    assert "l.to_agent = 'orch-console'" in low
    assert "l.requires_response and l.responded_at is null" in low
    # progression thresholds derived from the bus row:
    assert "l.responded_at is not null" in low     # -> delegate_done (review)
    assert "l.read_at is not null" in low          # -> in_progress
    # freshness = age of REAL last bus movement (or the ask if undelegated):
    assert "coalesce(l.created_at, a.created_at)" in low
    assert "updated_age_s" in low and "asked_age_s" in low
    # needs_you pinned to the very top of the order:
    order = low.split("order by", 1)[1]
    assert "then 0" in order


def test_fetch_asks_passthrough(monkeypatch):
    seen = {}
    def _fake_query(sql, params):
        seen["q"] = (sql, params)
        return [{"id": 1}]
    monkeypatch.setattr(db, "_query", _fake_query)
    rows = db.fetch_asks()
    assert rows == [{"id": 1}]
    assert "operator_asks" in seen["q"][0].lower()


def _load_console_assign():
    spec = importlib.util.spec_from_file_location(
        "console_assign_mod", _REPO / "scripts" / "console_assign.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeCursor:
    def __init__(self, fetch_queue):
        self.executed = []
        self._fetch = list(fetch_queue)
    def execute(self, sql, params=None):
        self.executed.append((sql, params))
    def fetchone(self):
        return self._fetch.pop(0)
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, cur):
        self._cur = cur
        self.committed = False
    def cursor(self):
        return self._cur
    def commit(self):
        self.committed = True
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_console_assign_stamps_link_row_in_same_transaction(monkeypatch):
    """A console assign inserts the directive (with a fresh uuid thread_id) AND the
    operator_asks LINK row in ONE transaction, then commits both — so an assign is
    a tracked operator ask atomically. Status is NOT written (derive-live only)."""
    mod = _load_console_assign()
    # agents-exists check -> truthy; directive INSERT ... RETURNING id, thread_id.
    cur = _FakeCursor(fetch_queue=[(1,), (4242, "th-uuid-abc")])
    conn = _FakeConn(cur)
    monkeypatch.setattr(mod, "_dsn", lambda: "postgres://fake")
    monkeypatch.setattr(mod.psycopg, "connect", lambda dsn: conn)

    new_id = mod.assign("cc-irsyad", "ship the thing", "P1", source_msg_id=99)
    assert new_id == 4242
    assert conn.committed is True

    sqls = [e[0] for e in cur.executed]
    # 1) agents existence guard, 2) directive insert, 3) operator_asks link insert
    assert len(cur.executed) == 3
    assert "from agents" in sqls[0].lower()
    directive = cur.executed[1]
    assert "insert into agent_messages" in directive[0].lower()
    assert "thread_id" in directive[0].lower() and "gen_random_uuid()" in directive[0].lower()
    assert "returning id, thread_id" in directive[0].lower()
    link = cur.executed[2]
    assert "insert into operator_asks" in link[0].lower()
    # the link row stores the SAME thread_id the directive returned, + the origin.
    assert link[1] == ("ship the thing", 99, "th-uuid-abc", "cc-irsyad")
    # never writes a status column (the whole point — status is derived live).
    assert "status" not in link[0].lower()


def test_console_assign_unknown_agent_exits_2_before_any_insert(monkeypatch):
    """An unknown agent aborts BEFORE the directive/link write (exit 2 → 400)."""
    mod = _load_console_assign()
    cur = _FakeCursor(fetch_queue=[None])  # agents check returns no row
    conn = _FakeConn(cur)
    monkeypatch.setattr(mod, "_dsn", lambda: "postgres://fake")
    monkeypatch.setattr(mod.psycopg, "connect", lambda dsn: conn)
    with pytest.raises(SystemExit) as ei:
        mod.assign("nope", "do it", "P2")
    assert ei.value.code == 2
    # only the guard ran; no directive/link insert, no commit.
    assert len(cur.executed) == 1
    assert conn.committed is False


def test_api_ask_close_requires_auth(server):
    r = httpx.post(server + "/api/ask-close", json={"id": 1, "action": "confirm"}, timeout=5)
    assert r.status_code == 401


def test_api_ask_close_bad_action_400_no_subprocess(server):
    with patch("subprocess.run") as mock_run:
        r = httpx.post(server + "/api/ask-close", headers=H(),
                       json={"id": 1, "action": "nuke"}, timeout=5)
    assert r.status_code == 400
    mock_run.assert_not_called()


def test_api_ask_close_bad_id_400_no_subprocess(server):
    with patch("subprocess.run") as mock_run:
        r = httpx.post(server + "/api/ask-close", headers=H(),
                       json={"id": "not-an-int", "action": "confirm"}, timeout=5)
    assert r.status_code == 400
    mock_run.assert_not_called()


def test_api_ask_close_confirm_shells_out_to_writer(server):
    """A valid confirm shells out to asks_close.py with (id, 'confirm') — the
    operator's authoritative done (a delegate reply only shows REVIEW)."""
    ok = MagicMock(returncode=0, stdout="ok\n", stderr="")
    with patch("subprocess.run", return_value=ok) as mock_run:
        r = httpx.post(server + "/api/ask-close", headers=H(),
                       json={"id": 77, "action": "confirm"}, timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["id"] == 77 and body["action"] == "confirm"
    argv = mock_run.call_args.args[0]
    assert argv[-3].endswith("asks_close.py")
    assert argv[-2:] == ["77", "confirm"]


def test_api_ask_close_drop_shells_out_to_writer(server):
    ok = MagicMock(returncode=0, stdout="ok\n", stderr="")
    with patch("subprocess.run", return_value=ok) as mock_run:
        r = httpx.post(server + "/api/ask-close", headers=H(),
                       json={"id": 5, "action": "drop"}, timeout=5)
    assert r.status_code == 200
    assert r.json()["action"] == "drop"
    assert mock_run.call_args.args[0][-2:] == ["5", "drop"]


def test_api_ask_close_writer_failure_maps_500(server):
    bad = MagicMock(returncode=2, stdout="", stderr="error: id 9 not found or already closed?\n")
    with patch("subprocess.run", return_value=bad):
        r = httpx.post(server + "/api/ask-close", headers=H(),
                       json={"id": 9, "action": "confirm"}, timeout=5)
    assert r.status_code == 500
    assert r.json()["ok"] is False


# --- fc-v55: lane phantom-dark-twin dedup (console display side) ---------------
# A family registered on two hosts (e.g. cc-scholar-1 Mac-Studio + cc-scholar-2
# Mini, both lane='scholar', both desired_state='up') otherwise emits BOTH: the
# no-local-pane twin classifies offline -> flagged DARK (a false alarm). The fix
# collapses same-lane rows to ONE card, preferring the live-local-pane row, and
# never flags a lane dark if ANY row is live.

def test_dedupe_collapses_phantom_dark_twin_to_one_live_lane():
    rows = [
        # dark twin: no local pane on THIS console host (remote), desired up.
        {"agent_id": "cc-scholar-2", "base_agent_id": "cc-scholar", "lane": "scholar",
         "tmux_session": "scholar", "host": "Mini", "desired_state": "up",
         "heartbeat_age_s": 40, "activity": "stale — 2 days ago",
         "live": {"running": False}},
        # the real live pane binds here (local, working).
        {"agent_id": "cc-scholar-1", "base_agent_id": "cc-scholar", "lane": "scholar",
         "tmux_session": None, "host": "Mac-Studio", "desired_state": "up",
         "heartbeat_age_s": 8, "activity": "stale — 2 days ago",
         "live": {"running": True, "state": "working", "activity": "Baked for 7m 38s"}},
    ]
    out = console_app._dedupe_lanes_by_family(rows)
    # exactly ONE lane entry for 'scholar', and it is the LIVE one.
    assert len(out) == 1
    kept = out[0]
    assert kept["agent_id"] == "cc-scholar-1"
    # not dark: the live pane classifies working, never flagged.
    state, flagged = console_app._lane_bucket(kept)
    assert state == "working" and flagged is False
    # item 3: the live activity replaces the days-stale stored string.
    assert kept["activity"] == "Baked for 7m 38s"


def test_dedupe_keeps_distinct_live_instances():
    """Two GENUINELY live instances that happen to share a lane label are both kept
    (distinct real workers) — the collapse only kills no-pane twins."""
    rows = [
        {"agent_id": "cc-mirror-1", "lane": "mirror", "tmux_session": "mirror-1",
         "desired_state": "up", "heartbeat_age_s": 5,
         "live": {"running": True, "state": "working"}},
        {"agent_id": "cc-mirror-2", "lane": "mirror", "tmux_session": "mirror-2",
         "desired_state": "up", "heartbeat_age_s": 6,
         "live": {"running": True, "state": "idle"}},
    ]
    out = console_app._dedupe_lanes_by_family(rows)
    assert {r["agent_id"] for r in out} == {"cc-mirror-1", "cc-mirror-2"}


def test_dedupe_fully_dark_lane_collapses_to_one_card():
    """A lane dark on BOTH rows shows a SINGLE honest dark card, not duplicates."""
    rows = [
        {"agent_id": "cc-ghost-1", "lane": "ghost", "tmux_session": None,
         "host": "Mini", "desired_state": "up", "heartbeat_age_s": 900,
         "live": {"running": False}},
        {"agent_id": "cc-ghost-2", "lane": "ghost", "tmux_session": "ghost",
         "host": "Mac-Studio", "desired_state": "up", "heartbeat_age_s": 200,
         "live": {"running": False}},
    ]
    out = console_app._dedupe_lanes_by_family(rows)
    assert len(out) == 1
    state, flagged = console_app._lane_bucket(out[0])
    # still honestly dark (nothing is live) and still flagged (desired up).
    assert state == "offline" and flagged is True


def test_dedupe_distinct_lanes_untouched():
    """Distinct lane labels never collapse (no over-dedup of a real family)."""
    rows = [
        {"agent_id": "cc-irsyad-prog1", "lane": "irsyad-prog1", "tmux_session": "irsyad-prog1",
         "heartbeat_age_s": 5, "live": {"running": True, "state": "working"}},
        {"agent_id": "cc-irsyad-prog2", "lane": "irsyad-prog2", "tmux_session": "irsyad-prog2",
         "heartbeat_age_s": 6, "live": {"running": True, "state": "idle"}},
    ]
    out = console_app._dedupe_lanes_by_family(rows)
    assert len(out) == 2
