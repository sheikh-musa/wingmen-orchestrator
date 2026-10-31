#!/usr/bin/env python3
"""PostToolUse backstop: catch secret-SHAPED output the PreToolUse guard missed
(obfuscation, a phrasing the command-shape rules didn't anticipate, or a bug in them).

Musa op#24408, bus #48293/#48310/#48312. Content-based, not name-based, so it catches a
VALUE even with no variable name in sight.

On a match: redact the matched span in the on-disk session transcript (the JSONL file
at `transcript_path`), page orch-console with ONLY the pattern class + host + agent +
tool (never the matched text), and tell the model (non-blocking -- the output already
happened; this is detection + local containment, not prevention).

Redaction method (bus #48312 concern #4 -- "editing a LIVE session's JSONL can corrupt
it... redact by exact span only"): the last line is parsed as JSON, every STRING VALUE
in the structure is scanned and the matched span replaced in place, then the object is
re-serialized with json.dumps. This can never produce invalid JSON (unlike a raw
text/byte-offset splice on the line, which risks corrupting quoting/escaping right at
the match boundary) and it only ever touches string content -- keys, structure, line
count and every other field are byte-for-byte unchanged. See
tests/test_secrets_output_scanner.py::test_redaction_preserves_transcript_structure
for the proof this session doctor was asked for; an actual `claude --continue` replay
is the final manual check at gate time (not automatable safely from inside this hook's
own test suite).

Paging is deduped per class per tool call (bus #48900/#48901/#48920): a single real
event can match in both the tool_use input and the tool_response (e.g. a Write whose
response echoes a preview of its own content), which previously produced two separate
P1 pages for one leak. Redaction still runs for every match -- it's idempotent, so
running it twice for the same class costs nothing -- only the page is deduped.

Fixture allowlist (bus #48965/#48982): developing against THIS file's own test suite
means editing/reading content that is intentionally secret-shaped, which otherwise
pages orch-console for every fixture -- 20+ real P1 pages in ~25 minutes, burying real
alerts. A narrow, explicit allowlist recognizes only this repo's own known fixture
conventions (a handful of placeholder DSN hosts, a test-/fake-/example- token prefix,
the Telegram test bot id, and the dedicated secrets-hook test files by path) -- never a
broad heuristic that could mask a real leak. Redaction and the stderr note to the model
still happen for an allowlisted hit; only the page to orch-console is skipped.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from secret_shape_patterns import SECRET_VALUE_PATTERNS, find_hits  # noqa: E402

# kept as SECRET_PATTERNS (the pre-existing name tests/this module reference) --
# sourced from the shared module so the guard's Rule E and this scanner can't drift
# apart (bus #48685/#48695).
SECRET_PATTERNS = SECRET_VALUE_PATTERNS

REDACTION = "[REDACTED by secrets_output_scanner -- pattern:{cls}]"

# bus #74676: secret-READING CLI commands whose output has NO fixed shape any
# SECRET_PATTERNS class could ever match -- cc-cosem-adcda-1 ran `firebase
# functions:secrets:access SCHEDULE_PDF_S2S_SECRET --project cosem-adcda-cb6d9`
# unredirected and the raw vaulted secret printed straight into the transcript;
# this scanner never fired because an arbitrary secret value has no regex-
# matchable structure (unlike an API key prefix or a DSN scheme). Detected by
# COMMAND instead: when tool_input names one of these, the captured stdout
# itself becomes a one-off literal redaction pattern (see
# _secret_reading_command_hit below) -- reuses the exact same find+replace
# machinery the shape-based classes use, with no risk of over-matching
# unrelated text (the pattern is an exact-escaped literal of the real captured
# output, not a generalized shape).
SECRET_READING_COMMAND_RE = re.compile(
    r"\bfirebase\s+functions:secrets:access\b|\bgcloud\s+secrets\s+versions\s+access\b")


def _secret_reading_command_hit(input_text: str, tool_response) -> "tuple[str, re.Match, re.Pattern] | None":
    """Returns (cls, match, pattern) if `input_text` names a known secret-reading
    CLI command AND the captured stdout is non-empty, else None.

    Deliberately conservative in the FLAG-everything direction: any non-empty
    stdout from one of these commands is treated as presumptively the secret,
    including a --help/error-message run that happens to print something --
    over-redacting a false positive costs nothing (redaction is idempotent and
    the fixture allowlist still exempts a known test marker from paging), while
    under-redacting a real leak is the exact failure class this closes.

    `tool_response` may be the dict shape a Bash tool_response normally carries
    (stdout/stderr keys) or a bare string -- extracting just `stdout` when a
    dict is available avoids building the pattern from the WHOLE JSON-encoded
    response blob (stdout+stderr+flags), which would be needlessly broad and
    miss the value if it ever appears in a different surrounding context."""
    if not SECRET_READING_COMMAND_RE.search(input_text):
        return None
    if isinstance(tool_response, dict):
        value = (tool_response.get("stdout") or "").strip()
    elif isinstance(tool_response, str):
        value = tool_response.strip()
    else:
        value = ""
    if not value:
        return None
    pattern = re.compile(re.escape(value))
    match = pattern.search(value)
    return ("secret-cli-read", match, pattern) if match else None

# PERF (bus #54580/#54643, fleet-health measured this hook at 61.8%+33.8% CPU on gzbai
# during ordinary lane tool-output scanning): profiling on a representative 1.6MB clean
# tool output found scan() cost scales with text size (~1.1 microseconds/byte dominated
# by postgres-dsn-kv's backtracking lookahead) with no bound on tool-output size -- a
# 32MB output (e.g. a large file Read or persisted spill) measured ~36s of pure regex
# work. 64 KiB is comfortably bigger than any real single-line secret shape and keeps
# the worst-case cost (every pattern's anchor present, text adversarially dense) in the
# tens-of-milliseconds range instead of unbounded. This is a backstop scanner, not the
# pre-execution guard: per orch-console's ask, detection may weaken PAST this cap -- it
# must not weaken WITHIN it, which it doesn't (the cap only truncates what scan() sees,
# never which patterns run or how they match).
MAX_SCAN_BYTES = 65536

# fixture allowlist (bus #48965/#48982) -- see module docstring.
FIXTURE_FILE_PATH_RE = re.compile(r"tests?/test_secrets_\w*\.py")
# the postgres-dsn pattern matches only "scheme://user:pass@" (stops at "@"); the host
# follows immediately after the match, so this is checked against the text AFTER the
# match end, not the match itself.
FIXTURE_DSN_HOST_RE = re.compile(
    r"(?:[\w.-]*\.)?example(?:\.(?:com|internal))?(?::\d+)?[/\s]|"
    r"localhost(?::\d+)?[/\s]|host(?::\d+)?[/\s]",
    re.IGNORECASE,
)
FIXTURE_TOKEN_MARKER_RE = re.compile(r"(?:test|fake|example)-?", re.IGNORECASE)
FIXTURE_TELEGRAM_BOT_ID = "123456789"

# bus #49007/#49062: the file-path skip must never read tool_response -- a compound
# Bash command (`cat .env; pytest tests/test_secrets_x.py`) can concatenate an
# unrelated real secret's output with the dedicated test file's own path mention in
# ONE response string, which previously suppressed the page for the real secret too.
# A Bash command naming the fixture file must also be that command's ONLY statement --
# chaining it with anything else via ;/&&/||/newline is exactly the shape that could
# smuggle an unrelated secret past this skip, so it disqualifies the whole call.
_STATEMENT_SEPARATOR_RE = re.compile(r";|&&|\|\||\n")


def _mentions_fixture_file(tool_input) -> bool:
    """True only if THIS CALL'S INPUT unambiguously targets one of this repo's
    dedicated secrets-hook test files by path (bus #48982: "skip tests/test_secrets_*.py
    by path") -- a direct file_path field (Write/Edit/.../NotebookEdit, no chaining risk
    since the whole call is about that one file), or a Bash command that names it as its
    sole statement."""
    if not isinstance(tool_input, dict):
        return False
    file_path = tool_input.get("file_path")
    if isinstance(file_path, str) and FIXTURE_FILE_PATH_RE.search(file_path):
        return True
    command = tool_input.get("command")
    if isinstance(command, str) and not _STATEMENT_SEPARATOR_RE.search(command):
        if FIXTURE_FILE_PATH_RE.search(command):
            return True
    return False


def _is_fixture_hit(cls: str, match: re.Match) -> bool:
    """True if a matched secret-shape is one of this repo's known test fixtures, not a
    real credential. Deliberately narrow -- only the specific conventions this repo's
    own tests use, never a substring match against arbitrary content."""
    matched = match.group(0)
    if FIXTURE_TOKEN_MARKER_RE.search(matched):
        return True
    if cls == "postgres-dsn":
        tail = match.string[match.end():match.end() + 64]
        if FIXTURE_DSN_HOST_RE.match(tail):
            return True
    if cls == "telegram-bot-token" and matched.startswith(FIXTURE_TELEGRAM_BOT_ID):
        return True
    return False


def scan(text: str) -> list[tuple[str, re.Match]]:
    scanned = text if len(text) <= MAX_SCAN_BYTES else text[:MAX_SCAN_BYTES]
    return find_hits(scanned, SECRET_PATTERNS)


def _tail_sha256(text: str, cap: int = MAX_SCAN_BYTES) -> str | None:
    """sha256 of the content PAST the scan cap, for forensics only -- never the matched
    value itself (a far smaller, crackable oracle space); this hashes bulk unscanned
    content so a future investigation can confirm whether a specific known blob was the
    truncated tail, without this hook ever having read it closely. None when `text`
    wasn't actually truncated."""
    if len(text) <= cap:
        return None
    import hashlib

    return hashlib.sha256(text[cap:].encode("utf-8", errors="replace")).hexdigest()


