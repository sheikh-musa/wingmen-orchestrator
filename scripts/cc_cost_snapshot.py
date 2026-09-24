#!/usr/bin/env python3
"""cc_cost_snapshot.py — API-EQUIVALENT $ snapshot over cc_session_costs (SRE §4.6 spend view).

READ-ONLY. Computes list-price API-equivalent cost per body / per day from transcript
token counts. NOT cash — it is the "what this workload would cost at API list price"
exposure metric (fable report §1.2). Rates default to Opus 4.8 list (the current fleet
pin: all 32 fleet_lanes rows = claude-opus-4-8), since cc_session_costs has NO per-session
model column — per-MODEL split is only meaningful once the hub's model-per-role pilot
diversifies pins AND per-session model is captured (follow-up). Cross-checked vs the fable
report: last-30d total ~$59.8k, cache-read ~81%.
"""
import os, sys, argparse, datetime
import psycopg2

# Opus 4.8 list, $/MTok (fable report §1.2, 2026-09-22). Keyed for future multi-model.
RATES = {
    "claude-opus-4-8": {"in": 5.0, "out": 25.0, "cache_write": 6.25, "cache_read": 0.50},
}
DEFAULT_MODEL = "claude-opus-4-8"

def _cost(row, rates):
    inp, out, cw, cr = (float(x or 0) for x in row)
    return ((inp*rates["in"] + out*rates["out"]
            + cw*rates["cache_write"] + cr*rates["cache_read"]) / 1_000_000.0)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--by-day", action="store_true", help="also show per-day fleet totals")
    args = ap.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("could-not-measure: DATABASE_URL not set", file=sys.stderr); return 2
    r = RATES[DEFAULT_MODEL]
    conn = psycopg2.connect(dsn); conn.autocommit = True
    cur = conn.cursor()
    since = f"now() - interval '{int(args.days)} days'"
    ts = "COALESCE(ended_at, created_at)"
    # per-body totals
    cur.execute(f"""
      SELECT cc_identity,
             sum(input_tokens), sum(output_tokens),
             sum(cache_creation_input_tokens), sum(cache_read_input_tokens),
             count(*)
      FROM cc_session_costs WHERE {ts} >= {since}
      GROUP BY cc_identity ORDER BY cc_identity""")
    rows = cur.fetchall()
    bodies = []
    tot_in=tot_out=tot_cw=tot_cr=0
    for ident, inp, out, cw, crd, n in rows:
        cost = _cost((inp, out, cw, crd), r)
        bodies.append((ident or "(null)", cost, crd or 0, n))
        tot_in += inp or 0; tot_out += out or 0; tot_cw += cw or 0; tot_cr += crd or 0
    fleet = _cost((tot_in, tot_out, tot_cw, tot_cr), r)
    cache_read_cost = (float(tot_cr) * r["cache_read"]) / 1e6
    bodies.sort(key=lambda x: x[1], reverse=True)

    print(f"=== API-equivalent $ snapshot (Opus-4.8 list) — last {args.days}d ===")
    print(f"FLEET TOTAL: ${fleet:,.0f}  |  cache-read ${cache_read_cost:,.0f} "
          f"({(cache_read_cost/fleet*100 if fleet else 0):.0f}% — the bloat line)  |  sessions={sum(b[3] for b in bodies)}")
    print(f"{'body':<24}{'$ API-equiv':>12}{'cache-read tok':>16}{'sessions':>10}")
    for ident, cost, crd, n in bodies[:args.top]:
        print(f"{ident:<24}{('$'+format(cost,',.0f')):>12}{crd:>16,}{n:>10}")
    if args.by_day:
        cur.execute(f"""
          SELECT to_char({ts},'YYYY-MM-DD') d,
                 sum(input_tokens), sum(output_tokens),
                 sum(cache_creation_input_tokens), sum(cache_read_input_tokens)
          FROM cc_session_costs WHERE {ts} >= {since}
          GROUP BY d ORDER BY d""")
        print("\n=== per-day fleet $ (API-equiv) ===")
        for d, inp, out, cw, crd in cur.fetchall():
            print(f"  {d}: ${_cost((inp,out,cw,crd), r):,.0f}")
    conn.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
