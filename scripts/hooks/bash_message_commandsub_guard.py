#!/usr/bin/env python3
"""PreToolUse guard: refuse a Bash command whose --body/--subject/--title/-m argument
would let the SHELL execute a command substitution before the intended recipient
program (bus_send.py, gh, git commit, ...) ever sees the string.

Near-miss (orch-console bus #83623, after hub bus 83620, ~2026-10-30 07:10Z): a
bus_send.py --body argument was written with a backtick-wrapped command inside
DOUBLE quotes. Bash expands backticks and $(...) inside double quotes before
the string ever reaches the
Python argparse that was supposed to just log it as plain text -- so the install
script's --apply --step actually ran for real (console verified zero effect this
time; it could have been much worse). The same trap class had already hit before
(see memory backtick-in-double-quoted-bash-string-near-miss-sudo) -- console's own
ruling: "memory alone does not stop it," hence this hook.

Scope, deliberately narrow per console's own instruction (do not break legitimate
`$(...)` use OUTSIDE these specific message arguments):
  - Only these flag names are inspected: --body, --subject, --title, -m (covers
    bus_send.py --body/--subject, gh --body/--title, git commit -m).
  - Only THAT flag's own argument value is inspected, not the rest of the command.
  - A SINGLE-quoted value is always allowed, even if it contains a literal backtick
    or `$(` -- single quotes suppress all shell expansion, so there is no hazard.
  - An unquoted or double-quoted value containing a backtick or `$(` is refused --
    unquoted is just as exploitable as double-quoted (the shell expands both), and
    scoping the refusal to the ONLY exploitable quoting styles is what keeps this
    narrow instead of a blanket backtick/`$(` ban across the whole command.

Fleet-wide (every body that has this repo as its project, per the request this
guard answers), registered in .claude/settings.json alongside console_irsyad_guard.py
-- no body carve-out; this hazard is not console-specific (any body can write a
backtick/$(...) into a quoted bus_send/gh/git message by hand).

Exit 2 + stderr = the tool call is refused and the reason (with the fix) is shown
to the model. Exit 0 = allowed.
"""
import json
import re
import sys

_GUARDED_FLAGS = frozenset({"--body", "--subject", "--title", "-m"})

_FIX_MESSAGE = (
    "Point it at a quoted heredoc instead -- e.g. --body-file <(cat <<'EOF'\n...\nEOF\n) "
    "or write the text to a file first and pass --body-file <path> -- or, if the literal "
    "backtick/$(...) text is genuinely meant to be read as plain text rather than run, "
    "single-quote the whole argument ('...') so the shell never expands it."
)

# A GUARDED flag as its own token: not preceded by a non-space character (so
# "--bodyfile" or "x--body" never match), and followed by '=' (inline value),
# whitespace (space-separated value), or end-of-string.
_FLAG_RE = re.compile(r"(?<!\S)(--body|--subject|--title|-m)(?:(=)|(?=\s|$))")


def _is_single_quoted(value: str) -> bool:
    return len(value) >= 2 and value[0] == "'" and value[-1] == "'"


def _has_command_substitution(value: str) -> bool:
    return "`" in value or "$(" in value


def _scan_value(cmd: str, pos: int):
    """Extract the argument value starting at `pos` (right after the flag, or
    right after its '='). Returns (value_text_INCLUDING_any_surrounding_quote_
    chars, end_index). Raises ValueError on an unbalanced quote -- caller fails
    closed. A single/double quoted value absorbs everything up to its own
    unescaped matching quote, including embedded whitespace; an unquoted value
    ends at the next whitespace."""
    n = len(cmd)
    while pos < n and cmd[pos] in " \t":
        pos += 1
    if pos >= n:
        raise ValueError("flag has no value")
    quote = cmd[pos]
    if quote == "'":
        end = cmd.find("'", pos + 1)
        if end == -1:
            raise ValueError("unbalanced single quote")
        return cmd[pos:end + 1], end + 1
    if quote == '"':
        i = pos + 1
        while i < n:
            if cmd[i] == "\\":
                i += 2
                continue
            if cmd[i] == '"':
                return cmd[pos:i + 1], i + 1
            i += 1
        raise ValueError("unbalanced double quote")
    i = pos
    while i < n and cmd[i] not in " \t":
        i += 1
    return cmd[pos:i], i


def _find_dangerous_flag_values(cmd: str):
    """Returns a list of (flag, value) for every GUARDED flag whose value
    contains a backtick or $( and is NOT single-quoted. Raises ValueError if a
    matched flag's value can't be parsed (unbalanced quote) -- caller fails
    closed."""
    findings = []
    for m in _FLAG_RE.finditer(cmd):
        flag, has_inline_eq = m.group(1), m.group(2)
        start = m.end(2) if has_inline_eq else m.end(1)
        value, _end = _scan_value(cmd, start)
        if not _is_single_quoted(value) and _has_command_substitution(value):
            findings.append((flag, value))
    return findings


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        # Fail closed: can't inspect the call, so refuse rather than risk missing
        # the exact hazard this guard exists for.
        sys.stderr.write(
            "BLOCKED by bash_message_commandsub_guard: could not read the tool input; "
            "refusing (fail-closed, bus #83623).\n"
        )
        return 2
    if payload.get("tool_name") != "Bash":
        return 0
    cmd = (payload.get("tool_input") or {}).get("command")
    if not isinstance(cmd, str) or not cmd.strip():
        return 0

    try:
        findings = _find_dangerous_flag_values(cmd)
    except ValueError:
        sys.stderr.write(
            "BLOCKED by bash_message_commandsub_guard: this command's quoting could not be "
            "parsed cleanly, so a --body/--subject/--title/-m command-substitution hazard "
            "can't be ruled out; refusing (fail-closed, bus #83623). " + _FIX_MESSAGE + "\n"
        )
        return 2

    if findings:
        flag, value = findings[0]
        sys.stderr.write(
            f"BLOCKED by bash_message_commandsub_guard: the {flag} argument contains a "
            "backtick or $(...) and is not single-quoted, so bash would execute it as a "
            "real command BEFORE the recipient program ever sees the text -- this is "
            "exactly the shape that ran install_ceayj_provisioner.py --apply --step 3 by "
            f"accident (orch-console bus #83623/83620). Offending argument: {value!r}. "
            + _FIX_MESSAGE + "\n"
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