def _redact_strings(obj, pattern: re.Pattern, cls: str):
    if isinstance(obj, str):
        if pattern.search(obj):
            return pattern.sub(REDACTION.format(cls=cls), obj)
        return obj
    if isinstance(obj, dict):
        return {k: _redact_strings(v, pattern, cls) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_strings(v, pattern, cls) for v in obj]
    return obj


def redact_last_line(transcript_path: str, cls: str, pattern: re.Pattern) -> bool:
    """Redact `pattern` matches in the last JSONL line of transcript_path, in place.
    Returns True if a redaction was made. Never touches any other line."""
    return redact_recent_lines(transcript_path, cls, pattern, max_lines=1)


def _subagent_transcript_path(transcript_path: str | None, agent_id: str | None) -> str | None:
    """bus #48907/#49029/#49030: a subagent's OWN transcript is a SEPARATE on-disk file,
    `<session-dir>/subagents/agent-<agent_id>.jsonl`, not a view onto whatever
    `transcript_path` the hook payload carries for that call. The 2026-10-01 real leak
    (bus #49029) proved this empirically: the scanner paged, `redact_recent_lines`
    reported no error, yet the raw secret sat unredacted in the subagent's own file --
    because `transcript_path` pointed at the session-level transcript, which never
    contained that subagent's tool_use/tool_result lines to begin with. Redacting both
    files is harmless when they happen to coincide (redaction is idempotent).

    op#29348 (2026-10-12, 34-secret leak): the earlier `dirname(transcript_path)`
    derivation was WRONG for the current Claude Code layout, where the session
    transcript is `<proj>/<session-id>.jsonl` and the subagents dir is
    `<proj>/<session-id>/subagents/` (under a SESSION-ID subdir = the transcript path
    minus its `.jsonl`), NOT `<proj>/subagents/`. So the computed path did not exist,
    `redact_recent_lines` hit OSError and no-op'd, and the subagent's 34 inherited-env
    secrets sat unredacted in the REAL file (verified empirically). Try the session-dir
    layout first, fall back to the legacy dirname layout, and PREFER whichever actually
    exists on disk so both layouts are covered."""
    if not transcript_path or not agent_id:
        return None
    candidates = []
    if transcript_path.endswith(".jsonl"):
        # <proj>/<session-id>.jsonl -> <proj>/<session-id>/subagents/agent-<id>.jsonl
        candidates.append(os.path.join(transcript_path[:-len(".jsonl")], "subagents", f"agent-{agent_id}.jsonl"))
    # Legacy / coincident layout: subagents dir a sibling of the transcript FILE.
    candidates.append(os.path.join(os.path.dirname(transcript_path), "subagents", f"agent-{agent_id}.jsonl"))
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[0]


