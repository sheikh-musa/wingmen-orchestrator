"""Live tmux pane peek tests (read-only) — thread f869956c/msg 6156.

capture_pane must NEVER shell out for a session that isn't in the real,
current live_sessions() list — an attacker-supplied/unrecognized name must
be rejected before any subprocess call, not just have its output discarded.
"""
from unittest.mock import patch, MagicMock

from nervous_system.console import panes


def _run(returncode=0, stdout=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    return m


def test_live_sessions_parses_tmux_output(monkeypatch):
    with patch("subprocess.run", return_value=_run(0, "orch\ncosem-tdu\nreviewer-abc-123\n")) as mock_run:
        sessions = panes.live_sessions()
    assert sessions == ["orch", "cosem-tdu", "reviewer-abc-123"]
    args = mock_run.call_args[0][0]
    assert args == [panes._TMUX, "list-sessions", "-F", "#{session_name}"]


def test_live_sessions_returns_empty_on_tmux_failure():
    with patch("subprocess.run", return_value=_run(1, "")):
        assert panes.live_sessions() == []


def test_live_sessions_returns_empty_on_exception():
    with patch("subprocess.run", side_effect=OSError("no tmux")):
        assert panes.live_sessions() == []


def test_capture_pane_rejects_unrecognized_session_without_shelling_out():
    """The core safety property: an unrecognized/adversarial session name
    must be rejected by the live_sessions() membership check BEFORE
    capture-pane is ever invoked — not just have its result discarded."""
    with patch.object(panes, "live_sessions", return_value=["orch", "cosem-tdu"]):
        with patch("subprocess.run") as mock_run:
            result = panes.capture_pane("; rm -rf / #")
    assert result is None
    mock_run.assert_not_called()


def test_capture_pane_rejects_empty_session():
    with patch.object(panes, "live_sessions", return_value=["orch"]):
        with patch("subprocess.run") as mock_run:
            assert panes.capture_pane("") is None
    mock_run.assert_not_called()


def test_capture_pane_returns_text_for_a_live_session():
    # Blank-line-separated so each survives as its OWN logical line: the soft-wrap
    # reflow (op#3729) deliberately rejoins a RUN of consecutive prose lines into
    # one line (see test_reflow_* + test_capture_pane_reflows_* below), so a plain
    # "line0\nline1\n..." fixture would collapse to a single line. A blank line is
    # a reflow boundary, so these stay distinct. Under the cap (60), no chrome.
    lines = "\n\n".join(f"line{i}" for i in range(50))
    with patch.object(panes, "live_sessions", return_value=["cosem-tdu"]):
        with patch("subprocess.run", return_value=_run(0, lines)) as mock_run:
            result = panes.capture_pane("cosem-tdu")
    assert result is not None
    assert result.splitlines() == [f"line{i}" for i in range(50)]
    args, kwargs = mock_run.call_args
    assert args[0] == [panes._TMUX, "capture-pane", "-t", "=cosem-tdu:0.0", "-p", "-S", "-200"]
    # argv-list only — never a shell string, never shell=True
    assert kwargs.get("shell") is not True


def test_capture_pane_caps_at_60_lines_after_filtering():
    # filter-then-cap, not cap-then-filter: raw window is 200 lines, capped to the
    # last 60 only AFTER chrome is stripped + reflowed (a chrome-dominated raw tail
    # must not leave a near-empty result). Blank-separated so the reflow (op#3729)
    # keeps each line its own logical line instead of rejoining the prose run.
    lines = "\n\n".join(f"real activity line {i}" for i in range(100))
    with patch.object(panes, "live_sessions", return_value=["cosem-tdu"]):
        with patch("subprocess.run", return_value=_run(0, lines)):
            result = panes.capture_pane("cosem-tdu")
    got = result.splitlines()
    assert len(got) == 60
    assert got == [f"real activity line {i}" for i in range(40, 100)]


def test_capture_pane_strips_chrome_so_a_narrow_raw_tail_still_yields_real_content():
    """Regression for the operator's exact complaint: a session with real
    activity buried under boot-banner/footer chrome must still show the real
    activity, not just whatever raw lines happen to be at the very tail."""
    raw = (
        "╔══════════════════════════╗\n"
        "║   WINGMEN AGENT BOOT     ║\n"
        "╚══════════════════════════╝\n"
        "Sub-tag: cc-cosem-tdu-1  |  Base: cc-cosem-tdu\n"
        "\n"
        "▶ Building session context for cc-cosem-tdu...\n"
        "  real assistant activity right here\n"
        "\n"
        "────────────────────────────────────────\n"
        "❯ \n"
        "────────────────────────────────────────\n"
        "  ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · ← for agents\n"
    )
    with patch.object(panes, "live_sessions", return_value=["cosem-tdu"]):
        with patch("subprocess.run", return_value=_run(0, raw)):
            result = panes.capture_pane("cosem-tdu")
    assert "real assistant activity right here" in result
    assert "WINGMEN AGENT BOOT" in result  # banner TEXT kept (not a pure-border line)
    assert "╔" not in result and "╚" not in result and "════" not in result
    assert "bypass permissions" not in result
    assert "❯" not in result


def test_is_chrome_strips_pure_box_drawing_lines():
    assert panes._is_chrome("╔══════════════════════════╗") is True
    assert panes._is_chrome("────────────────────────────────") is True
    assert panes._is_chrome("║") is True


def test_is_chrome_keeps_banner_text_alongside_a_border_char():
    assert panes._is_chrome("║   WINGMEN AGENT BOOT     ║") is False


def test_is_chrome_strips_bare_prompt_but_keeps_typed_content():
    assert panes._is_chrome("❯ ") is True
    assert panes._is_chrome("❯") is True
    assert panes._is_chrome("❯ some pending typed instruction") is False


def test_is_chrome_strips_the_permissions_footer():
    assert panes._is_chrome(
        "  ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · ← for agents"
    ) is True
    assert panes._is_chrome("  ⏵⏵ bypass permissions on (shift+tab to cycle) · PR #58 · ← for agents") is True


def test_is_chrome_strips_the_clear_hint_line():
    assert panes._is_chrome("                    new task? /clear to save 401.1k tokens") is True


def test_is_chrome_strips_blank_lines():
    assert panes._is_chrome("") is True
    assert panes._is_chrome("   ") is True


def test_is_chrome_keeps_real_activity_lines():
    assert panes._is_chrome("▶ Building session context for cc-cosem-tdu...") is False
    assert panes._is_chrome("✻ Churned for 1m 19s") is False
    assert panes._is_chrome("  Net: the migration plan exists, is executed.") is False


# --- soft-wrap REFLOW (op#3729) — root-cause coverage the port never added ---
# The TUI word-wraps one sentence across physical lines; reflow collapses a run of
# consecutive non-chrome, non-structure prose lines back into a single flowing
# line, breaking only on chrome/blank lines and structure-starts (bullets, task/
# tool glyphs, "N." markers) so lists + blank-separated paragraphs stay separate.
def test_reflow_rejoins_soft_wrapped_prose_into_one_line():
    assert panes._reflow("sentence part one\nsentence part two") == [
        "sentence part one sentence part two"
    ]


def test_reflow_breaks_on_a_structure_start_so_lists_stay_lists():
    assert panes._reflow("intro prose here\n- bullet a\n- bullet b") == [
        "intro prose here", "- bullet a", "- bullet b",
    ]


def test_reflow_breaks_on_a_blank_line_between_paragraphs():
    assert panes._reflow("para one\n\npara two") == ["para one", "para two"]


def test_capture_pane_reflows_consecutive_prose_into_one_line():
    """Integration lock for the intended reflow behavior that collapses a run of
    consecutive prose lines — the exact case the pre-reflow tests didn't
    anticipate (op#3729 landed after them). Consecutive prose with no boundary
    rejoins with single spaces."""
    lines = "\n".join(f"line{i}" for i in range(5))
    with patch.object(panes, "live_sessions", return_value=["cosem-tdu"]):
        with patch("subprocess.run", return_value=_run(0, lines)):
            result = panes.capture_pane("cosem-tdu")
    assert result == "line0 line1 line2 line3 line4"


def test_capture_pane_target_uses_exact_match_syntax():
    """The '=' prefix is tmux's exact-match session selector — a session
    named e.g. 'orch' must never accidentally target 'orchestrator-2' via
    tmux's default substring matching."""
    with patch.object(panes, "live_sessions", return_value=["orch"]):
        with patch("subprocess.run", return_value=_run(0, "")) as mock_run:
            panes.capture_pane("orch")
    target = mock_run.call_args[0][0][mock_run.call_args[0][0].index("-t") + 1]
    assert target == "=orch:0.0"


def test_capture_pane_returns_none_on_capture_failure():
    with patch.object(panes, "live_sessions", return_value=["orch"]):
        with patch("subprocess.run", return_value=_run(1, "")):
            assert panes.capture_pane("orch") is None


def test_capture_pane_returns_none_on_exception():
    with patch.object(panes, "live_sessions", return_value=["orch"]):
        with patch("subprocess.run", side_effect=OSError("boom")):
            assert panes.capture_pane("orch") is None


# ── GAP-B: expected-fp consults the SHARED per-group resolver ─────────────────
# Proves the console "expected" account follows a per-group pin exactly as the
# lane boot will — display==boot by construction.

def _seed_orch(tmp_path):
    keys = tmp_path / "keys"
    keys.mkdir()

    def make_key(name, tok):
        p = keys / name
        p.write_text(tok + "\n")
        return str(p)

    def ptr(name, target):
        (tmp_path / name).write_text(target + "\n")

    return make_key, ptr


def _fp(tok):
    import hashlib
    return hashlib.sha256(tok.encode("utf-8")).hexdigest()[:12]


def test_expected_fp_honors_group_pointer(tmp_path, monkeypatch):
    make_key, ptr = _seed_orch(tmp_path)
    fleet = make_key("musa-oauth-token", "MUSA")
    grp = make_key("musa2-oauth-token", "MUSA2")
    ptr(".lane_default_token", fleet)
    ptr(".group_default_token.irsyad", grp)
    monkeypatch.setattr(panes, "_ORCH_DIR", str(tmp_path))
    # a lane in the pinned family shows the GROUP account
    assert panes._expected_fp("irsyad-coord") == _fp("MUSA2")
    # a lane outside the family shows the fleet default (unaffected)
    assert panes._expected_fp("cosem-tdu") == _fp("MUSA")


def test_expected_fp_backcompat_without_group_file(tmp_path, monkeypatch):
    make_key, ptr = _seed_orch(tmp_path)
    fleet = make_key("musa-oauth-token", "MUSA")
    ptr(".lane_default_token", fleet)
    monkeypatch.setattr(panes, "_ORCH_DIR", str(tmp_path))
    assert panes._expected_fp("irsyad-coord") == _fp("MUSA")


# ---- _self_reported_hub_account(): op#42896/#42909, orch-console #43092/#43109 ----
# When the hub's SSH scan is unavailable (gzb has no provisioned interactive reach,
# scripts/lib/hub_reach.py), fall back to cc-orchestrator's OWN agent_status.auth_fp --
# but ONLY if fresh, and never conflated with a process-verified scan.
import datetime


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        pass

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _FakeCursor(self._row)


def test_self_reported_hub_account_fresh_row_returns_fp(monkeypatch):
    now = datetime.datetime.now(datetime.timezone.utc)
    monkeypatch.setattr("psycopg.connect", lambda *a, **k: _FakeConn(("abc123def456", now)))
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    assert panes._self_reported_hub_account("cc-orchestrator") == {"fp": "abc123def456", "stale": False}


def test_self_reported_hub_account_stale_row_is_flagged_not_shown_as_current(monkeypatch):
    old = (datetime.datetime.now(datetime.timezone.utc)
           - datetime.timedelta(seconds=panes._SELF_REPORT_FRESH_S + 1))
    monkeypatch.setattr("psycopg.connect", lambda *a, **k: _FakeConn(("abc123def456", old)))
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    assert panes._self_reported_hub_account("cc-orchestrator") == {"fp": None, "stale": True}


def test_self_reported_hub_account_no_row_is_safe(monkeypatch):
    monkeypatch.setattr("psycopg.connect", lambda *a, **k: _FakeConn(None))
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    assert panes._self_reported_hub_account("cc-orchestrator") == {"fp": None, "stale": False}


def test_self_reported_hub_account_db_error_is_safe(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("no db")
    monkeypatch.setattr("psycopg.connect", _boom)
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    assert panes._self_reported_hub_account("cc-orchestrator") == {"fp": None, "stale": False}


# ---- token_ground_truth(): remote-body self-report integration ----
def test_token_ground_truth_falls_back_to_self_report_when_ssh_unreachable(monkeypatch):
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _run(0, ""))
    monkeypatch.setattr(panes, "_remote_hub_scan", lambda *a, **k: None)
    monkeypatch.setattr(panes, "_remote_body_host", lambda session, fallback: fallback)
    monkeypatch.setattr(panes, "_self_reported_hub_account",
                         lambda session: {"fp": "selffp123456", "stale": False})
    monkeypatch.setattr(panes, "_resolve_lane_token_path", lambda session, orch_dir=None: None)
    monkeypatch.setattr(panes, "_env_default_fp", lambda: None)
    monkeypatch.setattr(panes, "_remote_hub_expected_scan", lambda *a, **k: None)
    monkeypatch.setattr(panes, "_account_labels", lambda: {"selffp123456": "Max (Musa)"})
    row = next(r for r in panes.token_ground_truth(include_remote=True)["rows"]
               if r["session"] == "cc-orchestrator")
    assert row["verified"] is False
    assert row["self_reported"] is True
    assert row["self_report_stale"] is False
    assert row["account"] == "Max (Musa)"
    assert row["fp"] == "selffp123456"
    assert row["mismatch"] is False


def test_token_ground_truth_self_reported_mismatch_is_still_red(monkeypatch):
    # the core safety property (orch-console #43109): a self-reported fp that doesn't
    # match the body's expected account must be RED, whether self-reported or not.
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _run(0, ""))
    monkeypatch.setattr(panes, "_remote_hub_scan", lambda *a, **k: None)
    monkeypatch.setattr(panes, "_remote_body_host", lambda session, fallback: fallback)
    monkeypatch.setattr(panes, "_self_reported_hub_account",
                         lambda session: {"fp": "wrongfp0000", "stale": False})
    monkeypatch.setattr(panes, "_resolve_lane_token_path", lambda session, orch_dir=None: "/some/path")
    monkeypatch.setattr(panes, "_read_token_fp", lambda path: "expectedfp99")
    monkeypatch.setattr(panes, "_account_labels", lambda: {})
    row = next(r for r in panes.token_ground_truth(include_remote=True)["rows"]
               if r["session"] == "cc-orchestrator")
    assert row["self_reported"] is True
    assert row["mismatch"] is True


