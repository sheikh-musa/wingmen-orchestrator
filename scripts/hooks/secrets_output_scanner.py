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
"""
from __future__ import annotations

import json
import re
import sys

SECRET_PATTERNS = {
    "anthropic-api-key": re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "supabase-service-key": re.compile(r"sbp_[a-f0-9]{20,}"),
    "telegram-bot-token": re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b"),
    "postgres-dsn": re.compile(r"postgres(?:ql)?://[^:\s]+:[^@\s]+@"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    "vercel-token": re.compile(r"\bvcp_[A-Za-z0-9]{20,}\b"),
    "github-token": re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),
}

REDACTION = "[REDACTED by secrets_output_scanner -- pattern:{cls}]"


def scan(text: str) -> list[tuple[str, re.Match]]:
    hits = []
    for cls, pattern in SECRET_PATTERNS.items():
        m = pattern.search(text)
        if m:
            hits.append((cls, m))
    return hits


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
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return False
    if not lines:
        return False

    last = lines[-1]
    stripped = last.rstrip("\n")
    if not stripped:
        return False
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return False

    redacted = _redact_strings(obj, pattern, cls)
    new_line = json.dumps(redacted) + "\n"
    if new_line == last:
        return False

    lines[-1] = new_line
    with open(transcript_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return True


def _page_orch_console(cls: str, tool_name: str) -> None:
    import os
    import subprocess

    orch_root = os.environ.get("ORCH_ROOT", os.path.expanduser("~/wingmen/orchestrator"))
    bus_send = os.path.join(orch_root, "scripts", "bus_send.py")
    venv_py = os.path.join(orch_root, ".venv", "bin", "python3")
    if not os.path.exists(bus_send):
        return
    host = os.environ.get("FLEET_HOST_ID", "unknown-host")
    agent = os.environ.get("AGENT_ID", os.environ.get("CC_BASE_AGENT_ID", "unknown-agent"))
    body = (
        f"secrets_output_scanner auto-redacted a secret-shaped match in the on-disk "
        f"session transcript. pattern class: {cls}; tool: {tool_name}; host: {host}; "
        f"agent: {agent}. The matched text itself is never relayed. This is a backstop "
        f"detection (Musa op#24408) -- it cannot un-send anything already transmitted."
    )
    try:
        subprocess.run(
            [venv_py if os.path.exists(venv_py) else "python3", bus_send,
             "--to", "orch-console", "--type", "update",
             "--subject", f"secrets_output_scanner: auto-redacted ({cls})",
             "--priority", "P1", "--req"],
            input=body, text=True, cwd=orch_root, timeout=20,
        )
    except Exception:
        pass  # paging must never crash the hook / block the tool result


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # PostToolUse: fail-open, the tool call already completed

    tool_name = payload.get("tool_name") or ""
    tool_response = payload.get("tool_response")
    transcript_path = payload.get("transcript_path")
    text = tool_response if isinstance(tool_response, str) else json.dumps(tool_response or "")

    hits = scan(text)
    if not hits:
        return 0

    for cls, _match in hits:
        if transcript_path:
            redact_last_line(transcript_path, cls, SECRET_PATTERNS[cls])
        _page_orch_console(cls, tool_name)

    classes = ", ".join(cls for cls, _ in hits)
    sys.stderr.write(
        f"secrets_output_scanner: this output contained secret-shaped content ({classes}); "
        "it has been redacted on disk and orch-console paged. Treat the underlying value "
        "as potentially exposed going forward.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
