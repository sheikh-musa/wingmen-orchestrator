#!/usr/bin/env python3
"""agent_status_stamp — the ONE writer of a lane's identity stamp + heartbeat.

Why this exists (2026-10-01, operator: "I don't see any irsyad lanes in fleet
console"): launch_dangerous_cc.sh stamped host / tmux_session / boot model
(`session-launch model=<m> repo=<r>`) ONCE at boot, in an inline `python -c` that
swallowed every error (`except Exception: pass` + `2>/dev/null`) and never
retried. Its heartbeat loop then only touched last_heartbeat. So a lane that
booted while the substrate pooler was refusing connections (cc-irsyad-2 and
cc-irsyad-coord-1 both booted on gzb at 15:12:55Z, ~10 min into the 2026-10-01
password-rotation breaker window) kept host=NULL, tmux_session=NULL and the bare
'session-launch' placeholder FOREVER, with a perfectly fresh heartbeat — a live
body the console could neither place on a host nor show a model for.

Rules (enforced here, tested in tests/test_agent_status_stamp.py):
  * every beat RE-ASSERTS host + tmux_session, so one lost boot stamp self-heals
    on the next beat (<= 300s) instead of never;
  * NULL / empty NEVER overwrites a populated host or tmux_session
    (COALESCE(NULLIF(new,''), old));
  * the boot model string fills current_task on a beat ONLY while it still holds
    the bare 'session-launch' placeholder (or NULL) and the row is not offline —
    a beat never clobbers a real task string, and never resurrects an exited row;
  * the identity GUC is set in the SAME txn as the write (hardened identity
    trigger, BUG-024/ARCH-035);
  * the DSN is resolved FILE-FIRST (substrate_dsn.dsn_from_env_file, op#24342) so
    an inherited pre-rotation DATABASE_URL can never hammer the pooler;
  * failures are LOUD on stderr (the launcher routes the beat's stderr to its
    heartbeat error log) — never silently swallowed.

CLI (called by scripts/launch_dangerous_cc.sh):
  agent_status_stamp.py --mode boot|beat --agent-id cc-x-1 [--host H] [--session S]
      [--model M] [--repo R] [--auth-account A] [--auth-fp F] [--retries N]
Exit 0 on a committed write, 1 on failure (after retries), 2 on bad args.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

PLACEHOLDER_TASK = "session-launch"

# boot: the authoritative stamp of a fresh launch. host/session are fill-or-replace
# with a REAL value only (a blank resolver never NULLs a populated field); auth_*
# keep the launcher's historic NULLIF semantics (op#7094: a fresh boot's auth is
# authoritative — blank reads as "missing", never a stale previous incarnation's fp).
BOOT_SQL = (
    "UPDATE agent_status SET "
    "current_task = %(task)s, "
    "tmux_session = COALESCE(NULLIF(%(session)s, ''), tmux_session), "
    "host = COALESCE(NULLIF(%(host)s, ''), host), "
    "auth_account = NULLIF(%(auth_account)s, ''), "
    "auth_fp = NULLIF(%(auth_fp)s, ''), "
    "updated_at = now() "
    "WHERE agent_id = %(agent_id)s"
)

# beat: liveness + self-heal. Never NULLs anything; fills the boot model string only
# over the bare placeholder of a non-offline row; fills a missing auth_fp only.
BEAT_SQL = (
    "UPDATE agent_status SET "
    "last_heartbeat = now(), "
    "tmux_session = COALESCE(NULLIF(%(session)s, ''), tmux_session), "
    "host = COALESCE(NULLIF(%(host)s, ''), host), "
    "current_task = CASE "
    "  WHEN %(task)s::text IS NOT NULL AND status <> 'offline' "
    "   AND (current_task IS NULL OR current_task = '" + PLACEHOLDER_TASK + "') "
    "  THEN %(task)s::text ELSE current_task END, "
    "auth_fp = COALESCE(auth_fp, NULLIF(%(auth_fp)s, '')), "
    "updated_at = now() "
    "WHERE agent_id = %(agent_id)s"
)


def boot_task(model: "str | None", repo: "str | None") -> "str | None":
    """The `session-launch model=<m> repo=<r>` string the console parses
    (app._BOOT_MODEL_RE). None when the model is unknown — never invented."""
    model = (model or "").strip()
    if not model:
        return None
    return f"{PLACEHOLDER_TASK} model={model} repo={(repo or '').strip()}".rstrip()


def build_params(agent_id, host=None, session=None, model=None, repo=None,
                 auth_account=None, auth_fp=None) -> dict:
    return {
        "agent_id": agent_id,
        "host": (host or "").strip(),
        "session": (session or "").strip(),
        "task": boot_task(model, repo),
        "auth_account": (auth_account or "").strip(),
        "auth_fp": (auth_fp or "").strip(),
    }


def write(conn, mode: str, params: dict) -> int:
    """One txn: identity GUC + the UPDATE. Returns rowcount. Caller commits."""
    if mode not in ("boot", "beat"):
        raise ValueError(f"mode must be boot|beat, got {mode!r}")
    if mode == "boot" and params.get("task") is None:
        # boot without a model keeps the historic placeholder rather than NULL.
        params = dict(params, task=PLACEHOLDER_TASK)
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id', %s, true)",
                    (params["agent_id"],))
        cur.execute(BOOT_SQL if mode == "boot" else BEAT_SQL, params)
        return cur.rowcount


def _resolve_dsn() -> str:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from substrate_dsn import dsn_from_env_file  # file-first (op#24342)
    return dsn_from_env_file()


def run(mode, params, retries=1, connect=None, sleep=time.sleep, dsn=None) -> bool:
    """Write with bounded retries (boot: a lost stamp is what hid the irsyad lanes).
    `connect` / `sleep` / `dsn` are seams for tests. Never raises; returns success."""
    if connect is None:
        import psycopg
        connect = psycopg.connect
    try:
        dsn = dsn or _resolve_dsn()
    except Exception as e:  # noqa: BLE001 — missing DSN: loud, no hammering
        print(f"agent_status_stamp[{mode}] {params.get('agent_id')}: no DSN: {e}",
              file=sys.stderr)
        return False
    last = None
    for attempt in range(max(1, retries)):
        try:
            with connect(dsn, connect_timeout=10) as conn:
                n = write(conn, mode, params)
                conn.commit()
            if n == 0:
                print(f"agent_status_stamp[{mode}] {params.get('agent_id')}: no agent_status row",
                      file=sys.stderr)
            return True
        except Exception as e:  # noqa: BLE001 — report + bounded retry, never crash a launcher
            last = e
            msg = str(e)
            # An auth failure feeds the pooler circuit breaker: never retry it.
            if "password authentication failed" in msg:
                break
            if attempt + 1 < max(1, retries):
                sleep(5 * (attempt + 1))
    print(f"agent_status_stamp[{mode}] {params.get('agent_id')}: write FAILED: {last}",
          file=sys.stderr)
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", choices=["boot", "beat"], required=True)
    ap.add_argument("--agent-id", required=True)
    ap.add_argument("--host", default="")
    ap.add_argument("--session", default="")
    ap.add_argument("--model", default="")
    ap.add_argument("--repo", default="")
    ap.add_argument("--auth-account", default="")
    ap.add_argument("--auth-fp", default="")
    ap.add_argument("--retries", type=int, default=1)
    a = ap.parse_args(argv)
    if not a.agent_id.strip():
        print("agent_status_stamp: empty --agent-id", file=sys.stderr)
        return 2
    params = build_params(a.agent_id.strip(), a.host, a.session, a.model, a.repo,
                          a.auth_account, a.auth_fp)
    return 0 if run(a.mode, params, retries=a.retries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
