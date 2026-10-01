#!/usr/bin/env python3
"""PreToolUse guard: block a secret VALUE from entering the transcript (Musa op#24408).

Fleet-wide (user-level ~/.claude/settings.json on both hosts, no body carve-out --
unlike console_irsyad_guard.py, every agent including the hub and cai needs this one).
Bus #48293 -> design (bus #48310) -> APPROVED WITH CHANGES (bus #48312). This is the
revised version; see reports/secrets-transcript-leak-prevention-design-op24408.md for
the full writeup of why the rule is shaped this way.

Two separate rule sets, because orch-console drew this line explicitly (bus #48312):

  Rule A -- SECRET FILES (unconditional, no sink exception): there is no safe way to
  cat/head/tail/less/sed -n/awk a whole secrets file, or Read-tool it, so these are
  blocked outright regardless of what follows in a pipeline. SOURCING one (`. file`,
  `source file`, a dotenv load) is USE, not PRINT, and stays allowed -- it never writes
  the file's content to stdout. `grep -o` against a name-only pattern is allowed (it
  can only ever produce a variable NAME, never a value).

  Rule B -- SECRET VALUES IN A COMMAND (sink-gated): a named secret env var ($DATABASE_URL
  etc.) passed to psql/python/a script/another program is USE and stays allowed; the
  same var as an argument to a PRINT-shaped command (echo/printf/print/tee/logger), or
  a bare environment-DUMP command (ps e*, /proc/*/environ, env, printenv, bare `set`,
  `export -p`, `tmux show-environment`), is blocked UNLESS the pipeline reaches a
  hash/length sink (shasum/sha*sum/md5(sum)?/openssl dgst/wc -c) with only cut/head/
  tr/awk trimming the hash afterward.

Exit 2 + stderr = refused, the reason is shown to the model (same contract as the
irsyad guard). Fail-closed on unparseable input.
"""
from __future__ import annotations

import json
import re
import shlex
import sys

BLOCK_MESSAGE = "secret would enter the transcript -- hash it or use the value without printing."

# ---- Rule A: known secret files -------------------------------------------------

SECRET_FILE_PATTERNS = [
    r"(^|/)\.env$",
    r"write_dsn\.env",
    r"bayanqa\.env",
    r"[\w-]*-oauth-token(\.[\w-]+)?$",
    r"(^|/)[\w-]*_key$",
    r"nric-key",
    r"cookies",
    r"wingmen_personal.*\.json",
    r"gzb_to_mini$",  # the private half only -- gzb_to_mini.pub is public, not a secret
    r"zahidah_panopto_session\.txt",
]
SECRET_FILE_RE = re.compile("|".join(SECRET_FILE_PATTERNS))

FILE_PRINT_LEADING_RE = re.compile(r"^(cat|head|tail|less|more)\b")
SED_PRINT_LEADING_RE = re.compile(r"^sed\b(?!.*-n)")  # sed without -n prints every line by default
AWK_PRINT_LEADING_RE = re.compile(r"^awk\b")
GREP_LEADING_RE = re.compile(r"^grep\b")

# top-level statement/pipeline splitting shared by Rule A and Rule B. Not a full shell
# parser (doesn't track quoting/subshell nesting) -- a known, documented limitation;
# see the design doc's "residual gap" notes and the false-positive replay report.
STATEMENT_SPLIT_RE = re.compile(r"\n|;|&&|\|\|")
ASSIGNMENT_PREFIX_RE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+")


def _split_statements(command: str) -> list[str]:
    return [s.strip() for s in STATEMENT_SPLIT_RE.split(command) if s.strip()]


def _split_pipeline(statement: str) -> list[str]:
    return [seg.strip() for seg in statement.split("|")]


def _leading_command(segment: str) -> str:
    """Segment with any leading VAR=val assignments stripped, so `FOO=bar cat x`
    still resolves to `cat` as the operative command."""
    return ASSIGNMENT_PREFIX_RE.sub("", segment.strip())


def _is_secret_file(token: str) -> bool:
    return bool(SECRET_FILE_RE.search(token))


def _segment_secret_file_token(segment: str) -> str | None:
    try:
        tokens = shlex.split(segment)
    except ValueError:
        tokens = segment.split()
    for token in tokens:
        if _is_secret_file(token):
            return token
    return None


def _is_source_or_dotenv(segment: str) -> bool:
    # `. file`, `source file` -- sourcing is USE, not PRINT; never writes the file's
    # content to stdout. (Already its own statement by the time this is checked.)
    if re.match(r"(\.|source)\s+\S*\.env\b", segment.strip()):
        return True
    if re.search(r"load_dotenv\s*\(", segment):
        return True
    return False


NAME_CHARSET_CLASS_RE = re.compile(r"\[A-Za-z0-9_\]|\[A-Z0-9_\]|\[A-Z_\]|\[a-z_\]")


