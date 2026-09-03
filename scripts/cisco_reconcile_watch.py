#!/usr/bin/env python3
"""cisco_reconcile_watch.py — DETECT-ONLY: watch the FIRST real Cisco coin-deposit reconcile.

WHY (2026-08-19, Nazim #28958 / cai CAI-RESP-1191): cai cleared the Cisco money-path go-live
on the audit-stack with ONE condition — actively watch the FIRST real admin/preparer use to
confirm it completes an approve→reconcile cycle CLEANLY (the real-world substitute for the
CAI-1118-blocked authenticated-UI smoke). This must survive console context resets, so the
DURABLE detection half lives here (SRE domain, launchd peer of the SLA/hub-bus monitors);
orch-console owns the RESPONSE.

ALERTS orch-console + cc-irsyad-coord when:
  (a) the FIRST row reaches status='reconciled'  → clean first real use → console confirms +
      graduates Cisco to 'Live now — done'.
  (b) a row sits NON-TERMINAL (draft / count_approved) for > STUCK_HOURS without reaching
      reconciled → the potential STUCK / wiring-bug case cai's watch is meant to catch.

STRICT PII DISCIPLINE (client money-path table): READ-ONLY, and it SELECTs ONLY the non-PII
column allowlist below — status + timestamps + the opaque public_id + counts. It NEVER reads
or pages a deposit amount, a bank/certis reference, a preparer/endorser identity, notes, or a
URL. DEAD-MAN'S-SWITCH: a connection/query failure (could-not-measure) PAGES LOUD — a silent
monitor on a money-path go-live is worse than none.

Deploys INERT: pages only under CISCO_WATCH_ENABLED=1 (set on the launchd). CISCO_WATCH_DRY=1
prints instead of inserting. Deduped so the daemon pages once per event. Retires after one
clean reconcile (console unloads the launchd).
"""
from __future__ import annotations

import os
import sys
import time

GOUMLYNE_ENV = "GOUMLYNE_RO_DATABASE_URL"  # CAI-1225 RO-move: read-only auditor_ro (was write-DSN); this monitor is SELECT-only
TABLE = "public.tabung_coin_deposits"
# NON-PII allowlist — the ONLY columns this monitor may ever read. status + timestamps +
# the opaque public uuid. NEVER add an amount / reference / identity / url / notes column.
SAFE_COLUMNS = ("public_id", "status", "created_at", "updated_at")
NON_TERMINAL = frozenset({"draft", "count_approved"})   # not yet reconciled
TERMINAL_CLEAN = "reconciled"
STUCK_HOURS = float(os.environ.get("CISCO_STUCK_HOURS", "2.5"))
PAGE_COOLDOWN_MIN = int(os.environ.get("CISCO_PAGE_COOLDOWN_MIN", "180"))
RECIPIENTS = ("orch-console", "cc-irsyad-coord")   # console owns response; coord tracks the client thread
ENABLED = os.environ.get("CISCO_WATCH_ENABLED") == "1"
DRY = os.environ.get("CISCO_WATCH_DRY") == "1"
SRE_FROM = "cc-fleet-health"

# Transient-blip retry for the goumlyne connect (port of ad38b99, orch-console-reviewed). A
# single transient DNS failure resolving the Supabase pooler host (aws-1-ap-southeast-1.pooler
# .supabase.com) used to trip the money-path dead-man (could-not-measure) on a blip that
# self-heals in seconds (2026-09-03: coord + orch-console both reproduced it then saw goumlyne
# recover on the first retry). We retry the connect+SELECT a few times with short linear backoff
# so a blip is absorbed — but a GENUINE, persistent failure STILL re-raises to the dead-man page
# (could-not-measure must fail LOUD on a real money-path outage; retries only buy a grace window).
_GOUMLYNE_ATTEMPTS = int(os.environ.get("CISCO_DB_ATTEMPTS", "3"))
_GOUMLYNE_RETRY_BASE_S = float(os.environ.get("CISCO_DB_RETRY_BASE_S", "1.0"))


def _sleep(seconds: float) -> None:
    """Indirection over time.sleep so tests can suppress real backoff delay."""
    time.sleep(seconds)


