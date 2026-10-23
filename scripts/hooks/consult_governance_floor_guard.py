#!/usr/bin/env python3
"""PreToolUse guard: CONSULT the governance-floors SSOT before you ASSERT a floor.

WHY THIS EXISTS (2026-10-15 incident): the console asserted that irsyad money go-live
needs "cai+Nazim" — a STALE floor. The real floor is console-safety-floor + Shuq/Wan
owner-sign, cai-OFF. The correct answer was in memory but was NOT consulted before being
asserted. Memory you must remember is a sentence, not a control
([[feedback_a_control_that_needs_remembering_is_a_sentence]]); so this makes the consult a
control — enforced in code, not by promise
([[feedback_enforce_process_in_code_not_promises]]).

WHAT IT DOES: it watches outbound text (bus rows, operator/Telegram sends, handoff docs)
for FLOOR-ASSERTION language ("go-live floor", "money-sign", "cai+Nazim", "owner-sign",
"requires cai", "console floor", ...). When it sees one, it requires that
`docs/governance-floors.md` was READ/CONSULTED earlier in THIS session. If it was, the
call passes untouched. If it was not, the call is surfaced with a loud reminder that
points at the SSOT — so the assertion cannot leave the session unconsulted.

CONSULTATION is recorded per-session: when this hook sees a Read of (or a bash read of)
docs/governance-floors.md, it drops a session-scoped marker file. Authoring/editing the
SSOT itself also counts (and is never treated as an assertion).

DESIGN TRADEOFF — reminder vs hard block (GOV_FLOOR_GUARD_MODE):
  * "gate"  (DEFAULT): a one-time CONSULT gate. The FIRST floor-assertion in a session that
            has not read the SSOT is refused (exit 2) with a reminder pointing here; once
            you read the doc, the marker is set and every later assertion in the session
            passes freely. It is NOT a permanent block on asserting floors — it blocks only
            "assert before you've looked". This is the safe default: the whole failure mode
            is asserting WITHOUT looking, and the fix (read the doc) clears the gate in one
            step. Because the trigger is heuristic text-matching, a rare false positive is
            cheap: read the SSOT once (you should anyway) and proceed.
  * "warn"  : never blocks (exit 0). On an unconsulted assertion it writes the same reminder
            to stderr (shown in the transcript). Use for a softer rollout where a hard
            one-time gate feels too aggressive — the cost is that nothing STOPS a stale
            assertion, it only nudges, so a mid-task injection could still miss it.
  * "off"   : no-op.
We ship "gate" because the incident was a silent stale assertion reaching the operator; a
nudge that can be missed would not have prevented it. "warn" is the escape hatch if the
heuristic proves noisy in practice.

Exit 2 + stderr = the tool call is refused and the reason is shown to the model.
Exit 0 = allow (optionally with a stderr nudge in "warn" mode).
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

SSOT_REL = "docs/governance-floors.md"
SSOT_BASENAME = "governance-floors.md"

# Floor-assertion language. Case-insensitive. Deliberately narrow — these phrases are how a
# floor/sign-off AUTHORITY gets asserted, not general governance chatter. (The incident
# phrase "cai+Nazim" is included verbatim.)
ASSERTION_PATTERNS = [
    r"go-?live\s+floor",
    r"floor\s+(?:for|to)\s+go-?live",
    r"money[-\s]?sign",
    r"owner[-\s]?sign",
    r"sign-?off\s+authorit",
    r"cai\s*\+\s*nazim",
    r"requires?\s+cai",
    r"console\s+(?:safety[-\s]?)?floor",
    r"go-?live\s+needs",
    r"governance\s+floor",
]
_ASSERTION_RE = re.compile("|".join(ASSERTION_PATTERNS), re.IGNORECASE)


def _mode() -> str:
    return (os.environ.get("GOV_FLOOR_GUARD_MODE") or "gate").strip().lower()


def is_console() -> bool:
    """CONSOLE-ONLY rollout (op#30353, Musa): the 2026-10-15 incident was a console slip,
    so scope the gate to the console body first — avoids false-positive blocks on lanes
    mid-task. Discriminator mirrors console_irsyad_guard.is_console()
    ([[reference_lane_vs_console_discriminator_is_cc_base_agent_id]]): ORCH_BODY_ROLE=console
    AND no CC_BASE_AGENT_ID (a lane, incl. coord, carries CC_BASE_AGENT_ID). Widen to all
    bodies later by deleting this gate once the heuristic proves non-noisy."""
    return os.environ.get("ORCH_BODY_ROLE") == "console" and not os.environ.get("CC_BASE_AGENT_ID")


def _state_dir() -> Path:
    d = os.environ.get("GOV_FLOOR_GUARD_STATE_DIR") or tempfile.gettempdir()
    return Path(d)


def _marker_path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "nosession")
    return _state_dir() / f"gov_floor_consulted_{safe}"


def _mentions_ssot(text: str) -> bool:
    return SSOT_REL in text or SSOT_BASENAME in text


def _is_consultation(tool_name: str, tool_input: dict) -> bool:
    """A Read of the SSOT, a bash read of it, or an Edit/Write TO it = consultation."""
    if tool_name == "Read":
        return _mentions_ssot(str(tool_input.get("file_path", "")))
    if tool_name in ("Edit", "Write", "NotebookEdit"):
        # Authoring/editing the SSOT is both consultation and never an assertion.
        return _mentions_ssot(str(tool_input.get("file_path", "")))
    if tool_name in ("Grep", "Glob"):
        blob = json.dumps(tool_input)
        return _mentions_ssot(blob)
    if tool_name == "Bash":
        return _mentions_ssot(str(tool_input.get("command", "")))
    return False


def _outbound_text(tool_name: str, tool_input: dict) -> str:
    """The text that would leave the session as an assertion. We look at Bash commands
    (tg_send.sh / nazim_send.sh / bus_send.py --body / log_console_msg.sh) and at
    Write/Edit content (a handoff/doc that asserts a floor)."""
    parts = []
    if tool_name == "Bash":
        parts.append(str(tool_input.get("command", "")))
    if tool_name == "Write":
        parts.append(str(tool_input.get("content", "")))
    if tool_name in ("Edit",):
        parts.append(str(tool_input.get("new_string", "")))
    if tool_name == "NotebookEdit":
        parts.append(str(tool_input.get("new_source", "")))
    return "\n".join(parts)


_REMINDER = (
    "CONSULT-BEFORE-ASSERT (consult_governance_floor_guard): this looks like a governance "
    "FLOOR / sign-off assertion, but docs/governance-floors.md has NOT been read in this "
    "session. That file is the SINGLE SOURCE OF TRUTH for who-signs-what and SUPERSEDES "
    "CLAUDE.md / handoff / memory prose. Read docs/governance-floors.md, confirm the floor "
    "against it, then re-issue. (A stale irsyad floor — 'cai+Nazim' — was asserted from "
    "memory on 2026-10-15 without consulting the record; that is why this gate exists.)\n"
)


def main() -> int:
    mode = _mode()
    if mode == "off":
        return 0
    # CONSOLE-ONLY rollout (op#30353): non-console bodies (lanes, coord) pass untouched.
    if not is_console():
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        # Fail-closed only in gate mode: if we cannot read the input we cannot prove a
        # consult happened, so block (consistent with the fleet's fail-closed guards). In
        # warn mode a parse failure must not block, so pass.
        if mode == "gate":
            sys.stderr.write(
                "BLOCKED by consult_governance_floor_guard: could not read tool input; "
                "refusing (fail-closed). Read docs/governance-floors.md and retry.\n"
            )
            return 2
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    session_id = str(payload.get("session_id") or "")
    marker = _marker_path(session_id)

    # 1) Record consultation (read of / edit to the SSOT) and never treat it as an assertion.
    if _is_consultation(tool_name, tool_input):
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("consulted\n")
        except OSError:
            pass
        return 0

    # 2) Already consulted this session? Pass everything.
    if marker.exists():
        return 0

    # 3) Does this call assert a floor?
    text = _outbound_text(tool_name, tool_input)
    if not text or not _ASSERTION_RE.search(text):
        return 0

    # 4) Floor assertion WITHOUT a prior consult.
    if mode == "warn":
        sys.stderr.write(_REMINDER)
        return 0
    # gate (default): refuse this one call with the reminder.
    sys.stderr.write("BLOCKED by " + _REMINDER)
    return 2


if __name__ == "__main__":
    sys.exit(main())