def _is_name_only_grep(segment: str) -> bool:
    # grep -o '<name-shape pattern>' file -- allowed only when the pattern can only
    # ever capture a variable NAME, never a value.
    m = re.search(r"grep\s+(?:-\w+\s+)*-o\w*\s+(?:-\w+\s+)*'([^']*)'", segment) or \
        re.search(r'grep\s+(?:-\w+\s+)*-o\w*\s+(?:-\w+\s+)*"([^"]*)"', segment)
    if not m:
        return False
    pattern = m.group(1)

    # case 1: anchored name-charset pattern, optionally ending in a single trailing
    # '=' (captures "NAME=" from a .env line -- never the value after it; a '=' with
    # more pattern after it, e.g. `^[A-Z_]+=.*`, is NOT name-only and falls through).
    if pattern.startswith("^") and "[A-Z_" in pattern:
        eq_count = pattern.count("=")
        if eq_count == 0:
            return True
        if eq_count == 1 and pattern.endswith("="):
            return True

    # case 2: whether anchored or not, the pattern can ONLY ever match/extract
    # identifier-shaped text -- a literal variable name (e.g. `GOUMLYNE_RO_DSN`,
    # which under -o can only ever echo back that same literal), or a name-charset
    # class ([A-Z_]* etc) with a literal prefix (e.g. `GOUMLYNE[A-Z_]*`) -- never a
    # wildcard or charset reaching into actual value characters (lowercase outside
    # the class, punctuation like :/@=, '.' as a wildcard).
    p = pattern[1:] if pattern.startswith("^") else pattern
    p = NAME_CHARSET_CLASS_RE.sub("", p)
    p = re.sub(r"[*+]", "", p)
    return bool(re.fullmatch(r"[A-Za-z0-9_]*", p))


GREP_SAFE_OUTPUT_FLAGS = {"l", "L", "c"}  # files-with-matches / files-without-match / count -- never print line content


def _grep_has_safe_output_mode(segment: str) -> bool:
    try:
        tokens = shlex.split(segment)
    except ValueError:
        tokens = segment.split()
    for tok in tokens:
        if tok in ("--files-with-matches", "--files-without-match", "--count"):
            return True
        if tok.startswith("--"):
            continue
        if tok.startswith("-") and len(tok) > 1 and (set(tok[1:]) & GREP_SAFE_OUTPUT_FLAGS):
            return True
    return False


def check_rule_a(command: str) -> str | None:
    for statement in _split_statements(command):
        if _is_source_or_dotenv(statement):
            continue
        for segment in _split_pipeline(statement):
            target = _segment_secret_file_token(segment)
            if not target:
                continue
            lead = _leading_command(segment)
            if GREP_LEADING_RE.match(lead):
                if _is_name_only_grep(segment) or _grep_has_safe_output_mode(segment):
                    continue
                return f"grep on secret file {target!r} without a name-only -o pattern or a content-safe output mode (-l/-L/-c)"
            if FILE_PRINT_LEADING_RE.match(lead) or SED_PRINT_LEADING_RE.match(lead) or AWK_PRINT_LEADING_RE.match(lead):
                return f"prints the contents of secret file {target!r}"
    return None


# ---- Rule C: direct invocation of the key / its fetch wrappers, by name ---------
# Unconditional, no sink exception -- this is the LOCK 1 residual gap (bus #48310):
# even with the key root-locked on gzb, the narrow sudo wrapper is still invocable
# directly by an agent shell, so it's blocked here at the command-shape layer too.

DIRECT_KEY_INVOCATION_RE = re.compile(
    r"\bgzb_to_mini\b|\bfetch-secrets-ssh(\.sh)?\b|\bserve-secrets-bundle(\.sh)?\b"
)


def check_rule_c(command: str) -> str | None:
    m = DIRECT_KEY_INVOCATION_RE.search(command)
    if m:
        return f"directly invokes the secrets-fetch key/wrapper ({m.group(0)!r})"
    return None


# ---- Rule B: secret values in a command -----------------------------------------

SENSITIVE_VAR_RE = re.compile(
    r"\$\{?(DATABASE_URL|WRITE_DSN|\w*_DSN|\w*_TOKEN|\w*_KEY|\w*SECRET\w*|\w*PASSWORD\w*|"
    r"GOUMLYNE_\w*|API_KEY\w*)\b\}?"
)

PRINT_LEADING_RE = re.compile(r"^(echo|printf|print|tee|logger)\b")
PS_DUMP_LEADING_RE = re.compile(r"^ps\s+e\w*\b")
PRINTENV_LEADING_RE = re.compile(r"^printenv\b")
EXPORT_DUMP_LEADING_RE = re.compile(r"^export\s+-p\b")
TMUX_DUMP_LEADING_RE = re.compile(r"^tmux\s+show-environment\b")
PROC_ENVIRON_RE = re.compile(r"/proc/[0-9A-Za-z$*]+/environ")  # usually an ARGUMENT (e.g. to cat), not leading

