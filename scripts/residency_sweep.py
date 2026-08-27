#!/usr/bin/env python3
"""residency_sweep.py — standing TENANT-RESIDENCY-001 self-audit (cai-ratified doctrine).

For each client, asserts the client has ZERO rows in any FOREIGN project (a project
that is not that client's designated store). Uses cc-ihsanos's dynamic org_id sweep
(CAI-RESP-369) so it self-adapts as tables are added, plus the organizations-registry
check (org table keys on id, not org_id). Alerts the operator on any n>0.

Doctrine (CAI-RESP-368/369): a client's rows live in that client's silo, always. This
sweep is the enforcement teeth — a stale/commingled tenant surfaces on a schedule
instead of only when someone thinks to look (how the 2026-07-02 irsyad leftover, ~2760
PII rows frozen since 06-12 on ceayj, went unnoticed).

Config below: per client, the org id + the foreign projects it must NOT appear in.
GREEN = zero rows everywhere foreign. Runs on a launchd timer; alerts via tg_send.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ORCH = Path(os.path.expanduser("~/wingmen/orchestrator"))
load_dotenv(ORCH / ".env")
LOG = ORCH / "logs" / "residency_sweep.log"

# Per client: org id + FOREIGN projects (must contain ZERO of this client's rows).
# dsn_env = the .env var holding that foreign project's DSN. Extend as clients/silos grow.
CLIENTS = [
    {
        "alias": "irsyad",
        "org_id": "8de59182-eae0-422b-9d1c-0ff05094badb",
        "foreign": [
            {"ref": "ceayjeamtmcyzzvqflus (ihsanos multi-tenant DB)", "dsn_env": "IHSANOS_PROD_RO_DATABASE_URL"},  # CAI-1225 RO-move: read-only auditor_ro (was write-DSN); sweep is SELECT-only
        ],
    },
]

# %%L / %%I are literal SQL format() specifiers (doubled so psycopg passes them
# through); %s is the single psycopg param (org_id).
# Known violations under gated remediation — logged but NOT re-alerted until resolved.
# Key: (client alias, foreign project ref). Remove the entry once the purge lands so a
# re-appearance alerts again. (irsyad/ceayj = CAI-RESP-369 stale tenant, purge gated on
# cai parity verdict + operator YES; expected until STEP 4.)
ACKNOWLEDGED = {
    ("irsyad", "ceayjeamtmcyzzvqflus"): "CAI-RESP-369 stale pre-silo tenant, purge gated on operator YES",
}

GEN_UNION = """
SELECT string_agg(
  format($f$SELECT %%L AS tbl, count(*) AS n FROM public.%%I WHERE org_id = %%L$f$,
         c.table_name, c.table_name, %s::text),
  $u$ UNION ALL $u$)
FROM information_schema.columns c
JOIN information_schema.tables t USING (table_schema, table_name)
WHERE c.table_schema='public' AND c.column_name='org_id' AND t.table_type='BASE TABLE'
"""


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def sweep_one(org_id: str, dsn: str) -> list[tuple[str, int]]:
    """Return [(table, count)] where this org has rows in the foreign project."""
    hits: list[tuple[str, int]] = []
    with psycopg.connect(dsn, connect_timeout=20) as conn, conn.cursor() as cur:
        cur.execute(GEN_UNION, (org_id,))
        union_sql = cur.fetchone()[0]
        if union_sql:
            cur.execute(f"SELECT tbl, n FROM ( {union_sql} ) s WHERE n > 0 ORDER BY n DESC")
            hits.extend(cur.fetchall())
        # organizations registry keys on id, not org_id — check separately
        try:
            cur.execute("SELECT count(*) FROM public.organizations WHERE id = %s", (org_id,))
            n = cur.fetchone()[0]
            if n:
                hits.append(("organizations (registry row)", n))
        except Exception:
            pass
    return hits


def main() -> int:
    findings: list[str] = []
    for client in CLIENTS:
        for fp in client["foreign"]:
            dsn = os.environ.get(fp["dsn_env"], "").strip()
            if not dsn:
                log(f"SKIP {client['alias']} vs {fp['ref']}: {fp['dsn_env']} not set")
                continue
            try:
                hits = sweep_one(client["org_id"], dsn)
            except Exception as e:
                log(f"ERROR sweeping {client['alias']} vs {fp['ref']}: {type(e).__name__}: {e}")
                findings.append(f"⚠️ residency sweep ERROR: {client['alias']} vs {fp['ref']} ({type(e).__name__})")
                continue
            if hits:
                total = sum(n for _, n in hits)
                detail = ", ".join(f"{t}={n}" for t, n in hits[:6])
                msg = (f"🔴 RESIDENCY VIOLATION: '{client['alias']}' has {total} rows across "
                       f"{len(hits)} table(s) in FOREIGN project {fp['ref']} — must be zero (TENANT-RESIDENCY-001). {detail}")
                log(msg)
                # Suppress alerts for KNOWN violations already in gated remediation
                # (avoid re-alerting the operator every run on a purge that's pending
                # cai verdict + operator YES). New/unacked violations still alert.
                ack = ACKNOWLEDGED.get((client["alias"], fp["ref"].split(" ")[0]))
                if ack:
                    log(f"  (acknowledged/in-remediation: {ack} — logged, not alerted)")
                else:
                    findings.append(msg)
            else:
                log(f"GREEN: {client['alias']} has zero rows in {fp['ref']}")
    if findings:
        body = ("🧭 Residency self-audit (TENANT-RESIDENCY-001) found issues:\n" + "\n".join(findings) +
                "\nClient data in the wrong project — see logs/residency_sweep.log")
        try:
            subprocess.run([str(ORCH / "scripts" / "tg_send.sh"), body], timeout=30)
        except Exception as e:
            log(f"tg_send failed: {e}")
    else:
        log("clean sweep — all clients residency-GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
