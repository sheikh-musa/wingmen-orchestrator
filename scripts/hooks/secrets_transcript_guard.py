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

  Rule F -- a postgres CLI tool (pg_dump/pg_restore/pg_dumpall/psql) given a DSN-shaped
  variable as a positional argument (bus #52114/#52348/#52387/#52393, real incident
  2026-10-05): any of the four can run long enough to exceed the tool timeout and get
  moved to a background task, whose status/output read can surface a process-listing
  snapshot of the live process's fully shell-expanded argv -- the password that was
  never visible in the COMMAND TEXT becomes visible once the shell expands it into the
  running process. Unconditional, same as Rule E -- this is enforcement, not a lane's
  own practice (orch-console #52393). The fix is PGHOST/PGPORT/PGUSER/PGDATABASE/
  PGPASSWORD env vars, not a sink after the fact (there is no sink for a process
  listing nobody in this command's pipeline controls). A literal DSN in the command
  text is Rule E's job, unconditionally, regardless of which tool it's an argument to.

  Rule G -- raw DDL against a PRODUCTION_SILOS store via psql/psycopg, bypassing
  apply_migration.py's --gate entirely (op#22669 item 3 / bus #44135, re-raised live by
  #53758/#53764). Not a secrets-exposure rule -- no value is printed -- the risk is an
  UNGATED PRODUCTION WRITE. Unconditional, no body carve-out: there is no legitimate
  reason to bypass the gate. ddl_coverage_watchdog.py remains the detect-after-the-fact
  backstop for whatever this heuristic still misses. The apply_migration.py import this
  rule needs is LAZY (bus #53806 review) -- paid only once the DDL+pg-tool/psycopg
  gates already matched, not on every tool call, and fails soft to an inline-literal
  PRODUCTION_SILOS copy rather than locking out Bash fleet-wide if that import breaks.

  Rule G exemption #2 -- exec_prod (irsyad's sanctioned production path: mgmt-API
  transport + agent-actor guard, no direct silo DSNs by design -- bus
  #56599/#56607/#56672/#56684/#56752, 2026-10-07). apply_migration.py is not
  irsyad's path; exec_prod needs the SAME --gate contract, but a FLAT "block
  every ungated exec_prod call" (an earlier version of this branch) would ALSO
  wrongly block the DML-only calls (backfills, dry-runs) exec_prod's own contract
  never requires a gate for -- re-creating the conflict from the other side
  (orch-console #56684). So this branch actually CLASSIFIES the SQL, same as
  exec_prod itself does: `--gate <digits>` present -> exempt outright (trust
  exec_prod's own bus-validated gate, mirrors APPLY_MIGRATION_REF_RE). Otherwise,
  parse `--inner-file <path>` from argv (exec_prod's real shape passes SQL via a
  FILE, never inline -- confirmed against cc-irsyad-coord's actual invocation,
  bus #56672) and classify its CONTENT via `ddl_detect.is_ddl_shaped` -- the ONE
  detector shared with exec_prod.py itself (cc-irsyad-2, bus #56752, q#206),
  vendored verbatim/sha256-pinned above rather than cross-imported, so the hook
  and exec_prod can never disagree on the same SQL. DDL-shaped -> blocks; DML-only
  -> allowed. No `--inner-file`, or the file is missing/unreadable, or inline SQL
  arrives via some other flag -> fail-closed, same posture as every other branch
  in this file.

  Documented limitation (orch-console review, bus #56642): EXEC_PROD_REF_RE is a
  substring match anywhere in the command text, same style as APPLY_MIGRATION_REF_RE
  -- it is not anchored to the leading command. A raw psql/psycopg DDL command that
  merely MENTIONS "exec_prod" (e.g. in a shell comment) plus a decorative `--gate 1`
  would pass this exemption too. Accepted under this file's stated false-negative-
  tolerant stance: exec_prod itself validates the --gate row against the fleet bus
  before executing anything, and ddl_coverage_watchdog.py remains the detect-after-
  the-fact backstop for whatever this heuristic still misses -- same posture as every
  other rule in this file, not a new gap.

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

Rule E, Write/Edit/MultiEdit/NotebookEdit coverage (bus #48903/#48922, LOCK2
follow-up): Rule E originally only scanned Bash command text, on the theory that a
PATH-ONLY tool's own CONTENT (Write's `content`, Edit's `new_string`, MultiEdit's
per-edit `new_string`, NotebookEdit's `new_source`) is the agent's own new text, not a
leak of what's on disk -- true for Rule A's concern (echoing EXISTING content), but
not for Rule E's: an agent can still type a literal secret-shaped VALUE into brand-new
content exactly as it can into a Bash command, and that was only ever caught post-hoc
by secrets_output_scanner.py (real example: cc-substrate op#24409, a fixture DSN typed
into a Write'd test-payload file). Scanned unconditionally, same as the Bash case --
no sink exception; a path already blocked by the secret-path check above never reaches
this (it's blocked for Rule A first, same message either way).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from secret_shape_patterns import SECRET_VALUE_PATTERNS  # noqa: E402
# ddl_detect.py: vendored verbatim, sha256-pinned, from sheikh-musa/ihsanos
# scripts/db/lib/ddl_detect.py (branch feat/q206-exec-prod-gate-contract, commit
# 4cecf9ef, sha256 a0895e0370751f113fc14dbde3611df53a3360700419ffecf7f3ef3630f9ae25)
# -- the ONE DDL-shape classifier shared with exec_prod.py itself (cc-irsyad-2,
# bus #56752, q#206), so the hook and exec_prod's own --gate requirement can never
# disagree on the same SQL. Vendored rather than cross-imported: importing across
# repos would depend on where ihsanos happens to be checked out on a given host,
# which differs between the Mini and gzb. Stdlib-only, zero deps -- cheap enough to
# import eagerly like secret_shape_patterns, unlike apply_migration.py's lazy
# psycopg-bearing import below. Re-sync by re-copying ddl_detect.py verbatim +
# updating this comment's pinned hash if ihsanos's copy ever changes; never
# hand-edit the vendored copy to diverge from theirs.
from ddl_detect import is_ddl_shaped  # noqa: E402

BLOCK_MESSAGE = "secret would enter the transcript -- hash it or use the value without printing."
LITERAL_SECRET_MESSAGE = (
    "this command types a secret VALUE literally instead of referencing it by name -- "
    "use the env var (e.g. $DATABASE_URL, $X) or a secrets-store lookup, never paste "
    "the literal value into a command."
)
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
# cc-quality bus #48712: the SAME process-$HOME-expansion bug as .ssh, found on the
# other two tilde-based SECRET_DIR_PREFIXES entries -- "~/.wingmen/private/" and
# "~/.wingmen/keys/" only ever protect the CURRENT process's own home, so e.g.
# /root/.wingmen/keys/... or another user's home silently fell through. Generic
# path-component match, same shape as SSH_DIR_COMPONENT_RE, independent of whose home.
WINGMEN_DIR_COMPONENT_RE = re.compile(r"(^|/)\.wingmen/(private|keys)/")


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
    if WINGMEN_DIR_COMPONENT_RE.search(path) or WINGMEN_DIR_COMPONENT_RE.search(expanded):
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


# ---- Rule E: a secret VALUE typed literally into a Bash command (bus #48685/#48695) -
# cc-fleet-health's real-leak-shapes sweep (#48685): shape 1 (~84x, mostly cai) a
# password-bearing DSN typed inline (`psql postgres://user:pass@host/db`, `DB="<dsn>"`,
# `export X=<dsn>`); shape 2 (~55x, cosem-adcda) a Bearer/bot token typed inline into
# curl/export. Both are already IN the tool_use INPUT by the time PreToolUse fires --
# no sink downstream can un-leak that, so this is unconditional, same as Rule A/C, not
# sink-gated like Rule B (Rule B is about a NAMED var being printed; this is about the
# literal value itself appearing in the command text, independent of what's done with
# it). Reuses the same pattern set the scanner backstops with (secret_shape_patterns.py)
# so the two layers can't drift apart.


def check_rule_e(command: str) -> str | None:
    for cls, pattern in SECRET_VALUE_PATTERNS.items():
        if pattern.search(command):
            return f"a literal {cls}-shaped value in the command text"
    return None


# ---- Rule F: a postgres CLI tool given a DSN as a positional arg (bus #52114/#52348/
# #52387/#52393, real incident 2026-10-05): `pg_dump "$DATABASE_URL" --schema-only ...`
# ran past the tool's 60s timeout and was moved to a background task; checking that
# task's status later surfaced a process-listing snapshot showing the live pg_dump's
# fully shell-expanded argv -- bash quoting "$VAR" protects against word splitting, not
# against the exec'd process's argv containing the expanded value. That snapshot (not
# this command itself) is what leaked into the transcript, so no sink downstream of
# THIS command can fix it -- the only durable fix is to never let the password reach
# the tool's argv at all.
#
# Covers pg_dump/pg_restore/pg_dumpall AND psql -- orch-console's #52393 explicitly
# overruled an earlier draft that carved psql out as a "pre-existing sanctioned idiom
# for quick queries": any of the four can time out into a background task and hit the
# same argv-exposure path, so psql is in scope too.
#
# A literal DSN (postgres:// URL, or libpq key=value with password=) in the command
# TEXT is already caught unconditionally by Rule E for every Bash command via
# SECRET_VALUE_PATTERNS (postgres-dsn / postgres-dsn-kv) -- Rule E runs before Rule F
# in main() and would block those forms first regardless of which tool they're an
# argument to. Rule F's own, narrower job is the form Rule E CAN'T see: a bare $VAR
# reference that only becomes a secret once the shell expands it into the exec'd
# process's argv.

PG_CLI_TOOLS = {"pg_dump", "pg_restore", "pg_dumpall", "psql"}
PG_CLI_ARGV_MESSAGE = (
    "its argv (and so its password) becomes visible to any process listing or "
    "background-task status read -- use PGHOST/PGPORT/PGUSER/PGDATABASE/PGPASSWORD "
    "env vars instead of a connection-string positional argument"
)


def check_rule_f(command: str) -> str | None:
    for statement in _split_statements(command):
        for segment in _split_pipeline(statement):
            # _leading_command already strips a leading VAR=val PREFIX (e.g.
            # `PGPASSWORD="$DB_PASSWORD" pg_dump ...`) -- that prefix is the sanctioned
            # decomposition itself and must not be scanned for a sensitive-var match;
            # only the tool's own ARGV (tokens after its own name) is in scope.
            lead = _leading_command(segment)
            tokens = lead.split()
            if not tokens:
                continue
            basename = tokens[0].rsplit("/", 1)[-1]
            if basename not in PG_CLI_TOOLS:
                continue
            argv = " ".join(tokens[1:])
            if SENSITIVE_VAR_RE.search(argv):
                return f"{basename} given a DSN-shaped variable as a positional argument -- {PG_CLI_ARGV_MESSAGE}"
    return None


# ---- Rule G: raw DDL against a PRODUCTION_SILOS store, bypassing apply_migration.py's
# --gate (op#22669 item 3 / bus #44135/#44139/#44140, re-raised live by #53758, filed as
# a backlog item at #53764). apply_migration.py's own --gate enforcement only fires for
# an apply that goes THROUGH it -- nothing stops a lane running DDL directly against a
# PRODUCTION_SILOS member via raw psql/psycopg instead. ddl_coverage_watchdog.py is the
# existing DETECT-ONLY backstop for this same gap (a schema-fingerprint diff, after the
# fact); this is the missing PROACTIVE half, the same "enforce in code, not detect
# after" pairing as #53680's launcher-coverage fix for this hook itself.
#
# Not a secrets-exposure rule like A/B/E/F above -- no value is being printed here. The
# risk is an UNGATED PRODUCTION WRITE, closer in kind to Rule D's "dangerous shape,
# block outright" than to the sink-gated rules, which is why it's unconditional (no
# CC_BASE_AGENT_ID carve-out the way Rule D has for consoles's legitimate cloud logins
# -- there is no body for which bypassing a production-DDL gate is legitimate; the gate
# itself already has its own --gate-owner allowance for who may AUTHOR the gate row).
#
# Heuristic, not a SQL parser (same documented limitation as the rest of this file):
# flags a DDL-shaped keyword in a command that (a) invokes psql or references psycopg,
# (b) resolves -- via the literal command text, any SENSITIVE_VAR_RE-matching var name
# the command references (resolved through THIS process's own environment, which the
# Bash tool about to run will inherit), or, for a psql/pg_* invocation specifically, the
# ambient PGHOST/PGDATABASE/PGUSER/DATABASE_URL/PGSERVICE env vars psql connects with
# even with NO var named in the command text at all -- to a PRODUCTION_SILOS ref
# string, and (c) does not itself invoke apply_migration.py (the sanctioned, gated
# path). False-negative-tolerant by design (same stance as Rule F): a determined
# bypass can still slip past this; it closes the DEFAULT-PATH bypass the real
# incidents actually used, not every conceivable obfuscation -- ddl_coverage_watchdog.py
# remains the backstop regardless.

# Deliberately NOT imported at module scope (cc-fleet-health review, bus #53806): this
# hook loads on EVERY tool call, every body, every host, as a fresh process with no
# sys.modules cache reuse -- an eager `from apply_migration import PRODUCTION_SILOS`
# pays psycopg's ~0.4s import cost on every call, not just DDL-shaped Bash ones, and
# worse, turns any host/worktree whose venv can't import psycopg into a total Bash
# lockout (hook fails to LOAD -> fail-closed -> every Bash call blocked, not just a
# false-positive on one command). Imported lazily inside check_rule_g, after the DDL +
# pg-tool/psycopg gates already matched (the rare hot case), and wrapped fail-soft: a
# broken import degrades Rule G to this same literal list (already hand-synced with
# docs/data-store-registry.md) instead of locking out Bash fleet-wide.
_PRODUCTION_SILOS_FALLBACK = frozenset({
    "tscuymavysscrvoberrr",  # orchestrator substrate (the monolith)
    "ceayjeamtmcyzzvqflus",  # ihsanos multi-tenant DB
    "goumlynecruxrlmzlntp",  # irsyad silo (goumlyne)
    "brrgastulcffamlbggyu",  # wingmen-personal
    "ywrpttpxwfcoodovxhsr",  # cosem-platform (ADCDA gov-PII)
})


def _production_silos() -> frozenset[str]:
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from apply_migration import PRODUCTION_SILOS
        return PRODUCTION_SILOS
    except Exception:
        return _PRODUCTION_SILOS_FALLBACK


DDL_KEYWORD_RE = re.compile(
    r"\b(CREATE|ALTER|DROP|TRUNCATE)\s+(TABLE|INDEX|POLICY|FUNCTION|TRIGGER|EXTENSION|"
    r"SCHEMA|VIEW|SEQUENCE|TYPE|ROLE|MATERIALIZED)\b"
    r"|\b(GRANT|REVOKE)\b[^;]{0,120}\bON\b",
    re.IGNORECASE,
)
PSYCOPG_REF_RE = re.compile(r"\bpsycopg2?\b")
APPLY_MIGRATION_REF_RE = re.compile(r"\bapply_migration\.py\b")
# bus #56599/#56607: irsyad's sanctioned production path. Matches a bare `exec_prod`
# basename invocation OR any `exec_prod.py` path reference, same literal-substring
# style as APPLY_MIGRATION_REF_RE (not anchored to leading-command position -- this
# heuristic doesn't parse the pipeline, it looks for the name anywhere in the text).
EXEC_PROD_REF_RE = re.compile(r"\bexec_prod(?:\.py)?\b")
# argparse's `type=int` accepts both `--gate 123` and `--gate=123`.
GATE_FLAG_RE = re.compile(r"--gate[=\s]+\d+")
# exec_prod's real invocation (cc-irsyad-coord, bus #56672): the SQL is a FILE path,
# never inline. `\S+` (not a quote-aware parser -- same heuristic-not-parser stance
# as the rest of this file): good enough for this fleet's unquoted tmp-file paths.
INNER_FILE_FLAG_RE = re.compile(r"--inner-file[=\s]+(\S+)")
PG_CONNECT_ENV_VARS = ("PGHOST", "PGDATABASE", "PGUSER", "DATABASE_URL", "PGSERVICE")
# a psycopg caller never references a shell $VAR (SENSITIVE_VAR_RE's shape) -- it names
# the var as a Python string literal, `os.environ['NAME']` / `os.environ.get("NAME")`.
# Deliberately NOT anchored to a `print(`/sink prefix the way PYTHON_PRINT_ENVIRON_RE is
# above -- any os.environ reference to a sensitive-shaped name is a candidate DSN here,
# not just one that's about to be printed.
OS_ENVIRON_REF_RE = re.compile(
    r"os\.environ(?:\[\s*['\"]([^'\"]+)['\"]\s*\]|\.get\(\s*['\"]([^'\"]+)['\"])"
)
RULE_G_MESSAGE = (
    "raw DDL against a PRODUCTION_SILOS store, bypassing apply_migration.py's --gate -- "
    "run this through scripts/apply_migration.py --silo <ref> --gate <bus-id> instead "
    "(op#22669 item 3 / bus #44135/#53764; ddl_coverage_watchdog.py only catches this "
    "AFTER the fact)."
)
RULE_G_EXEC_PROD_MESSAGE = (
    "exec_prod needs --gate <bus-id> for DDL -- same contract as apply_migration.py's "
    "--gate (op#22669 item 3; mirrored for exec_prod per bus #56599/#56607)."
)


def _resolved_values_for_silo_check(command: str, include_pg_connect_env: bool) -> list[str]:
    values = [command]
    for m in SENSITIVE_VAR_RE.finditer(command):
        val = os.environ.get(m.group(1))
        if val:
            values.append(val)
    for m in OS_ENVIRON_REF_RE.finditer(command):
        name = m.group(1) or m.group(2)
        if name and SENSITIVE_VAR_NAME_RE.match(name):
            val = os.environ.get(name)
            if val:
                values.append(val)
    if include_pg_connect_env:
        for name in PG_CONNECT_ENV_VARS:
            val = os.environ.get(name)
            if val:
                values.append(val)
    return values


# bus #56900 (cc-fleet-health, 2026-10-07): the first cut of exemption #2 entered the
# exec_prod branch on a bare substring match over the WHOLE command text, so any
# command that merely TALKED about exec_prod (a bus message quoting a PR title, a
# `grep exec_prod` over this very file) was blocked outright. Rule G now reasons per
# pipeline SEGMENT: a segment is an exec_prod INVOCATION only when one of the first
# few tokens of its leading command is the exec_prod program itself (bare basename
# or a path ending in exec_prod.py, with or without an interpreter in front); and
# segments whose leading command is a fleet MESSAGING sender (bus_send.py,
# nazim_send.sh, tg_send.sh, *_support_send.sh, ...) are not SQL carriers at all --
# their heredoc bodies routinely quote DDL words, silo refs and the word psycopg
# when agents discuss migrations -- so they are excluded from Rule G entirely (five
# false positives on orch-console + coord within an hour of #321 landing). A
# `bus_send.py ... && psql -c "DROP TABLE"` compound still gets its psql segment
# checked, because the exclusion is per segment, never per command.
EXEC_PROD_BASENAMES = {"exec_prod", "exec_prod.py"}
MESSAGING_SEND_BASENAMES = {
    "bus_send.py", "nazim_send.sh", "nazim_send_photo.sh", "tg_send.sh", "tg_send_file.sh",
    "log_console_msg.sh", "nudge_cai.sh", "cosem_tdu_support_send.sh",
    "cosem_exams_support_send.sh", "irsyad_support_send.sh",
}
MESSAGING_SEND_SUFFIX_RE = re.compile(r"_send(?:_photo|_file)?\.(?:sh|py)$")
# how many leading tokens may precede the program name: `python3 x.py`,
# `.venv/bin/python -I scripts/db/x.py`, `nice -n 5 python3 x.py` all fit in 4.
_LEADING_TOKENS_TO_INSPECT = 4


def _leading_tokens(segment: str) -> list[str]:
    lead = _leading_command(segment)
    try:
        tokens = shlex.split(lead)
    except ValueError:
        tokens = lead.split()
    return tokens


def _basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def _segment_is_messaging_send(segment: str) -> bool:
    tokens = _leading_tokens(segment)
    for tok in tokens[:_LEADING_TOKENS_TO_INSPECT]:
        base = _basename(tok)
        if base in MESSAGING_SEND_BASENAMES or MESSAGING_SEND_SUFFIX_RE.search(base):
            return True
    return False


# tokens that may legitimately precede the PROGRAM in a segment: interpreters,
# launchers and their flags. The program is the first token that is none of these --
# so `grep -c exec_prod file` resolves to program=grep (exec_prod is an ARGUMENT),
# while `python3 -I scripts/db/exec_prod.py ...` resolves to program=exec_prod.py.
_LAUNCHER_RE = re.compile(r"^(?:python(?:3(?:\.\d+)?)?|node|nice|env|sudo|command|time|nohup|timeout|caffeinate)$")


def _program_basename(segment: str) -> str:
    tokens = _leading_tokens(segment)
    for i, tok in enumerate(tokens[:_LEADING_TOKENS_TO_INSPECT + 2]):
        if tok.startswith("-"):
            continue
        base = _basename(tok)
        if _LAUNCHER_RE.match(base):
            # `timeout 30 x`, `nice -n 5 x`: a numeric operand follows these launchers
            continue
        if re.fullmatch(r"\d+[smhd]?", tok):
            continue
        return base
    return ""


def _segment_invokes_exec_prod(segment: str) -> bool:
    return _program_basename(segment) in EXEC_PROD_BASENAMES


# heredoc bodies (`<<EOF ... EOF`, `<<'EOF'`, `<<-EOF`) are DATA handed to the program
# on stdin -- message text, SQL destined for a gated tool, test fixtures. Rule G scans
# the COMMAND, not stdin data, so bodies are removed before segmenting; a heredoc fed
# to psql still gets its psql segment checked for DDL on the command line, and the
# exec_prod branch reads --inner-file content regardless (that is its whole point).
_HEREDOC_OPEN_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def _strip_heredoc_bodies(command: str) -> str:
    lines = command.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = _HEREDOC_OPEN_RE.search(line)
        if m:
            delim = m.group(2)
            i += 1
            while i < len(lines) and lines[i].strip() != delim:
                i += 1
            # drop the terminator line too; keep anything after it on later lines
        i += 1
    return "\n".join(out)


def _check_exec_prod_segment(segment: str) -> str | None:
    # irsyad's OWN sanctioned path (bus #56599/#56607/#56684). --gate present ->
    # trust exec_prod's own bus-validated gate, same as apply_migration.py.
    # Otherwise: a flat "block every ungated exec_prod call" would ALSO wrongly
    # block legitimate DML-only calls (backfills, dry-runs) that exec_prod's own
    # contract (PR#1051) never requires a gate for (orch-console #56684) -- so
    # the hook has to actually look at what the SQL does, same as exec_prod
    # itself does. The SQL lives in --inner-file, never inline (cc-irsyad-coord,
    # bus #56672), so this reads that file and classifies its CONTENT via
    # ddl_detect.is_ddl_shaped -- the ONE detector shared with exec_prod.py
    # itself (cc-irsyad-2, bus #56752, q#206), vendored here sha256-pinned (see
    # ddl_detect.py's own header). Fail-CLOSED on anything this can't classify
    # with confidence: no --inner-file at all (inline SQL via some other flag),
    # or the file is missing/unreadable.
    if GATE_FLAG_RE.search(segment):
        return None
    m = INNER_FILE_FLAG_RE.search(segment)
    if not m:
        return RULE_G_EXEC_PROD_MESSAGE
    try:
        inner_sql = Path(m.group(1)).read_text(encoding="utf-8")
    except OSError:
        return RULE_G_EXEC_PROD_MESSAGE
    if is_ddl_shaped(inner_sql):
        return RULE_G_EXEC_PROD_MESSAGE
    return None


def check_rule_g(command: str) -> str | None:
    command = _strip_heredoc_bodies(command)
    scan_segments: list[str] = []
    for statement in _split_statements(command):
        for segment in _split_pipeline(statement):
            if not segment:
                continue
            if _segment_is_messaging_send(segment):
                continue  # a message body is not SQL (bus #56900)
            if APPLY_MIGRATION_REF_RE.search(segment):
                continue  # the sanctioned, gated path -- never what this rule exists to catch
            if _segment_invokes_exec_prod(segment):
                verdict = _check_exec_prod_segment(segment)
                if verdict:
                    return verdict
                continue
            scan_segments.append(segment)

    scan = "\n".join(scan_segments)
    if not DDL_KEYWORD_RE.search(scan):
        return None

    uses_pg_cli = False
    uses_psycopg = False
    for segment in scan_segments:
        tokens = _leading_tokens(segment)
        basename = _basename(tokens[0]) if tokens else ""
        if basename in PG_CLI_TOOLS:
            uses_pg_cli = True
        if PSYCOPG_REF_RE.search(segment):
            uses_psycopg = True
    if not (uses_pg_cli or uses_psycopg):
        return None

    for value in _resolved_values_for_silo_check(scan, include_pg_connect_env=uses_pg_cli):
        for ref in _production_silos():
            if ref in value:
                return RULE_G_MESSAGE
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

#   _DSN/_TOKEN/_KEY carry a trailing `(?:_\w+)?` before the word boundary so a
# qualifier SUFFIX on an otherwise-sensitive name (e.g. CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE)
# still matches -- a bare trailing \b fails there because "N" and "_" are both \w, so no
# boundary exists right after "TOKEN" (bus #49026/#49029/#49030: a real leak reached
# prod because an ad-hoc masking sed matched "...TOKEN=" but not "...TOKEN_OVERRIDE=").
# The suffix must itself start with "_" (not bare \w*) so this stays a pattern over
# sensitive NAMES, not a substring match -- "TOKENIZER_PATH" must keep failing to match.
#
# \w*_DB_URL (bus #52393 -- orch-console named this form explicitly, e.g. CONSOLE_DB_URL)
# added alongside \w*_DSN/DATABASE_URL so Rule F catches every DSN-shaped var name the
# fleet actually uses, not just the one that happened to leak in the real incident.
SENSITIVE_VAR_RE = re.compile(
    r"\$\{?(DATABASE_URL|WRITE_DSN|\w*_DSN(?:_\w+)?|\w*_DB_URL|\w*_TOKEN(?:_\w+)?|\w*_KEY(?:_\w+)?|"
    r"\w*SECRET\w*|\w*PASSWORD\w*|GOUMLYNE_\w*|API_KEY\w*)\b\}?"
)
# same name alternation, bare (no $ / braces) -- for matching a NAME string literal
# inside os.environ['NAME'] / ENVIRON["NAME"], not a shell variable reference.
SENSITIVE_VAR_NAME_RE = re.compile(
    r"^(DATABASE_URL|WRITE_DSN|\w*_DSN(?:_\w+)?|\w*_DB_URL|\w*_TOKEN(?:_\w+)?|\w*_KEY(?:_\w+)?|"
    r"\w*SECRET\w*|\w*PASSWORD\w*|GOUMLYNE_\w*|API_KEY\w*)$"
)

PRINT_LEADING_RE = re.compile(r"^(echo|printf|print|tee|logger)\b")
# cc-fleet-health bus #49064 probe, gaps (c)/(d): the real Sep-24 incident used BSD
# `ps eww` (env-dump keyletter first, already matched), but two sibling shapes were
# missed -- macOS's dash-prefixed `-E` env flag (NOT the lowercase `-e`/`-ef` GNU/BSD
# "all processes" flag, which stays allowed -- it's ubiquitous and does not dump env),
# and the no-dash BSD keyletter cluster with `e` anywhere in it, not only leading
# (`ps auxe`, `ps wwe`), since BSD ps keyletters may be combined in any order.
PS_DUMP_LEADING_RE = re.compile(r"^ps\s+(?:-\w*E\w*|(?!-)\w*e\w*)\b")
PRINTENV_LEADING_RE = re.compile(r"^printenv\b")
EXPORT_DUMP_LEADING_RE = re.compile(r"^export\s+-p\b")
TMUX_DUMP_LEADING_RE = re.compile(r"^tmux\s+show-environment\b")
PROC_ENVIRON_RE = re.compile(r"/proc/[0-9A-Za-z$*]+/environ")  # usually an ARGUMENT (e.g. to cat), not leading
TEE_LEADING_RE = re.compile(r"^tee\b")

# cc-fleet-health bus #49064 probe, gap (a): the real Sep-24 incident's actual dump
# trigger (`ps eww`) was buried TWO quoting levels deep inside
# `ssh hub-vps "sshpass ... ssh gazzai@<host> 'ps eww -u gazzai -o pid,command'"` --
# invisible to a leading-command check on the OUTER segment, whose leading command is
# `ssh`, never `ps`. `bash -c`/`sh -c`/`zsh -c` wrap a sub-shell script the same way.
SSH_WRAPPER_LEADING_RE = re.compile(r"^(?:ssh|sshpass)\b")
SHELL_DASH_C_LEADING_RE = re.compile(r"^(?:bash|sh|zsh)\s+(?:-\w*\s+)*-\w*c\w*\b")
SSH_FLAGS_WITH_ARG = {"-p", "-i", "-o", "-l", "-F", "-J", "-L", "-R", "-D", "-c", "-m", "-w", "-B", "-b", "-E", "-e"}


def _extract_remote_script(segment: str, lead: str) -> str | None:
    """Best-effort extraction of the remote/sub-shell command text wrapped by
    ssh/sshpass/bash -c/sh -c/zsh -c, whether it arrived as ONE quoted shell token
    (`ssh host 'ps eww'`, the Sep-24 shape) or as several separate UNQUOTED words
    (`ssh host ps eww ...`, bus #49064 gap #3) -- shlex has already stripped quoting
    by the time we see tokens, so both shapes look identical: a run of tokens after
    the host (or after `-c`) is the remote script, rejoined with spaces. Not a full
    ssh-argv parser -- best-effort skip of ssh's own flags and their single-token
    arguments, same documented-limitation posture as the rest of this file's shell
    parsing."""
    if not (SSH_WRAPPER_LEADING_RE.match(lead) or SHELL_DASH_C_LEADING_RE.match(lead)):
        return None
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return None
    if not tokens:
        return None
    if SHELL_DASH_C_LEADING_RE.match(lead):
        last_c_flag = None
        for i, tok in enumerate(tokens):
            if tok.startswith("-") and "c" in tok:
                last_c_flag = i
        if last_c_flag is None or last_c_flag + 1 >= len(tokens):
            return None
        return " ".join(tokens[last_c_flag + 1:])
    # ssh/sshpass: sshpass always wraps a real `ssh` invocation for our purposes --
    # find that `ssh` token, skip its own flags (and single-token flag arguments) and
    # the host argument; everything left is the remote command.
    i = 0
    n = len(tokens)
    while i < n and tokens[i] != "ssh":
        i += 1
    if i >= n:
        return None
    i += 1
    while i < n and tokens[i].startswith("-"):
        flag = tokens[i]
        i += 1
        if flag in SSH_FLAGS_WITH_ARG and i < n:
            i += 1
    if i >= n:
        return None
    i += 1  # the host argument itself
    if i >= n:
        return None
    return " ".join(tokens[i:])

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


def _is_sink(segment: str, trigger_kind: str | None = None) -> bool:
    if bool(HASH_SINK_RE.search(segment)) or bool(ENV_SET_SINK_RE.search(segment)):
        return True
    # bus #49026/#49029/#49030 real leak: a sed-mask sink is unverifiable against an
    # UNBOUNDED "dumps the environment" trigger -- the real incident was exactly this: a
    # sed masking `CLAUDE_CODE_OAUTH_TOKEN=` on a /proc/<pid>/environ dump silently let
    # `CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE=` through raw, because no single hand-written sed
    # pattern can be trusted to enumerate every sensitive name a dump might contain. A
    # dump must resolve through an opaque hash/length sink instead, which redacts the
    # whole blob without needing to know which names it holds. This does NOT extend to
    # Rule A's grep-on-a-secret-FILE sink check (trigger_kind left as the None default
    # there) or Rule B's "prints a secret env var" trigger (one named var, bounded) --
    # both keep accepting sed-mask as already reviewed/accepted.
    if trigger_kind == "dumps the environment":
        return False
    return bool(SED_MASK_SINK_RE.search(segment))


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


def _trigger_kind(segment: str, lead: str, _depth: int = 0) -> str | None:
    """The Rule B trigger kind for this single pipeline segment, found either
    directly (ps eww, printenv, ...) or nested one or more levels deep behind
    ssh/sshpass/bash -c/sh -c/zsh -c (bus #49064: the real Sep-24 incident nested TWO
    levels, an outer `ssh` wrapping `sshpass ... ssh` wrapping the actual `ps eww`).
    Recurses until no further wrapper is found; `_depth` is a sanity backstop against
    pathological input, not expected to ever bind in practice."""
    if _segment_dump_kind(segment, lead):
        return "dumps the environment"
    if _segment_prints_secret(segment, lead):
        return "prints a secret env var"
    if _depth >= 6:
        return None
    inner = _extract_remote_script(segment, lead)
    if inner is None:
        return None
    for inner_statement in _split_statements(inner):
        for inner_seg in _split_pipeline(inner_statement):
            kind = _trigger_kind(inner_seg, _leading_command(inner_seg), _depth + 1)
            if kind:
                return kind
    return None


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
            kind = _trigger_kind(seg, lead)
            if kind:
                trigger_index, trigger_kind = i, kind
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
            if _is_sink(seg, trigger_kind):
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


def _new_content_text(tool_name: str, tool_input: dict) -> str:
    """The agent's own NEW content for a PATH-ONLY tool -- Rule E's concern (a secret
    VALUE typed literally), independent of Rule A's (existing file content echoed
    back). Returns "" for a tool/shape with no new-content field, never None, so
    callers can check_rule_e() it unconditionally."""
    if tool_name == "Write":
        return tool_input.get("content") or ""
    if tool_name == "Edit":
        return tool_input.get("new_string") or ""
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        return "\n".join(e.get("new_string") or "" for e in edits)
    if tool_name == "NotebookEdit":
        return tool_input.get("new_source") or ""
    return ""


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
        content = _new_content_text(tool_name, tool_input)
        if content:
            literal_reason = check_rule_e(content)
            if literal_reason:
                sys.stderr.write(f"BLOCKED by secrets_transcript_guard: {LITERAL_SECRET_MESSAGE} ({literal_reason})\n")
                return 2
        return 0

    if tool_name == "Bash":
        literal_reason = check_rule_e(text)
        if literal_reason:
            sys.stderr.write(f"BLOCKED by secrets_transcript_guard: {LITERAL_SECRET_MESSAGE} ({literal_reason})\n")
            return 2
        pg_dump_reason = check_rule_f(text)
        if pg_dump_reason:
            sys.stderr.write(f"BLOCKED by secrets_transcript_guard: {pg_dump_reason}\n")
            return 2
        ddl_reason = check_rule_g(text)
        if ddl_reason:
            sys.stderr.write(f"BLOCKED by secrets_transcript_guard: {ddl_reason}\n")
            return 2

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
