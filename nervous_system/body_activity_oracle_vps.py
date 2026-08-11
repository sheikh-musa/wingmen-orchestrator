#!/usr/bin/env python3
"""body_activity_oracle_vps — the G-b VPS-INSTANCE oracle (op#11774, console-signed
18932). Runs ON the hub's VPS, where the hub's tmux 'orch' pane is LOCAL, and
publishes the hub's verdict to the shared-substrate cache so the Mini SRE oracle can
read it WITHOUT ssh (VPS-instance-not-ssh, 18673).

DETECT-ONLY: reads the hub's local pane via the signed oracle + UPSERTs a verdict.
No send-keys, no re-drive; the mutating probe stays inert (PROBE_ARMED=False). On any
capture error the oracle returns UNSURE and we publish THAT honestly — never a
fabricated verdict. Fail-LOUD to the log; a bad cycle must not kill the loop
(systemd Restart=always is the backstop).

Deploy: systemd wingmen-body-activity-oracle-vps.service on hub-vps (WorkingDirectory
the VPS orchestrator tree, EnvironmentFile .env for DATABASE_URL, TMUX_BIN=/usr/bin/
tmux). See reports/vps-oracle-deploy-op11774.md.
"""
from __future__ import annotations

import os
import sys
import time

try:  # runtime bare import (nervous_system on sys.path); pytest package import
    import body_activity_oracle as oracle
except ImportError:  # pragma: no cover
    from nervous_system import body_activity_oracle as oracle

HUB_AGENT = os.environ.get("ORACLE_VPS_HUB_AGENT", "cc-orchestrator")
HUB_SESSION = os.environ.get("ORACLE_VPS_HUB_SESSION", "orch")
PUBLISH_SEC = int(os.environ.get("ORACLE_VPS_PUBLISH_SEC", "60"))
PUBLISH_HOST = os.environ.get("ORACLE_VPS_HOST_LABEL", "vps")


def _log(msg: str) -> None:
    print(f"[oracle-vps] {msg}", flush=True)


def publish_once(*, activity=None, publish=None) -> str:
    """One capture->classify->publish cycle. Reads the hub's LOCAL pane (resolve_host
    forced LOCAL so it uses _capture_local, not the remote read) and publishes the
    verdict. Seams injectable for tests. Returns the published state. Raises nothing
    that isn't logged — but re-raises publish failures to the caller for loud handling
    (the caller keeps the loop alive)."""
    activity = activity or (lambda: oracle.activity(
        HUB_AGENT,
        resolve_host=lambda a: oracle.LOCAL_HOST,     # the hub IS local on the VPS
        resolve_session=lambda a: HUB_SESSION))
    publish = publish or (lambda v: oracle.publish_verdict(HUB_AGENT, v, PUBLISH_HOST))
    v = activity()  # already returns UNSURE on any capture failure — never fabricates
    publish(v)
    return v.state


def main() -> int:
    if not oracle._dsn():
        _log("FATAL: no DATABASE_URL/SUPABASE_DB_URL — cannot publish")
        return 1
    _log(f"up — publishing {HUB_AGENT} (session '{HUB_SESSION}') every {PUBLISH_SEC}s "
         f"as host='{PUBLISH_HOST}'; detect-only, probe_armed={oracle.PROBE_ARMED}")
    once = "--once" in sys.argv
    while True:
        try:
            state = publish_once()
            _log(f"published {HUB_AGENT} -> {state}")
        except Exception as e:  # fail LOUD, keep the loop alive (systemd restarts too)
            _log(f"ERROR publishing verdict: {e!r}")
        if once:
            return 0
        time.sleep(PUBLISH_SEC)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    raise SystemExit(main())
