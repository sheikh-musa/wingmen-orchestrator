#!/usr/bin/env python3
"""session_provider_guard.py — refuse a cross-provider `--resume` unless the
session transcript scans clean (orch-console bus #87276, P0 Musa op 36795).

THE INCIDENT THIS CLOSES (31 Oct): a lane was running on DeepSeek. Its token was
switched and the lane came back on an Anthropic model, `--resume`-ing the SAME
transcript — and Claude Code replays the whole conversation to the provider on
every turn. Whatever had accumulated in that transcript (in the 30 Oct case, a
client credential) was re-sent to a provider that had never seen it. A provider
switch is therefore a DATA-EXPORT decision, not a billing decision.

RULE
  A resume/continue is refused when the provider the session last ran on differs
  from the provider the requested model routes to — UNLESS a scan of that
  transcript finds ZERO secret-shaped values. Zero findings = the replay carries
  nothing that must not leave its current provider, so it may proceed (loudly).
  Anything else: start fresh and hand off.

Also refused: a transcript too large to verify (fail-closed, never "probably
fine"), and any internal error (an unparseable decision is a REFUSAL, not an
allow — a guard that fails open is not a guard).

WHAT IT NEVER DOES: print the secret. Findings are reported as class + line only;
the decision line carries the transcript's sha256 prefix so the judgement is
attributable to an exact artifact without exposing its content.

PROVIDER TABLE: mirrors `_provider_for_model()` in scripts/lib/provider_routing.sh
(the shipped routing). tests/test_session_provider_guard.py asserts the two agree
on a model corpus, so a routing change cannot silently desync this guard.

CLI
  session_provider_guard.py check --model MODEL (--session ID | --continue --cwd DIR
                                                | --transcript PATH)
                                  [--transcript-root DIR] [--log-dir DIR] [--json]

  exit 0  ALLOW    (first line of stdout: "ALLOW <reason>")
  exit 3  REFUSE   (first line of stdout: "REFUSE <reason>")
  exit 1  usage/internal error (callers must treat as REFUSE)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TRANSCRIPT_ROOT = Path.home() / ".claude" / "projects"

# A transcript bigger than this cannot be verified in the time a lane boot may
# spend; refuse rather than scan a prefix and call it clean.
MAX_SCAN_BYTES = 128 * 1024 * 1024
# Enough findings to name the classes/regions and be certain the transcript is not
# clean — one is enough to refuse. Bounded so a transcript full of token-shaped
# strings cannot turn the boot check into a minutes-long scan.
MAX_FINDINGS = 50
# The last provider is read from the TAIL; a 250 MB transcript is common on this
# fleet and reading it whole for one field is not.
_TAIL_BYTES = 4 * 1024 * 1024

# Provider keys, in the SAME precedence order as the shell case statement
# (OpenRouter's vendor/model ids must win over the native qwen* wildcard).
PROVIDER_LABELS = {
    "anthropic": "Anthropic",
    "zai": "z.ai GLM",
    "moonshot": "Moonshot Kimi",
    "deepseek": "DeepSeek",
    "openrouter": "OpenRouter",
    "qwen": "Qwen (DashScope)",
}


def provider_for_model(model: str | None) -> str:
    """The provider key a model id routes to. Mirror of `_provider_for_model`.

    Anything unmatched is Anthropic: that is exactly what the shell function does
    (returns 1, exports nothing, the process keeps its Anthropic credentials).
    """
    m = (model or "").strip()
    if m.startswith("glm-"):
        return "zai"
    if m.startswith(("kimi-", "moonshot-")):
        return "moonshot"
    if m.startswith("deepseek-"):
        return "deepseek"
    if m.startswith(("moonshotai/", "deepseek/", "qwen/", "z-ai/")):
        return "openrouter"
    if m.startswith("qwen"):
        return "qwen"
    return "anthropic"


def label(provider: str) -> str:
    return PROVIDER_LABELS.get(provider, provider)


# ── transcript discovery ───────────────────────────────────────────────────────

def _project_slug(path: str) -> str:
    """Claude Code's on-disk project-dir name for a working directory."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(path))


def find_transcript(session_id: str, root: Path = DEFAULT_TRANSCRIPT_ROOT) -> Path | None:
    if not session_id:
        return None
    for candidate in sorted(root.glob(f"*/{session_id}.jsonl")):
        return candidate
    return None


