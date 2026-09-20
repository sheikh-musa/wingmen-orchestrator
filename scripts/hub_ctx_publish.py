#!/usr/bin/env python3
"""hub_ctx_publish.py — publish the hub's live context-window fill to cc_session_costs.

Runs ON THE VPS (where the hub's Claude session jsonl lives). The fleet console's
coordinator card reads latest_context_tokens from the freshest cc_session_costs
row per identity to draw the ctx% gauge (cai/Nazim have it). The hub runs on the
VPS, so the Mini's cost-writer never sees its jsonl -> the hub's row went ~46h
stale at 0 -> no ctx% on its card (operator 2026-08-02: "i still dont see orch's
context [BLOAT] in fleet console"). This is the analog of the VPS pane-publisher,
for the bloat number: parse the newest hub jsonl's LAST assistant-turn usage
(= live window fill) and upsert cc_session_costs['cc-orchestrator'].

Self-contained (no orch-repo imports beyond psycopg/dotenv) so it never depends
on the VPS checkout's code state. Mirrors the auto-writer's parse + upsert exactly
(source='auto_writer_v1', conflict on session_id+source) so it updates the same
row the Mini writer would if it could see this host.
"""
import glob
import json
import os
import sys
import time
from datetime import datetime, timezone

import psycopg
from dotenv import load_dotenv

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CC_IDENTITY = "cc-orchestrator"
SOURCE = "auto_writer_v1"
INTERVAL_S = 60


def _project_dir() -> str:
    """Claude Code's mangled ~/.claude/projects/<...> dir for REPO_ROOT, derived
    dynamically so this script is correct on any host/checkout (was previously
    hardcoded to wingmen-core's exact path -- broke the moment the hub session
    moved to a different host). Mangling rule confirmed empirically against this
    installation's own live project dirs: '/' and '.' both become '-'.
    Falls back to a fuzzy match (and then newest-jsonl-anywhere) if the exact
    derived name doesn't exist, since older Claude Code versions / edge cases
    on this fleet have shown dot-preserving variants -- best-effort by design,
    matches this script's own fail-soft posture (see main()'s try/except loop)."""
    mangled = "-" + REPO_ROOT.strip("/").replace("/", "-").replace(".", "-")
    base = os.path.expanduser("~/.claude/projects")
    exact = os.path.join(base, mangled)
    if os.path.isdir(exact):
        return exact
    # Fallback: compare with all non-alphanumerics stripped, so a dot-preserving
    # variant (older Claude Code versions on this fleet) still matches. A plain
    # dir-name prefix/rstrip trick is NOT used here on purpose -- it's brittle
    # against arbitrary repo basenames.
    target = "".join(ch for ch in mangled if ch.isalnum())
    try:
        candidates = [d for d in os.listdir(base)
                      if "".join(ch for ch in d if ch.isalnum()) == target]
    except OSError:
        candidates = []
    if len(candidates) == 1:
        return os.path.join(base, candidates[0])
    return exact  # let _parse_newest's glob come up empty rather than guess wrong


PROJ = _project_dir()


def _parse_newest():
    """(latest_context_tokens, session_id, sums, mtime) for the newest hub jsonl,
    or (None,...) if none. latest_context_tokens = last assistant turn's
    input+cache_read+cache_creation (the live window fill), exactly as the
    Mini auto-writer computes it."""
    files = sorted(glob.glob(os.path.join(PROJ, "*.jsonl")), key=os.path.getmtime, reverse=True)
    if not files:
        return None, None, None, None
    path = files[0]
    session_id = os.path.splitext(os.path.basename(path))[0]
    in_t = out_t = cc_t = cr_t = 0
    last_ctx = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict) or obj.get("type") != "assistant":
                    continue
                msg = obj.get("message")
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue
                m_in = int(usage.get("input_tokens") or 0)
                m_cc = int(usage.get("cache_creation_input_tokens") or 0)
                m_cr = int(usage.get("cache_read_input_tokens") or 0)
                in_t += m_in
                out_t += int(usage.get("output_tokens") or 0)
                cc_t += m_cc
                cr_t += m_cr
                last_ctx = m_in + m_cr + m_cc   # live window fill = LAST turn
    except OSError:
        return None, None, None, None
    return last_ctx, session_id, (in_t, out_t, cc_t, cr_t), os.path.getmtime(path)


def _publish(conn) -> "int|None":
    last_ctx, session_id, sums, mtime = _parse_newest()
    if last_ctx is None:
        return None
    in_t, out_t, cc_t, cr_t = sums
    ended = datetime.fromtimestamp(mtime, tz=timezone.utc)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM cc_session_costs WHERE session_id=%s AND source=%s LIMIT 1",
                    (session_id, SOURCE))
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE cc_session_costs SET input_tokens=%s, output_tokens=%s, "
                "cache_creation_input_tokens=%s, cache_read_input_tokens=%s, "
                "latest_context_tokens=%s, ended_at=%s WHERE id=%s",
                (in_t, out_t, cc_t, cr_t, last_ctx, ended, row[0]))
        else:
            cur.execute(
                "INSERT INTO cc_session_costs (cc_identity, session_id, started_at, ended_at, "
                "input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, "
                "latest_context_tokens, source, has_per_message_detail) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false)",
                (CC_IDENTITY, session_id, ended, ended, in_t, out_t, cc_t, cr_t, last_ctx, SOURCE))
    return last_ctx


def main() -> int:
    load_dotenv(os.path.join(REPO_ROOT, ".env"))
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        sys.stderr.write("hub_ctx_publish: no DATABASE_URL\n")
        return 1
    once = "--once" in sys.argv
    sys.stderr.write(f"hub_ctx_publish: up (interval={INTERVAL_S}s, proj={PROJ})\n")
    sys.stderr.flush()
    conn = None
    while True:
        try:
            if conn is None or conn.closed:
                conn = psycopg.connect(dsn, autocommit=True, connect_timeout=10)
            ctx = _publish(conn)
            sys.stderr.write(f"hub_ctx_publish: latest_context_tokens={ctx}\n")
            sys.stderr.flush()
        except Exception as e:
            sys.stderr.write(f"hub_ctx_publish: error {type(e).__name__}: {e}\n")
            sys.stderr.flush()
            try:
                if conn:
                    conn.close()
            except Exception:
                pass
            conn = None
        if once:
            return 0
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    sys.exit(main())
