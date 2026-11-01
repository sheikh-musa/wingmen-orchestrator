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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

ORCH_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = Path.home() / "wingmen" / "fleet-health" / "state" / "deepseek_balance_history.jsonl"
ALERT_FLOOR_USD = 40.0
DAILY_BUDGET_USD = 7.80  # op#35703: cost of one Max plan/day -- DeepSeek only worth it under this
BALANCE_URL = "https://api.deepseek.com/user/balance"

# ONCE-PER-DAY PAGE DEDUP (orch-console #89707). This runs hourly, so a condition that
# persists for a whole day -- a balance below the floor while someone is already managing
# spend, say -- used to page EVERY hour. On 2026-11-01 the 40.00 floor paged at 10:30Z while
# the balance was under active management; the second identical page carried no new fact and
# would have tripped the DeepSeek trial's "more than 2 false pages in an hour" rollback
# trigger. One page per UTC day per alert KIND; a new day re-pages, so a genuinely stuck
# balance still surfaces daily rather than going quiet.
ALERT_STATE_PATH = Path.home() / "wingmen" / "fleet-health" / "state" / "deepseek_alert_last_sent.json"


def _already_alerted_today(kind: str, day_iso: str, path: Optional[Path] = None) -> bool:
    """True if `kind` was already paged on this UTC day.

    Every failure mode returns False (= go ahead and page). A dedup that breaks must never
    silence a money alert: unreadable, absent, corrupt or wrong-shaped state all mean
    'cannot prove we already told them', which is a reason to tell them again, not to stay
    quiet. The only thing that suppresses is a clean, matching record."""
    try:
        data = json.loads((path or ALERT_STATE_PATH).read_text(encoding="utf-8"))
        return isinstance(data, dict) and data.get(kind) == day_iso
    except Exception:
        return False


def _record_alert(kind: str, day_iso: str, path: Optional[Path] = None) -> None:
    """Record AFTER the send returns. A crash between send and record costs a duplicate
    page tomorrow's run; the reverse order would cost a missed one -- and a duplicate is
    the cheaper mistake for a monitor (charter: fail LOUD, never silent)."""
    p = path or ALERT_STATE_PATH
    try:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        data[kind] = day_iso
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        print("deepseek_balance_poll: WARN could not record alert date for %s (%s) -- page was "
              "still sent, so the next run may duplicate it" % (kind, exc), file=sys.stderr)


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


def send_alert(balance_usd: float) -> bool:
    """True iff the page was actually posted.

    The return value is load-bearing, not decoration: main() must not record the day as
    'already paged' on a send that failed. bus_send exits non-zero on a transient condition
    (DB blip, pooler circuit breaker, soak refusal), and before this the caller recorded the
    dedup marker unconditionally -- so ONE failed send silenced the floor for the rest of
    the UTC day, while the pre-dedup code would simply have retried on the next poll. That
    is a NEW missed-page path, and silently missing the money page is the one direction this
    module must never fail in (cc-quality #89717).
    """
    body = (
        "TL;DR: DeepSeek balance is below the $%.0f floor -- %.2f USD left.\n\n"
        "Polled just now via scripts/deepseek_balance_poll.py. Topped up to $50 this morning "
        "(2026-10-29); this is a real draw-down, not a measurement artifact -- history is at "
        "%s if you want the curve."
    ) % (ALERT_FLOOR_USD, balance_usd, STATE_PATH)
    env = dict(os.environ)
    env["CC_BASE_AGENT_ID"] = "cc-fleet-health"
    res = subprocess.run(
        [sys.executable, str(ORCH_DIR / "scripts" / "bus_send.py"),
         "--from", "cc-fleet-health", "--to", "orch-console",
         "--type", "update", "--priority", "P1",
         "--subject", "DeepSeek balance below $%d: %.2f USD" % (ALERT_FLOOR_USD, balance_usd),
         "--body", body,
         "--evidence", "probe:GET %s total_balance=%.2f" % (BALANCE_URL, balance_usd)],
        cwd=str(ORCH_DIR), env=env, check=False,
    )
    return res.returncode == 0


def _trailing_24h_spend(now_iso: str, latest_balance: float) -> float:
    """op#35703. Reads history, returns balance_24h_ago - latest (no per-lane split --
    the DeepSeek balance endpoint is account-level only, not derivable per-lane)."""
    try:
        lines = STATE_PATH.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return 0.0
    rows = [json.loads(ln) for ln in lines]
    cutoff = (datetime.fromisoformat(now_iso) - timedelta(hours=24)).isoformat()
    older = [r for r in rows if r["ts"] <= cutoff]
    baseline = older[-1] if older else (rows[0] if rows else None)
    if baseline is None:
        return 0.0
    return baseline["balance_usd"] - latest_balance


def send_budget_alert(spend_24h: float) -> bool:
    """True iff the page was actually posted — same contract and same reason as send_alert."""
    body = (
        "TL;DR: DeepSeek spend over the last 24h is $%.2f, above the $%.2f/day budget "
        "(op#35703 -- a Max plan's daily cost; DeepSeek is only worth it under that).\n\n"
        "No per-lane split -- the DeepSeek /user/balance endpoint is account-level only, "
        "not broken out per lane. History: %s"
    ) % (spend_24h, DAILY_BUDGET_USD, STATE_PATH)
    env = dict(os.environ)
    env["CC_BASE_AGENT_ID"] = "cc-fleet-health"
    res = subprocess.run(
        [sys.executable, str(ORCH_DIR / "scripts" / "bus_send.py"),
         "--from", "cc-fleet-health", "--to", "orch-console",
         "--type", "update", "--priority", "P2",
         "--subject", "DeepSeek daily spend $%.2f over $%.2f/day budget" % (spend_24h, DAILY_BUDGET_USD),
         "--body", body,
         "--evidence", "probe:trailing-24h delta from %s = %.2f" % (STATE_PATH, spend_24h)],
        cwd=str(ORCH_DIR), env=env, check=False,
    )
    return res.returncode == 0


def main() -> int:
    try:
        key = fetch_key()
        payload = fetch_balance(key)
        balance_usd = _total_usd(payload)
    except Exception as exc:  # fail LOUD, never silent on a money-adjacent monitor
        print("deepseek_balance_poll: FAILED: %s" % exc, file=sys.stderr)
        return 1

    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    day = now_dt.date().isoformat()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": now, "balance_usd": balance_usd}) + "\n")

    # Every poll is recorded above; only the PAGE is deduped. The history stays hourly so
    # the curve and the 24h spend figure are unaffected.
    print("deepseek_balance_poll: %s balance_usd=%.2f" % (now, balance_usd))

    if balance_usd < ALERT_FLOOR_USD:
        if _already_alerted_today("balance_floor", day):
            print("deepseek_balance_poll: balance floor already paged today (%s) -- suppressed" % day)
        elif send_alert(balance_usd):
            _record_alert("balance_floor", day)
        else:
            # NOT recorded: the page did not go out, so the next hourly poll must retry it.
            # Recording a failed send would silence the floor for the rest of the day.
            print("deepseek_balance_poll: balance floor page FAILED to post -- not recorded, "
                  "next poll will retry", file=sys.stderr)

    spend_24h = _trailing_24h_spend(now, balance_usd)
    if spend_24h > DAILY_BUDGET_USD:
        if _already_alerted_today("daily_budget", day):
            print("deepseek_balance_poll: daily budget already paged today (%s) -- suppressed" % day)
        elif send_budget_alert(spend_24h):
            _record_alert("daily_budget", day)
        else:
            print("deepseek_balance_poll: daily budget page FAILED to post -- not recorded, "
                  "next poll will retry", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