def test_token_ground_truth_stale_self_report_falls_back_to_plain_unverified(monkeypatch):
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _run(0, ""))
    monkeypatch.setattr(panes, "_remote_hub_scan", lambda *a, **k: None)
    monkeypatch.setattr(panes, "_remote_body_host", lambda session, fallback: fallback)
    monkeypatch.setattr(panes, "_self_reported_hub_account",
                         lambda session: {"fp": None, "stale": True})
    monkeypatch.setattr(panes, "_resolve_lane_token_path", lambda session, orch_dir=None: None)
    monkeypatch.setattr(panes, "_env_default_fp", lambda: None)
    monkeypatch.setattr(panes, "_remote_hub_expected_scan", lambda *a, **k: None)
    monkeypatch.setattr(panes, "_account_labels", lambda: {})
    row = next(r for r in panes.token_ground_truth(include_remote=True)["rows"]
               if r["session"] == "cc-orchestrator")
    assert row["verified"] is False
    assert row["self_reported"] is False
    assert row["self_report_stale"] is True
    assert row["account"] is None


def test_token_ground_truth_no_scan_no_self_report_is_plain_unverified(monkeypatch):
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _run(0, ""))
    monkeypatch.setattr(panes, "_remote_hub_scan", lambda *a, **k: None)
    monkeypatch.setattr(panes, "_remote_body_host", lambda session, fallback: fallback)
    monkeypatch.setattr(panes, "_self_reported_hub_account",
                         lambda session: {"fp": None, "stale": False})
    monkeypatch.setattr(panes, "_resolve_lane_token_path", lambda session, orch_dir=None: None)
    monkeypatch.setattr(panes, "_env_default_fp", lambda: None)
    monkeypatch.setattr(panes, "_remote_hub_expected_scan", lambda *a, **k: None)
    monkeypatch.setattr(panes, "_account_labels", lambda: {})
    row = next(r for r in panes.token_ground_truth(include_remote=True)["rows"]
               if r["session"] == "cc-orchestrator")
    assert row["verified"] is False
    assert row["self_reported"] is False
    assert row["self_report_stale"] is False
    assert row["account"] is None


