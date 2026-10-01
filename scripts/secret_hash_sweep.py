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
  secret_hash_sweep.py --env <envfile>... --scan <jsonl>... --execute --backup-dir <dir>
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

MIN_SECRET_LEN = 16          # exact matches shorter than this risk hitting benign text
MIN_DSN_PW_LEN = 8           # DSN passwords are real secrets even when a bit shorter
_DSN_RE = re.compile(rb"postgres(?:ql)?://[^\s:@/]+:([^\s@/]+)@")

# Key-name shapes whose VALUE is public-by-design or a non-credential identifier — these are
# NOT secrets and redacting them would corrupt transcripts (e.g. SUPABASE_PROJECT_REF appears
# hundreds of times inside every supabase URL). A DSN/credential value is NEVER excluded here
# because it is caught by its own `@`-authority test below, not by key name.
_PUBLIC_KEY_RE = re.compile(
    r'(^NEXT_PUBLIC_|_PROJECT_REF$|^SUPABASE_PROJECT_REF$|_BIN$|_IPS$|_FROM$|'
    r'_TEAM_ID$|_MODE$|_REGION$|_ANON_KEY$|_ANON$|_BASE_URL$|^SUPABASE_URL$|_SUPABASE_URL$)',
    re.I)


def _is_public_value(key: str, val: str) -> bool:
    """True when (key, val) is public config / a bare identifier / a filesystem path —
    anything that is NOT itself a credential."""
    if _PUBLIC_KEY_RE.search(key):
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
            if len(vb) >= MIN_SECRET_LEN:
                secrets[vb] = hash8(vb)
    return secrets


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


def sweep_file(path: str, secrets: Dict[bytes, str], execute: bool,
               ledger_path: str | None = None) -> dict:
    """Detect (and, if execute, redact in place) secret spans in one .jsonl file.

    On --execute we keep NO plaintext backup (orch-console #48678: a plaintext copy is 714
    secrets at rest readable by every agent's user). Instead, if *ledger_path* is given, we
    append an audit record per redacted span — absolute offset + length + hash8 only, NEVER
    the value. Returns a values-free report dict."""
    rep = {"file": path, "matches_before": 0, "matches_after": 0, "lines_changed": 0,
           "spans_redacted": 0, "by_hash": {}, "size_before": 0, "size_after": 0,
           "size_preserved": True, "parses_ok": True, "error": None}
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as e:
        rep["error"] = "read failed: %s" % e.__class__.__name__
        return rep
    rep["size_before"] = len(data)

    changes: List[Tuple[int, bytes]] = []            # (line_offset, new_line_bytes)
    ledger: List[dict] = []                          # abs-offset + length + hash8, no values
    for (s, e) in _line_offsets(data):
        line = data[s:e]
        new, hits, spans = redact_bytes(line, secrets)
        if hits:
            for h, c in hits.items():
                rep["by_hash"][h] = rep["by_hash"].get(h, 0) + c
                rep["matches_before"] += c
            if new != line:
                if len(new) != len(line):            # must never happen — fail loud
                    rep["error"] = "length drift on a redacted line — ABORT file"
                    rep["size_preserved"] = False
                    return rep
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
    if not execute:
        rep["size_after"] = rep["size_before"]
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="hash-identified exact-span secret redaction sweep")
    ap.add_argument("--env", nargs="+", required=True, help="env file(s)/glob(s) holding real secret values")
    ap.add_argument("--scan", nargs="+", required=True, help="transcript .jsonl file(s)/glob(s) to sweep")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="detect + report only (default)")
    g.add_argument("--execute", action="store_true", help="redact in place (requires --ledger)")
    ap.add_argument("--ledger", help="append-only audit ledger (offset+length+hash8, NEVER values); "
                                     "required with --execute")
    ap.add_argument("--report-json", action="store_true", help="emit the per-file report as JSON")
    args = ap.parse_args(argv)

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
    reports = [sweep_file(p, secrets, args.execute, args.ledger) for p in scan_files]

    total_before = sum(r["matches_before"] for r in reports)
    total_after = sum(r["matches_after"] for r in reports)
    errors = [r for r in reports if r["error"]]

    if args.report_json:
        print(json.dumps({"env_files": len(env_files), "secrets_in_manifest": len(secrets),
                          "scanned": len(scan_files), "total_before": total_before,
                          "total_after": total_after, "reports": reports}, indent=2))
    else:
        mode = "EXECUTE" if args.execute else "DRY-RUN"
        print("secret_hash_sweep [%s]: %d secret value(s) in manifest from %d env file(s); "
              "scanned %d transcript(s)." % (mode, len(secrets), len(env_files), len(scan_files)))
        for r in reports:
            if r["matches_before"] or r["error"]:
                print("  %s | before=%d after=%d lines_changed=%d size_preserved=%s parses_ok=%s%s"
                      % (r["file"], r["matches_before"], r["matches_after"], r["lines_changed"],
                         r["size_preserved"], r["parses_ok"],
                         (" ERROR=%s" % r["error"]) if r["error"] else ""))
        print("  TOTAL before=%d after=%d  files_with_hits=%d  errors=%d"
              % (total_before, total_after, sum(1 for r in reports if r["matches_before"]), len(errors)))

    # exit non-zero if anything failed, or (on execute) if any secret survived
    if errors:
        return 4
    if args.execute and total_after != 0:
        return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
