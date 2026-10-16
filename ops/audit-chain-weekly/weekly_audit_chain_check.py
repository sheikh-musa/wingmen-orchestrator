#!/usr/bin/env python3
"""IRSYAD AUDIT-CHAIN WEEKLY RUNNER — durable security monitor (console #68629/#68632).

Walks the goumlyne (irsyad silo) audit_log hash-chain READ-ONLY, detects NEW forks /
orphaned prev_hash links (tampering / unauthorized audit_log writes), updates a tiny
state file, and emits a heartbeat row to the orchestrator bus (agent_messages) so the
dead-man (held_commitment #331) can confirm the run actually FIRED.

PRIMARY control; the manual 30-day backstop (hc#243) stays as the backstop, not a
replacement. Integrity was clean as of hc#243 #67294 (2026-10-14, 0 forks).

SUPPRESSION BASELINE (console #68632, for the record): the 3 already-adjudicated
known-benign breaks are carried forward and NEVER re-flagged. A break/fork OUTSIDE
that set is a NEW anomaly and pages P1.

Reads (no payloads -> no PII, nothing to gitignore):
  GOUMLYNE_RO_DATABASE_URL  — RO goumlyne (same env var as ops/audit-chain-amnesty/rederive_dump.py)
  DATABASE_URL              — orchestrator bus (for the heartbeat agent_messages write)

Modes:
  (default / --diagnose)  walk + print a full anomaly report; NO heartbeat, NO state write.
                          Use this to validate the reconstruction reproduces the known baseline
                          BEFORE trusting it (console: "don't trust the schedule — verify").
  --run                   walk + update state.json + emit the heartbeat (clean=P3, new-fork=P1).
                          This is what the systemd timer calls weekly.

Run: GOUMLYNE_RO_DATABASE_URL=... DATABASE_URL=... python3 weekly_audit_chain_check.py [--run]
"""
import os, sys, json, hashlib, datetime

ORG = '73339164-7c1f-40ba-a093-33f1f292dd4c'   # irsyad silo (goumlyne), audit_chain_boundaries#1
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.expanduser('~/wingmen/state/irsyad-audit-chain/state.json')

# console #68632: the ONLY suppressed (already-adjudicated, benign) anomalies. A break/fork
# whose row id is OUTSIDE this set is NEW and pages P1. Carried forward from the 2026-10-07
# dry-run ("3 known breaks 52597/107509/117201, 0 new, dupForks 1") + op23344 verdict.
KNOWN_BENIGN_BREAK_IDS = {52597, 107509, 117201}
GENESIS_MARKERS = {None, '', 'genesis'}


def _dsn(var, fallback_prefix):
    env = os.environ.get(var)
    if env:
        return env
    envfile = os.path.join(HERE, '..', '..', '.env')
    try:
        for line in open(envfile):
            line = line.strip()
            if line.startswith(fallback_prefix + '='):
                return line.split('=', 1)[1].strip().strip('"')
    except FileNotFoundError:
        pass
    raise SystemExit(f"{var} not found (env or {envfile})")


def walk():
    import psycopg2
    conn = psycopg2.connect(_dsn('GOUMLYNE_RO_DATABASE_URL', 'GOUMLYNE_RO_DATABASE_URL'))
    try:
        cur = conn.cursor()
        cur.execute("SELECT now()")
        snap_at = cur.fetchone()[0].isoformat()
        # id ASC: hashes/prev_hash only — NO payload (no PII).
        cur.execute(
            "SELECT id, prev_hash, hash FROM public.audit_log WHERE org_id=%s ORDER BY id ASC",
            (ORG,))
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()

    # first occurrence index of each hash -> lets us measure how far back a re-anchor's parent is
    hash_to_idx = {}
    for i, (_rid, _prev, h) in enumerate(rows):
        if h is not None and h not in hash_to_idx:
            hash_to_idx[h] = i
    all_hashes = set(hash_to_idx)

    genesis_ids = []      # rows whose prev_hash is a genesis marker (expect exactly 1 legit)
    orphan_ids = []       # prev_hash resolves to NO real row hash and is not genesis (ALWAYS-PAGE tamper signal)
    reanchors = []        # (id, depth) prev_hash resolves to a real EARLIER row, not the immediate predecessor
    classify = {}         # per-row category for the 3 adjudicated ids (documentation/report only)
    prev_row_hash = None
    for i, (rid, prev, h) in enumerate(rows):
        if prev in GENESIS_MARKERS:
            genesis_ids.append(rid)
            cat = 'genesis'
        elif prev not in all_hashes:
            orphan_ids.append(rid)
            cat = 'orphan'
        elif prev != prev_row_hash:
            # depth = how many committed rows back the parent sits (shallow=concurrency; deep=suspicious backdate)
            depth = i - hash_to_idx[prev]
            reanchors.append((rid, depth))
            cat = 'reanchor'
        else:
            cat = 'normal'
        if rid in KNOWN_BENIGN_BREAK_IDS:
            classify[rid] = cat
        prev_row_hash = h

    tip_id = rows[-1][0] if rows else None
    # console #68640 HARD INVARIANT: orphans + genesis-forks ALWAYS page -- NEVER suppressed by the
    # baseline (the baseline only ever excused re-anchors, which are not paged anyway). No subtraction.
    genesis_forks = genesis_ids[1:]  # the first genesis is legitimate; any extra is a fork
    new_forks = sorted(set(genesis_forks) | set(orphan_ids))
    reanchors.sort(key=lambda t: -t[1])  # deepest first (most notable for a human look)
    return {
        'snapshot_at': snap_at,
        'rows': len(rows),
        'tip': tip_id,
        'genesis_ids': genesis_ids,
        'orphan_ids': orphan_ids,
        'reanchor_count': len(reanchors),
        'reanchors': reanchors,            # [(id, depth), ...] deepest first
        'max_reanchor_depth': (reanchors[0][1] if reanchors else 0),
        'genesis_forks': genesis_forks,
        'new_forks': new_forks,
        'baseline_classification': classify,
    }