def test_token_ground_truth_ssh_verified_scan_wins_over_self_report(monkeypatch):
    # a process-verified SSH scan must never even consult the weaker self-report signal.
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _run(0, ""))
    monkeypatch.setattr(panes, "_remote_hub_scan", lambda *a, **k: {"fp": "sshfp000000", "model": None})
    monkeypatch.setattr(panes, "_remote_body_host", lambda session, fallback: fallback)
    called = {"n": 0}

    def _sr(session):
        called["n"] += 1
        return {"fp": "shouldnotuse", "stale": False}

    monkeypatch.setattr(panes, "_self_reported_hub_account", _sr)
    monkeypatch.setattr(panes, "_resolve_lane_token_path", lambda session, orch_dir=None: None)
    monkeypatch.setattr(panes, "_env_default_fp", lambda: None)
    monkeypatch.setattr(panes, "_remote_hub_expected_scan", lambda *a, **k: None)
    monkeypatch.setattr(panes, "_account_labels", lambda: {"sshfp000000": "Max (Musa)"})
    row = next(r for r in panes.token_ground_truth(include_remote=True)["rows"]
               if r["session"] == "cc-orchestrator")
    assert row["verified"] is True
    assert row["self_reported"] is False
    assert row["fp"] == "sshfp000000"
    assert called["n"] == 0


