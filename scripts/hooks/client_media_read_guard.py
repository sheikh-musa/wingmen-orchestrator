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
"""
import glob
import json
import os
import re
import sys

_TG_MEDIA_RE = re.compile(r"^(.*)/logs/tg_media/(.+)$")


def is_console() -> bool:
    return os.environ.get("ORCH_BODY_ROLE") == "console" and not os.environ.get("CC_BASE_AGENT_ID")


def match_tg_media(path: str):
    """Return (repo_root, filename) if *path* resolves under a tg_media dir,
    at ANY depth, else None (cc-quality bus #52593/#52596: logs/tg_media has
    347 real files across 13 per-project subdirectories today -- irsyad,
    gazzabyte-irsyad, cosem-adcda, ... -- a one-level-only match left all of
    them unguarded). *filename* is reduced to the basename, since
    stage_client_file.py keys its export by basename stem regardless of
    which subdirectory the file arrived in (Path(original_name).stem in
    main())."""
    if not path:
        return None
    normalized = os.path.normpath(path).replace(os.sep, "/")
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
    sys.stderr.write(
        "BLOCKED by client_media_read_guard: this is raw inbound client media that hasn't been "
        "staged CLEAN yet (bus #52461/#52465, real incident -- a lane Read a genuine gov trainee "
        "gradebook screenshot before it was staged). Ask the console to run "
        "scripts/stage_client_file.py on it first and wait for a CLEAN verdict before reading it.\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
