#!/usr/bin/env python3
"""PreToolUse guard: a lane must not Read raw inbound Telegram client media
(orchestrator/logs/tg_media/**) until it has been staged CLEAN.

Real incident (bus #52461, 2026-10-05): a lane Read a genuine UAE-gov trainee
gradebook screenshot directly out of logs/tg_media -- the whole point of
stage_client_file.py (orch-console #51060: "a lane must never ... read raw
cell values") applies just as much to an image as to an xlsx/docx/pdf, but
nothing stopped a lane from just Read-ing the raw file and skipping staging
entirely. Enforced here, not left to a lane remembering to run the stager
first (orch-console #52465: "it won't stop the next lane").

Scope: fires only on tool_name == "Read" whose file_path resolves under some
"<repo>/logs/tg_media/" directory. Exempt: the console body (ORCH_BODY_ROLE
=console, no CC_BASE_AGENT_ID -- same discriminator as console_irsyad_guard,
see reference_lane_vs_console_discriminator_is_cc_base_agent_id) -- the
console is who runs the stager against the raw file in the first place
(stage_client_file.py docstring: "staged here first, by a human"), so it
necessarily needs direct access. The stager script itself is separately
exempt by construction: it opens files via Python's own open()/PIL, never
via Claude's Read tool, so this guard never sees its internal reads at all.

"Staged CLEAN" is detected the same way stage_client_file.py's own --export
marks CLEAN: it writes reports/client-file-staging/<op_id>/<stem>.md, and
HOLD writes nothing, ever (fail-closed by construction in the stager itself)
-- so an exported report with this file's exact stem anywhere under that
tree is fail-closed proof a CLEAN run happened on it, UNLESS that report is
itself a structure-only (sensitive-channel) export, which never counts (see
is_staged_clean). No op_id plumbing needed: tg_media filenames are already
collision-proof per-file (channel_updateid_fileuniqueid, bus #47469), so the
stem alone is specific enough. Matches at ANY depth under tg_media/, not
just directly inside it -- logs/tg_media/<project>/<file> is the real,
standing layout (op#15741 per-project subdir routing).

Exit 2 + stderr = the tool call is refused and the reason is shown to the model.

Read-attestation allow-path (Musa op#20593): cc-irsyad-coord sometimes must Read
a gazzabyte-irsyad_ media file directly to build an irsyad feature. That is
allowed ONLY IF it first ran `stage_client_file.py attest-read` against the file,
which appends a READ-ATTEST record to reports/client-file-staging/
_operator_attested_exports.log. This guard then allows the Read only while a
record exists for that EXACT realpath whose stored sha256 == the file's CURRENT
sha256, written within READ_ATTEST_TTL_MIN minutes. The sha256 binding means a
file swapped after the attest cannot ride a stale record. Every other case
(other base, other channel, no/stale/mismatched record) stays denied, and the
console-only exemption is unchanged.
"""
from __future__ import annotations

import datetime
import glob
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from scripts.lib.agent_identity import resolve_base_agent_id_for_hook  # noqa: E402

_TG_MEDIA_RE = re.compile(r"^(.*)/logs/tg_media/(.+)$")

# ── read-attestation allow-path (Musa op#20593) ─────────────────────────────
# A READ-ATTEST record is valid only this many minutes after it was written.
READ_ATTEST_TTL_MIN = 15
# The ONLY base agent, and the ONLY channel prefix, the allow-path ever applies
# to. Both match stage_client_file.py's attest-read write-refusal constants.
_READ_ATTEST_REQUIRED_BASE = "cc-irsyad-coord"
_IRSYAD_CHANNEL_PREFIX = "gazzabyte-irsyad_"
# First field of a READ-ATTEST record, distinguishing it from an operator-
# attested export line (whose first field is an ISO timestamp) in the shared log.
_READ_ATTEST_RECORD_TAG = "READ-ATTEST"
# The shared append-only audit log, relative to the repo root.
_ATTEST_LOG_RELPATH = ("reports", "client-file-staging", "_operator_attested_exports.log")


def is_console() -> bool:
    return os.environ.get("ORCH_BODY_ROLE") == "console" and not os.environ.get("CC_BASE_AGENT_ID")


def base_agent_id(cwd: str | None = None) -> str:
    """The lane's resolved base agent id. Tries CC_BASE_AGENT_ID first (the
    same discriminator is_console() keys off, and the same env var
    stage_client_file.py resolves the attesting agent from); when the hook
    subprocess doesn't inherit it (bus #68342 — confirmed recurring for
    cc-irsyad-coord's attest-read allow-path below), falls back to the
    per-worktree marker keyed by *cwd* (see resolve_base_agent_id_for_hook)."""
    return resolve_base_agent_id_for_hook(cwd)