# ---- _remote_body_host(): op#43153 -- "host VPS" was a stale hardcoded literal ----
def test_remote_body_host_resolves_from_agent_status(monkeypatch):
    monkeypatch.setattr("psycopg.connect", lambda *a, **k: _FakeConn(("gzbai",)))
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    assert panes._remote_body_host("cc-orchestrator", "VPS") == "gzbai"


def test_remote_body_host_falls_back_on_no_row(monkeypatch):
    monkeypatch.setattr("psycopg.connect", lambda *a, **k: _FakeConn(None))
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    assert panes._remote_body_host("cc-orchestrator", "VPS") == "VPS"


def test_remote_body_host_falls_back_on_null_host(monkeypatch):
    monkeypatch.setattr("psycopg.connect", lambda *a, **k: _FakeConn((None,)))
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    assert panes._remote_body_host("cc-orchestrator", "VPS") == "VPS"


def test_remote_body_host_falls_back_on_db_error(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("no db")
    monkeypatch.setattr("psycopg.connect", _boom)
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    assert panes._remote_body_host("cc-orchestrator", "VPS") == "VPS"


def test_token_ground_truth_uses_resolved_host_not_hardcoded_literal(monkeypatch):
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _run(0, ""))
    monkeypatch.setattr(panes, "_remote_hub_scan", lambda *a, **k: None)
    monkeypatch.setattr(panes, "_self_reported_hub_account",
                         lambda session: {"fp": "selffp123456", "stale": False})
    monkeypatch.setattr(panes, "_remote_body_host", lambda session, fallback: "gzbai")
    monkeypatch.setattr(panes, "_resolve_lane_token_path", lambda session, orch_dir=None: None)
    monkeypatch.setattr(panes, "_env_default_fp", lambda: None)
    monkeypatch.setattr(panes, "_remote_hub_expected_scan", lambda *a, **k: None)
    monkeypatch.setattr(panes, "_account_labels", lambda: {"selffp123456": "Max (Musa)"})
    row = next(r for r in panes.token_ground_truth(include_remote=True)["rows"]
               if r["session"] == "cc-orchestrator")
    assert row["host"] == "gzbai"


