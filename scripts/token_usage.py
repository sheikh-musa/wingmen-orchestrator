#!/usr/bin/env python3
"""token_usage.py — fleet token-usage readout from Claude Code transcripts.

Sums usage recorded in ~/.claude/projects/**/*.jsonl (every assistant turn
carries a `usage` block) so the orchestrator (or operator) can see how hard
we're leaning on the Claude Max window at a glance.

HONEST CAVEAT: this reports TOKENS CONSUMED (input / output / cache), computed
from the local transcripts. It does NOT read the official Max *weekly-limit
remaining* — that lives on Anthropic's side and isn't cleanly queryable. The
real "you're near the cap" signal is Claude Code's own rate-limit warning,
which the orchestrator relays when it appears. Treat the rolling-7d output
number as the best local proxy for weekly-window pressure.

Usage:
  python3 scripts/token_usage.py            # session (latest transcript) + rolling 7d
  python3 scripts/token_usage.py --days 1   # change the rolling window
  python3 scripts/token_usage.py --json     # machine-readable (for the console)
"""
import argparse, glob, json, os, sys, time

ROOT = os.path.expanduser("~/.claude/projects")


def _usage(d):
    return (d.get("message") or {}).get("usage") or d.get("usage")


def _accumulate(paths, since=None):
    tot = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0, "msgs": 0}
    for p in paths:
        if since and os.path.getmtime(p) < since - 86400:
            continue  # whole file too old (mtime is last write); -1d slop for safety
        try:
            fh = open(p, errors="ignore")
        except OSError:
            continue
        with fh:
            for ln in fh:
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                u = _usage(d)
                if not u:
                    continue
                tot["input"] += u.get("input_tokens", 0)
                tot["output"] += u.get("output_tokens", 0)
                tot["cache_read"] += u.get("cache_read_input_tokens", 0)
                tot["cache_creation"] += u.get("cache_creation_input_tokens", 0)
                tot["msgs"] += 1
    return tot


def _fmt(n):
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= div:
            return f"{n/div:.2f}{unit}"
    return str(n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    now = time.time()
    since = now - args.days * 86400
    all_paths = glob.glob(os.path.join(ROOT, "**", "*.jsonl"), recursive=True)
    if not all_paths:
        print("no transcripts found under", ROOT, file=sys.stderr)
        return 1
    latest = max(all_paths, key=os.path.getmtime)

    session = _accumulate([latest])
    window = _accumulate(all_paths, since=since)

    if args.json:
        print(json.dumps({"session": session, "window": window,
                          "window_days": args.days,
                          "session_file": os.path.basename(latest)}))
        return 0

    def line(label, t):
        print(f"  {label:<16} in={_fmt(t['input']):>8}  out={_fmt(t['output']):>8}  "
              f"cache_read={_fmt(t['cache_read']):>8}  cache_new={_fmt(t['cache_creation']):>8}  "
              f"({t['msgs']:,} msgs)")

    print("Claude Code token usage (local transcripts):")
    line("this session", session)
    line(f"rolling {args.days:g}d", window)
    print("\n  NOTE: output is the real generation cost; cache_read dominates on long")
    print("  sessions (prompt cache, cheap on Max). This is NOT the official weekly")
    print("  limit — closest local proxy for weekly-window pressure is rolling-7d output.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