def _retry(op, *, attempts: int, base_delay_s: float, retry_on, sleep=_sleep):
    """Call op(); on a `retry_on` exception, retry up to `attempts` TOTAL tries with linear
    backoff (base_delay_s * attempt_number between tries). Re-raises the last exception after
    the final attempt — so a persistent failure still surfaces to the dead-man's-switch.
    Exceptions outside `retry_on` propagate immediately (never mask a non-transient bug). `op`
    must be idempotent/side-effect-free — the goumlyne read here is SELECT-only."""
    for attempt in range(1, attempts + 1):
        try:
            return op()
        except retry_on:
            if attempt == attempts:
                raise
            sleep(base_delay_s * attempt)


# ── PURE classification (unit-tested; no DB, no PII) ─────────────────────────
def is_stuck(status: str, age_s: "float | None", stuck_hours: float = STUCK_HOURS) -> bool:
    """PURE. A row is STUCK iff it is in a NON-TERMINAL status AND has sat there longer than
    stuck_hours (age = now - updated_at). A reconciled row is never stuck; a fresh non-terminal
    row within threshold is normal in-progress, not stuck. None age fails SAFE to not-stuck
    (the caller pages could-not-measure separately)."""
    if status not in NON_TERMINAL or age_s is None:
        return False
    return age_s > stuck_hours * 3600.0


def select_stuck(rows, stuck_hours: float = STUCK_HOURS):
    """PURE. From [(public_id, status, age_s)], return those that are_stuck — the wiring-bug
    candidates. Terminal/reconciled and within-threshold rows are excluded."""
    return [r for r in rows if is_stuck(r[1], r[2], stuck_hours)]


def _dsn_goumlyne() -> "str | None":
    return os.environ.get(GOUMLYNE_ENV)


def _load_dotenv_launchd():
    """launchd's process env has no .env — load the orch .env (does NOT override set vars)."""
    try:
        from dotenv import load_dotenv
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        load_dotenv(os.path.join(here, ".env"))
    except Exception:  # noqa: BLE001
        pass


def _already_paged(cur, key: str, cooldown_min: int) -> bool:
    """Dedup on the substrate: a page from us whose subject contains `key` within cooldown."""
    cur.execute(
        "SELECT 1 FROM agent_messages WHERE from_agent=%s AND subject LIKE %s "
        "AND created_at > now() - make_interval(mins => %s) LIMIT 1",
        (SRE_FROM, f"%{key}%", cooldown_min))
    return cur.fetchone() is not None


def _page(cur, subject: str, body: str, priority: str) -> None:
    """NON-DESTRUCTIVE page to RECIPIENTS on the substrate. Carries status/counts/timestamps
    ONLY — never a PII value. Caller dedups + commits."""
    for to_agent in RECIPIENTS:
        if DRY:
            print(f"[cisco-watch DRY] would page {to_agent} [{priority}] :: {subject}")
        else:
            cur.execute(
                "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,requires_response,priority) "
                "VALUES (%s,%s,'update',%s,%s,true,%s)", (SRE_FROM, to_agent, subject, body, priority))


def _page_loud_cannot_measure(reason: str) -> int:
    """Dead-man's-switch: could-not-measure PAGES LOUD (not green). Own connection (the goumlyne
    read is what failed). Substrate page. Deduped."""
    import psycopg
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15) as c, c.cursor() as cur:
            if _already_paged(cur, "CISCO WATCH COULD-NOT-MEASURE", PAGE_COOLDOWN_MIN):
                print("[cisco-watch] could-not-measure but already paged within cooldown — deduped")
                return 2
            subject = "CISCO WATCH COULD-NOT-MEASURE — Cisco reconcile go-live UNVERIFIED"
            body = (f"🟠 cisco_reconcile_watch could not read goumlyne {TABLE} to verify the Cisco "
                    f"reconcile go-live: {reason}. Could-not-measure is NOT green (dead-man's-switch) — "
                    f"the money-path first-use watch (cai CAI-RESP-1191) is blind until this clears. "
                    f"No PII read. Investigate the goumlyne read path.")
            if not DRY:
                _page(cur, subject, body, "P1")
                c.commit()
            else:
                print(f"[cisco-watch DRY] would LOUD-page could-not-measure :: {reason}")
    except Exception as e:  # noqa: BLE001 — even the loud-page failed; last resort stderr
        print(f"[cisco-watch] FATAL: could-not-measure AND loud-page failed ({e})", file=sys.stderr)
    return 2


