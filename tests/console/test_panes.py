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
    lines = "\n".join(f"line{i}" for i in range(50))
    with patch.object(panes, "live_sessions", return_value=["cosem-tdu"]):
        with patch("subprocess.run", return_value=_run(0, lines)) as mock_run:
            result = panes.capture_pane("cosem-tdu")
    assert result is not None
    # capped at the last 40 lines even if tmux somehow returned more
    assert result.splitlines() == [f"line{i}" for i in range(10, 50)]
    args, kwargs = mock_run.call_args
    assert args[0] == [panes._TMUX, "capture-pane", "-t", "=cosem-tdu:0.0", "-p", "-S", "-40"]
    # argv-list only — never a shell string, never shell=True
    assert kwargs.get("shell") is not True


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
