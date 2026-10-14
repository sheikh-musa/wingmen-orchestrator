"""Regression test for bus #58267/#58273 (held_commitment #360).

Bug: a lane relaunched via `--resume <session-id>` (switch_lane_token.sh's
conversation-preserving re-token, or a manual `claude --resume`) never got the
unread-inbox briefing that scripts/build_launch_context.py computes, because
the staging condition in scripts/launch_dangerous_cc.sh gated on "no
passthrough args at all" — and `-- --resume <id>` IS a passthrough arg. This
stranded any mail that landed (or any instance-address mail the lane wasn't
registered under yet) between sessions: a fresh launch got the briefing via
the SessionStart hook; a resumed one silently didn't.

Fix: gate on $SCHEDULED_PROMPT_FILE (the script's own authoritative signal
for "this is the bounded, non-interactive scheduled sweep, which already
does its own careful unread-inbox query per skills/scheduled-sweep-prompt.md
Step 1") instead of inferring "scheduled" from "has any passthrough args."

This test does NOT re-implement the condition — it extracts and actually
EXECUTES the real argv-parser block and the real staging-condition block
verbatim from the shipped script (wet-proving the shipped artifact, not a
paraphrase of it), for three scenarios:
  1. fresh interactive launch (no passthrough)      -> context IS staged
  2. `-- --resume <id>` interactive relaunch         -> context IS staged (the fix)
  3. scheduled_cc_sweep.sh's exact invocation shape
     (`--scheduled-prompt <file> -- --max-turns 20`) -> context is NOT staged
"""
from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = _REPO_ROOT / "scripts" / "launch_dangerous_cc.sh"
SRC = LAUNCHER.read_text()


def _extract(start_marker: str, end_marker: str, src: str = SRC) -> str:
    start = src.index(start_marker)
    end = src.index(end_marker, start) + len(end_marker)
    return src[start:end]


# The exact single-pass argv parser (REPO_OVERRIDE="" ... the loop's `done`).
PARSER_BLOCK = _extract('REPO_OVERRIDE=""\n', "\ndone\n")

# The exact staging condition (if [ -n "$LAUNCH_CONTEXT" ] ... its closing `fi`).
STAGING_BLOCK = _extract(
    'if [ -n "$LAUNCH_CONTEXT" ] && [ -z "$SCHEDULED_PROMPT_FILE" ]; then\n',
    "\nfi\n",
)


def test_parser_block_located_and_shape_is_current():
    """Sanity check the extraction itself found real, current code (so a future
    refactor that drops these exact markers FAILS this test loudly instead of
    the scenario tests below silently exercising an empty/stale string)."""
    assert "CLAUDE_PASSTHROUGH+=(" in PARSER_BLOCK
    assert "--scheduled-prompt" in PARSER_BLOCK
    assert "echo" in STAGING_BLOCK and "cc_launch_ctx.txt" in STAGING_BLOCK


def test_staging_condition_no_longer_keys_off_passthrough_emptiness():
    """The old bug's exact shape must be gone: gating on
    `${#CLAUDE_PASSTHROUGH[@]} -eq 0` conflated ANY passthrough (including
    `--resume`) with scheduled-mode. Pin its absence so it can't regress."""
    assert "CLAUDE_PASSTHROUGH[@]} -eq 0" not in STAGING_BLOCK
    assert "SCHEDULED_PROMPT_FILE" in STAGING_BLOCK


def _run_scenario(argv: list[str], ctx_file: Path) -> bool:
    """Feed argv through the REAL parser block, then the REAL staging block
    (with the hardcoded /tmp path redirected to an isolated per-test file),
    and report whether the context got staged.

    Returns True iff ctx_file now exists with the test context content.
    """
    staging = STAGING_BLOCK.replace("/tmp/cc_launch_ctx.txt", str(ctx_file))
    script = textwrap.dedent(
        f"""
        set -euo pipefail
        {PARSER_BLOCK}
        LAUNCH_CONTEXT="TEST-UNREAD-INBOX-CONTEXT-BLOCK"
        TEAL=""; RESET=""
        {staging}
        """
    )
    subprocess.run(
        ["bash", "-c", script, "launch_dangerous_cc.sh"] + argv,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if not ctx_file.exists():
        return False
    return ctx_file.read_text() == "TEST-UNREAD-INBOX-CONTEXT-BLOCK\n"


def test_fresh_launch_stages_context(tmp_path):
    """Baseline: no passthrough args at all -> must still stage (no regression
    for the common case)."""
    ctx_file = tmp_path / "ctx.txt"
    assert _run_scenario([], ctx_file) is True


def test_resume_relaunch_stages_context_the_fix(tmp_path):
    """THE FIX: the real-world invocation shape `-- --resume <id>`
    (scripts/launch_dangerous_cc.sh's own usage docs:
    `./scripts/launch_dangerous_cc.sh -- --resume <session-id>`) must now
    stage the context too, so a resumed session's SessionStart hook (which
    DOES fire on resume, with source=resume, per Claude Code docs) actually
    injects the unread-inbox briefing instead of silently skipping it."""
    ctx_file = tmp_path / "ctx.txt"
    assert _run_scenario(["--", "--resume", "abc123-session-id"], ctx_file) is True


def test_other_interactive_passthrough_also_stages_context(tmp_path):
    """A different kind of interactive passthrough (e.g. an operator's
    `-- --model foo` escape hatch) is not scheduled-sweep mode either, so it
    must also stage -- confirms the fix is general, not a --resume special-case."""
    ctx_file = tmp_path / "ctx.txt"
    assert _run_scenario(["--", "--model", "claude-opus-4-8"], ctx_file) is True


def test_scheduled_sweep_invocation_does_not_stage_context(tmp_path):
    """scheduled_cc_sweep.sh's exact invocation shape (verified against the
    live caller: --scheduled-prompt <file> -- --max-turns 20) must NOT stage
    the generic context block -- the sweep prompt does its own unread-inbox
    query (skills/scheduled-sweep-prompt.md Step 1), and the session is
    turn-bounded, so prepending the full world-state/governance block would
    be wasted turns, not a stranded-mail bug."""
    ctx_file = tmp_path / "ctx.txt"
    assert (
        _run_scenario(
            ["--scheduled-prompt", "skills/scheduled-sweep-prompt.md", "--", "--max-turns", "20"],
            ctx_file,
        )
        is False
    )


def test_scheduled_cc_sweep_caller_invocation_shape_is_unchanged():
    """Pin the actual caller (scripts/scheduled_cc_sweep.sh) still invokes the
    launcher the way the scenario above assumes -- if this ever drifts, the
    scenario test above is testing a shape nothing calls anymore."""
    caller_src = (_REPO_ROOT / "scripts" / "scheduled_cc_sweep.sh").read_text()
    m = re.search(
        r'--scheduled-prompt\s+skills/scheduled-sweep-prompt\.md\s*\\\s*\n\s*--\s*\\\s*\n\s*--max-turns\s+20',
        caller_src,
    )
    assert m, "scheduled_cc_sweep.sh's launcher invocation shape changed — update the scenario test above to match"
