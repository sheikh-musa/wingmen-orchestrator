#!/usr/bin/env python3
"""secret_hash_sweep.py — hash-identified, EXACT-SPAN, SIZE-PRESERVING redaction of real
secret VALUES out of Claude Code session transcripts (.jsonl), plus a fleetwide detect sweep.

WHY THIS EXISTS (orch-console P1 #48640): the regex outbound scrubber (nervous_system.
secret_redact) only knows a handful of secret *shapes* and misses bot tokens in odd spots,
DSN passwords, client keys, OAuth tokens. This tool instead takes the set of REAL secret
values (every value in the fleet's .env files) and redacts those exact byte spans wherever
they appear — catching what the regex scanner misses. It is the committed form of the
2026-09-30 hub sweep (#47234).

SAFETY INVARIANTS (all enforced here):
  * NEVER PRINTS A SECRET VALUE. Every secret is referred to ONLY by sha256[:8]. Reports
    carry per-file / per-hash COUNTS, never values.
  * SIZE-PRESERVING, IN-PLACE at existing offsets. The transcript may be a LIVE session the
    body is still appending to. We open r+b and overwrite each secret span with a marker
    PADDED to the exact original byte length, seeking to the span's offset — so the
    appending fd at EOF is never disturbed and no later byte offset shifts (same discipline
    as redact_transcript_pii.py / CAI-RESP-1409).
  * JSON-SAFE marker. The replacement uses only characters legal inside a JSON string
    ("[REDACTED:<hash8>]" then '#' padding, or '*'*len when the value is shorter than the
    marker), so every rewritten line still json.loads() — i.e. the session still loads.
  * REVERSIBLE. --execute backs each touched file up (0600) to --backup-dir before writing.
  * FAIL LOUD. Any step that can't be verified aborts that file and is reported; never a
    silent partial redaction.

Secret-value selection (build_secret_set): every KEY=VALUE value in the given env files whose
value is >= MIN_SECRET_LEN bytes (long enough that an exact match can't be benign prose), plus
the embedded password of any postgres DSN value. Trivial values are skipped by the length gate.

Usage:
  secret_hash_sweep.py --env <envfile>... --scan <jsonl>... --dry-run
  secret_hash_sweep.py --env <envfile>... --scan <jsonl|txt>... --execute --ledger <file>
  (globs are accepted for --env and --scan)
"""
from __future__ import annotations

import argparse
import glob as _glob
import hashlib
import json
import os
import re
import stat
import sys
from typing import Dict, List, Tuple

MIN_SECRET_LEN = 16          # floor for CREDENTIAL-shaped keys (_TOKEN/_KEY/_SECRET/_PASSWORD/_DSN)
# bus #56377 (NAZIM_MODEL "claude-fable-5-1", 16 chars, redacted as if a secret): a
# non-credential key's value must clear a HIGHER bar before it's secret-eligible --
# short config strings (model ids, role names, session names) are common and real
# credentials are essentially never this short anyway. Credential-shaped keys keep
# the lower MIN_SECRET_LEN floor (see _CREDENTIAL_KEY_RE below).
MIN_SECRET_LEN_NONCRED = 20
MIN_DSN_PW_LEN = 8           # DSN passwords are real secrets even when a bit shorter
_DSN_RE = re.compile(rb"postgres(?:ql)?://[^\s:@/]+:([^\s@/]+)@")
# ANY real-length Anthropic credential (OAuth sk-ant-oat…, API sk-ant-api…), manifest or not
# (orch-console #49038: one tool-results file held 4 real tokens; a musa2-only manifest saw 1).
# Real ones are ~100+ chars; >=60 after the prefix keeps short doc/example strings out.
_TOKEN_SHAPE_RE = re.compile(rb"sk-ant-[A-Za-z0-9_-]{60,}")
_ENV_LINE_RE = re.compile(rb"^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*=")

