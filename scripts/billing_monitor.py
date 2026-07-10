#!/usr/bin/env python3
"""GitHub billing/budget monitor (cc-infra, CAI-RESP-418).

Polls GitHub's enhanced billing "usage report" (ONE call covers all 5 budgets:
Actions, Codespaces, Packages, Git LFS/storage, Copilot/AI-credits) and posts to
the orch bus:
  - a P1/P2 ALERT the first time month-to-date net spend crosses a budget
    threshold (dedup'd via a state file so we alert once per threshold per month),
  - a weekly heartbeat summary (so a silently-dead monitor is detectable and we
    never need to eyeball the billing page),
  - a fail-LOUD warning if the billing API errors (a broken monitor must be visible).

Read-only against GitHub. The only write is an agent_messages row on the orch bus.
Auth: reuses the fleet GH_TOKEN (classic, now carries the `user` scope that the
per-user billing endpoints require). DB via DATABASE_URL. Both from the orch .env.

Endpoint: GET /users/{user}/settings/billing/usage  (the legacy per-product
/billing/{actions,packages,shared-storage} endpoints are 410 Gone on the enhanced
billing platform). If the account is ever NOT on enhanced billing, this 404/410s
and we fail loud rather than silently report $0.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

ORCH = Path(os.environ.get("ORCH_HOME", str(Path(__file__).resolve().parents[1])))
STATE_DIR = Path(os.environ.get("BILLING_STATE_DIR", str(Path.home() / "wingmen" / "infra" / "billing_monitor")))
STATE_FILE = STATE_DIR / "state.json"

BILLING_USER = os.environ.get("GH_BILLING_USER", "sheikh-musa")
BUDGET_USD = float(os.environ.get("BILLING_BUDGET_USD", "20"))
# net-spend thresholds (fraction of BUDGET_USD) that fire an alert, low->high.
THRESHOLDS = [0.75, 0.90, 1.00]
# Pro plan monthly included Actions (Linux) minutes — a secondary gauge, because
# net $ stays 0 until included minutes are exhausted; watching gross minutes warns
# BEFORE the $ meter starts. Override if the plan changes.
ACTIONS_INCLUDED_MIN = float(os.environ.get("ACTIONS_INCLUDED_MIN", "3000"))
HEARTBEAT_DAYS = int(os.environ.get("BILLING_HEARTBEAT_DAYS", "7"))

# All 5 budgets we surface, even when usage is zero (so the report is complete).
PRODUCTS = ["actions", "codespaces", "packages", "git_lfs", "copilot"]
# GitHub's `product` field -> our budget bucket. Storage lines ride under actions/
# packages SKUs; we keep them under their product. Unknown products pass through.
PRODUCT_ALIAS = {"actions": "actions", "codespaces": "codespaces",
                 "packages": "packages", "git_lfs": "git_lfs",
                 "copilot": "copilot"}


def _load_env(path: Path) -> None:
    """Minimal .env loader (no python-dotenv dependency guarantee at runtime)."""
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


def _http_get_json(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "wingmen-billing-monitor",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def fetch_usage(token: str, year: int, month: int) -> dict:
    url = (f"https://api.github.com/users/{BILLING_USER}"
           f"/settings/billing/usage?year={year}&month={month}")
    return _http_get_json(url, token)


def aggregate(usage: dict) -> dict:
    """Return {product: {gross, net}}, total_net, and actions_minutes."""
    by_product: dict[str, dict[str, float]] = {p: {"gross": 0.0, "net": 0.0} for p in PRODUCTS}
    total_net = 0.0
    actions_minutes = 0.0
    actions_by_repo: dict[str, float] = {}
    for it in usage.get("usageItems", []):
        prod = PRODUCT_ALIAS.get(it.get("product", ""), it.get("product", "other"))
        bucket = by_product.setdefault(prod, {"gross": 0.0, "net": 0.0})
        bucket["gross"] += float(it.get("grossAmount", 0) or 0)
        net = float(it.get("netAmount", 0) or 0)
        bucket["net"] += net
        total_net += net
        if it.get("product") == "actions" and it.get("unitType") == "Minutes":
            mins = float(it.get("quantity", 0) or 0)
            actions_minutes += mins
            repo = it.get("repositoryName") or "(account)"
            actions_by_repo[repo] = actions_by_repo.get(repo, 0.0) + mins
    return {
        "by_product": by_product,
        "total_net": total_net,
        "actions_minutes": actions_minutes,
        "actions_by_repo": actions_by_repo,
    }


def crossed_threshold(frac: float) -> float | None:
    hit = None
    for t in THRESHOLDS:
        if frac >= t:
            hit = t
    return hit


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def post_bus(subject: str, body: str, priority: str) -> None:
    import psycopg
    dsn = os.environ["DATABASE_URL"]
    with psycopg.connect(dsn, connect_timeout=15) as c, c.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-infra',true)")
        cur.execute(
            "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,requires_response,priority) "
            "VALUES ('cc-infra','cc-orchestrator','update',%s,%s,false,%s)",
            (subject, body, priority))
        c.commit()


def summary_line(agg: dict, month_label: str) -> str:
    bp = agg["by_product"]
    parts = [f"{p}=${bp[p]['net']:.4f}" for p in PRODUCTS if p in bp]
    frac = agg["total_net"] / BUDGET_USD if BUDGET_USD else 0
    mins = agg["actions_minutes"]
    minfrac = mins / ACTIONS_INCLUDED_MIN if ACTIONS_INCLUDED_MIN else 0
    return (f"[{month_label}] net spend ${agg['total_net']:.4f} / ${BUDGET_USD:.0f} "
            f"budget ({frac*100:.1f}%). Actions {mins:.0f} min / {ACTIONS_INCLUDED_MIN:.0f} "
            f"included ({minfrac*100:.0f}%). Per-product net: " + ", ".join(parts))


def main() -> int:
    _load_env(ORCH / ".env")
    token = os.environ.get("GH_TOKEN")
    if not token:
        print("no GH_TOKEN", file=sys.stderr)
        return 2
    now = datetime.now(timezone.utc)
    month_label = now.strftime("%Y-%m")

    try:
        usage = fetch_usage(token, now.year, now.month)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        detail = getattr(e, "read", lambda: b"")() if hasattr(e, "read") else b""
        try:
            post_bus("[cc-infra][billing-monitor] API ERROR — budgets unreadable",
                     f"Billing usage fetch failed: {e}. Detail: {detail[:300]!r}. "
                     "Monitor cannot see spend until this clears — check GH_TOKEN `user` "
                     "scope / enhanced-billing enablement.", "P2")
        except Exception as pe:
            print(f"double failure: {e} / bus {pe}", file=sys.stderr)
        return 1

    agg = aggregate(usage)
    frac = agg["total_net"] / BUDGET_USD if BUDGET_USD else 0.0
    line = summary_line(agg, month_label)
    print(line)

    state = load_state()
    mkey = f"alerted:{month_label}"
    last_alerted = float(state.get(mkey, 0.0))

    hit = crossed_threshold(frac)
    if hit is not None and hit > last_alerted:
        pr = "P1" if hit >= 0.90 else "P2"
        post_bus(f"[cc-infra][billing-monitor] {int(hit*100)}% of ${BUDGET_USD:.0f} budget crossed",
                 f"ALERT: {line}\n\nCrossed the {int(hit*100)}% net-spend threshold this month. "
                 f"Top Actions consumers: " +
                 ", ".join(f"{r} {m:.0f}min" for r, m in
                           sorted(agg['actions_by_repo'].items(), key=lambda x: -x[1])[:5]),
                 pr)
        state[mkey] = hit
        save_state(state)
        return 0

    # weekly heartbeat (also proves the monitor is alive)
    last_hb = state.get("last_heartbeat")
    due = True
    if last_hb:
        try:
            due = (now - datetime.fromisoformat(last_hb)).days >= HEARTBEAT_DAYS
        except ValueError:
            due = True
    if due:
        post_bus("[cc-infra][billing-monitor] weekly budget heartbeat", line, "P3")
        state["last_heartbeat"] = now.isoformat()
        save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