# ---- summary counts: op#43153 -- self-reported gets its OWN count, never folded ----
# into "unverified" (that conflation was the false "1 unverified" alarm).
def test_summary_self_reported_row_is_not_counted_as_unverified(monkeypatch):
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _run(0, ""))
    monkeypatch.setattr(panes, "_remote_hub_scan", lambda *a, **k: None)
    monkeypatch.setattr(panes, "_self_reported_hub_account",
                         lambda session: {"fp": "selffp123456", "stale": False})
    monkeypatch.setattr(panes, "_remote_body_host", lambda session, fallback: fallback)
    monkeypatch.setattr(panes, "_resolve_lane_token_path", lambda session, orch_dir=None: None)
    monkeypatch.setattr(panes, "_env_default_fp", lambda: None)
    monkeypatch.setattr(panes, "_remote_hub_expected_scan", lambda *a, **k: None)
    monkeypatch.setattr(panes, "_account_labels", lambda: {"selffp123456": "Max (Musa)"})
    summary = panes.token_ground_truth(include_remote=True)["summary"]
    assert summary["self_reported"] == 1
    assert summary["unverified"] == 0


def test_summary_stale_self_report_still_counts_as_unverified(monkeypatch):
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _run(0, ""))
    monkeypatch.setattr(panes, "_remote_hub_scan", lambda *a, **k: None)
    monkeypatch.setattr(panes, "_self_reported_hub_account",
                         lambda session: {"fp": None, "stale": True})
    monkeypatch.setattr(panes, "_remote_body_host", lambda session, fallback: fallback)
    monkeypatch.setattr(panes, "_resolve_lane_token_path", lambda session, orch_dir=None: None)
    monkeypatch.setattr(panes, "_env_default_fp", lambda: None)
    monkeypatch.setattr(panes, "_remote_hub_expected_scan", lambda *a, **k: None)
    monkeypatch.setattr(panes, "_account_labels", lambda: {})
    summary = panes.token_ground_truth(include_remote=True)["summary"]
    assert summary["self_reported"] == 0
    assert summary["unverified"] == 1