# Key-name shapes whose VALUE is public-by-design or a non-credential identifier — these are
# NOT secrets and redacting them would corrupt transcripts (e.g. SUPABASE_PROJECT_REF appears
# hundreds of times inside every supabase URL). A DSN/credential value is NEVER excluded here
# because it is caught by its own `@`-authority test below, not by key name.
_PUBLIC_KEY_RE = re.compile(
    r'(^NEXT_PUBLIC_|_PROJECT_REF$|^SUPABASE_PROJECT_REF$|_BIN$|_IPS$|_FROM$|'
    r'_TEAM_ID$|_MODE$|_REGION$|_ANON_KEY$|_ANON$|_BASE_URL$|^SUPABASE_URL$|_SUPABASE_URL$|'
    # bus #56377: a *_MODEL value (e.g. NAZIM_MODEL=claude-fable-5-1) is a public model
    # id, never a credential; *_ROLE/*_SESSION/*_PATH/*_DIR name a config string or
    # filesystem location, same trust class as the _BIN/_IPS entries above.
    r'_MODEL$|_ROLE$|_SESSION$|_PATH$|_DIR$)',
    re.I)

# orch-console #56377: a known-credential key name is the ONLY thing that justifies the
# LOWER MIN_SECRET_LEN floor for a short-ish value; everything else must clear
# MIN_SECRET_LEN_NONCRED. Named explicitly (not inferred from absence of a public-key
# match) so a brand-new, unrecognized key defaults to the SAFER higher floor.
_CREDENTIAL_KEY_RE = re.compile(r'(_TOKEN$|_KEY$|_SECRET$|_PASSWORD$|_DSN$)', re.I)

# bus #56377: a bare boolean/int config value (AUTO_WAKE_ENABLED=true, CONSOLE_PORT=8787)
# is never a credential, whatever its key is called or how long it happens to be.
_BOOL_INT_VALUE_RE = re.compile(r'^(true|false|[0-9]+)$', re.I)

# orch-console #51379 (bus #51371/#51377): SEED_USER_ID -- a plain identifier, not a
# credential -- hash-matched a current .env value (it legitimately reappears in prod query
# results) and paged a P1. An *_ID/*_UUID/*_ORG-suffixed key names an IDENTIFIER by
# convention (CLIENT_ID, ORG_ID, SEED_USER_ID, ...), never the secret itself, so it is
# excluded here the same way *_TEAM_ID already was -- explicit, named, tested
# (tests/test_secret_hash_sweep.py), not an ad hoc tweak to the key-name regex above.
_IDENTIFIER_KEY_RE = re.compile(r'(_ID$|_UUID$|_ORG$)', re.I)

# Value-shape exclusion, independent of key name: a bare UUID (8-4-4-4-12 hex) is never one
# of our secret classes by itself, whatever the key is called -- defense in depth for an
# identifier value under a key name _IDENTIFIER_KEY_RE doesn't happen to match.
_UUID_VALUE_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)


def _is_public_value(key: str, val: str) -> bool:
    """True when (key, val) is public config / a bare identifier / a filesystem path —
    anything that is NOT itself a credential."""
    if _PUBLIC_KEY_RE.search(key) or _IDENTIFIER_KEY_RE.search(key):
        return True
    if _UUID_VALUE_RE.match(val):
        return True
    if _BOOL_INT_VALUE_RE.match(val):
        return True
    # a plain http(s) URL with NO userinfo (`user:pass@`) in its authority is a public endpoint
    m = re.match(r'https?://([^/]*)', val)
    if m and '@' not in m.group(1):
        return True
    # an absolute filesystem path (e.g. GOOGLE_APPLICATION_CREDENTIALS=/Users/x/creds.json) is
    # a pointer, not the secret itself; redacting it would corrupt benign transcript text.
    if re.match(r'^/[^\s]+/[^\s]+$', val) and '@' not in val:
        return True
    return False


