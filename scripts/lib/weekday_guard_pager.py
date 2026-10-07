"""weekday_guard_pager.py — page orch-console when the weekday/date guard CRASHES.

Rule (orch-console #58089): a confirmed weekday/date MISMATCH refuses the send; a guard
CRASH (import error, python missing, timeout, any non-clean result that is not a confirmed
mismatch) FAILS OPEN — the message is sent unguarded — and orch-console is paged loudly.
A broken safety check must never become an outage of every operator/client send.

This pager is itself best-effort:
  * never raises (catches BaseException — bus_send.dburl raises SystemExit);
  * time-bounded: the bus write runs in a daemon thread joined for <= PAGE_TIMEOUT s, with
    PGCONNECT_TIMEOUT capped, so it can delay a send by a few seconds at most;
  * deduped: at most one page per source script per DEDUP_SEC (stamp files in STATE_DIR);
    a FAILED page does not stamp, so the next crash retries;
  * every crash is appended to STATE_DIR/weekday_guard_crash.log regardless of the page.

API:  page_guard_crash(source, error, tb="", *, state_dir=None, sender=None, timeout=...)
      -> "paged" | "deduped" | "dryrun" | "timeout" | "failed: <why>"
CLI (used by date_weekday_guard.sh, in the background):
      python3 weekday_guard_pager.py --source <script> --error "<summary>"   (traceback on stdin)
Env:  WEEKDAY_GUARD_STATE_DIR   override STATE_DIR (default ~/wingmen/fleet-health/state)
      WEEKDAY_GUARD_PAGE_DRYRUN=<file>  append the page as JSON to <file> instead of the bus
                                        (tests / sandbox runs — never pages for real)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

FROM_AGENT = "cc-fleet-health"
TO_AGENT = "orch-console"
SUBJECT_PREFIX = "weekday guard CRASHED — sent unguarded:"
DEDUP_SEC = 15 * 60
PAGE_TIMEOUT = 4.0
TB_MAX = 1500
LOG_NAME = "weekday_guard_crash.log"


def _state_dir(state_dir=None) -> Path:
    return Path(state_dir or os.environ.get("WEEKDAY_GUARD_STATE_DIR")
                or os.path.expanduser("~/wingmen/fleet-health/state"))


def _log(state: Path, line: str) -> None:
    try:
        state.mkdir(parents=True, exist_ok=True)
        with open(state / LOG_NAME, "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {line}\n")
    except Exception as e:  # noqa: BLE001 — logging is best-effort
        print(f"weekday_guard_pager: could not write crash log ({e})", file=sys.stderr)


def _bus_sender(**kw):
    """The real page: scripts/bus_send.py's send(), loaded relative to this file."""
    orch = Path(__file__).resolve().parent.parent.parent
    if str(orch) not in sys.path:
        sys.path.insert(0, str(orch))
    os.environ.setdefault("PGCONNECT_TIMEOUT", "3")
    from scripts.bus_send import send
    return send(kw["from_agent"], kw["to"], kw["mtype"], kw["subject"], kw["body"],
                kw["priority"], req=kw["req"])


def _dryrun_sender(path):
    def _send(**kw):
        with open(path, "a") as f:
            f.write(json.dumps(kw, ensure_ascii=False) + "\n")
        return 0, "dryrun"
    return _send


def page_guard_crash(source: str, error: str, tb: str = "", *, state_dir=None, sender=None,
                     timeout: float = PAGE_TIMEOUT, stream=None) -> str:
    out = stream or sys.stderr
    try:
        state = _state_dir(state_dir)
        src = re.sub(r"[^A-Za-z0-9_.-]", "_", source or "unknown")[:80]
        tb = (tb or "").strip()
        if len(tb) > TB_MAX:
            tb = "…(truncated)…\n" + tb[-TB_MAX:]
        _log(state, f"CRASH source={src} error={error!r}")

        stamp = state / f"weekday_guard_page.{src}.stamp"
        try:
            if time.time() - stamp.stat().st_mtime < DEDUP_SEC:
                print(f"weekday_guard_pager: already paged for {src} in the last "
                      f"{DEDUP_SEC // 60} min — not re-paging.", file=out)
                return "deduped"
        except FileNotFoundError:
            pass

        dry = os.environ.get("WEEKDAY_GUARD_PAGE_DRYRUN") if sender is None else None
        if sender is None:
            sender = _dryrun_sender(dry) if dry else _bus_sender
        payload = dict(
            from_agent=FROM_AGENT, to=TO_AGENT, mtype="blocker", priority="P1", req=True,
            subject=f"{SUBJECT_PREFIX} {src} ({error.split(':', 1)[0][:60]})",
            body=(f"TL;DR: the weekday/date send guard CRASHED in {src}, so a message was "
                  "SENT WITHOUT the weekday/date check (fail-open by design, #58089).\n\n"
                  f"What: {error}\n"
                  "Why it matters: until fixed, wrong weekday/date pairs can reach the operator "
                  "and clients again.\n"
                  "What to do: check scripts/lib/date_weekday_guard.py and the sender's "
                  ".venv/bin/python3 on this host; crash log: "
                  f"{state / LOG_NAME}. Deduped to one page per script per 15 min.\n\n"
                  f"Traceback (truncated):\n{tb or '(none captured)'}\n"),
        )
        result = {}

        def _run():
            try:
                result["ok"] = sender(**payload)
            except BaseException as e:  # noqa: BLE001 — SystemExit from dburl, etc.
                result["err"] = f"{type(e).__name__}: {e}"

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            _log(state, f"PAGE TIMEOUT source={src} after {timeout}s")
            print(f"weekday_guard_pager: page to {TO_AGENT} timed out after {timeout}s "
                  "(message was still sent).", file=out)
            return "timeout"
        if "err" in result:
            _log(state, f"PAGE FAILED source={src} {result['err']}")
            print(f"weekday_guard_pager: page to {TO_AGENT} FAILED ({result['err']}) "
                  "(message was still sent).", file=out)
            return f"failed: {result['err']}"
        try:
            state.mkdir(parents=True, exist_ok=True)
            stamp.touch()
        except Exception:  # noqa: BLE001
            pass
        _log(state, f"PAGED source={src} -> {TO_AGENT} {result.get('ok')}")
        return "dryrun" if dry else "paged"
    except BaseException as e:  # noqa: BLE001 — the pager must never raise
        try:
            print(f"weekday_guard_pager: internal error ({type(e).__name__}: {e})", file=out)
        except Exception:  # noqa: BLE001
            pass
        return f"failed: {type(e).__name__}"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--source", required=True)
    p.add_argument("--error", required=True)
    a = p.parse_args(argv)
    try:
        tb = sys.stdin.read() if not sys.stdin.isatty() else ""
    except Exception:  # noqa: BLE001
        tb = ""
    print(f"weekday_guard_pager: {page_guard_crash(a.source, a.error, tb)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
