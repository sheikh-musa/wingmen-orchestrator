#!/usr/bin/env python3
"""redact_transcript_pii.py — targeted, SIZE-PRESERVING PII redaction of a Claude Code
session transcript (.jsonl), per CAI-RESP-1409.

WHY SIZE-PRESERVING: the target transcript may be a LIVE session the coord is still
appending to. Its append file-descriptor sits at EOF; if we changed the byte length of any
historical line the offsets would shift under that fd and corrupt the log. So every redacted
line is rewritten to the EXACT original byte length (the JSON envelope is kept for /resume
threading, the PII payload is replaced with a marker, and the line is padded with trailing
spaces — which json.loads tolerates). Redaction only overwrites historical lines at their
existing offsets, so a concurrent EOF append is untouched.

NEVER PRINTS PII: it classifies target lines by markers (email/card patterns, base64/image
blocks, oversize lines) and reports only line numbers, block types, and byte counts. The raw
values are never emitted to stdout/stderr.

Target line = any line that contains an email address, a card fingerprint/last4 marker, an
image/base64 block, or is oversize (>20000 bytes) — a deliberately GENEROUS superset for a
card-data incident (cai: calibrate up on remedy-rigor). Non-target lines are left verbatim,
preserving the coord's legitimate unrelated work.

Usage:
  redact_transcript_pii.py <transcript.jsonl> --dry-run
  redact_transcript_pii.py <transcript.jsonl> --execute --backup <restricted_bak_path>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import shutil
from typing import List, Tuple

EMAIL = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# card-data markers seen in a Stripe export transcript (match on the LABELS, not values)
CARD = re.compile(rb"(Card Fingerprint|Card Last4|card_fingerprint|Fingerprint|Last4|Card Address|Card Name|Customer Email)", re.I)
IMG = re.compile(rb'"type"\s*:\s*"image"|base64')
OVERSIZE = 20000

MARKER = "[REDACTED-CAI1409: PII tool block removed]"


def is_target(raw: bytes) -> bool:
    return bool(
        EMAIL.search(raw)
        or CARD.search(raw)
        or IMG.search(raw)
        or len(raw) > OVERSIZE
    )


def redact_line(raw: bytes) -> Tuple[bytes, str]:
    """Return a same-byte-length redacted line + a short type label (no PII)."""
    orig_len = len(raw)  # includes trailing newline
    has_nl = raw.endswith(b"\n")
    body = raw[:-1] if has_nl else raw
    label = "opaque"
    try:
        obj = json.loads(body)
    except Exception:
        # Unparseable line: blank it entirely to a same-length filler (safety over fidelity).
        filler = ('{"redacted":"' + MARKER + '"}').encode()
        return _pad(filler, orig_len, has_nl), "unparseable->blanked"

    # Preserve the envelope; redact only the PII-bearing payload fields.
    for key in ("message", "toolUseResult", "content", "toolUseResultText"):
        if key in obj:
            label = key
            if isinstance(obj[key], dict):
                role = obj[key].get("role")
                obj[key] = {k: v for k, v in (("role", role),) if v is not None}
                obj[key]["content"] = MARKER
            else:
                obj[key] = MARKER
    obj["_cai1409_redacted"] = True

    compact = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(compact) + (1 if has_nl else 0) > orig_len:
        # Redacted form somehow longer (envelope itself oversize): fall back to a minimal
        # same-length blanking that drops the envelope but stays valid + fits.
        compact = ('{"_cai1409_redacted":true}').encode()
    return _pad(compact, orig_len, has_nl), label


def _pad(compact: bytes, orig_len: int, has_nl: bool) -> bytes:
    target = orig_len - (1 if has_nl else 0)
    if len(compact) < target:
        compact = compact + b" " * (target - len(compact))
    return compact + (b"\n" if has_nl else b"")


def scan(path: str) -> List[Tuple[int, int, int]]:
    """Return [(lineno, byte_offset, byte_len)] for target lines. No PII emitted."""
    targets = []
    off = 0
    with open(path, "rb") as f:
        for i, raw in enumerate(f, 1):
            if is_target(raw):
                targets.append((i, off, len(raw)))
            off += len(raw)
    return targets


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--backup", help="restricted path to copy the original to before editing")
    args = ap.parse_args(argv)

    if not os.path.exists(args.path):
        print(f"no such transcript: {args.path}", file=sys.stderr)
        return 2

    targets = scan(args.path)
    total = sum(t[2] for t in targets)
    print(f"transcript: {args.path}")
    print(f"target lines (PII candidates): {len(targets)}  ({total} bytes)")
    for (ln, off, blen) in targets:
        print(f"  line {ln}: offset {off}, {blen} bytes")

    if args.dry_run or not args.execute:
        print("DRY-RUN — nothing written.")
        return 0

    if not args.backup:
        print("--execute requires --backup <restricted_path>", file=sys.stderr)
        return 2

    # 1) forensic backup to a restricted location (has PII — caller must restrict perms).
    shutil.copy2(args.path, args.backup)
    os.chmod(args.backup, 0o600)
    print(f"backup (PII, restricted): {args.backup}")

    # 2) same-length in-place overwrite at each target offset.
    redacted = 0
    with open(args.path, "r+b") as f:
        for (ln, off, blen) in targets:
            f.seek(off)
            raw = f.read(blen)
            new, label = redact_line(raw)
            if len(new) != blen:
                print(f"  ABORT line {ln}: length mismatch {len(new)}!={blen}", file=sys.stderr)
                return 3
            f.seek(off)
            f.write(new)
            redacted += 1
            print(f"  redacted line {ln} ({label}), {blen} bytes preserved")
        f.flush()
        os.fsync(f.fileno())
    print(f"redacted {redacted} lines in place (size preserved).")

    # 3) verify: every line still parses; no email/card/base64 remains anywhere.
    bad = 0
    resid = 0
    with open(args.path, "rb") as f:
        for i, raw in enumerate(f, 1):
            try:
                json.loads(raw[:-1] if raw.endswith(b"\n") else raw)
            except Exception:
                bad += 1
                print(f"  VERIFY FAIL line {i}: not valid JSON", file=sys.stderr)
            if EMAIL.search(raw) or IMG.search(raw) or CARD.search(raw):
                resid += 1
                print(f"  VERIFY: residual PII marker still on line {i}", file=sys.stderr)
    print(f"verify: parse-failures={bad}, residual-PII-lines={resid}")
    return 0 if (bad == 0 and resid == 0) else 4


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
