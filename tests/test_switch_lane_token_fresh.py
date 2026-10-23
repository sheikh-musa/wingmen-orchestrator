"""--fresh forces a NO-RESUME relaunch (op#29665 / #65797).

Flipping a lane onto a text-only GLM model must NOT resume the Claude conversation
(image blocks + Claude thinking/tool artifacts are wrong on a GLM lane, and resuming
burns the whole old context at provider rates). --fresh skips resume-session
resolution so RESUME_ID stays empty and `-- --resume <id>` is never forwarded.

Source-level assertions (the launch path does a real tmux kill+relaunch, not unit-
testable end-to-end): we assert the no-resume-under-fresh CONTRACT holds in the script.
"""
import pathlib

SRC = (pathlib.Path(__file__).resolve().parents[1] / "scripts" / "switch_lane_token.sh").read_text()
# Code only (drop comment lines) — comments mention `-- --resume` descriptively.
CODE = "\n".join(ln for ln in SRC.splitlines() if not ln.lstrip().startswith("#"))


def test_fresh_flag_is_parsed():
    assert "--fresh) FRESH=1" in SRC, "the --fresh flag must be parsed"
    assert 'FRESH="${SWITCH_FRESH:-0}"' in SRC, "FRESH default + SWITCH_FRESH env override"


def test_fresh_branch_forces_empty_resume_id_and_skips_resolution():
    # The --fresh branch must set RESUME_ID="" and must NOT call resolve_resume_session,
    # so no session is ever found to forward as --resume.
    start = SRC.index('if [ "$FRESH" = "1" ]; then')
    branch = SRC[start:start + 700]
    assert 'RESUME_ID=""' in branch, "--fresh must force RESUME_ID empty"
    assert "resolve_resume_session" not in branch, "--fresh must NOT resolve a session to resume"


def test_resume_is_forwarded_only_under_nonempty_resume_id():
    # Invariant that makes empty RESUME_ID == no --resume: every `-- --resume` forward
    # is guarded by a non-empty RESUME_ID check. So FRESH (RESUME_ID="") => no --resume.
    assert '-- --resume' in CODE, "resume path still exists for the normal (non-fresh) case"
    for idx in _find_all(CODE, '-- --resume'):
        preceding = CODE[max(0, idx - 400):idx]
        assert 'if [ -n "$RESUME_ID" ]' in preceding, (
            "a `-- --resume` forward must be inside an `if [ -n \"$RESUME_ID\" ]` guard "
            "so an empty RESUME_ID (what --fresh forces) never forwards --resume"
        )


def test_fresh_warns_handoff_is_the_only_reconstitution_source():
    start = SRC.index('if [ "$FRESH" = "1" ]; then')
    branch = SRC[start:start + 700]
    assert "handoff" in branch.lower(), "--fresh must warn that a handoff is the sole reconstitution source"
    assert "NOT be resumed" in branch or "NO-RESUME" in branch


def test_fresh_in_usage_and_dryrun():
    assert "[--fresh]" in SRC, "usage strings mention --fresh"
    assert "--fresh FORCED" in SRC, "dry-run output distinguishes a forced-fresh relaunch"


def _find_all(s, sub):
    i = s.find(sub)
    while i != -1:
        yield i
        i = s.find(sub, i + 1)
