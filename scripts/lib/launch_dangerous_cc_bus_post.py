#!/usr/bin/env python3
"""Bounded, best-effort agent_messages post for launch_dangerous_cc.sh's
EXIT trap + Vercel deploy-poller (console review, bus #80132 conditions
1/2). All fields arrive via environment variables -- never argv, since
process listings are guard-scanned and visible to anyone on the box. A
hard ~10s bound (SIGALRM) means a hanging send() can never delay session
teardown; any failure (timeout, no DSN, DB down, SystemExit) is swallowed
and logged to stderr. This process always exits 0.

connect_timeout=5 / statement_timeout_ms=8000 (bus #80233/#80236, coord's
PR#591 review): SIGALRM can't interrupt a libpq call blocked in C, so the
alarm alone only bounds a connect that fails outright (DNS/refused/
unroutable) -- a connect that SUCCEEDS and then stalls on the INSERT is
invisible to it. Both numbers sit inside the alarm's 10s with headroom, so
the real libpq-level bound fires first in the stall case and the alarm
stays a pure backstop for anything neither one catches.

Env vars read:
  LDCC_FROM_BASE  (required) -- from_agent base id
  LDCC_TO         (required)
  LDCC_TYPE       (required) -- message_type
  LDCC_SUBJECT    (required)
  LDCC_BODY       (required)
  LDCC_PRIORITY   (required)
  LDCC_REQ        ("1" -> requires_response=True; default False)
  LDCC_SUB_TAG    (optional)
  LAUNCH_DANGEROUS_CC_TEST_MODE ("1" -> is_test=True; off by default)
"""
import os
import signal
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_BOUND_SECONDS = 10


class _PostTimeout(Exception):
    pass


def _on_alarm(signum, frame):
    raise _PostTimeout(f"bus post exceeded the {_BOUND_SECONDS}s bound")


def main() -> int:
    old_handler = signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(_BOUND_SECONDS)
    try:
        from scripts.bus_send import send
        send(
            os.environ["LDCC_FROM_BASE"],
            os.environ["LDCC_TO"],
            os.environ["LDCC_TYPE"],
            os.environ["LDCC_SUBJECT"],
            os.environ["LDCC_BODY"],
            os.environ["LDCC_PRIORITY"],
            req=os.environ.get("LDCC_REQ") == "1",
            sub_tag=os.environ.get("LDCC_SUB_TAG") or None,
            is_test=os.environ.get("LAUNCH_DANGEROUS_CC_TEST_MODE") == "1",
            connect_timeout=5, statement_timeout_ms=8000,
        )
    except (Exception, SystemExit, _PostTimeout) as e:  # noqa: BLE001 -- best-effort,
        # never raise: this must never change the EXIT trap's exit status.
        sys.stderr.write(f"launch_dangerous_cc_bus_post: FAILED (swallowed): {e}\n")
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
    return 0


if __name__ == "__main__":
    sys.exit(main())