def test_remote_hub_scan_key_only_for_userhost_not_alias(monkeypatch):
    """gzb repoint (Nazim 43612): a bare ssh-config alias (`gzb`) carries its own
    IdentityFile, so _remote_hub_scan must NOT force the console's wingmen_vps key onto it;
    a user@host target (wingmen-core) still needs the explicit -i key."""
    captured = {}

    class _R:
        returncode = 1
        stdout = ""
        stderr = ""

    def fake_run(argv, **kw):
        captured["argv"] = list(argv)
        return _R()

    monkeypatch.setattr(panes.subprocess, "run", fake_run)

    # alias target -> no explicit -i (the alias supplies its own key)
    monkeypatch.setattr(panes, "_resolve_hub_ssh_target", lambda: "gzb")
    panes._remote_hub_scan(force=True)
    assert "gzb" in captured["argv"]
    assert "-i" not in captured["argv"]

    # user@host target -> explicit -i <console key>
    monkeypatch.setattr(panes, "_resolve_hub_ssh_target", lambda: "root@91.107.235.77")
    panes._remote_hub_scan(force=True)
    assert "-i" in captured["argv"]
    assert "root@91.107.235.77" in captured["argv"]


def test_remote_hub_scan_unresolved_target_is_unverified(monkeypatch):
    """No holder / unknown -> ssh_target None -> scan returns None (UNVERIFIED), no ssh run."""
    ran = {"called": False}

    def fake_run(argv, **kw):  # pragma: no cover - must NOT be reached
        ran["called"] = True
        raise AssertionError("ssh should not run for an unresolved target")

    monkeypatch.setattr(panes.subprocess, "run", fake_run)
    monkeypatch.setattr(panes, "_resolve_hub_ssh_target", lambda: None)
    assert panes._remote_hub_scan(force=True) is None
    assert ran["called"] is False