def hash8(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()[:8]


def _strip_env_value(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1]
    return v


def build_secret_set(env_paths: List[str]) -> Dict[bytes, str]:
    """Return {secret_value_bytes: hash8}. Values only — never logged in the clear."""
    secrets: Dict[bytes, str] = {}
    for p in env_paths:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            continue
        for line in lines:
            line = line.rstrip("\n")
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            key = key.strip()
            val = _strip_env_value(raw)
            if not val:
                continue
            # always pull a DSN's embedded password out as its own candidate (credential,
            # regardless of key name) BEFORE the public-value gate
            for m in _DSN_RE.finditer(("=" + val).encode("utf-8")):
                pw = m.group(1)
                if len(pw) >= MIN_DSN_PW_LEN:
                    secrets[pw] = hash8(pw)
            if _is_public_value(key, val):
                continue
            vb = val.encode("utf-8")
            floor = MIN_SECRET_LEN if _CREDENTIAL_KEY_RE.search(key) else MIN_SECRET_LEN_NONCRED
            if len(vb) >= floor:
                secrets[vb] = hash8(vb)
    return secrets


def shape_secrets(data: bytes, known: Dict[bytes, str]) -> Dict[bytes, str]:
    """Token-shaped values in *data* that the manifest does not already hold. {value: hash8}."""
    found: Dict[bytes, str] = {}
    for m in _TOKEN_SHAPE_RE.finditer(data):
        v = m.group(0)
        if v not in known:
            found[v] = hash8(v)
    return found


# An env-style ORIGINAL filename: .env, .env.local, .env.production, <name>.env (not examples).
_ENV_NAME_RE = re.compile(r"(^|/)(\.env(\.[\w.-]+)?|[\w.-]+\.env)$")
_ENV_NAME_EXCLUDE_RE = re.compile(r"\.(example|sample|template)$")
_origin_cache: Dict[str, Dict[str, str]] = {}


def _index_session_backups(root: str, sess: str) -> Dict[str, str]:
    """{backupFileName: absolute original path} from a session's transcript(s), by walking the
    PARSED JSON (regex was too brittle: three record shapes exist). Shapes Claude Code writes:
      1. {"<abs path>": {"backupFileName": …}}
      2. {"trackingPath": "<abs path>", "backup": {"backupFileName": …}}
      3. {"<relative path>": {"backupFileName": …, "realParentDir": "<abs dir>"}}  (gzb 10-02)
    Only an ABSOLUTE result counts; anything else stays unresolved (=> treated as a leak)."""
    idx: Dict[str, str] = {}

    def add(name, origin):
        if isinstance(name, str) and isinstance(origin, str) and origin.startswith("/"):
            idx[name] = os.path.normpath(origin)

    def walk(o):
        if isinstance(o, dict):
            tp, bk = o.get("trackingPath"), o.get("backup")
            if isinstance(bk, dict) and "backupFileName" in bk and isinstance(tp, str):
                if tp.startswith("/"):
                    add(bk["backupFileName"], tp)
                elif isinstance(bk.get("realParentDir"), str):
                    add(bk["backupFileName"], os.path.join(bk["realParentDir"], tp))
            for k, v in o.items():
                if isinstance(v, dict) and "backupFileName" in v and k not in ("backup",):
                    if k.startswith("/"):
                        add(v["backupFileName"], k)
                    elif isinstance(v.get("realParentDir"), str):
                        add(v["backupFileName"], os.path.join(v["realParentDir"], k))
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for tf in _glob.glob(os.path.join(root, "projects", "*", sess + ".jsonl")):
        try:
            with open(tf, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if "backupFileName" not in line:
                        continue
                    try:
                        walk(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            continue
    return idx


def file_history_origin(path: str) -> str | None:
    """The ORIGINAL file a ~/.claude/file-history/<session>/<backup>@vN snapshot was taken of,
    read from that session's transcript (Claude Code records
    "trackedFileBackups": {"<original path>": {"backupFileName": "<backup>@vN", ...}}).
    None when it can't be resolved; callers must then treat the snapshot as a possible leak."""
    norm = path.replace(os.sep, "/")
    i = norm.rfind("/file-history/")
    if i == -1:
        return None
    root = norm[:i]                                   # the ~/.claude dir
    parts = norm[i + len("/file-history/"):].split("/")
    if len(parts) != 2:
        return None
    sess, backup = parts
    key = root + "\0" + sess
    if key not in _origin_cache:
        _origin_cache[key] = _index_session_backups(root, sess)
    return _origin_cache[key].get(backup)


def classify(path: str, data: bytes) -> str:
    """'env-snapshot' = a Claude Code file-history backup OF AN ENV FILE (same trust boundary as
    the .env itself, #49038: classify, never page, never edit). Requires BOTH identity (the
    recorded original filename is env-named) AND shape (>=80% env lines). Shape alone is not
    enough (cc-quality #49063: an export-heavy SCRIPT passes it and would hide a real leak), and
    an unresolvable origin fails toward 'file-history' = a leak (pages, redacted). Else 'transcript'."""
    if "/file-history/" not in path.replace(os.sep, "/"):
        return "transcript"
    origin = file_history_origin(path)
    if not origin or not _ENV_NAME_RE.search(origin) or _ENV_NAME_EXCLUDE_RE.search(origin):
        return "file-history"
    lines = [l for l in data.splitlines() if l.strip() and not l.lstrip().startswith(b"#")]
    if lines and sum(1 for l in lines if _ENV_LINE_RE.match(l)) >= 0.8 * len(lines):
        return "env-snapshot"
    return "file-history"


def marker_for(value: bytes, h8: str) -> bytes:
    """A same-length, JSON-string-safe replacement for *value*."""
    m = b"[REDACTED:" + h8.encode() + b"]"
    if len(m) <= len(value):
        return m + b"#" * (len(value) - len(m))
    return b"*" * len(value)


def redact_bytes(raw: bytes, secrets: Dict[bytes, str]):
    """Replace every secret span in *raw* with a same-length marker.
    Returns (new_bytes, {hash8: count}, spans) where spans is a list of
    (offset_in_raw, length, hash8) — enough for an audit ledger, never the value.
    Length is preserved exactly, so every recorded offset stays valid."""
    out = bytearray(raw)
    spans: List[Tuple[int, int, str]] = []
    hits: Dict[str, int] = {}
    # longest-first + same-length replacement means offsets never shift and a shorter secret
    # that is a substring of a longer one can't re-match inside an already-written marker.
    for value in sorted(secrets, key=len, reverse=True):
        h8 = secrets[value]
        marker = marker_for(value, h8)
        start = 0
        while True:
            i = out.find(value, start)
            if i == -1:
                break
            out[i:i + len(value)] = marker
            spans.append((i, len(value), h8))
            hits[h8] = hits.get(h8, 0) + 1
            start = i + len(value)
    return bytes(out), hits, spans


def _line_offsets(data: bytes) -> List[Tuple[int, int]]:
    """List of (start, end_exclusive) byte spans for each line INCLUDING its trailing \\n."""
    spans, start = [], 0
    while start < len(data):
        nl = data.find(b"\n", start)
        if nl == -1:
            spans.append((start, len(data)))
            break
        spans.append((start, nl + 1))
        start = nl + 1
    return spans


def _file_unchanged_since(path: str, size_before: int, mtime_ns_before: int) -> bool:
    """orch-console #51400: Claude Code APPENDS to a live session's .jsonl continuously.
    An in-place rewrite at an offset computed from a stale read could land on a file whose
    earlier content shifted (or whose last line was mid-write when we read it). True only if
    *path*'s (size, mtime) right now still match the snapshot taken at read time -- the
    re-check happens immediately before the write in sweep_file, not at some earlier point,
    so the window this closes is the smallest it can be. False (anything differs, or the
    file is gone) means: abort this file for THIS cycle, no partial write -- the recurring
    sweep re-scans every run, so a real secret here is caught again next cycle, not lost."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    return st.st_size == size_before and st.st_mtime_ns == mtime_ns_before


def sweep_file(path: str, secrets: Dict[bytes, str], execute: bool,
               ledger_path: str | None = None, shape_match: bool = True) -> dict:
    """Detect (and, if execute, redact in place) secret spans in one file.

    Mode is chosen per file: a .jsonl transcript keeps the JSON gate (every redacted line must
    still json.loads, so the session still loads); any other file (tool-results/*.txt, #49038)
    is 'plaintext': the same exact-span, size-preserving, ledgered redaction without that gate.
    With shape_match (default), any real-length sk-ant- token is redacted too, manifest or not.
    An 'env-snapshot' (see classify) is detected + counted but NEVER edited.

    On --execute we keep NO plaintext backup (orch-console #48678: a plaintext copy is 714
    secrets at rest readable by every agent's user). Instead, if *ledger_path* is given, we
    append an audit record per redacted span — absolute offset + length + hash8 only, NEVER
    the value. Returns a values-free report dict."""
    rep = {"file": path, "matches_before": 0, "matches_after": 0, "lines_changed": 0,
           "spans_redacted": 0, "by_hash": {}, "size_before": 0, "size_after": 0,
           "size_preserved": True, "parses_ok": True, "error": None,
           "mode": "jsonl" if path.endswith(".jsonl") else "plaintext",
           "class": "transcript", "by_source": {}}
    try:
        with open(path, "rb") as fh:
            data = fh.read()
            st_before = os.fstat(fh.fileno())      # same fd as the read -- no separate race window
    except OSError as e:
        rep["error"] = "read failed: %s" % e.__class__.__name__
        return rep
    rep["size_before"] = len(data)
    rep["class"] = classify(path, data)
    shaped = shape_secrets(data, secrets) if shape_match else {}
    if shaped:
        secrets = dict(secrets); secrets.update(shaped)
    shaped_h8 = set(shaped.values())

    changes: List[Tuple[int, bytes]] = []            # (line_offset, new_line_bytes)
    ledger: List[dict] = []                          # abs-offset + length + hash8, no values
    for (s, e) in _line_offsets(data):
        line = data[s:e]
        new, hits, spans = redact_bytes(line, secrets)
        if hits:
            for h, c in hits.items():
                rep["by_hash"][h] = rep["by_hash"].get(h, 0) + c
                rep["matches_before"] += c
                src = "shape" if h in shaped_h8 else "manifest"
                rep["by_source"][src] = rep["by_source"].get(src, 0) + c
            if new != line:
                if len(new) != len(line):            # must never happen — fail loud
                    rep["error"] = "length drift on a redacted line — ABORT file"
                    rep["size_preserved"] = False
                    return rep
                if rep["mode"] == "jsonl":
                    try:
                        json.loads(new.rstrip(b"\n"))     # redacted line must still parse
                    except Exception:
                        rep["parses_ok"] = False
                        rep["error"] = "redacted line no longer valid JSON — ABORT file"
                        return rep
                changes.append((s, new))
                for (rel, ln, h8) in spans:
                    ledger.append({"file": path, "offset": s + rel, "length": ln, "hash8": h8})

    rep["lines_changed"] = len(changes)
    rep["spans_redacted"] = len(ledger)
    if not execute or rep["class"] == "env-snapshot":
        rep["size_after"] = rep["size_before"]
        rep["matches_after"] = rep["matches_before"] if rep["class"] == "env-snapshot" else 0
        return rep

    if changes and not _file_unchanged_since(path, rep["size_before"], st_before.st_mtime_ns):
        rep["error"] = "ABORTED: file changed since read (live-append race) — retry next cycle"
        rep["size_after"] = rep["size_before"]
        rep["matches_after"] = rep["matches_before"]
        rep["lines_changed"] = 0
        rep["spans_redacted"] = 0
        return rep

    if changes:
        if ledger_path:
            os.makedirs(os.path.dirname(os.path.abspath(ledger_path)), exist_ok=True)
            # append audit records; create 0600 if new
            newfile = not os.path.exists(ledger_path)
            with open(ledger_path, "a", encoding="utf-8") as lf:
                for rec in ledger:
                    lf.write(json.dumps(rec) + "\n")
            if newfile:
                os.chmod(ledger_path, stat.S_IRUSR | stat.S_IWUSR)   # 0600 (offsets+hash only)
        # in-place, at existing offsets — never rewrite the whole file (live-append safe)
        with open(path, "r+b") as fh:
            for off, new in changes:
                fh.seek(off)
                fh.write(new)
            fh.flush()
            os.fsync(fh.fileno())

    # re-read + re-scan to verify 0 remain and size held
    with open(path, "rb") as fh:
        after = fh.read()
    rep["size_after"] = len(after)
    rep["size_preserved"] = (rep["size_after"] == rep["size_before"])
    _, after_hits, _ = redact_bytes(after, secrets)
    rep["matches_after"] = sum(after_hits.values())
    return rep


def load_ledger_spans(ledger_path: str, hash8: str) -> Dict[str, List[Tuple[int, int]]]:
    """{file: [(offset, length), ...]} for every ledger record matching *hash8*,
    de-duplicated. Never reads a value — the ledger holds none."""
    by_file: Dict[str, set] = {}
    with open(ledger_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("hash8") != hash8:
                continue
            by_file.setdefault(rec["file"], set()).add((rec["offset"], rec["length"]))
    return {f: sorted(s) for f, s in by_file.items()}


def reverse_file(path: str, spans: List[Tuple[int, int]], value: bytes, h8: str,
                  execute: bool) -> dict:
    """Restore *value* at each (offset, length) in *spans* inside *path*, IFF the byte
    span currently holds exactly the marker `marker_for(value, h8)` produced it with —
    anything else (already restored, or genuinely different content) is left alone and
    counted, never forced. Same invariants as sweep_file: size-preserving, in-place at
    existing offsets, live-append-race-guarded, JSON-parse-gated for .jsonl, no plaintext
    backup (the ledger IS the audit trail; the value being restored here is, by
    construction, NOT a secret — that's the whole false-positive this undoes)."""
    rep = {"file": path, "spans_total": len(spans), "restored": 0, "already_ok": 0,
           "mismatched": 0, "error": None, "size_before": 0, "size_after": 0,
           "size_preserved": True, "parses_ok": True,
           "mode": "jsonl" if path.endswith(".jsonl") else "plaintext"}
    marker = marker_for(value, h8)
    try:
        with open(path, "rb") as fh:
            data = fh.read()
            st_before = os.fstat(fh.fileno())
    except OSError as e:
        rep["error"] = "read failed: %s" % e.__class__.__name__
        return rep
    rep["size_before"] = len(data)

    writes: List[Tuple[int, bytes]] = []
    touched_lines: set = set()
    line_spans = _line_offsets(data) if rep["mode"] == "jsonl" else [(0, len(data))]
    for off, length in spans:
        if off < 0 or off + length > len(data):
            rep["mismatched"] += 1
            continue
        cur = data[off:off + length]
        if cur == value:
            rep["already_ok"] += 1
            continue
        if cur != marker:
            rep["mismatched"] += 1
            continue
        writes.append((off, value))
        for (s, e) in line_spans:
            if s <= off < e:
                touched_lines.add((s, e))
                break

    if rep["mismatched"]:
        rep["error"] = "%d span(s) hold neither the expected marker nor the restore value — ABORT file, no writes" % rep["mismatched"]
        rep["size_after"] = rep["size_before"]
        return rep

    if not writes:
        rep["size_after"] = rep["size_before"]
        return rep

    if rep["mode"] == "jsonl":
        buf = bytearray(data)
        for off, val in writes:
            buf[off:off + len(val)] = val
        for (s, e) in touched_lines:
            try:
                json.loads(bytes(buf[s:e]).rstrip(b"\n"))
            except Exception:
                rep["error"] = "restored line no longer valid JSON — ABORT file, no writes"
                rep["size_after"] = rep["size_before"]
                return rep

    if not execute:
        rep["restored"] = len(writes)   # verify-only: what WOULD be restored
        rep["size_after"] = rep["size_before"]
        return rep

    if not _file_unchanged_since(path, rep["size_before"], st_before.st_mtime_ns):
        rep["error"] = "ABORTED: file changed since read (live-append race) — retry next pass"
        rep["size_after"] = rep["size_before"]
        return rep

    with open(path, "r+b") as fh:
        for off, val in writes:
            fh.seek(off)
            fh.write(val)
        fh.flush()
        os.fsync(fh.fileno())

    with open(path, "rb") as fh:
        after = fh.read()
    rep["size_after"] = len(after)
    rep["size_preserved"] = (rep["size_after"] == rep["size_before"])
    rep["restored"] = sum(1 for off, val in writes if after[off:off + len(val)] == val)
    if rep["restored"] != len(writes):
        rep["error"] = "post-write readback mismatch — some span(s) did not take"
    return rep


def detect_false_positive_shapes(reports: List[dict], value_len_by_hash8: Dict[str, int],
                                  min_spans: int = 50, min_files: int = 3,
                                  max_len: int = 20) -> List[Tuple[str, int, int, int]]:
    """bus #56377: a single SHORT value hitting a LARGE number of spans across MANY
    files is the false-positive shape (a short, common config string that leaked into
    the manifest), not a real leak -- a genuine credential is long and appears in a
    handful of places at most. Returns [(hash8, spans, files, length), ...] for every
    hash8 whose aggregate across *reports* trips all three thresholds. Shape-matched
    sk-ant- hits (not in value_len_by_hash8) are always long and never trip this."""
    spans: Dict[str, int] = {}
    files: Dict[str, set] = {}
    for r in reports:
        for h, c in r["by_hash"].items():
            spans[h] = spans.get(h, 0) + c
            files.setdefault(h, set()).add(r["file"])
    suspects = []
    for h, n in spans.items():
        length = value_len_by_hash8.get(h)
        if length is not None and length < max_len and n > min_spans and len(files[h]) > min_files:
            suspects.append((h, n, len(files[h]), length))
    return suspects


def _expand(globs: List[str]) -> List[str]:
    out: List[str] = []
    for g in globs:
        hits = _glob.glob(os.path.expanduser(g))
        out.extend(hits if hits else ([g] if os.path.exists(os.path.expanduser(g)) else []))
    # de-dup, keep order
    seen, uniq = set(), []
    for p in out:
        rp = os.path.realpath(p)
        if rp not in seen and os.path.isfile(rp):
            seen.add(rp); uniq.append(p)
    return uniq


def _main_reverse(args) -> int:
    if not args.ledger:
        print("secret_hash_sweep --reverse: --ledger is required", file=sys.stderr)
        return 2
    value = args.restore_value.encode("utf-8")
    computed = hash8(value)
    if computed != args.restore_hash8:
        print("secret_hash_sweep --reverse: REFUSE — sha256(--restore-value)[:8]=%s != "
              "--restore-hash8=%s. Wrong value for this hash; nothing touched."
              % (computed, args.restore_hash8), file=sys.stderr)
        return 2

    by_file = load_ledger_spans(args.ledger, args.restore_hash8)
    if not by_file:
        print("secret_hash_sweep --reverse: no ledger entries for hash8=%s" % args.restore_hash8)
        return 0

    reports = [reverse_file(f, spans, value, args.restore_hash8, execute=args.reverse_execute)
               for f, spans in sorted(by_file.items())]
    restored = sum(r["restored"] for r in reports)
    already_ok = sum(r["already_ok"] for r in reports)
    mismatched = sum(r["mismatched"] for r in reports)
    errors = [r for r in reports if r["error"]]

    mode = "EXECUTE" if args.reverse_execute else "VERIFY-ONLY"
    print("secret_hash_sweep --reverse [%s]: hash8=%s across %d file(s)."
          % (mode, args.restore_hash8, len(reports)))
    for r in reports:
        if r["restored"] or r["mismatched"] or r["error"]:
            print("  %s | spans=%d restored=%d already_ok=%d mismatched=%d size_preserved=%s%s"
                  % (r["file"], r["spans_total"], r["restored"], r["already_ok"], r["mismatched"],
                     r["size_preserved"], (" ERROR=%s" % r["error"]) if r["error"] else ""))
    print("  TOTAL restored=%d already_ok=%d mismatched=%d files=%d errors=%d"
          % (restored, already_ok, mismatched, len(reports), len(errors)))
    return 4 if errors else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="hash-identified exact-span secret redaction sweep")
    ap.add_argument("--env", nargs="+", help="env file(s)/glob(s) holding real secret values")
    ap.add_argument("--scan", nargs="+",
                    help="file(s)/glob(s) to sweep: .jsonl transcripts (JSON-gated) or plaintext (e.g. tool-results/*.txt)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="detect + report only (default)")
    g.add_argument("--execute", action="store_true", help="redact in place (requires --ledger)")
    g.add_argument("--reverse", action="store_true",
                   help="undo a false-positive redaction from the ledger: restore --restore-value "
                        "at every --restore-hash8 span. Verify-only unless --reverse-execute is also given.")
    ap.add_argument("--ledger", help="append-only audit ledger (offset+length+hash8, NEVER values); "
                                     "required with --execute or --reverse")
    ap.add_argument("--restore-hash8", help="--reverse: the hash8 identifying the false-positive span")
    ap.add_argument("--restore-value", help="--reverse: the ORIGINAL (non-secret, by construction) "
                                             "value to restore; verified against --restore-hash8 before any write")
    ap.add_argument("--reverse-execute", action="store_true",
                    help="--reverse: actually write; without this, --reverse only verifies + reports counts")
    ap.add_argument("--report-json", action="store_true", help="emit the per-file report as JSON")
    ap.add_argument("--no-shape-match", action="store_true",
                    help="manifest values only; skip the any-real-length sk-ant- token matcher")
    args = ap.parse_args(argv)

    if args.reverse:
        if not args.restore_hash8 or args.restore_value is None:
            print("secret_hash_sweep --reverse: --restore-hash8 and --restore-value are required",
                  file=sys.stderr)
            return 2
        return _main_reverse(args)

    if not args.env or not args.scan:
        print("secret_hash_sweep: --env and --scan are required outside --reverse mode", file=sys.stderr)
        return 2

    if args.execute and not args.ledger:
        print("secret_hash_sweep: --execute requires --ledger (audit trail; no plaintext backups)",
              file=sys.stderr)
        return 2

    env_files = _expand(args.env)
    secrets = build_secret_set(env_files)
    if not secrets:
        print("secret_hash_sweep: REFUSE — built 0 secret values from %d env file(s); "
              "cannot run a sweep with an empty manifest" % len(env_files), file=sys.stderr)
        return 3

    scan_files = _expand(args.scan)
    guard_tripped: List[Tuple[str, int, int, int]] = []
    if args.execute:
        # bus #56377: measure the shape BEFORE committing to --execute. A detect-only
        # pre-pass costs a second read of each file but never risks writing on a shape
        # that looks like a false positive (see detect_false_positive_shapes).
        detect_reports = [sweep_file(p, secrets, False, None, shape_match=not args.no_shape_match)
                           for p in scan_files]
        value_len_by_hash8 = {h: len(v) for v, h in secrets.items()}
        guard_tripped = detect_false_positive_shapes(detect_reports, value_len_by_hash8)
        if guard_tripped:
            reports = detect_reports
        else:
            reports = [sweep_file(p, secrets, True, args.ledger, shape_match=not args.no_shape_match)
                       for p in scan_files]
    else:
        reports = [sweep_file(p, secrets, False, None, shape_match=not args.no_shape_match)
                   for p in scan_files]

    if guard_tripped:
        print("secret_hash_sweep: EXECUTE REFUSED — false-positive shape detected, falling back "
              "to DETECT:", file=sys.stderr)
        for h, n, nf, length in guard_tripped:
            print("  hash8=%s spans=%d files=%d length=%d (>%d spans, >%d files, <%d chars — "
                  "looks like a short config value, not a leak)"
                  % (h, n, nf, length, 50, 3, 20), file=sys.stderr)

    total_before = sum(r["matches_before"] for r in reports)
    paging = [r for r in reports if r["class"] != "env-snapshot"]
    total_paging = sum(r["matches_before"] for r in paging)
    env_snapshot_hits = total_before - total_paging
    total_after = sum(r["matches_after"] for r in paging)    # snapshots are never edited
    errors = [r for r in reports if r["error"]]

    if args.report_json:
        print(json.dumps({"env_files": len(env_files), "secrets_in_manifest": len(secrets),
                          "scanned": len(scan_files), "total_before": total_before,
                          "total_after": total_after, "total_paging": total_paging,
                          "env_snapshot_hits": env_snapshot_hits, "guard_tripped": guard_tripped,
                          "reports": reports}, indent=2))
    else:
        mode = "EXECUTE-REFUSED(guard)" if guard_tripped else ("EXECUTE" if args.execute else "DRY-RUN")
        print("secret_hash_sweep [%s]: %d secret value(s) in manifest from %d env file(s); "
              "scanned %d transcript(s)." % (mode, len(secrets), len(env_files), len(scan_files)))
        for r in reports:
            if r["matches_before"] or r["error"]:
                print("  %s | %s/%s before=%d after=%d lines_changed=%d size_preserved=%s parses_ok=%s%s"
                      % (r["file"], r["mode"], r["class"], r["matches_before"], r["matches_after"],
                         r["lines_changed"], r["size_preserved"], r["parses_ok"],
                         (" ERROR=%s" % r["error"]) if r["error"] else ""))
        print("  TOTAL before=%d after=%d  paging=%d env_snapshot_hits=%d  files_with_hits=%d  errors=%d"
              % (total_before, total_after, total_paging, env_snapshot_hits,
                 sum(1 for r in reports if r["matches_before"]), len(errors)))

    # exit non-zero if anything failed, or (on execute) if any secret survived
    if errors:
        return 4
    if guard_tripped:
        return 6
    if args.execute and total_after != 0:
        return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
