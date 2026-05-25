"""Anonymize cc-scholar incident jsonls — preserve sizes + structure + mtimes,
redact all user/assistant content text.

Per CAI-RESP-164 R1: fixture must produce <3-of-3 → monitored (NOT SIGTERM).
Source: ~/.claude/projects/-Users-sheikhmusa-wingmen-projects-ai-scholar/

TARGET_MTIME corresponds to 2026-05-19 22:44 SGT (UTC+8) — the false-positive
near-miss that prompted CAI-RESP-164.
"""
import json
import os
import time
from pathlib import Path

SRC = Path.home() / ".claude" / "projects" / "-Users-sheikhmusa-wingmen-projects-ai-scholar"
DST = Path(__file__).parent

# 2026-05-19 22:44:00 local (machine TZ is SGT)
TARGET_MTIME = time.mktime(time.strptime("2026-05-19 22:44:00", "%Y-%m-%d %H:%M:%S"))


def redact(content):
    """Replace any text with same-length 'x' padding to preserve file size."""
    if isinstance(content, str):
        return "x" * len(content) if content else ""
    if isinstance(content, list):
        out = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    out.append({"type": "text", "text": "x" * len(block["text"])})
                else:
                    out.append(block)
            else:
                out.append(block)
        return out
    return content


def pick_files():
    if not SRC.exists():
        return []
    candidates = [
        p for p in SRC.iterdir()
        if p.is_file() and p.name.endswith(".jsonl") and not p.name.startswith(".")
    ]
    candidates.sort(key=lambda p: abs(p.stat().st_mtime - TARGET_MTIME))
    return candidates[:10]


def anonymize_file(src: Path, dst: Path):
    mt = src.stat().st_mtime
    lines_out = []
    with src.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and isinstance(obj.get("message"), dict):
                if "content" in obj["message"]:
                    obj["message"]["content"] = redact(obj["message"]["content"])
            lines_out.append(json.dumps(obj))
    dst.write_text("\n".join(lines_out) + "\n")
    os.utime(dst, (mt, mt))


if __name__ == "__main__":
    files = pick_files()
    if not files:
        print(f"NOTHING TO ANONYMIZE — source dir empty or missing: {SRC}")
        raise SystemExit(1)
    for idx, src in enumerate(files):
        dst = DST / f"session-{idx:02d}.jsonl"
        anonymize_file(src, dst)
        print(f"  anonymized {src.name} -> {dst.name}")
    print(f"anonymized {len(files)} fixtures to {DST}")