# bus #74596/#74623: redact_recent_lines silently missed the real span TWICE in one
# session (both pages fired correctly, both left the raw value on disk). Root cause:
# window_start = len(lines) - max_lines is computed against the file's line count AT
# THIS FUNCTION'S OWN READ TIME, not against the specific line the hook was actually
# called about. In a busy session, bookkeeping lines (attachment / api-request-blob /
# permission-mode / etc.) get appended by the harness AFTER the real content line but
# BEFORE this function reads the file -- pushing the real line further back than
# max_lines=3 by the time it looks. A fixed narrow window CANNOT be trusted alone; this
# constant bounds a VERIFY-and-widen retry (never a full-file rescan) rather than
# silently claiming success on a window that this exact interleaving defeats.
WIDE_VERIFY_LINES = 50


def _redact_window(transcript_path: str, cls: str, pattern: re.Pattern, max_lines: int) -> bool:
    """Redact `pattern` matches in each of the last `max_lines` JSONL lines of
    transcript_path, in place. Returns True if any redaction was made. Never touches
    a line outside that window. The single-pass primitive redact_recent_lines wraps
    with a verify-and-widen retry."""
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return False
    if not lines:
        return False

    window_start = max(0, len(lines) - max_lines)
    changed = False
    for i in range(window_start, len(lines)):
        line = lines[i]
        stripped = line.rstrip("\n")
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue

        redacted = _redact_strings(obj, pattern, cls)
        new_line = json.dumps(redacted) + "\n"
        if new_line != line:
            lines[i] = new_line
            changed = True

    if changed:
        with open(transcript_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    return changed


def _pattern_present_in_recent(transcript_path: str, pattern: re.Pattern, max_lines: int) -> bool:
    """True iff `pattern` still matches anywhere in the last `max_lines` raw lines of
    transcript_path. Used ONLY to decide whether a widen-retry is needed -- a cheap
    text search, no JSON parsing (a redaction already landed is still valid JSON; a
    miss is a plain substring the regex will find in the raw line either way)."""
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return False
    window_start = max(0, len(lines) - max_lines)
    return any(pattern.search(line) for line in lines[window_start:])


def redact_recent_lines(transcript_path: str, cls: str, pattern: re.Pattern, max_lines: int = 3) -> bool:
    """Redact `pattern` matches in each of the last `max_lines` JSONL lines of
    transcript_path, in place. Returns True if any redaction was made.

    bus #48685/#48695: the assistant's tool_use (the command/input) and its tool_result
    (the output) are SEPARATE JSONL lines -- a secret typed literally into the input can
    land one line before the result that redact_last_line (max_lines=1) alone would
    check, so this widens the window without touching anything further back.

    VERIFY-AND-WIDEN (bus #74596/#74623): after the narrow-window pass, re-check
    whether `pattern` is STILL present within WIDE_VERIFY_LINES (a bounded safety
    margin, never a full-file rescan). If so, the real line was pushed beyond
    `max_lines` by bookkeeping lines appended ahead of this read -- retry over the
    wider window instead of silently trusting the narrow one that missed it. A match
    beyond WIDE_VERIFY_LINES is a documented limit, not silently claimed clean."""
    changed = _redact_window(transcript_path, cls, pattern, max_lines)
    if max_lines < WIDE_VERIFY_LINES and _pattern_present_in_recent(transcript_path, pattern, WIDE_VERIFY_LINES):
        changed = _redact_window(transcript_path, cls, pattern, WIDE_VERIFY_LINES) or changed
    return changed


def _find_persisted_output_paths(transcript_path: str, max_lines: int = 3) -> list[str]:
    """bus #51269/#51285/#51290: Claude Code caps the inline `toolUseResult.stdout` it
    writes to the transcript at a fixed size and spills the FULL output to a separate
    plain-text file (`toolUseResult.persistedOutputPath`) when a tool's output is large.
    A secret-shaped value past that cap is invisible to both the normal tool_response
    scan (payload.tool_response is the same capped copy) AND the existing JSON-line
    redaction (which only ever edits transcript_path's own lines) -- confirmed
    empirically with a synthetic fixture-marked DSN placed past the cap: zero hits, zero
    redaction, the raw value sat untouched in the persisted file. This finds any such
    path referenced in the last `max_lines` of transcript_path, so the caller can scan +
    redact that file too, independently of whatever the capped tool_response contained."""
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    if not lines:
        return []
    window_start = max(0, len(lines) - max_lines)
    paths = []
    for line in lines[window_start:]:
        stripped = line.rstrip("\n")
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        tur = obj.get("toolUseResult")
        if isinstance(tur, dict):
            p = tur.get("persistedOutputPath")
            if isinstance(p, str) and p:
                paths.append(p)
    return paths


def _read_capped_with_tail_hash(path: str, cap: int = MAX_SCAN_BYTES) -> tuple[str, str | None]:
    """Read at most `cap` bytes of `path` for scanning, and sha256 everything after
    that point WITHOUT ever holding the tail in memory at once -- a persisted spill
    file can be arbitrarily large (bus #54580/#54643), so this bounds the read/decode
    cost the same way scan()'s own cap bounds the regex cost, rather than reading a
    multi-MB-or-larger file fully just to throw most of it away. Returns (prefix_text,
    tail_sha256_or_None); the hash is None iff the file was not actually truncated."""
    import hashlib

    try:
        with open(path, "rb") as f:
            head = f.read(cap)
            hasher = hashlib.sha256()
            truncated = False
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                truncated = True
                hasher.update(chunk)
    except OSError:
        return "", None
    return head.decode("utf-8", errors="replace"), (hasher.hexdigest() if truncated else None)


def scan_persisted_output(path: str) -> list[tuple[str, re.Match]]:
    """Scan a Claude-Code-persisted spilled-output file (plain text, not JSONL) for
    every secret pattern. Same return shape as scan(), so a hit here feeds the same
    paging/fixture-allowlist logic as a normal tool_response/tool_input hit.

    cc-quality #51317 (BLOCKING, confirmed empirically): a large spilled output can
    legitimately contain invalid UTF-8 (exactly the kind of output that gets spilled
    past the inline cap in the first place) -- encoding="utf-8" with no errors=
    handling raised an uncaught UnicodeDecodeError here, crashing the hook BEFORE the
    main redact/log/page loop ran at all, which skipped redaction/paging for every hit
    in that invocation, including real ones found in the normal tool_response/
    tool_input. errors="replace" (now applied to just the capped prefix, decoded from
    bytes read by _read_capped_with_tail_hash) avoids the crash while still correctly
    matching a secret shape in the surrounding valid text (verified)."""
    prefix_text, _tail_hash = _read_capped_with_tail_hash(path)
    if not prefix_text:
        return []
    return scan(prefix_text)


def redact_persisted_output(path: str, cls: str, pattern: re.Pattern) -> bool:
    """Redact `pattern` matches in a persisted spilled-output file, in place. Plain
    string substitution -- there is no JSON structure to preserve here, unlike
    redact_recent_lines. Idempotent: a no-op if the pattern doesn't match (safe to call
    for every known class against every persisted path, not just the one it was found
    in). errors="replace" on read for the same reason as scan_persisted_output above."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return False
    new_content = pattern.sub(REDACTION.format(cls=cls), content)
    if new_content == content:
        return False
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except OSError:
        return False
    return True


# bus #51285: the hook never persisted transcript_path/session/tool anywhere durable --
# once a page was sent, the ORIGINATING event (which file, which tool, which session)
# was unrecoverable. Confirmed empirically investigating #51269: an independent
# fleet-wide search for the redaction marker found nothing at or before the page's
# timestamp. This logs enough to locate a future event's transcript after the fact --
# NEVER the matched value or a hash of it (a hash is still a fixed-size oracle for a
# small credential-shaped space; not worth the forensic convenience).
EVENT_LOG_PATH = os.path.join(
    os.environ.get("ORCH_ROOT", os.path.expanduser("~/wingmen/orchestrator")),
    "logs", "secrets_output_scanner_events.log",
)


def _log_event(cls: str, tool_name: str, transcript_path: str | None, sub_transcript_path: str | None,
                session_id: str | None, agent_id: str | None, agent_type: str | None, agent: str,
                cwd: str | None = None, tail_sha256: str | None = None) -> None:
    import datetime
    import stat

    record = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "cls": cls,
        "tool_name": tool_name,
        "transcript_path": transcript_path,
        "sub_transcript_path": sub_transcript_path,
        "session_id": session_id,
        "agent": agent,
        "agent_id": agent_id,
        "agent_type": agent_type,
        # bus #51293: a hit with no AGENT_ID/CC_BASE_AGENT_ID in the env (agent falls
        # back to "unknown-agent") is otherwise unattributable after the fact -- cwd is
        # the hook payload's own field, not derived, and is the best identity proxy
        # available for exactly that case (confirmed: it pinned a real #51293 hit to a
        # specific ad-hoc Bash session by its cwd alone).
        "cwd": cwd,
        # PERF byte-cap forensics (bus #54580/#54643): None unless this event's source
        # text was longer than MAX_SCAN_BYTES -- see scan()/_tail_sha256 docstrings.
        "tail_sha256": tail_sha256,
    }
    try:
        log_dir = os.path.dirname(EVENT_LOG_PATH)
        os.makedirs(log_dir, exist_ok=True)
        is_new = not os.path.exists(EVENT_LOG_PATH)
        with open(EVENT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        if is_new:
            os.chmod(EVENT_LOG_PATH, stat.S_IRUSR | stat.S_IWUSR)  # 600
    except OSError:
        pass  # logging must never crash the hook / block the tool result


def _page_orch_console(cls: str, tool_name: str, agent_id: str | None = None, agent_type: str | None = None,
                        redacted: bool = True) -> None:
    import os
    import subprocess

    orch_root = os.environ.get("ORCH_ROOT", os.path.expanduser("~/wingmen/orchestrator"))
    bus_send = os.path.join(orch_root, "scripts", "bus_send.py")
    venv_py = os.path.join(orch_root, ".venv", "bin", "python3")
    if not os.path.exists(bus_send):
        return
    host = os.environ.get("FLEET_HOST_ID", "unknown-host")
    agent = os.environ.get("AGENT_ID", os.environ.get("CC_BASE_AGENT_ID", "unknown-agent"))
    # bus #48907: a tool call originating inside a SUBAGENT carries agent_id/agent_type
    # in the hook payload (Claude Code hooks docs) -- without this, every subagent hit
    # paged as the bare session identity above, or fell back to "unknown-agent" when
    # even that was unset. There is no separate per-subagent transcript file (confirmed
    # against the docs) -- transcript_path is the one shared session file regardless of
    # origin, so redaction already lands correctly; this only fixes attribution.
    if agent_type:
        agent = f"{agent} (subagent: {agent_type}" + (f"/{agent_id})" if agent_id else ")")
    # bus #87010/#87072/#87073 (3rd recurrence, 2026-10-31): this body used to claim
    # "auto-redacted" UNCONDITIONALLY, even on the exact runs where main() had already
    # given up after 3 retries and written "NO ON-DISK REDACTION ACTUALLY LANDED" to
    # stderr. The stderr note was honest; the page orch-console actually reads was not --
    # "a backstop that detects but does not scrub is a false sense of safety" (#87081).
    # `redacted` must reflect what main() actually verified, not be assumed true here.
    if redacted:
        subject = f"secrets_output_scanner: auto-redacted ({cls})"
        body = (
            f"secrets_output_scanner auto-redacted a secret-shaped match in the on-disk "
            f"session transcript. pattern class: {cls}; tool: {tool_name}; host: {host}; "
            f"agent: {agent}. The matched text itself is never relayed. This is a backstop "
            f"detection (Musa op#24408) -- it cannot un-send anything already transmitted."
        )
    else:
        subject = f"secrets_output_scanner: DETECTED, NOT REDACTED ({cls})"
        body = (
            f"secrets_output_scanner detected a secret-shaped match but could NOT redact it "
            f"on disk after retries. pattern class: {cls}; tool: {tool_name}; host: {host}; "
            f"agent: {agent}. The matched text itself is never relayed, but the raw value may "
            f"still be sitting unredacted in the transcript or a persisted-output file -- "
            f"investigate and contain. This is the backstop failing to close the loop, not "
            f"just the original leak (Musa op#24408)."
        )
    # orch-console bus #48740: synthetic/demo replay runs (e.g. scratch_shape_replay.py)
    # trigger this same real auto-redact path, paging a real P1 for every demo hit and
    # diluting real pages. SECRETS_SCANNER_DEMO=1 (set only by demo/replay tooling, never
    # in a live agent session) downgrades to a [DEMO]-tagged P3, no requires_response.
    # The real-hit path (unset) is untouched -- still P1 + req.
    is_demo = os.environ.get("SECRETS_SCANNER_DEMO") == "1"
    args = [venv_py if os.path.exists(venv_py) else "python3", bus_send,
            "--to", "orch-console", "--type", "update"]
    if is_demo:
        args += ["--subject", f"[DEMO] {subject}", "--priority", "P3"]
    else:
        args += ["--subject", subject, "--priority", "P1", "--req"]
    try:
        subprocess.run(args, input=body, text=True, cwd=orch_root, timeout=20)
    except Exception:
        pass  # paging must never crash the hook / block the tool result


def _session_id_from_transcript_path(transcript_path: str | None) -> str | None:
    if not transcript_path:
        return None
    base = os.path.basename(transcript_path)
    return base[:-len(".jsonl")] if base.endswith(".jsonl") else base


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # PostToolUse: fail-open, the tool call already completed

    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input")
    tool_response = payload.get("tool_response")
    transcript_path = payload.get("transcript_path")
    # bus #48907: present only when this tool call originated inside a subagent.
    agent_id = payload.get("agent_id")
    agent_type = payload.get("agent_type")
    cwd = payload.get("cwd")

    output_text = tool_response if isinstance(tool_response, str) else json.dumps(tool_response or "")
    # bus #48685/#48695: a secret typed LITERALLY into the command itself (not a $VAR
    # reference) is already in the transcript via the tool_use INPUT, before the tool
    # even runs -- scanning only tool_response misses that half entirely (shapes 1/2).
    input_text = json.dumps(tool_input) if tool_input else ""

    hits = scan(output_text) + scan(input_text)
    # forensics for the byte cap (bus #54580/#54643): None unless output_text was
    # actually longer than MAX_SCAN_BYTES, in which case this is a fingerprint of the
    # unscanned remainder -- lets a future investigation confirm whether a specific
    # known blob was the truncated tail, without this hook having scanned it closely.
    output_tail_hash = _tail_sha256(output_text)

    sub_transcript_path = _subagent_transcript_path(transcript_path, agent_id)

    # bus #51269/#51285/#51290: tool_response above can be a CAPPED copy of a large
    # output -- a secret past that cap is scanned nowhere unless we also check the
    # externally-persisted full-output file(s), independently of whether `hits` found
    # anything at all (that's exactly the failure mode: it found nothing, because the
    # capped copy never contained the match).
    persisted_paths = []
    if transcript_path:
        persisted_paths += _find_persisted_output_paths(transcript_path)
    if sub_transcript_path and sub_transcript_path != transcript_path:
        persisted_paths += _find_persisted_output_paths(sub_transcript_path)
    persisted_paths = list(dict.fromkeys(persisted_paths))  # de-dup, preserve order

    persisted_hits = []
    for p in persisted_paths:
        persisted_hits += scan_persisted_output(p)

    # (cls, match, pattern) triples throughout -- the shape-based hits above use the
    # fixed module-level SECRET_PATTERNS[cls]; the command-detected hit below (bus
    # #74676) carries its OWN dynamically-built literal pattern instead, since there
    # is no fixed shape for an arbitrary secret value to register in SECRET_PATTERNS.
    all_hits = [(cls, match, SECRET_PATTERNS[cls]) for cls, match in hits + persisted_hits]
    cli_hit = _secret_reading_command_hit(input_text, tool_response)
    if cli_hit:
        all_hits.append(cli_hit)
    if not all_hits:
        return 0

    # fixture allowlist (bus #48965/#48982): redaction + the stderr note below still run
    # for a fixture hit -- cheap, idempotent, never wrong to do. Only the page is skipped,
    # for either a known fixture shape (_is_fixture_hit) or a call that names one of the
    # dedicated secrets-hook test files (_mentions_fixture_file), e.g. the fixture SSH-key
    # header in test_detects_ssh_private_key, which carries no test-/fake-/example- marker
    # of its own and is only identifiable by its file.
    is_fixture_call = _mentions_fixture_file(tool_input)
    session_id = _session_id_from_transcript_path(transcript_path)
    agent = os.environ.get("AGENT_ID", os.environ.get("CC_BASE_AGENT_ID", "unknown-agent"))

    # bus #48900/#48901/#48920: the same class can match in BOTH input_text and
    # output_text for one tool call (e.g. a Write whose tool_response echoes back a
    # preview of the content it just wrote) -- that produced two independent pages for
    # a single real event. One hit across input+response is one page; dedupe by class,
    # not by (cls, match) pair, since two matches of the same class are still one leak
    # event worth reporting once. Redaction is unaffected -- it's idempotent per class
    # (a second call over an already-redacted line is a no-op), so only paging is deduped.
    # bus #68892 (cc-cosem-adcda-2 triage, 2026-10-16): the stderr message below used to
    # claim "redacted on disk" UNCONDITIONALLY -- the return values of redact_recent_lines/
    # redact_persisted_output were never checked. A real incident showed the hook paging
    # correctly (a hit WAS found) while the raw value sat unredacted on disk -- the claim
    # was false and nobody could tell from the hook's own output. VERIFY, don't assert:
    # track whether any redact_* call actually changed a file, and if a hit was found but
    # NOTHING was actually redacted anywhere, say so loudly instead of lying.
    #
    # RETRY, bounded: a transcript write can plausibly still be in flight relative to this
    # PostToolUse hook's own invocation (the harness's write-to-disk and this hook's
    # execution are not provably ordered from in here), so a same-content line that isn't
    # on disk YET would make the first attempt a correct no-op, not a bug. Retry the same
    # redact_* calls a few times with a short sleep before concluding redaction truly
    # failed -- cheap (the tool call already completed; this hook's own latency is the
    # only cost) and never produces a WRONG success claim either way.
    # Two passes, deliberately separate: the first establishes what ACTUALLY happened
    # (per-class redaction outcome, across every occurrence of that class in all_hits --
    # bus #48900/#48901/#48920's same-class-in-input-and-response shape means a class can
    # appear more than once here), the second logs/pages using that settled truth. A
    # single combined pass used to page on the FIRST occurrence's own redacted_this_hit
    # before a later occurrence of the same class had even been attempted, which could
    # report success or failure that a sibling occurrence later contradicted.
    any_redacted = False
    redacted_by_class: dict[str, bool] = {}
    for cls, match, pattern in all_hits:
        redacted_this_hit = False
        for attempt in range(3):
            if attempt:
                time.sleep(0.15)
            if transcript_path:
                # window=3: covers the tool_use line and the tool_result line even with one
                # intervening line (observed in some transcript shapes); cheap and harmless
                # to widen since _redact_strings is a no-op on any line with no match.
                redacted_this_hit |= redact_recent_lines(transcript_path, cls, pattern, max_lines=3)
            if sub_transcript_path and sub_transcript_path != transcript_path:
                # bus #49029/#49030: this call originated inside a subagent -- also redact
                # its own transcript file, which `transcript_path` above does not cover.
                redacted_this_hit |= redact_recent_lines(sub_transcript_path, cls, pattern, max_lines=3)
            for p in persisted_paths:
                redacted_this_hit |= redact_persisted_output(p, cls, pattern)
            if redacted_this_hit:
                break
        any_redacted |= redacted_this_hit
        redacted_by_class[cls] = redacted_by_class.get(cls, False) or redacted_this_hit

    seen_classes = set()
    for cls, match, pattern in all_hits:
        if cls in seen_classes:
            continue
        seen_classes.add(cls)
        _log_event(cls, tool_name, transcript_path, sub_transcript_path, session_id,
                   agent_id, agent_type, agent, cwd=cwd, tail_sha256=output_tail_hash)
        if is_fixture_call or _is_fixture_hit(cls, match):
            continue
        _page_orch_console(cls, tool_name, agent_id=agent_id, agent_type=agent_type,
                           redacted=redacted_by_class.get(cls, False))

    classes = ", ".join(cls for cls, _, _ in all_hits)
    if any_redacted:
        sys.stderr.write(
            f"secrets_output_scanner: this output contained secret-shaped content ({classes}); "
            "it has been redacted on disk and orch-console paged. Treat the underlying value "
            "as potentially exposed going forward.\n"
        )
    else:
        # The one thing the old message must never do: claim success it didn't verify.
        sys.stderr.write(
            f"secrets_output_scanner: this output contained secret-shaped content ({classes}) "
            "and orch-console was paged, but NO ON-DISK REDACTION ACTUALLY LANDED after retries "
            "-- the raw value may still be sitting in the transcript or a persisted-output file. "
            "This is the backstop failing to close the loop, not just the original leak -- "
            "treat it as still fully exposed and investigate why redaction didn't take.\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