def sha256_file(path: str):
    """Hex sha256 of a file, reading bytes only to hash them (never to emit
    content); None if the file can't be read."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _parse_attest_ts(raw: str):
    """Parse a READ-ATTEST record's ISO timestamp to an aware UTC datetime;
    None if unparseable (caller treats as not-fresh — fail-closed)."""
    try:
        dt = datetime.datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)


def read_attest_entries(repo_root: str) -> list[dict]:
    """Parse the READ-ATTEST records out of the shared append-only audit log
    under *repo_root*. Non-READ-ATTEST lines (operator-attested exports) and
    malformed lines are skipped. Returns dicts with ts/agent_id/file_path/sha256."""
    log = os.path.join(repo_root, *_ATTEST_LOG_RELPATH)
    entries: list[dict] = []
    try:
        with open(log, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return entries
    for line in lines:
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 7 or parts[0] != _READ_ATTEST_RECORD_TAG:
            continue
        entries.append({
            "ts": _parse_attest_ts(parts[1]),
            "agent_id": parts[2],
            "file_path": parts[3],
            "sha256": parts[4],
        })
    return entries


def _has_fresh_read_attestation(file_path, current_sha256, now, log_entries) -> bool:
    """PURE decision core (no I/O): is there a READ-ATTEST record for THIS exact
    *file_path* whose stored sha256 == *current_sha256*, written no more than
    READ_ATTEST_TTL_MIN minutes before *now*? The sha256 match is what prevents a
    swapped file from riding a stale record; a future-dated or unparseable ts is
    treated as not-fresh (fail-closed)."""
    ttl = datetime.timedelta(minutes=READ_ATTEST_TTL_MIN)
    for e in log_entries:
        if e.get("file_path") != file_path:
            continue
        if e.get("sha256") != current_sha256:
            continue
        ts = e.get("ts")
        if ts is None:
            continue
        if datetime.timedelta(0) <= (now - ts) <= ttl:
            return True
    return False


def match_tg_media(path: str):
    """Return (repo_root, filename) if *path* resolves under a tg_media dir,
    at ANY depth, else None (cc-quality bus #52593/#52596: logs/tg_media has
    347 real files across 13 per-project subdirectories today -- irsyad,
    gazzabyte-irsyad, cosem-adcda, ... -- a one-level-only match left all of
    them unguarded). *filename* is reduced to the basename, since
    stage_client_file.py keys its export by basename stem regardless of
    which subdirectory the file arrived in (Path(original_name).stem in
    main()). Resolves symlinks via realpath, not just normpath (cc-storefront
    opus minors-lens F1, bus #52650): a symlink whose own path doesn't contain
    logs/tg_media/ but points INTO it would otherwise evade the regex
    entirely."""
    if not path:
        return None
    normalized = os.path.realpath(path).replace(os.sep, "/")
    m = _TG_MEDIA_RE.match(normalized)
    if not m:
        return None
    return m.group(1), os.path.basename(m.group(2))


def is_staged_clean(repo_root: str, filename: str) -> bool:
    """A CLEAN export must exist for *filename*'s stem, AND it must be a
    FULL export, not a structure-only (sensitive-channel) one (cc-quality
    bus #52593/#52596). Both export_to_markdown() and export_structure_only()
    write to the identical reports/client-file-staging/<op_id>/<stem>.md
    path, and export_structure_only() always writes the literal
    "(structure only" sentinel -- its presence means content was
    deliberately withheld on a sensitive channel, which must NOT be enough
    to unlock a Read of the raw file; that would defeat the sensitive-
    channel override's entire purpose the moment the two mechanisms meet."""
    stem = os.path.splitext(filename)[0]
    pattern = os.path.join(repo_root, "reports", "client-file-staging", "*", stem + ".md")
    for match in glob.glob(pattern):
        try:
            content = open(match, encoding="utf-8").read()
        except OSError:
            continue
        if "(structure only" in content:
            continue
        return True
    return False


def main() -> int:
    if is_console():
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.stderr.write(
            "BLOCKED by client_media_read_guard: guard could not read the tool input; refusing "
            "(fail-closed, bus #52461).\n"
        )
        return 2
    if payload.get("tool_name") != "Read":
        return 0
    tool_input = payload.get("tool_input") or {}
    path = tool_input.get("file_path") or ""
    match = match_tg_media(path)
    if match is None:
        return 0
    repo_root, filename = match
    if is_staged_clean(repo_root, filename):
        return 0
    # Read-attestation allow-path (Musa op#20593): cc-irsyad-coord may Read a
    # gazzabyte-irsyad_ media file IFF it holds a fresh, sha256-matched
    # READ-ATTEST record for this exact file. All three conditions must hold;
    # any other base, channel, or a missing/stale/mismatched record falls
    # through to the unchanged deny below.
    if base_agent_id(payload.get("cwd")) == _READ_ATTEST_REQUIRED_BASE and filename.startswith(_IRSYAD_CHANNEL_PREFIX):
        real = os.path.realpath(path)
        current_sha = sha256_file(real)
        if current_sha is not None:
            now = datetime.datetime.now(datetime.timezone.utc)
            if _has_fresh_read_attestation(real, current_sha, now, read_attest_entries(repo_root)):
                return 0
    sys.stderr.write(
        "BLOCKED by client_media_read_guard: this is raw inbound client media that hasn't been "
        "staged CLEAN yet (bus #52461/#52465, real incident -- a lane Read a genuine gov trainee "
        "gradebook screenshot before it was staged). Ask the console to run "
        "scripts/stage_client_file.py on it first and wait for a CLEAN verdict before reading it. "
        "(cc-irsyad-coord building an irsyad feature: run scripts/stage_client_file.py attest-read "
        "--file <path> --purpose ... --directive op#20593 first — valid 15 min, bound to the file's sha256.)\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