def latest_transcript_for_cwd(cwd: str, root: Path = DEFAULT_TRANSCRIPT_ROOT) -> Path | None:
    """The conversation `--continue` would resume: newest transcript in this dir."""
    project = root / _project_slug(cwd)
    if not project.is_dir():
        return None
    files = [p for p in project.glob("*.jsonl") if p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


# ── transcript inspection ──────────────────────────────────────────────────────

def last_provider(transcript: Path) -> str | None:
    """Provider of the most recent assistant turn in the transcript.

    Reads only the tail: the newest assistant turn is what decides, and a fleet
    transcript can be hundreds of megabytes.
    """
    try:
        size = transcript.stat().st_size
        with transcript.open("rb") as fh:
            if size > _TAIL_BYTES:
                fh.seek(size - _TAIL_BYTES)
                raw = fh.read()
                raw = raw.split(b"\n", 1)[-1]  # the first line is a partial
            else:
                raw = fh.read()
    except OSError:
        return None
    for line in reversed(raw.splitlines()):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(obj, dict):
            continue
        model = None
        msg = obj.get("message")
        if isinstance(msg, dict):
            model = msg.get("model")
        if not isinstance(model, str):
            model = obj.get("model") if isinstance(obj.get("model"), str) else None
        if model:
            return provider_for_model(model)
    return None


_CONNINFO_OTHERS = ("host=", "port=", "user=", "dbname=")


def _conninfo_hit(text: str) -> bool:
    """Bounded approximation of the shared `postgres-dsn-kv` class.

    The shared regex is `(?=[^\\n]*\\bpassword=\\S+)(?:\\b(?:host|port|user|dbname|password)=\\S+\\s*){3,}`
    — a lookahead plus a `{3,}` run, which backtracks catastrophically when a line
    repeats `password=` (measured: never returned on a 640-byte line). Its meaning is
    "a `password=` value plus at least three libpq key=value tokens"; we walk the
    line once and check a window directly. Linear time in every case.

    NOT a strict equivalent, and the docstring should not pretend otherwise
    (cc-quality 87538 F1). It is a conservative SUPERSET with one deliberate
    exception, closed here: three or more BARE `password=` tokens and no other
    libpq key score 3 in the shared pattern and used to score 1 here, i.e. the one
    direction that fails OPEN. All password occurrences in the window now count.
    Remaining divergences are over-reports (fail-closed): the window is proximity,
    not the shared pattern's contiguity, so a benign line mentioning the keyword
    near libpq-style prose can refuse a resume. That costs one fresh boot.
    """
    for i, m in enumerate(re.finditer(r"\bpassword=\S+", text)):
        if i > 50:  # bounded: a line is not a hundred-credential document
            break
        # Both directions: the other key=value fields may FOLLOW the password
        # field, not only precede it (measured against the shared pattern).
        window = text[max(0, m.start() - 400):m.end() + 400]
        passwords = len(re.findall(r"\bpassword=\S+", window))
        others = sum(1 for tok in _CONNINFO_OTHERS if tok in window)
        if passwords + others >= 3:
            return True
    return False


def scan_transcript(transcript: Path) -> tuple[list[tuple[str, int]], str, int]:
    """Stream the transcript through the fleet's secret-shape detector.

    Returns (findings, sha256-prefix, size). Findings are (class, line-no) — never
    the value.

    LINE-ORIENTED ON PURPOSE. The shared detector's patterns are written for one
    tool output at a time; `postgres-dsn-kv` in particular is a backtracking time
    bomb (measured: a 4 MB blob, and even a single 640-byte line repeating
    `password=`, did not return in two minutes). This scan therefore

      * walks the transcript LINE by line (a JSONL transcript puts one turn per
        line, so every regex keeps a bounded context and the cost is linear), and
      * skips a line with no anchor literal at all without running any regex
        (`Bearer` has no anchor — it is case-insensitive — so `earer` stands in),
      * runs the other classes through the shared `find_hits`, and substitutes the
        bounded `_conninfo_hit` for `postgres-dsn-kv`.

    The equivalence of that substitution is pinned in
    tests/test_session_provider_guard.py, not assumed.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
    from secret_shape_patterns import (  # noqa: E402
        SECRET_VALUE_ANCHORS, SECRET_VALUE_PATTERNS, find_hits)

    patterns = {cls: pat for cls, pat in SECRET_VALUE_PATTERNS.items()
                if cls != "postgres-dsn-kv"}
    anchors = tuple({a.encode() for vals in SECRET_VALUE_ANCHORS.values()
                     for a in vals}) + (b"earer",)

    digest = hashlib.sha256()
    findings: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    size = 0
    line_no = 0
    with transcript.open("rb") as fh:
        for raw in fh:  # one JSONL turn per line; a giant line is still bounded
            size += len(raw)
            line_no += 1
            # The digest always covers the WHOLE file so the decision names an exact
            # artifact, even when the walk below stops early at MAX_FINDINGS.
            digest.update(raw)
            if len(findings) >= MAX_FINDINGS:
                continue
            if not any(a in raw for a in anchors):
                continue  # provably nothing the detector could match
            text = raw.decode("utf-8", errors="replace")
            hits = [(cls, None) for cls, _m in find_hits(text, patterns)]
            if _conninfo_hit(text):
                hits.append(("postgres-dsn-kv", None))
            for cls, _match in hits:
                if (cls, line_no) in seen:
                    continue
                seen.add((cls, line_no))
                findings.append((cls, line_no))
                if len(findings) >= MAX_FINDINGS:
                    break
    return findings, digest.hexdigest()[:12], size


# ── decision ───────────────────────────────────────────────────────────────────

@dataclass
class Decision:
    allow: bool
    reason: str
    detail: str = ""
    handoff: str = ""
    session_provider: str = ""
    target_provider: str = ""
    findings: list[tuple[str, int]] = field(default_factory=list)


def decide(model: str, transcript: Path | None, *, log_dir: Path | None = None) -> Decision:
    target = provider_for_model(model)
    if transcript is None:
        return Decision(True, "no transcript to resume — nothing can be replayed",
                        target_provider=target)
    try:
        size = transcript.stat().st_size
    except OSError as exc:
        return Decision(False, f"cannot stat the transcript ({exc.__class__.__name__}) — "
                               "refusing to resume something it cannot verify",
                        target_provider=target)
    if size > MAX_SCAN_BYTES:
        return Decision(
            False,
            f"transcript is {size // (1024 * 1024)} MB, over the {MAX_SCAN_BYTES // (1024 * 1024)} MB "
            "verification ceiling — start fresh and hand off",
            target_provider=target)

    sess_prov = last_provider(transcript)
    if sess_prov is not None and sess_prov == target:
        return Decision(True, f"same provider ({label(target)}) — nothing crosses a boundary",
                        session_provider=sess_prov, target_provider=target)

    findings, sha12, _ = scan_transcript(transcript)
    crossing = (f"{label(sess_prov) if sess_prov else 'unknown provider'} → {label(target)}")
    if not findings:
        return Decision(
            True,
            f"provider changes ({crossing}) and the transcript scans clean "
            f"(sha256:{sha12}) — resume allowed",
            session_provider=sess_prov or "unknown", target_provider=target)

    classes = sorted({cls for cls, _ in findings})
    lines = ", ".join(sorted({str(ln) for _, ln in findings}, key=int)[:8])
    detail = (f"{len(findings)} secret-shaped value(s) in the transcript "
              f"(sha256:{sha12}): class {', '.join(classes)} at line(s) {lines}")
    handoff = _write_handoff(log_dir, transcript, sess_prov, target, detail) if log_dir else ""
    return Decision(
        False,
        f"provider changes ({crossing}) and the transcript is NOT clean — {detail}. "
        "Resuming would replay those values to the new provider. Start fresh (drop "
        "the resume flag) and hand off context; scrub the transcript with "
        "scripts/secret_hash_sweep.py before resuming across a provider.",
        detail=detail, handoff=handoff,
        session_provider=sess_prov or "unknown", target_provider=target,
        findings=findings)


def _write_handoff(log_dir: Path, transcript: Path, sess: str | None,
                   target: str, detail: str) -> str:
    """A fresh-start note the booted lane can actually read."""
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"{transcript.stem}.md"
        path.write_text(
            "# Cross-provider resume refused\n\n"
            f"- transcript: {transcript}\n"
            f"- session provider: {label(sess) if sess else 'unknown'}\n"
            f"- requested provider: {label(target)}\n"
            f"- {detail}\n\n"
            "This session started FRESH. The previous conversation was not resumed "
            "because replaying it to a different provider would export whatever it "
            "contains. Continue from the durable record: STATUS.md, the last bus rows "
            "for this lane, and the branch's commits. Scrub the old transcript with "
            "scripts/secret_hash_sweep.py if you need it back.\n"
        )
        return str(path)
    except OSError:
        return ""


# ── CLI ────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    chk = sub.add_parser("check", help="decide whether a resume may proceed")
    chk.add_argument("--model", required=True, help="the model the lane will boot on")
    chk.add_argument("--session", help="session id to resume")
    chk.add_argument("--continue", dest="cont", action="store_true",
                     help="the launch uses --continue (latest in --cwd)")
    chk.add_argument("--cwd", help="working directory of the launch (for --continue)")
    chk.add_argument("--transcript", help="explicit transcript path (tests/ops)")
    chk.add_argument("--transcript-root", default=str(DEFAULT_TRANSCRIPT_ROOT))
    chk.add_argument("--log-dir", help="where a fresh-start handoff note is written")
    chk.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.transcript_root)
    try:
        if args.transcript:
            transcript = Path(args.transcript)
            transcript = transcript if transcript.exists() else None
        elif args.cont:
            transcript = latest_transcript_for_cwd(args.cwd or os.getcwd(), root)
        elif args.session:
            transcript = find_transcript(args.session, root)
        else:
            print("REFUSE usage: one of --session / --continue / --transcript is required",
                  file=sys.stdout)
            return 3
        d = decide(args.model, transcript,
                   log_dir=Path(args.log_dir) if args.log_dir else None)
    except Exception as exc:  # fail closed: an unparseable decision is a refusal
        print(f"REFUSE internal error in session_provider_guard "
              f"({exc.__class__.__name__}) — refusing the resume", file=sys.stdout)
        return 3

    if args.json:
        print(json.dumps({
            "allow": d.allow, "reason": d.reason,
            "session_provider": d.session_provider, "target_provider": d.target_provider,
            "findings": [{"class": c, "line": ln} for c, ln in d.findings],
            "handoff": d.handoff,
        }))
    else:
        print(f"{'ALLOW' if d.allow else 'REFUSE'} {d.reason}")
        if d.handoff:
            print(f"HANDOFF {d.handoff}")
    return 0 if d.allow else 3


if __name__ == "__main__":
    sys.exit(main())