# ---- _remote_hub_expected_scan(): orch-console #51982/#46625 -- the hub's
# .orch_default_token POINTER lives on gzb, not the console's own (Mini) checkout,
# so _expected_fp always misses it locally; this reads it over the SAME ssh reach.

def test_remote_hub_expected_scan_returns_fp_on_success(monkeypatch):
    monkeypatch.setattr(panes, "_resolve_hub_ssh_target", lambda: "gzb")
    monkeypatch.setattr(panes.subprocess, "run", lambda *a, **k: _run(0, "582043088eae\n"))
    assert panes._remote_hub_expected_scan(force=True) == "582043088eae"


def test_remote_hub_expected_scan_bad_output_is_none(monkeypatch):
    monkeypatch.setattr(panes, "_resolve_hub_ssh_target", lambda: "gzb")
    monkeypatch.setattr(panes.subprocess, "run", lambda *a, **k: _run(0, "not-a-fingerprint\n"))
    assert panes._remote_hub_expected_scan(force=True) is None


def test_remote_hub_expected_scan_nonzero_exit_is_none(monkeypatch):
    """exit 3/4/5 in _REMOTE_EXPECTED_SCAN_SH (no pointer / unreadable target / empty
    token) must all degrade to None, never raise."""
    monkeypatch.setattr(panes, "_resolve_hub_ssh_target", lambda: "gzb")
    monkeypatch.setattr(panes.subprocess, "run", lambda *a, **k: _run(3, ""))
    assert panes._remote_hub_expected_scan(force=True) is None


def test_remote_hub_expected_scan_unresolved_target_is_none_no_ssh(monkeypatch):
    ran = {"called": False}

    def fake_run(argv, **kw):  # pragma: no cover - must NOT be reached
        ran["called"] = True
        raise AssertionError("ssh should not run for an unresolved target")

    monkeypatch.setattr(panes.subprocess, "run", fake_run)
    monkeypatch.setattr(panes, "_resolve_hub_ssh_target", lambda: None)
    assert panes._remote_hub_expected_scan(force=True) is None
    assert ran["called"] is False


def test_remote_hub_expected_scan_ssh_exception_is_none(monkeypatch):
    def _boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(panes, "_resolve_hub_ssh_target", lambda: "gzb")
    monkeypatch.setattr(panes.subprocess, "run", _boom)
    assert panes._remote_hub_expected_scan(force=True) is None


def test_token_ground_truth_hub_expected_falls_back_to_remote_scan(monkeypatch):
    """When the LOCAL .orch_default_token read misses (the real-world case on the
    Mini, since the file lives on gzb) but the remote expected-scan succeeds, the
    hub's 'expected' must reflect the REMOTE pointer, not the generic .env default
    -- this is the exact bug orch-console reported (expected=Musa when gzb's own
    pointer has long pinned Syed)."""
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _run(0, ""))
    monkeypatch.setattr(panes, "_remote_hub_scan", lambda *a, **k: {"fp": "582043088eae", "model": None})
    monkeypatch.setattr(panes, "_remote_body_host", lambda session, fallback: fallback)
    monkeypatch.setattr(panes, "_resolve_lane_token_path", lambda session, orch_dir=None: None)  # local miss
    monkeypatch.setattr(panes, "_remote_hub_expected_scan", lambda *a, **k: "582043088eae")
    monkeypatch.setattr(panes, "_account_labels", lambda: {"582043088eae": "Max (Syed)"})
    row = next(r for r in panes.token_ground_truth(include_remote=True)["rows"]
               if r["session"] == "cc-orchestrator")
    assert row["expected"] == "Max (Syed)"
    assert row["expected_fp"] == "582043088eae"
    assert row["mismatch"] is False  # live fp == remote-expected fp


