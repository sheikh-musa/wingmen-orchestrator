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
  - ONE narrow exception (orch-console bus #83846/#83852, 2026-10-30; the
    bypass fix is bus #83877/#83878): a value that is EXACTLY
    `$(cat <<'DELIM' ... DELIM)` -- a single-quoted heredoc delimiter, `cat`
    with no other arguments, nothing else inside the parens -- is allowed
    even though it contains `$(`. This is the standard fleet-wide pattern for
    a multi-line git commit / bus_send message (every lane's own commit
    instructions use it). It is safe because a SINGLE-QUOTED heredoc
    delimiter suppresses ALL expansion inside the body, exactly like single
    quotes -- but ONLY up to the point where bash itself would end the
    heredoc, i.e. the FIRST line equal to the delimiter; the checker parses
    it that way (not with a backtracking regex that could match a LATER
    occurrence of the delimiter and wrongly call a live trailing
    substitution "inert body"). An UNQUOTED delimiter (`<<EOF` instead of
    `<<'EOF'`), a body line equal to the delimiter followed by more content,
    or any extra text inside the parens besides `cat <<'DELIM' ... DELIM`,
    does NOT match this exception and stays refused.

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


def _has_command_substitution(value: str) -> bool:
    return "`" in value or "$(" in value


# The one narrow allow (bus #83846/#83852), FIXED after a confirmed bypass
# (cc-quality bus #83877/#83878, 2026-10-30): the ENTIRE segment is exactly
# `$(cat <<'DELIM' ... DELIM)` (or `<<-'DELIM'`) -- single-quoted delimiter,
# `cat` with no other args, nothing else inside the parens.
#
# The FIRST cut used one `.*?` (non-greedy) + `\1` backref regex to find the
# body/terminator boundary. That is NOT how bash parses a heredoc: bash ends
# the heredoc at the FIRST line equal to the delimiter, full stop. A single
# backtracking regex instead searches for *some* delimiter line that lets the
# rest of the pattern match -- so a body containing its OWN delimiter line
# (e.g. `$(cat <<'EOF'\nEOF\n)$(id)\nEOF\n)`) let the regex skip past the
# real (first, empty-body-terminating) `EOF` and match a LATER one, treating
# a live trailing `)$(id)` as inert body text. Grounded against real bash by
# cc-quality: the embedded substitution executed. Fix: parse it the way bash
# does, line by line, not with backtracking -- see
# `_is_safe_heredoc_cat_substitution` below.
_HEREDOC_CAT_OPEN_RE = re.compile(
    r"^\s*\$\(\s*cat\s+<<(-?)\s*'([^'\n]+)'[ \t]*\n"
)


def _is_safe_heredoc_cat_substitution(text: str) -> bool:
    """True iff `text` (one full shell-word segment) is EXACTLY
    `$(cat <<[-]'DELIM' ... DELIM)` and nothing more, parsed the way bash
    itself terminates a heredoc:

    - the body ends at the FIRST line that reads as the delimiter once
      TRAILING whitespace is stripped (cc-quality bus #83914, grounded
      against real /bin/bash 3.2.57: a delimiter line with a trailing space
      still terminates the heredoc there, not at a later clean occurrence --
      the exact-match version of this check let a guard-refused injection
      through by one space). For the `<<-` dash form, LEADING tabs are also
      stripped first -- bash strips leading tabs for `<<-`, nothing else for
      plain `<<`, and never anything trailing-side beyond this.
    - nothing may follow that terminator line except optional whitespace and
      the closing `)`, then end of value -- any content in between (another
      line, a second delimiter, a trailing substitution) refuses.
    - defense in depth across bash/shell versions (console bus #83924): if
      any OTHER line, under a looser FULL (leading+trailing) whitespace
      strip, also reads as the delimiter, a different shell could plausibly
      terminate the heredoc there instead of at the chosen line -- that
      ambiguity refuses rather than being interpreted, even when it is not a
      hazard under the specific bash this fleet runs.

    An unquoted delimiter, extra args to `cat`, no terminator line at all, or
    any text inside the parens besides `cat <<'DELIM' ... DELIM` all fail to
    match and fall through to the normal backtick/$(...) refusal."""
    m = _HEREDOC_CAT_OPEN_RE.match(text)
    if not m:
        return False
    dash, delim = m.group(1), m.group(2)
    lines = text[m.end():].split("\n")

    def _terminator_candidate(line: str) -> str:
        stripped = line.lstrip("\t") if dash else line
        return stripped.rstrip(" \t")

    term_idx = None
    for i, line in enumerate(lines):
        if _terminator_candidate(line) == delim:
            term_idx = i
            break
    if term_idx is None:
        return False

    for i, line in enumerate(lines):
        if i != term_idx and line.strip(" \t") == delim:
            return False

    tail = "\n".join(lines[term_idx + 1:])
    return bool(re.match(r"^\s*\)\s*$", tail))


def _scan_word(cmd: str, pos: int):
    """Extract a full shell WORD starting at `pos` (right after the flag, or
    right after its '='), as the CONCATENATION of adjacent quoted/unquoted
    segments bash would glue into one word -- e.g. "x"`cmd` is ONE word, not
    a safely-quoted value followed by stray trailing text (cc-quality PR#657
    review, Finding A: the earlier single-segment scan missed exactly this
    adjacent-concatenation shape, a confirmed bypass grounded against real
    bash). Returns (segments, end_index) where segments is a list of
    (kind, text_WITHOUT_surrounding_quote_chars) and kind is one of 'single'
    (quotes suppress all expansion -- always safe), 'double', or 'unquoted'
    (both expand backtick/$(...) -- checked by the caller). Raises ValueError
    on an unbalanced quote -- caller fails closed.

    An UNQUOTED newline, ';', '&' or '|' ends the shell word exactly like a
    space does (bash control-operator/word-terminator semantics) -- without
    this, a clean quoted flag value followed by a newline and an unquoted
    NEXT command on the next line gets glued into one giant word, and that
    next command's own content (e.g. a legitimate heredoc-cat) gets scanned
    as if it were part of THIS flag's value (orch-console bus #84048, a
    false-positive refusal of a valid two-command script)."""
    _TERMINATORS = " \t\n;&|"
    n = len(cmd)
    while pos < n and cmd[pos] in " \t":
        pos += 1
    if pos >= n:
        raise ValueError("flag has no value")
    segments = []
    while pos < n and cmd[pos] not in _TERMINATORS:
        ch = cmd[pos]
        if ch == "'":
            end = cmd.find("'", pos + 1)
            if end == -1:
                raise ValueError("unbalanced single quote")
            segments.append(("single", cmd[pos + 1:end]))
            pos = end + 1
        elif ch == '"':
            i = pos + 1
            seg_start = i
            while True:
                if i >= n:
                    raise ValueError("unbalanced double quote")
                if cmd[i] == "\\":
                    i += 2
                    continue
                if cmd[i] == '"':
                    break
                i += 1
            segments.append(("double", cmd[seg_start:i]))
            pos = i + 1
        else:
            start = pos
            while pos < n and cmd[pos] not in _TERMINATORS and cmd[pos] not in "'\"":
                pos += 1
            segments.append(("unquoted", cmd[start:pos]))
    return segments, pos


def _find_dangerous_flag_values(cmd: str):
    """Returns a list of (flag, raw_value) for every GUARDED flag whose full
    shell WORD (the concatenation of its adjacent quoted/unquoted segments)
    contains a backtick or $( in any NON-single-quoted segment -- a
    single-quoted segment is always safe even when concatenated next to a
    dangerous one, since single quotes still suppress expansion in their own
    span. Raises ValueError if a matched flag's value can't be parsed
    (unbalanced quote) -- caller fails closed."""
    findings = []
    for m in _FLAG_RE.finditer(cmd):
        flag, has_inline_eq = m.group(1), m.group(2)
        start = m.end(2) if has_inline_eq else m.end(1)
        value_start = start
        while value_start < len(cmd) and cmd[value_start] in " \t":
            value_start += 1
        segments, end = _scan_word(cmd, start)
        dangerous = any(
            kind != "single"
            and _has_command_substitution(text)
            and not _is_safe_heredoc_cat_substitution(text)
            for kind, text in segments
        )
        if dangerous:
            findings.append((flag, cmd[value_start:end]))
    return findings


def main() -> int:
    try:
        return _main_inner()
    except Exception:
        # Fail closed (cc-quality PR#657 review, Finding B): check (4) requires
        # that a hook exception REFUSES, not allows -- a bare `sys.exit(main())`
        # with no outer guard would let any unanticipated exception propagate
        # out of main() entirely, exit the process with code 1 (not 2), and a
        # PreToolUse hook exit of 1 is a non-blocking error -- the tool call
        # PROCEEDS. This outer catch-all is the one-line fix: whatever goes
        # wrong, refuse rather than silently fail open.
        sys.stderr.write(
            "BLOCKED by bash_message_commandsub_guard: unexpected error while checking "
            "this command; refusing rather than risk a silent fail-open (fail-closed).\n"
        )
        return 2


def _main_inner() -> int:
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
