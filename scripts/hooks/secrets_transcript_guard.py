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

  Rule D -- LANE-SCOPED human-owner cloud login / IAM mutation block (bus #48386, added
  after a real incident 2026-10-01 18:14Z: a lane reached Musa's OWNER gcloud login,
  shared via the OS user, and ran an IAM-mutating command on a client prod project).
  Scoped to LANES only (CC_BASE_AGENT_ID set in the hook's own environment) -- the
  console/hub legitimately uses human-owner cloud logins for operator-authorized work;
  a lane never should. Blocks: a gcloud/firebase command naming or switching to a
  human account (anything with '@' not ending in .gserviceaccount.com); any IAM/policy
  mutation subcommand (add-iam-policy-binding, remove-iam-policy-binding, set-iam-policy,
  projects create/delete, services disable) unconditionally for a lane, any account; and
  print-access-token unless it explicitly names a service account (fail-closed on a
  bare print-access-token with no --account -- the incident's own shape); and
  `secrets versions access` unless it explicitly names a service account (cc-quality
  bus #48466 MEDIUM -- reading a client prod secret via the shared owner login is the
  same trust boundary the incident exposed). Tolerates a leading `sudo`/absolute-path/
  `command` prefix (bus #48466 LOW).

  Rule D is a STOPGAP for these enumerated dangerous shapes, not a general "a lane
  can't reach the owner cloud login" control -- e.g. `gcloud storage rm` on a client
  bucket still isn't caught. The durable fix is OS-level credential isolation (per-lane
  CLOUDSDK_CONFIG with no human ADC reachable from a lane's OS user), tracked as
  separate work (bus #48469) -- do not treat that work as superseded by Rule D.

Exit 2 + stderr = refused, the reason is shown to the model (same contract as the
irsyad guard). Fail-closed on unparseable input.

Tool-path coverage (bus #48639/#48642, real incident 2026-10-01 14:37Z): a secret can
enter the transcript through the Edit/MultiEdit/Write/Read/NotebookEdit tool_result
snippet, not just Bash stdout -- an Edit on `.env` echoes the surrounding file content
back into the transcript the same as a `cat` would. These five tools are PATH-ONLY:
the tool_input is just a file path (plus, for Edit/MultiEdit, the replacement text,
which is the AGENT's own new content, not a leak of what's already on disk -- the
leak risk is specifically the snippet of EXISTING content the tool echoes back).
Blocked unconditionally -- no sink exception, same as Rule A -- whenever the target
path is a secret path: the existing Rule A filename patterns, OR a path under one of
SECRET_DIR_PREFIXES (/dev/shm/wingmen-secrets/, ~/.wingmen/private/, ~/.wingmen/keys/,
~/.ssh/), OR a client-credential-shaped filename (service-account JSON). The sanctioned
way to change one key in a .env-shaped file is scripts/env_set.sh (reads the new value
from stdin, edits by key name, prints only a sha1 fingerprint -- never the value).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys

BLOCK_MESSAGE = "secret would enter the transcript -- hash it or use the value without printing."
TOOL_SECRET_FILE_MESSAGE = (
    "this path is a secret file -- Read/Edit/MultiEdit/Write/NotebookEdit on it would put "
    "its contents into the transcript. For a .env-shaped file, change one key without "
    "echoing via `scripts/env_set.sh <file> <KEY>` (reads the new value from stdin). For a "
    "key/credential file (ssh keys, service-account JSON, the wingmen private store), make "
    "the change outside the agent's tool loop."
)

# ---- Rule A: known secret files -------------------------------------------------

SECRET_FILE_PATTERNS = [
    r"(^|/)\.env(\.[\w-]+)?$",
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
# cc-quality PR#245 review (bus #48441 MEDIUM #1): these also dump a whole file's bytes
# to the transcript and were previously unguarded for non-DSN secret files (the DSN
# alone is backstopped by secrets_output_scanner's postgres-dsn pattern; a non-DSN
# secret file read this way was not).
BINARY_DUMP_LEADING_RE = re.compile(r"^(base64|strings|xxd|od|hexdump|dd|nl|tac|rev)\b")
PYTHON_LEADING_RE = re.compile(r"^python3?\b")
PYTHON_OPEN_FILE_RE = re.compile(r"open\(\s*['\"]([^'\"]+)['\"]")

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


# bus #48639/#48642: path-prefix coverage for the PATH-ONLY tools (Read/Edit/MultiEdit/
# Write/NotebookEdit), broader than SECRET_FILE_RE's filename patterns since these tools
# take a real filesystem path, not shell text to re-parse -- a whole directory can be
# named without the shell-tokenizing complexity Rule A's Bash matching needs.
SECRET_DIR_PREFIXES = tuple(
    os.path.expanduser(p) for p in (
        "/dev/shm/wingmen-secrets/",
        "~/.wingmen/private/",
        "~/.wingmen/keys/",
        "~/.ssh/",
    )
)
CLIENT_CRED_FILE_RE = re.compile(r"service[-_]?account[\w.-]*\.json$|[\w-]*-sa\.json$", re.IGNORECASE)

# found via synthetic tool-path replay (not the real 1200 corpus, which is Bash-text
# only): os.path.expanduser("~/.ssh/") resolves against THIS PROCESS's own $HOME, so the
# tilde-prefix check above only ever protects the current user's own .ssh dir -- it
# never catches e.g. /root/.ssh/ (exactly where LOCK 1 relocates gzb_to_mini) or another
# user's home when the hook's process home differs from the path's owner. Generic
# path-component match closes that regardless of whose home it is.
SSH_DIR_COMPONENT_RE = re.compile(r"(^|/)\.ssh/")


def _is_secret_path(path: str) -> bool:
    if not path:
        return False
    expanded = os.path.expanduser(path)
    if _is_secret_file(path) or _is_secret_file(expanded):
        return True
    if CLIENT_CRED_FILE_RE.search(path):
        return True
    if SSH_DIR_COMPONENT_RE.search(path) or SSH_DIR_COMPONENT_RE.search(expanded):
        return True
    return any(expanded.startswith(prefix) for prefix in SECRET_DIR_PREFIXES)


def _segment_secret_file_token(segment: str) -> str | None:
    try:
        tokens = shlex.split(segment)
    except ValueError:
        tokens = segment.split()
    for token in tokens:
        if _is_secret_file(token):
            return token
        # cc-quality PR#245 re-review (bus #48499 LOW): `dd if=.env` -- the operand is
        # one shlex token ("if=.env"), so the anchored `(^|/)\.env$` pattern never sees
        # a bare ".env" start-of-token; check the value half of a key=value operand too.
        if "=" in token:
            value = token.split("=", 1)[1]
            if value and _is_secret_file(value):
                return value
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


def _segment_python_open_secret_file(segment: str, lead: str) -> str | None:
    # `python3 -c "print(open('.env').read())"` -- the filename is a substring of a
    # single shlex token (the quoted -c script), so the token-based secret-file check
    # below never sees it as its own token; extracted separately here.
    if not PYTHON_LEADING_RE.match(lead) or "print" not in segment:
        return None
    m = PYTHON_OPEN_FILE_RE.search(segment)
    if not m:
        return None
    path = m.group(1)
    return path if _is_secret_file(path) else None


def _pipeline_resolves_through_sink(segments: list[str], trigger_index: int) -> bool:
    # mirrors Rule B's sink resolution (defined below) -- a grep match line on a secret
    # file is as safe as an env-dump once it has gone through a hash/sed-mask sink with
    # nothing but trimming after it. Module-level forward reference: resolved at call
    # time (check_rule_a runs from main(), after the whole module has loaded).
    sink_index = None
    for i in range(trigger_index, len(segments)):
        if _is_sink(segments[i]):
            sink_index = i
            break
    if sink_index is None:
        return False
    return all(SAFE_AFTER_SINK_RE.match(_leading_command(seg)) for seg in segments[sink_index + 1:])


def check_rule_a(command: str) -> str | None:
    for statement in _split_statements(command):
        if _is_source_or_dotenv(statement):
            continue
        segments = _split_pipeline(statement)
        for i, segment in enumerate(segments):
            lead = _leading_command(segment)
            py_target = _segment_python_open_secret_file(segment, lead)
            if py_target:
                return f"python open()+print of secret file {py_target!r}"
            target = _segment_secret_file_token(segment)
            if not target:
                continue
            if GREP_LEADING_RE.match(lead):
                if _is_name_only_grep(segment) or _grep_has_safe_output_mode(segment):
                    continue
                if _pipeline_resolves_through_sink(segments, i):
                    continue
                return f"grep on secret file {target!r} without a name-only -o pattern or a content-safe output mode (-l/-L/-c)"
            if (FILE_PRINT_LEADING_RE.match(lead) or SED_PRINT_LEADING_RE.match(lead)
                    or AWK_PRINT_LEADING_RE.match(lead) or BINARY_DUMP_LEADING_RE.match(lead)):
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


# ---- Rule D: lane-scoped human-owner cloud login / IAM mutation block (bus #48386) -

CLOUD_CLI_LEADING_RE = re.compile(r"^(gcloud|firebase)\b")
# cc-quality PR#245 Rule D re-review (bus #48466 LOW): `sudo gcloud ...` / absolute-path
# `/usr/bin/gcloud ...` / `command gcloud ...` all slipped the leading-command match.
CLOUD_CLI_PREFIX_STRIP_RE = re.compile(
    r"^(?:sudo\s+|command\s+|/usr/(?:local/)?bin/|/opt/homebrew/bin/)+"
)
IAM_MUTATION_SUBCOMMAND_RE = re.compile(
    r"\b(add-iam-policy-binding|remove-iam-policy-binding|set-iam-policy|"
    r"projects\s+(create|delete)|services\s+disable)\b"
)
ACCOUNT_SWITCH_RE = re.compile(
    r"\bgcloud\s+config\s+set\s+account\s+(\S+)|\bgcloud\s+auth\s+login\s+(\S+)"
)
PRINT_ACCESS_TOKEN_RE = re.compile(r"\bprint-access-token\b")
# cc-quality PR#245 Rule D re-review (bus #48466 MEDIUM, "the sharp edge"): a lane
# reading a CLIENT PROD SECRET via the shared owner login is exactly the trust
# boundary the 2026-10-01 incident exposed. In-scope for a secrets-leak-prevention
# control (op#24408), unlike generic destructive ops (gcloud storage rm) which
# orch-console scoped to the separate OS-level credential-isolation PR (bus #48469).
SECRET_VERSION_ACCESS_RE = re.compile(r"\bsecrets\s+versions\s+access\b")
OWNER_CLOUD_ACTION_MESSAGE = "owner-level cloud action: send the exact command to orch-console"


def _is_human_account(value: str) -> bool:
    value = value.strip().strip("'\"")
    return "@" in value and not value.endswith(".gserviceaccount.com")


def _account_flag_value(segment: str) -> str | None:
    m = re.search(r"--account=(\S+)", segment) or re.search(r"--account\s+(\S+)", segment)
    return m.group(1) if m else None


def check_rule_d(command: str) -> str | None:
    if not os.environ.get("CC_BASE_AGENT_ID"):
        return None  # console/hub -- human-owner cloud logins are legitimately theirs
    for statement in _split_statements(command):
        for segment in _split_pipeline(statement):
            lead = CLOUD_CLI_PREFIX_STRIP_RE.sub("", _leading_command(segment))
            if not CLOUD_CLI_LEADING_RE.match(lead):
                continue

            acct = _account_flag_value(segment)
            if acct and _is_human_account(acct):
                return OWNER_CLOUD_ACTION_MESSAGE

            m = ACCOUNT_SWITCH_RE.search(segment)
            if m:
                switched = m.group(1) or m.group(2)
                if _is_human_account(switched):
                    return OWNER_CLOUD_ACTION_MESSAGE

            if IAM_MUTATION_SUBCOMMAND_RE.search(segment):
                return OWNER_CLOUD_ACTION_MESSAGE

            if PRINT_ACCESS_TOKEN_RE.search(segment):
                if not acct or _is_human_account(acct):
                    return OWNER_CLOUD_ACTION_MESSAGE

            if SECRET_VERSION_ACCESS_RE.search(segment):
                if not acct or _is_human_account(acct):
                    return OWNER_CLOUD_ACTION_MESSAGE
    return None


# ---- Rule B: secret values in a command -----------------------------------------

SENSITIVE_VAR_RE = re.compile(
    r"\$\{?(DATABASE_URL|WRITE_DSN|\w*_DSN|\w*_TOKEN|\w*_KEY|\w*SECRET\w*|\w*PASSWORD\w*|"
    r"GOUMLYNE_\w*|API_KEY\w*)\b\}?"
)
# same name alternation, bare (no $ / braces) -- for matching a NAME string literal
# inside os.environ['NAME'] / ENVIRON["NAME"], not a shell variable reference.
SENSITIVE_VAR_NAME_RE = re.compile(
    r"^(DATABASE_URL|WRITE_DSN|\w*_DSN|\w*_TOKEN|\w*_KEY|\w*SECRET\w*|\w*PASSWORD\w*|"
    r"GOUMLYNE_\w*|API_KEY\w*)$"
)

PRINT_LEADING_RE = re.compile(r"^(echo|printf|print|tee|logger)\b")
PS_DUMP_LEADING_RE = re.compile(r"^ps\s+e\w*\b")
PRINTENV_LEADING_RE = re.compile(r"^printenv\b")
EXPORT_DUMP_LEADING_RE = re.compile(r"^export\s+-p\b")
TMUX_DUMP_LEADING_RE = re.compile(r"^tmux\s+show-environment\b")
PROC_ENVIRON_RE = re.compile(r"/proc/[0-9A-Za-z$*]+/environ")  # usually an ARGUMENT (e.g. to cat), not leading
TEE_LEADING_RE = re.compile(r"^tee\b")

# cc-quality PR#245 review (bus #48441 LOW #3): non-shell print of a sensitive var by
# NAME -- `python3 -c "print(os.environ['TOKEN'])"` / `awk 'BEGIN{print ENVIRON["TOKEN"]}'`
# -- bypassed Rule B entirely since it's not $VAR-shaped. The DSN is backstopped by
# secrets_output_scanner's postgres-dsn pattern regardless; this closes the gap for
# every other named secret. Matched against the FULL raw command text, not a split
# statement/segment -- the `;` inside a `python -c "a; b"` string is not a real
# statement separator, but STATEMENT_SPLIT_RE (not quote-aware) treats it as one and
# would otherwise tear the `print(` away from the `os.environ[...]` it wraps.
PYTHON_PRINT_ENVIRON_RE = re.compile(
    r"print\s*\(\s*(?:str\(|repr\()?\s*os\.environ(?:\[\s*['\"]([^'\"]+)['\"]\s*\]|"
    r"\.get\(\s*['\"]([^'\"]+)['\"])"
)
AWK_PRINT_ENVIRON_RE = re.compile(r"print\s+ENVIRON\[\s*['\"]([^'\"]+)['\"]\s*\]")


def check_rule_b_environ_print(command: str) -> str | None:
    for m in PYTHON_PRINT_ENVIRON_RE.finditer(command):
        name = m.group(1) or m.group(2)
        if name and SENSITIVE_VAR_NAME_RE.match(name):
            return f"python os.environ[{name!r}] passed directly to print()"
    for m in AWK_PRINT_ENVIRON_RE.finditer(command):
        name = m.group(1)
        if name and SENSITIVE_VAR_NAME_RE.match(name):
            return f"awk ENVIRON[{name!r}] passed directly to print"
    return None

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

# scripts/env_set.sh (bus #48639/#48642 delta) only ever prints a sha1 FINGERPRINT of
# the value it receives on stdin -- by construction, the raw value never reaches its
# own stdout. Structurally identical to a hash sink, even though it isn't literally
# `shasum` in this pipeline segment, so `printf '%s' "$VAR" | scripts/env_set.sh ...`
# (the sanctioned way to use it) must resolve the same way `| shasum` does, not get
# blamed by Rule B's own print-trigger for the printf feeding it.
ENV_SET_SINK_RE = re.compile(r"\benv_set\.sh\b")


def _is_sink(segment: str) -> bool:
    return (bool(HASH_SINK_RE.search(segment)) or bool(SED_MASK_SINK_RE.search(segment))
            or bool(ENV_SET_SINK_RE.search(segment)))


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
            seg = segments[i]
            # cc-quality PR#245 review (bus #48441 LOW #4): tee duplicates the raw
            # value to another destination (a file, /dev/stderr) regardless of what a
            # LATER segment does with its own stdout copy -- a hash sink downstream of
            # a tee does not retroactively un-leak what tee already wrote.
            if i > trigger_index and TEE_LEADING_RE.match(_leading_command(seg)):
                return f"{trigger_kind} and pipes it through tee (duplicates the raw value to another destination) before any hash sink"
            if _is_sink(seg):
                sink_index = i
                break
        if sink_index is None:
            return f"{trigger_kind} with no hash/length sink in the pipeline"

        for seg in segments[sink_index + 1:]:
            if not SAFE_AFTER_SINK_RE.match(_leading_command(seg)):
                return "has a hash sink but keeps processing the raw value afterward (not just trimming the hash)"

    return None  # every triggering statement, if any, resolved safely through a sink


# ---- entrypoint -------------------------------------------------------------------

# bus #48639/#48642: these five tools are PATH-ONLY (tool_input is a file path, not
# shell text) -- gated uniformly via _is_secret_path, unconditionally, no sink exception.
PATH_ONLY_TOOLS = ("Read", "NotebookEdit", "Edit", "MultiEdit", "Write")


def _command_text(tool_name: str, tool_input: dict) -> str | None:
    if tool_name == "Bash":
        return tool_input.get("command") or ""
    if tool_name in PATH_ONLY_TOOLS:
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

    if tool_name in PATH_ONLY_TOOLS:
        if _is_secret_path(text):
            sys.stderr.write(f"BLOCKED by secrets_transcript_guard: {TOOL_SECRET_FILE_MESSAGE}\n")
            return 2
        return 0

    reason = (check_rule_a(text) or check_rule_c(text) or check_rule_b(text)
              or check_rule_b_environ_print(text))
    if reason:
        sys.stderr.write(f"BLOCKED by secrets_transcript_guard: {BLOCK_MESSAGE} ({reason})\n")
        return 2

    cloud_reason = check_rule_d(text)
    if cloud_reason:
        sys.stderr.write(f"BLOCKED by secrets_transcript_guard: {cloud_reason}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
