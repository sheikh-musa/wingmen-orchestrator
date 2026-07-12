"""Progress-stall detector (op #3695) — pure-logic tests, no DB/tmux.

The two properties that make it safe: (1) the pane fingerprint IGNORES footer
animation (spinner/timer/token counter) so a genuinely-busy long build isn't a
false positive, but DOES move on real new output; (2) a lane is flagged only when
it's both not-progressing AND holding unactioned work (an idle-and-done lane is
never flagged).
"""
import sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "nervous_system"))
import lane_watchdog as w  # noqa: E402


def _pane(scrollback, footer):
    # A Claude-Code-ish pane: conversation scrollback, the ❯ composer, then footer.
    return scrollback + "\n❯ \n" + footer


BODY = "Assistant: analyzing the repo\n● Running tests\n  42 passed"


def test_digest_ignores_footer_animation():
    # Same scrollback, only the animated footer (elapsed + token counter) differs.
    a = _pane(BODY, "  esc to interrupt (12s · ↑ 1.2k tokens)")
    b = _pane(BODY, "  esc to interrupt (48s · ↑ 3.7k tokens)")
    assert w.scrollback_digest(a) == w.scrollback_digest(b)   # spinner/timer != progress


def test_digest_moves_on_real_new_output():
    a = _pane(BODY, "  esc to interrupt (12s)")
    b = _pane(BODY + "\n● Building the bundle", "  esc to interrupt (12s)")
    assert w.scrollback_digest(a) != w.scrollback_digest(b)   # new scrollback = progress


def test_digest_static_for_idle_pane():
    # An idle pane (empty composer, idle footer) is fully static across scans.
    idle = _pane(BODY, "  ? for shortcuts · for agents")
    assert w.scrollback_digest(idle) == w.scrollback_digest(idle)


def test_stall_requires_both_no_progress_AND_unactioned_work():
    over = w.PROGRESS_STALL_SEC + 60
    under = w.PROGRESS_STALL_SEC - 60
    assert w.progress_stalled(over, 2) is True         # stalled long + has work -> flag
    assert w.progress_stalled(over, 0) is False        # idle-and-DONE (no work) -> never flag
    assert w.progress_stalled(under, 2) is False       # progressed recently -> not a stall


def test_fingerprint_shape_combines_three_signals(monkeypatch):
    # progress_fingerprint blends pane digest + last bus id + last commit; any one
    # advancing changes the fingerprint (so a bus post / commit resets the timer
    # even if the pane is momentarily static).
    monkeypatch.setattr(w, "lane_progress_state", lambda s: ("100", "abc123", 3))
    fp1, un = w.progress_fingerprint("lane", _pane(BODY, "footer"))
    assert un == 3 and "abc123" in fp1 and "100" in fp1
    monkeypatch.setattr(w, "lane_progress_state", lambda s: ("101", "abc123", 3))  # new bus post
    fp2, _ = w.progress_fingerprint("lane", _pane(BODY, "footer"))
    assert fp1 != fp2                                   # bus-post advance = progress