def write_state(res):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    try:
        state = json.load(open(STATE_PATH))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    state[ORG] = {'rows': res['rows'], 'tip': res['tip']}
    json.dump(state, open(STATE_PATH, 'w'), indent=2)


def _git_sha():
    """Short git sha of the orchestrator repo this runner ran from (auditability, console #56629)."""
    import subprocess
    try:
        return subprocess.check_output(
            ['git', '-C', HERE, 'rev-parse', '--short', 'HEAD'],
            stderr=subprocess.DEVNULL, timeout=10).decode().strip()
    except Exception:
        return 'unknown'


def emit_heartbeat(res):
    import psycopg2
    clean = not res['new_forks']
    prio = 'P3' if clean else 'P1'
    subject = (f"audit-chain weekly: {res['rows']} rows walked, tip {res['tip']}, "
               f"{len(res['new_forks'])} new forks")
    body = {
        'runner': 'weekly_audit_chain_check.py',
        'org': ORG, 'silo': 'goumlyne',
        'rows_walked': res['rows'], 'tip': res['tip'],
        'reanchors_benign_count': res['reanchor_count'],
        'reanchors_id_depth': res['reanchors'],         # (id, rows-back-to-parent); deep = look
        'max_reanchor_depth': res['max_reanchor_depth'],
        'genesis_rows': res['genesis_ids'],
        'orphans_PAGE': res['orphan_ids'],
        'genesis_forks_PAGE': res['genesis_forks'],
        'new_forks': res['new_forks'],
        'verdict': 'CLEAN (0 orphans, 0 genesis-forks)' if clean else f"ALARM: tamper signal {res['new_forks']}",
        'note': 'orphans+genesis-forks ALWAYS page P1 (never suppressed); re-anchors reported not paged (console #68640)',
        'ran_at': datetime.datetime.utcnow().isoformat() + 'Z',
        'git_sha': _git_sha(),
    }
    conn = psycopg2.connect(_dsn('DATABASE_URL', 'DATABASE_URL'))
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body,
                                           requires_response, priority)
               VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            ('audit-chain-runner', 'cc-irsyad-coord',
             ('blocker' if not clean else 'update'),
             subject, json.dumps(body), (not clean), prio))
        mid = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return mid
    finally:
        conn.close()


def main():
    run_mode = '--run' in sys.argv
    res = walk()
    report = {k: v for k, v in res.items() if k != 'snapshot_at'}
    print("=== IRSYAD AUDIT-CHAIN WEEKLY CHECK ===")
    print(f"snapshot_at      : {res['snapshot_at']}")
    print(f"rows walked      : {res['rows']}")
    print(f"tip id           : {res['tip']}")
    print(f"genesis rows     : {res['genesis_ids']}  (first is legit; extras = forks)")
    print(f"orphan prev_hash : {res['orphan_ids']}  (prev points to no real row = ALWAYS-PAGE tamper signal)")
    print(f"benign reanchors : {res['reanchor_count']}  (id,depth)={res.get('reanchors')}  max_depth={res.get('max_reanchor_depth')}  (deep=look)")
    print(f"baseline classify: {res.get('baseline_classification')}  (3 adjudicated ids, doc/report only — NOT a tamper suppressor)")
    print(f"baseline (suppressed): {sorted(KNOWN_BENIGN_BREAK_IDS)}")
    print(f"NEW forks (ALARM): {res['new_forks']}")
    print(f"VERDICT          : {'CLEAN' if not res['new_forks'] else 'ALARM — NEW forks: ' + str(res['new_forks'])}")
    if not run_mode:
        print("\n[diagnose mode] no state write, no heartbeat. Pass --run to update state + emit heartbeat.")
        return 0 if not res['new_forks'] else 2
    write_state(res)
    mid = emit_heartbeat(res)
    print(f"\n[run mode] state.json updated (tip {res['tip']}); heartbeat agent_messages id={mid} ({'P3 clean' if not res['new_forks'] else 'P1 ALARM'}).")
    return 0 if not res['new_forks'] else 2


if __name__ == '__main__':
    sys.exit(main())