def test_token_ground_truth_hub_remote_scan_wins_over_a_real_env_default(monkeypatch):
    """cc-quality's mutation-testing finding (review of 171db24798a9d299): every
    OTHER test in this file mocks _env_default_fp to None, which can never
    distinguish the fix from the exact bug that already shipped (_expected_fp's
    internal env-default collapse winning over the remote scan, because the real
    .env always resolves to a REAL fp, never None, on a live body). This pins the
    actual precedence with a REALISTIC non-None env default standing in for it:
    local miss + a working remote scan + a real (different) env-default fp ->
    the remote scan's fp must win. Reverting the fix back to
    `exp_fp = _expected_fp(sess)` makes this fail (env-default wins instead)."""
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _run(0, ""))
    monkeypatch.setattr(panes, "_remote_hub_scan", lambda *a, **k: {"fp": "582043088eae", "model": None})
    monkeypatch.setattr(panes, "_remote_body_host", lambda session, fallback: fallback)
    monkeypatch.setattr(panes, "_resolve_lane_token_path", lambda session, orch_dir=None: None)  # local miss
    monkeypatch.setattr(panes, "_remote_hub_expected_scan", lambda *a, **k: "582043088eae")  # Syed
    monkeypatch.setattr(panes, "_env_default_fp", lambda: "68142948c003")  # Musa -- REAL, not None
    monkeypatch.setattr(panes, "_account_labels",
                         lambda: {"582043088eae": "Max (Syed)", "68142948c003": "Max (Musa)"})
    row = next(r for r in panes.token_ground_truth(include_remote=True)["rows"]
               if r["session"] == "cc-orchestrator")
    assert row["expected"] == "Max (Syed)"
    assert row["expected_fp"] == "582043088eae"
    assert row["mismatch"] is False


def test_token_ground_truth_hub_expected_scan_not_attempted_without_include_remote(monkeypatch):
    """include_remote=False must behave exactly as before -- no new SSH call."""
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _run(0, ""))
    monkeypatch.setattr(panes, "_resolve_lane_token_path", lambda session, orch_dir=None: None)
    monkeypatch.setattr(panes, "_env_default_fp", lambda: None)

    def _boom(*a, **k):  # pragma: no cover - must NOT be reached
        raise AssertionError("remote expected-scan must not run without include_remote")

    monkeypatch.setattr(panes, "_remote_hub_expected_scan", _boom)
    row = next(r for r in panes.token_ground_truth(include_remote=False)["rows"]
               if r["session"] == "cc-orchestrator")
    assert row["expected_fp"] is None


def test_remote_scan_sh_resolves_pid_via_tmux_not_a_broad_pgrep():
    """orch-console #51921: `pgrep -f 'claude --dangerously'` matched the tmux
    server's own long-lived `tmux new-session -d -s orch ... claude --dangerously...`
    invocation (its argv literally contains that substring) instead of the real
    claude child, so the hub's reported account silently froze at whatever it
    booted on originally -- surfaced as a false "hub is on Musa" when live had
    moved to Syed. Pins the fix at the source-text level (a full SSH round-trip
    isn't CI-feasible): the script must resolve its target pid from the orch tmux
    pane, and must NEVER fall back to a bare `pgrep -f 'claude`-style host-wide
    match that a wrapper process's own argv could satisfy."""
    assert "pgrep -f 'claude" not in panes._REMOTE_SCAN_SH
    assert "tmux list-panes" in panes._REMOTE_SCAN_SH
    assert "pane_pid" in panes._REMOTE_SCAN_SH
