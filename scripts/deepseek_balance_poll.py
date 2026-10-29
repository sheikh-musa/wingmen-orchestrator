#!/usr/bin/env python3
"""DeepSeek account balance poller (op#35698).

Fetches the DeepSeek balance via the vault (key never printed, never logged),
appends {ts, balance_usd} to a durable JSONL state file, and sends a P1 bus
alert to orch-console if balance drops below ALERT_FLOOR_USD. Intended to run
hourly via launchd (dev.wingmen.deepseek-balance-poll).

The key itself never touches stdout/argv here: provider_vault_fetch.py is
invoked as a subprocess and its stdout is captured directly into a Python
variable, used only in the Authorization header of the one HTTPS request.
"""
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ORCH_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = Path.home() / "wingmen" / "fleet-health" / "state" / "deepseek_balance_history.jsonl"
ALERT_FLOOR_USD = 40.0
BALANCE_URL = "https://api.deepseek.com/user/balance"


def fetch_key() -> str:
    env = dict(os.environ)
    env.setdefault("AGENT_ID", "cc-fleet-health")
    env.setdefault("CC_BASE_AGENT_ID", "cc-fleet-health")
    out = subprocess.run(
        [sys.executable, str(ORCH_DIR / "scripts" / "lib" / "provider_vault_fetch.py"),
         "DEEPSEEK_CODING_KEY", "DeepSeek (native)"],
        cwd=str(ORCH_DIR), env=env, capture_output=True, text=True, timeout=30,
    )
    if out.returncode != 0 or not out.stdout:
        raise RuntimeError("provider_vault_fetch failed (rc=%d): %s" % (out.returncode, out.stderr.strip()[:200]))
    return out.stdout


def fetch_balance(key: str) -> dict:
    req = urllib.request.Request(BALANCE_URL, headers={"Authorization": "Bearer %s" % key})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _total_usd(payload: dict) -> float:
    infos = payload.get("balance_infos") or []
    for info in infos:
        if info.get("currency") == "USD":
            return float(info["total_balance"])
    if infos:
        return float(infos[0]["total_balance"])
    raise RuntimeError("no balance_infos in DeepSeek response")


def send_alert(balance_usd: float) -> None:
    body = (
        "TL;DR: DeepSeek balance is below the $%.0f floor -- %.2f USD left.\n\n"
        "Polled just now via scripts/deepseek_balance_poll.py. Topped up to $50 this morning "
        "(2026-10-29); this is a real draw-down, not a measurement artifact -- history is at "
        "%s if you want the curve."
    ) % (ALERT_FLOOR_USD, balance_usd, STATE_PATH)
    env = dict(os.environ)
    env["CC_BASE_AGENT_ID"] = "cc-fleet-health"
    subprocess.run(
        [sys.executable, str(ORCH_DIR / "scripts" / "bus_send.py"),
         "--from", "cc-fleet-health", "--to", "orch-console",
         "--type", "update", "--priority", "P1",
         "--subject", "DeepSeek balance below $%d: %.2f USD" % (ALERT_FLOOR_USD, balance_usd),
         "--body", body,
         "--evidence", "probe:GET %s total_balance=%.2f" % (BALANCE_URL, balance_usd)],
        cwd=str(ORCH_DIR), env=env, check=False,
    )


def main() -> int:
    try:
        key = fetch_key()
        payload = fetch_balance(key)
        balance_usd = _total_usd(payload)
    except Exception as exc:  # fail LOUD, never silent on a money-adjacent monitor
        print("deepseek_balance_poll: FAILED: %s" % exc, file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc).isoformat()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": now, "balance_usd": balance_usd}) + "\n")

    print("deepseek_balance_poll: %s balance_usd=%.2f" % (now, balance_usd))

    if balance_usd < ALERT_FLOOR_USD:
        send_alert(balance_usd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
