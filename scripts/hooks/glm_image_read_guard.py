#!/usr/bin/env python3
"""PreToolUse guard: a PROVIDER-ROUTED lane must not ingest an IMAGE file — a
hard data-residency wall.

WHY (residency). Some fleet lanes are routed to a THIRD-PARTY, non-Anthropic
provider — today z.ai GLM, a Singapore endpoint — by scripts/lib/
provider_routing.sh, which exports ANTHROPIC_BASE_URL (e.g.
https://api.z.ai/api/anthropic) + ANTHROPIC_AUTH_TOKEN into the lane's env at
launch (OpenRouter/Moonshot/DeepSeek/Qwen rows share the same shape). A normal
Claude lane has ANTHROPIC_BASE_URL UNSET. The provider_policy table lets some
lanes (cosem) run on GLM "text_only=true"; the HARD rule is that client
IMAGE/visual data — screenshots, photos, scanned IDs — must NEVER be sent to a
non-Anthropic provider. cosem-platform lanes Read image proofs (screenshots),
so before any routed lane can ingest an image, this guard BLOCKS it.

The vector is ingestion into the model transcript: Claude's Read tool renders an
image file visually to the model (the primary case), and a Bash content-dumper
(cat/base64/xxd/...) on an image path pushes its bytes into the transcript the
same way. Either way the bytes then travel to whatever endpoint ANTHROPIC_BASE_
URL points at. This guard refuses both for a routed lane.

ROUTING DISCRIMINATOR (precise, fail-closed). A lane is PROVIDER-ROUTED when
ANTHROPIC_BASE_URL is set to a non-empty value whose host is NOT an
anthropic.com host (api.anthropic.com or any *.anthropic.com). Anything else —
unset, empty, or an anthropic.com host — is a normal Claude lane and this guard
is a PURE NO-OP (exit 0, nothing parsed, nothing blocked). A set-but-
unparseable base URL is treated as routed (fail-closed): we never assume a
present override is safe. The hook reads the claude process's OWN inherited env
(what provider_routing.sh exported at launch), so a command that re-sources a
.env cannot change the verdict.

SCOPE / no over-block. Only IMAGE files are refused (extensions below); a
routed lane Reading .py/.md/.json/etc. is untouched, and a Bash command that
merely lists/moves/git-adds an image (no content ingestion) is untouched. We do
NOT try to distinguish "client" from "non-client" images: for a routed lane the
residency rule is categorical, and we cannot reliably tell them apart — so every
image is refused while the lane is routed. The cost is a routed lane being told
to read a decorative PNG on Anthropic instead; the residency downside of the
reverse is unacceptable.

Exit 2 + stderr = the tool call is refused and the reason is shown to the model.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from urllib.parse import urlparse

# Image / visual-document extensions. PDF is INCLUDED deliberately: the residency
# rule names "scanned IDs", and scanned IDs / visual proofs routinely arrive as
# PDF, which carries rasterised client imagery just as a screenshot does. .tif is
# the common alias of .tiff. Vector/text formats (.svg) are NOT here — they are
# XML text the model reads as text, not rasterised client imagery, and blocking
# repo SVG assets would over-block; a determined raster-in-SVG case is accepted
# as a residual gap, same false-negative-tolerant stance as the fleet's other
# heuristic guards.
IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif",
    ".bmp", ".tiff", ".tif", ".pdf",
}

# Bash leading commands that dump a FILE's CONTENT to stdout (and so into the
# transcript). Mirrors secrets_transcript_guard's print/binary-dump sets — a
# command whose operative program is one of these, with an image path argument,
# ingests the image. `ls`/`mv`/`cp`/`git add`/`file`/`stat` are NOT here: they
# touch the path without ingesting its bytes, so they are not over-blocked.
CONTENT_DUMP_LEADING_RE = re.compile(
    r"^(cat|head|tail|less|more|base64|strings|xxd|od|hexdump|dd|nl|tac|rev)\b"
)
# Strip a leading run of VAR=val assignments so `FOO=bar cat img.png` still
# resolves to `cat` as the operative command (same idea as the other guards).
ASSIGNMENT_PREFIX_RE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+")
STATEMENT_SPLIT_RE = re.compile(r"\n|;|&&|\|\|")


def _base_url() -> str:
    return (os.environ.get("ANTHROPIC_BASE_URL") or "").strip()


def _is_anthropic_host(host: str) -> bool:
    host = (host or "").strip().lower()
    # Strip a possible port.
    host = host.split(":", 1)[0]
    return host == "anthropic.com" or host == "api.anthropic.com" or host.endswith(".anthropic.com")


def is_provider_routed() -> bool:
    """True when the lane is routed to a non-Anthropic provider. False for an
    unset/empty base URL or an anthropic.com host. Fail-closed: a non-empty
    base URL we cannot parse a trusted anthropic host out of is treated as
    routed."""
    url = _base_url()
    if not url:
        return False
    try:
        host = urlparse(url).hostname
    except Exception:
        return True  # set but unparseable -> fail-closed -> routed
    if not host:
        return True  # e.g. "not a url" -> no host -> fail-closed -> routed
    return not _is_anthropic_host(host)


def _is_image_path(path: str) -> bool:
    if not path:
        return False
    return os.path.splitext(path.strip())[1].lower() in IMAGE_EXTENSIONS


def _leading_command(segment: str) -> str:
    return ASSIGNMENT_PREFIX_RE.sub("", segment.strip())


def _bash_dumps_image(command: str) -> str | None:
    """Return the offending image path if a content-dumping Bash segment has an
    image-file argument, else None."""
    if not command:
        return None
    for statement in (s.strip() for s in STATEMENT_SPLIT_RE.split(command)):
        for segment in (seg.strip() for seg in statement.split("|")):
            lead = _leading_command(segment)
            if not CONTENT_DUMP_LEADING_RE.match(lead):
                continue
            try:
                tokens = shlex.split(lead)
            except ValueError:
                tokens = lead.split()
            for tok in tokens[1:]:
                # `dd if=img.png` — operand is one token `if=img.png`.
                candidate = tok.split("=", 1)[1] if "=" in tok else tok
                if _is_image_path(candidate):
                    return candidate
    return None


def _deny(detail: str) -> int:
    sys.stderr.write(
        "BLOCKED by glm_image_read_guard: " + detail + " This lane is routed to a "
        "NON-ANTHROPIC provider (ANTHROPIC_BASE_URL is set to a third-party endpoint, "
        "e.g. z.ai GLM in Singapore). Client image/visual data — screenshots, photos, "
        "scanned IDs — must NEVER be sent to a non-Anthropic provider (data residency). "
        "Do image/visual work on an Anthropic (Claude) lane, not a routed one. If this "
        "file is genuinely non-visual despite its extension, handle it on a Claude lane.\n"
    )
    return 2


def main() -> int:
    # Normal Claude lane (base URL unset/empty or an anthropic host): pure no-op.
    # Checked BEFORE reading stdin so a non-routed lane is never affected at all.
    if not is_provider_routed():
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.stderr.write(
            "BLOCKED by glm_image_read_guard: guard could not read the tool input; refusing "
            "(fail-closed — this lane is routed to a non-Anthropic provider and must not "
            "ingest an image before the input can be verified).\n"
        )
        return 2

    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}

    if tool == "Read":
        path = tool_input.get("file_path") or ""
        if _is_image_path(path):
            return _deny(f"this Read targets an image file ({path!r}).")
        return 0

    if tool == "Bash":
        cmd = tool_input.get("command") or ""
        offender = _bash_dumps_image(cmd)
        if offender:
            return _deny(f"this command dumps the contents of an image file ({offender!r}).")
        return 0

    # Any other tool is out of scope for this guard.
    return 0


if __name__ == "__main__":
    sys.exit(main())
