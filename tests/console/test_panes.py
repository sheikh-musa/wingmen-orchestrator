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
    assert panes._expected_fp("cosem-tdu") == _fp("MUSA")
