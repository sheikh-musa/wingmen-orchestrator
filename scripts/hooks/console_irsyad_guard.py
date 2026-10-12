#!/usr/bin/env python3
"""PreToolUse guard: the console body (Nazim) stays OFF irsyad work — enforced, not remembered.

Musa op#21944 (2026-09-22) + op#22229/22233 (2026-09-24, second relapse): irsyad investigation,
design, code review and data access belong to cc-irsyad-coord + its musa2 lanes. The console's
only irsyad role is a yes/no floor ruling on coord's EVIDENCE PACK, which arrives over the bus
(agent_messages) — so the console never needs the irsyad silos or the ihsanos code.

Scope: blocks ONLY when the claude process itself is the console — ORCH_BODY_ROLE=console AND no
CC_BASE_AGENT_ID. Lanes (coord included) carry CC_BASE_AGENT_ID and are exempt. The hook reads the
claude process env it inherits, so a command that re-sources .env cannot change the verdict
(see reference_lane_vs_console_discriminator_is_cc_base_agent_id).

Exit 2 + stderr = the tool call is refused and the reason is shown to the model.
"""
import json
import os
import re
import shlex
import sys
from pathlib import Path

BLOCKED = [
    (r"goumlynecruxrlmzlntp|GOUMLYNE_", "irsyad silo (goumlyne)"),
    (r"ceayjeamtmcyzzvqflus|IHSANOS_PROD_DATABASE_URL", "ihsanos multi-tenant DB (ceayj)"),
    (r"write_dsn\.env|WRITE_DSN_ALLOWED", "client-silo write DSN"),
    (r"sheikh-musa/ihsanos\b", "ihsanos repo (irsyad code)"),
    (r"wingmen/projects/ihsanos", "ihsanos checkout (irsyad code)"),
]


def is_console() -> bool:
    return os.environ.get("ORCH_BODY_ROLE") == "console" and not os.environ.get("CC_BASE_AGENT_ID")


# --- Narrow, FAIL-CLOSED allow-exception (bus #62942) -----------------------
# The console's one legitimate irsyad touch is a yes/no floor RULING that it posts
# over the bus to cc-irsyad-coord. Coord's exec_prod gate validator (LAYER-VOCAB-001)
# REQUIRES the whole-word store alias/ref in the decision text, so that ruling text
# necessarily names the irsyad store ('goumlyne…') -- which the BLOCKED patterns above
# would otherwise refuse, blocking the exact route the guard itself prescribes.
# This exception allows ONE shape and nothing else: a Bash call that is EXACTLY a
# `scripts/bus_send.py --to cc-irsyad-coord[-N]` post with benign flags, no shell
# chaining, no command substitution/redirect, no second subprocess. Because the only
# program that runs is bus_send.py and there is no operator/substitution, the store ref
# can appear ONLY inside the post's own args (text going to coord) -- never in anything
# that executes against the silo. Anything that does not match returns False and falls
# through to the BLOCKED checks (fail-closed).
# Aligned to the REAL scripts/bus_send.py flags (PR#370 follow-up): --req (NOT the
# fictional --requires-response), plus the benign routing-metadata flags. --from is
# DELIBERATELY excluded: the console must not post under another identity; bus_send
# auto-resolves from_agent fail-closed. A drift test asserts the canonical allowed
# shape actually parses on bus_send.build_parser() so this list can't drift again.
_ALLOWED_BUS_FLAGS = frozenset({
    "--to", "--type", "--subject", "--priority",
    "--body", "--body-file", "--req",
    "--thread", "--reply-to", "--link-ask",
})
# A token that is ENTIRELY these chars is a shell control operator (;, &, &&, |, ||,
# <, >, (, ) and any run of them) -> a second command / redirect -> reject.
_OP_CHARS = frozenset(";&|<>()")
# EXACT program/script allowlist (no basename matching — /tmp/python3 and
# /tmp/x/bus_send.py must NOT pass). Resolved against THIS guard's repo root so an
# absolute `.venv/bin/python3` / `scripts/bus_send.py` invocation is accepted too.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALLOWED_PY = frozenset({str(_REPO_ROOT / ".venv" / "bin" / "python3"), ".venv/bin/python3", "python3"})
_ALLOWED_BUS = frozenset({str(_REPO_ROOT / "scripts" / "bus_send.py"), "scripts/bus_send.py"})


def _is_allowed_coord_bus_post(payload) -> bool:
    if payload.get("tool_name") != "Bash":
        return False
    cmd = (payload.get("tool_input") or {}).get("command")
    if not isinstance(cmd, str) or not cmd.strip():
        return False
    # (b) No newline/CR anywhere -> a multi-line body MUST use --body-file, never an
    # inline newline that could smuggle a second command line past the tokeniser.
    if "\n" in cmd or "\r" in cmd:
        return False
    # (d) Command substitution / backticks are invisible to token inspection; reject on
    # the raw string so a `$(...)`/backtick can never smuggle an executing subcommand.
    if "$(" in cmd or "`" in cmd:
        return False
    # (a) Operator-AWARE tokenise: punctuation_chars splits GLUED operators so
    # "x;whoami" -> ["x", ";", "whoami"] (plain shlex.split keeps it as one token).
    # commenters='' so '#' is literal (never truncates analysis). Fail closed on any
    # tokeniser error (unbalanced quotes etc).
    try:
        lexer = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        toks = list(lexer)
    except ValueError:
        return False
    if len(toks) < 2:
        return False
    # Reject if ANY token is entirely operator chars (catches ; && || | < > ( ) and
    # glued forms broken out by (a): x;whoami, a&&whoami, x|y, x>f, x<f, x&, ...).
    if any(t and all(c in _OP_CHARS for c in t) for t in toks):
        return False
    # (c) EXACT program + script match — NO basename fallback.
    if toks[0] not in _ALLOWED_PY:
        return False
    if toks[1] not in _ALLOWED_BUS:
        return False
    # Every --flag must be in the allowlist; --to must resolve to cc-irsyad-coord[-N].
    rest = toks[2:]
    to_val = None
    for idx, t in enumerate(rest):
        if t.startswith("--"):
            flag, eq, inline = t.partition("=")
            if flag not in _ALLOWED_BUS_FLAGS:
                return False
            if flag == "--to":
                to_val = inline if eq else (rest[idx + 1] if idx + 1 < len(rest) else None)
    if to_val is None or not re.fullmatch(r"cc-irsyad-coord(?:-\d+)?", to_val):
        return False
    return True


def main() -> int:
    if not is_console():
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.stderr.write(
            "BLOCKED by console_irsyad_guard: guard could not read the tool input; refusing "
            "(fail-closed for the console body -- Musa op#21944/#22229, enforce-in-code).\n"
        )
        return 2
    # The one sanctioned irsyad touch: a bus_send ruling to cc-irsyad-coord (bus #62942).
    if _is_allowed_coord_bus_post(payload):
        return 0
    tool_input = payload.get("tool_input") or {}
    haystack = json.dumps(tool_input)
    for pattern, what in BLOCKED:
        if re.search(pattern, haystack):
            sys.stderr.write(
                f"BLOCKED by console_irsyad_guard: this touches the {what}. The console stays off irsyad "
                "(Musa op#21944/#22229). Route it to cc-irsyad-coord via _bus_tmp.post(); if a gate needs "
                "evidence, ask coord for it, never fetch it yourself.\n"
            )
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
