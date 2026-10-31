#!/usr/bin/env python3
"""session_start_facts.py — the GLOBAL SessionStart hook: fleet facts only.

Installed at the user level (~/.claude/settings.json) so it fires for EVERY
session on the Mini, not just sessions inside the orchestrator checkout
(Memory 2.0 Part 2 PR B, op#36768; Option A approved by console, bus #87068).

Why a separate script instead of widening session_start_reconstitute.py: that
hook carries console reconstitution, and `_body_role()` falls back to the live
checkout's .env where ORCH_BODY_ROLE=console. Run globally, it would have told
any session opened in an unrelated repo "You are Nazim / console body" and
injected the console handoff -- the op#29128 / bus #63372 mixup, everywhere.
This script can never claim an identity: it reads facts and prints them.

Hard limits (console ruling): under 3s, any error or cold DB prints nothing
and exits 0, output is a few hundred tokens scoped to this session.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.lib import boot_facts  # noqa: E402


def main() -> int:
    # Drain the hook payload so the writer never sees a broken pipe. Nothing
    # in it changes the scope: that comes from cwd and CC_CLIENT.
    if not sys.stdin.isatty():
        try:
            sys.stdin.read()
        except OSError:
            pass
    boot_facts.emit(boot_facts.facts_block())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # A hook fault must never block a boot.
        sys.exit(0)
