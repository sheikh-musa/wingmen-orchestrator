#!/usr/bin/env python3
"""PreToolUse guard: refuse Edit/Write/NotebookEdit targeting the LIVE ~/wingmen/orchestrator
checkout directly -- enforced, not remembered.

WHY (cc-fleet-health's own memory, live-checkout-edit-slip-20261007): the standing rule
"never checkout inside ~/wingmen/orchestrator -- use `git worktree add` first" (this repo's own
CLAUDE.md) has been self-corrected by hand SIX-PLUS times across 2026-10-07 through 2026-10-31,
each time the same root habit (read/investigate in the live checkout because it's already open,
then reach for Edit/Write on that same path instead of stopping to make a worktree first). Every
occurrence was caught via `git status --short` before a commit, so no live PreToolUse hook or
launchd job ever actually ran buggy code from one of these slips -- but the whole point of a
"caught every time so far" streak is that it eventually won't be, and the live checkout is not a
scratch directory: a hook file there is loaded fresh on every tool call fleet-wide, a launchd
script there runs unreviewed on its next tick, and an uncommitted live file can silently diverge
from what CI/review actually passed. The memory's own conclusion after the 5th+ recurrence: "the
real fix is mechanical... not another willpower reminder -- self-reminders have now failed 4
times running." This is that mechanical fix.

SCOPE: blocks Edit, Write and NotebookEdit when the resolved target path is inside the live
checkout root (LIVE_ORCH_DIR, default ~/wingmen/orchestrator) -- not a sibling worktree
(`orchestrator.wt-<name>`, `~/wingmen/fleet-health/wt/<name>`, etc. all live elsewhere and are
unaffected; a prefix check against the root WITH a trailing slash is what keeps
`orchestrator.wt-foo` from accidentally matching `orchestrator/`). Deliberately unconditional --
tracked or untracked, existing file or brand-new -- because the 2026-10-17 recurrence was a
brand-new file, not an edit to an existing one, and the lesson from that one was that "tracked
files only" was already too narrow.

NOT in scope: Bash (git fetch/merge --ff-only, running tests, tailing logs, etc. against the live
checkout are routine and necessary -- this guard would break real workflows if it tried to parse
every possible redirect/cp/mv target there, and the documented failure class is specifically
"Claude's own Edit/Write/NotebookEdit tool used as if the live path were a scratch file", not
shell redirection). A plain `python3 scripts/foo.py` run via Bash that writes its own state/log
files under the live checkout (e.g. logs/, state/, reports/) is the live checkout's ordinary
runtime behavior, not this failure class, and is unaffected.

Exit 2 + stderr = the tool call is refused and the reason is shown to the model.
"""
import json
import os
import sys
from pathlib import Path

LIVE_ORCH_DIR = Path(os.environ.get("LIVE_CHECKOUT_EDIT_GUARD_ROOT",
                                     os.path.expanduser("~/wingmen/orchestrator"))).resolve()

_PATH_KEYS = ("file_path", "notebook_path", "path")


def _target_path(tool_input: dict) -> "Path | None":
    for key in _PATH_KEYS:
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            try:
                return Path(val).expanduser().resolve()
            except (OSError, RuntimeError):
                return Path(val).expanduser()
    return None


def is_inside_live_checkout(target: Path) -> bool:
    try:
        target.relative_to(LIVE_ORCH_DIR)
    except ValueError:
        return False
    return True


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        # Fail-closed would block every Edit/Write fleet-wide on any stdin hiccup -- too broad
        # a blast radius for what this guard protects against. Fail-open instead: a guard that
        # cannot even read its own input has no target path to check against, so there is
        # nothing concrete to refuse (unlike console_irsyad_guard, which fails closed because
        # ITS payload-read failure means an identity check it cannot safely skip).
        return 0
    if payload.get("tool_name") not in ("Edit", "Write", "NotebookEdit"):
        return 0
    target = _target_path(payload.get("tool_input") or {})
    if target is None or not is_inside_live_checkout(target):
        return 0
    sys.stderr.write(
        f"BLOCKED by live_checkout_edit_guard: {target} is inside the LIVE checkout "
        f"({LIVE_ORCH_DIR}), not a worktree. This repo's own CLAUDE.md rule: create a worktree "
        f"FIRST (`git worktree add ~/wingmen/<name>/wt/<slug> -b <branch> "
        "origin/fable/substrate-safe-fixes`), then make this exact edit there instead -- never "
        "in the live tree, not even to read-then-revert. If you already made other edits in the "
        "live checkout this turn, save them first (`git diff -- <files> > /tmp/x.patch`), revert "
        "(`git checkout -- <files>`), then re-apply the patch inside the new worktree.\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