def run_once() -> int:
    """One detect pass. READ-ONLY on goumlyne (SAFE_COLUMNS only). Pages (a) first reconcile,
    (b) each stuck row; could-not-measure pages loud. Returns 0 clean / 2 could-not-measure."""
    _load_dotenv_launchd()
    import psycopg
    gdsn = _dsn_goumlyne()
    if not gdsn:
        return _page_loud_cannot_measure(f"{GOUMLYNE_ENV} not set")
    def _read_goumlyne():
        # Retried as a UNIT on a transient connect blip (safe: SELECT-only, no side effects). A
        # persistent OperationalError re-raises to the dead-man page in the except below.
        with psycopg.connect(gdsn, connect_timeout=15) as g, g.cursor() as gcur:
            # (a) first reconciled? count only.
            gcur.execute(f"SELECT count(*) FROM {TABLE} WHERE status = %s", (TERMINAL_CLEAN,))
            reconciled_n = int(gcur.fetchone()[0])
            # (b) non-terminal rows + age — SAFE_COLUMNS ONLY (public_id, status, updated_at age)
            gcur.execute(
                f"SELECT public_id, status, extract(epoch from (now() - updated_at)) "
                f"FROM {TABLE} WHERE status = ANY(%s)", (list(NON_TERMINAL),))
            nonterm = [(str(r[0]), r[1], (float(r[2]) if r[2] is not None else None)) for r in gcur.fetchall()]
            gcur.execute(f"SELECT count(*) FROM {TABLE}")
            total_n_row = int(gcur.fetchone()[0])
        return reconciled_n, nonterm, total_n_row

    try:
        reconciled_n, nonterm, total_n_row = _retry(
            _read_goumlyne, attempts=_GOUMLYNE_ATTEMPTS,
            base_delay_s=_GOUMLYNE_RETRY_BASE_S, retry_on=psycopg.OperationalError)
    except Exception as e:  # noqa: BLE001 — dead-man's-switch (persistent failure pages loud)
        return _page_loud_cannot_measure(str(e).splitlines()[0] if str(e) else type(e).__name__)

    stuck = select_stuck(nonterm)
    print(f"[cisco-watch] rows={total_n_row} reconciled={reconciled_n} non_terminal={len(nonterm)} stuck={len(stuck)}")

    if not ENABLED:
        if reconciled_n or stuck:
            print("[cisco-watch] event present but CISCO_WATCH_ENABLED!=1 — INERT (detect-only, no page)")
        return 0

    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15) as c, c.cursor() as cur:
        # (a) first clean reconcile — page ONCE (dedup on the fixed subject key).
        if reconciled_n >= 1 and not _already_paged(cur, "CISCO GO-LIVE FIRST RECONCILE", 7 * 24 * 60):
            subject = "CISCO GO-LIVE FIRST RECONCILE — a coin-deposit reached status=reconciled (clean first use)"
            body = (f"✅ The FIRST real Cisco coin-deposit has reached status='reconciled' on goumlyne "
                    f"({TABLE}). That is a CLEAN approve→reconcile cycle — the cai CAI-RESP-1191 condition "
                    f"for the money-path go-live. ACTION (console): confirm + graduate Cisco to 'Live now — done' "
                    f"+ tell the client it's confirmed. Then this watch RETIRES (unload the launchd). "
                    f"reconciled_count={reconciled_n}, total_rows={total_n_row}. Status/counts only — no PII.")
            _page(cur, subject, body, "P1")
            print(f"[cisco-watch{' DRY' if DRY else ''}] PAGED first-reconcile")
        # (b) stuck rows — page once per public_id.
        for pub_id, status, age_s in stuck:
            key = f"CISCO STUCK {pub_id}"
            if _already_paged(cur, key, PAGE_COOLDOWN_MIN):
                continue
            hrs = (age_s or 0) / 3600.0
            subject = f"CISCO STUCK: deposit {pub_id} in status={status} for ~{hrs:.1f}h — possible wiring bug"
            body = (f"⚠️ A Cisco coin-deposit (public_id {pub_id}) has sat in NON-TERMINAL status='{status}' "
                    f"for ~{hrs:.1f}h (> {STUCK_HOURS:.1f}h) without reaching 'reconciled' on goumlyne. This is the "
                    f"potential STUCK / reconcile-wiring case cai CAI-RESP-1191 asked to catch. ACTION: investigate "
                    f"the approve→reconcile path + fix fast. Status/timestamps only — no amounts/identities read.")
            _page(cur, subject, body, "P1")
            print(f"[cisco-watch{' DRY' if DRY else ''}] PAGED stuck {pub_id} ({status}, {hrs:.1f}h)")
        if not DRY:
            c.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_once())
