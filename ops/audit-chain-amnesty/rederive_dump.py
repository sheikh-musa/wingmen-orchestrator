#!/usr/bin/env python3
"""Step 1 of the CAI-RESP-1363 frozen-amnesty re-derivation (independently re-runnable).

Dumps the goumlyne (irsyad silo) audit_log chain READ-ONLY at the current tip, plus snapshot
metadata, for rederive_frozen_list.mjs (step 2) to classify. Reads GOUMLYNE_RO_DATABASE_URL.

Output goumlyne_chain.jsonl carries audit PAYLOADS -> it is GITIGNORED (never commit client
audit content). Only the scripts + the payload-free result (frozen_amnesty_goumlyne.json) +
snapshot_meta.json are committed. Re-run: python rederive_dump.py && node rederive_frozen_list.mjs
-> compare frozen_row_ids to audit_chain_boundaries#1.amnesty_row_ids (orch substrate).
"""
import os, json, psycopg2

ORG = '73339164-7c1f-40ba-a093-33f1f292dd4c'   # irsyad silo (goumlyne), audit_chain_boundaries#1
HERE = os.path.dirname(os.path.abspath(__file__))

def ro_dsn():
    env = os.environ.get('GOUMLYNE_RO_DATABASE_URL')
    if env:
        return env
    for l in open(os.path.join(HERE, '..', '..', '.env')):
        l = l.strip()
        if l.startswith('GOUMLYNE_RO_DATABASE_URL='):
            return l.split('=', 1)[1].strip().strip('"')
    raise SystemExit("GOUMLYNE_RO_DATABASE_URL not found")

def main():
    conn = psycopg2.connect(ro_dsn()); cur = conn.cursor()
    cur.execute("SELECT now()")
    snap_at = cur.fetchone()[0].isoformat()
    cur.execute("SELECT id, hash FROM public.audit_log WHERE org_id=%s ORDER BY id DESC LIMIT 1", (ORG,))
    tip_id, tip_hash = cur.fetchone()
    cur.execute("SELECT count(*) FROM public.audit_log WHERE org_id=%s AND id<=%s", (ORG, tip_id))
    n = cur.fetchone()[0]
    meta = {"org_id": ORG, "project_ref": "goumlynecruxrlmzlntp", "snapshot_at": snap_at,
            "tip_id": tip_id, "tip_hash": tip_hash, "row_count_le_tip": n, "genesis": "genesis", "cutover_hint": 742}
    json.dump(meta, open(os.path.join(HERE, 'snapshot_meta.json'), 'w'), indent=2)
    cur.execute("""SELECT id, prev_hash, hash, payload, entity_type, action FROM public.audit_log
                   WHERE org_id=%s AND id<=%s ORDER BY id ASC""", (ORG, tip_id))
    with open(os.path.join(HERE, 'goumlyne_chain.jsonl'), 'w') as f:
        c = 0
        for rid, prev, h, payload, et, act in cur:
            f.write(json.dumps({"id": rid, "prev_hash": prev, "hash": h,
                                "payload": payload, "entity_type": et, "action": act}) + "\n"); c += 1
    print(f"snapshot tip_id={tip_id} rows={c} snapshot_at={snap_at}")
    cur.close(); conn.close()

if __name__ == '__main__':
    main()