HASH_SINK_RE = re.compile(r"\b(shasum|sha1sum|sha256sum|sha512sum|md5sum|md5|openssl\s+dgst|wc\s+-c)\b")

# a sed substitution whose REPLACEMENT is a fixed masking token (***, or literally
# "redact[ed]") rather than anything derived from the matched text -- observed twice
# in the real-transcript replay (`s/:[^:@]+@/:***@/` masking a DSN password,
# `s/=.*/=<redacted>/` masking a value after its name) as an established idiom with
# the same safety property as a hash sink: the raw secret substring never reaches
# output. Not in orch-console's original hash/length-sink list (bus #48312 #3) --
# called out explicitly in the replay report as a judgment call, not hidden.
SED_MASK_SINK_RE = re.compile(r"\bsed\b.*\bs[/#|].*[/#|].*(redact|\*\*\*)", re.IGNORECASE)

SAFE_AFTER_SINK_RE = re.compile(r"^(cut|head|tr|awk)\b")


def _is_sink(segment: str) -> bool:
    return bool(HASH_SINK_RE.search(segment)) or bool(SED_MASK_SINK_RE.search(segment))


def _segment_dump_kind(segment: str, lead: str) -> bool:
    """True if this PIPELINE SEGMENT, by its LEADING command, is an environment dump.
    Leading-command-position matching (not bare substring search) is what keeps this
    from firing on e.g. `echo "--- env ---"` -- the word "env" there is an echo
    ARGUMENT, never the segment's own command."""
    if PS_DUMP_LEADING_RE.match(lead) or PRINTENV_LEADING_RE.match(lead):
        return True
    if EXPORT_DUMP_LEADING_RE.match(lead) or TMUX_DUMP_LEADING_RE.match(lead):
        return True
    if lead == "env" or lead == "set":
        return True
    if PROC_ENVIRON_RE.search(segment):
        return True
    return False


def _segment_prints_secret(segment: str, lead: str) -> bool:
    # the sensitive-var reference must be IN THIS SEGMENT, not merely somewhere else
    # in a longer multi-stage command (that cross-segment correlation was the source
    # of false positives like `curl -H "apikey: $X" | python3 -c "print(y)"`).
    return bool(PRINT_LEADING_RE.match(lead)) and bool(SENSITIVE_VAR_RE.search(segment))


def check_rule_b(command: str) -> str | None:
    # evaluated per STATEMENT (split on top-level ;, &&, ||, newline) so an unrelated
    # later statement in a multi-statement command can never be blamed for an earlier
    # one's trigger, and per PIPELINE SEGMENT within a statement so a trigger must
    # share a segment with its own hash-sink resolution, not borrow one from elsewhere.
    for statement in _split_statements(command):
        segments = _split_pipeline(statement)
        trigger_index = None
        trigger_kind = None
        for i, seg in enumerate(segments):
            lead = _leading_command(seg)
            if _segment_dump_kind(seg, lead):
                trigger_index, trigger_kind = i, "dumps the environment"
                break
            if _segment_prints_secret(seg, lead):
                trigger_index, trigger_kind = i, "prints a secret env var"
                break
        if trigger_index is None:
            continue  # no trigger in this statement -- plain USE (psql/python/a script) is allowed

        sink_index = None
        for i in range(trigger_index, len(segments)):
            if _is_sink(segments[i]):
                sink_index = i
                break
        if sink_index is None:
            return f"{trigger_kind} with no hash/length sink in the pipeline"

        for seg in segments[sink_index + 1:]:
            if not SAFE_AFTER_SINK_RE.match(_leading_command(seg)):
                return "has a hash sink but keeps processing the raw value afterward (not just trimming the hash)"

    return None  # every triggering statement, if any, resolved safely through a sink


# ---- entrypoint -------------------------------------------------------------------

def _command_text(tool_name: str, tool_input: dict) -> str | None:
    if tool_name == "Bash":
        return tool_input.get("command") or ""
    if tool_name in ("Read", "NotebookEdit"):
        return tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    if tool_name == "Grep":
        return json.dumps(tool_input)
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.stderr.write(
            "BLOCKED by secrets_transcript_guard: could not read the tool input; "
            "refusing fail-closed (Musa op#24408).\n"
        )
        return 2

    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    text = _command_text(tool_name, tool_input)
    if not text:
        return 0

    if tool_name == "Read":
        if _is_secret_file(text):
            sys.stderr.write(f"BLOCKED by secrets_transcript_guard: {BLOCK_MESSAGE}\n")
            return 2
        return 0

    reason = check_rule_a(text) or check_rule_c(text) or check_rule_b(text)
    if reason:
        sys.stderr.write(f"BLOCKED by secrets_transcript_guard: {BLOCK_MESSAGE} ({reason})\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
