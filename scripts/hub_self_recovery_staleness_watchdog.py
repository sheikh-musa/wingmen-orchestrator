#!/usr/bin/env python3
"""hub_self_recovery_staleness_watchdog.py — DETECT-ONLY: catch the hub-self-recovery
timer going silently dead.

WHY (hc#175, CAI-RESP-1439 pre-arming precondition, discovered during drill design
CAI-RESP-1449 / cc-quality #50036): `nervous_system/hub_self_recovery.py` runs on gzb
every ~2min and stamps `hub_self_recovery_settings.last_tick_at` on every real tick
(`stamp_tick()`, in the `finally` of `evaluate()`, plus `record_no_session()` for the
no-tmux-session branch) — this is ITS OWN internal liveness marker, by design (module
docstring, "audit #43169 finding 6"). But nothing outside that module ever READS the
marker to page on it: a DB-unreachable tick fails closed (takes no action) but
SILENTLY — the connect-time failure happens before the `finally` block's stamp, so
`last_tick_at` does not even advance, and there was no reader anywhere to notice. An
armed-but-silently-dead recovery process is a WORSE failure mode than an observe-only
one going quiet (the whole point of this fleet's self-recovery work is to not need a
human watching it) — so this closes BEFORE `HSR_MODE=act` is ever set, per the
original commitment framing.

This is a pure remote DB-read check — it does NOT run on gzb, does NOT touch the hub's
tmux pane, and never asserts anything about `orch_lease`/`holder_host`. It only reads
one timestamp column in the shared Supabase DB and pages if it's gone stale. Same
shape/conventions as hub_bus_currency_monitor.py (its closest fleet peer): PURE
classifier (unit-tested, no DB), a thin DB-read, a deduped page via a stable subject
marker (no separate state file), ENABLED env-gate (armed by default here — unlike a
pane-facing/action-adjacent monitor, a false-positive page here costs nothing more
than a diagnostic P2, so there is no reason to ship this one INERT-by-default).

THRESHOLD: stale past ~2x the ~2min tick interval (CAI-RESP-1439 payload's own
guidance, "e.g. 5-10min") — default 480s (8min, roughly the midpoint), configurable.
A NULL last_tick_at (never ticked even once — pre-deployment, or the timer has never
reached the stamp at all) is ALSO stale: indistinguishable from "supposed to be
running but isn't", never treated as a free pass.
"""
from __future__ import annotations

import os
import sys

RECIPIENTS = ("orch-console", "cc-fleet-health")  # Nazim relays to operator; SRE owns the loop
SRE_FROM = "cc-fleet-health"
STALE_S = float(os.environ.get("HSR_STALENESS_WARN_S", "480"))  # ~2x-4x the ~2min tick; mid of the 5-10min guidance
PAGE_COOLDOWN_MIN = int(os.environ.get("HSR_STALENESS_PAGE_COOLDOWN_MIN", "60"))
ENABLED = os.environ.get("HSR_STALENESS_WATCHDOG_ENABLED", "1") == "1"  # armed by default — detect-only, cheap page
DRY = os.environ.get("HSR_STALENESS_WATCHDOG_DRY") == "1"


# ── PURE classification (unit-tested; no DB) ─────────────────────────────────
def classify_tick_staleness(last_tick_age_s: "float | None", stale_s: float = STALE_S) -> "tuple[str, str]":
    """PURE. `last_tick_age_s` is seconds since `hub_self_recovery_settings.last_tick_at`,
    or None if the column/row is NULL (never ticked). Returns (verdict, reason), verdict
    in {'ok', 'stale'}. NULL is always stale — never a free pass for "hasn't started yet"
    (see module docstring): a silently-dead timer and a never-started one look identical
    from here, and both mean the pre-arming precondition is unmet."""
    if last_tick_age_s is None:
        return "stale", "last_tick_at is NULL — hub-self-recovery timer has never stamped a tick (dead or never started)"
    if last_tick_age_s > stale_s:
        return "stale", (f"last_tick_at is {last_tick_age_s / 60.0:.1f}m old, past the "
                          f"{stale_s / 60.0:.1f}m staleness threshold — the ~2min tick has stopped reaching the stamp")
    return "ok", f"last_tick_at is {last_tick_age_s / 60.0:.1f}m old — within the {stale_s / 60.0:.1f}m threshold"


def _dsn() -> "str | None":
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def _read_last_tick_age(cur) -> "tuple[float | None, str | None]":
    """Returns (age_s, last_tick_reason) for the singleton settings row. age_s is None if
    the row/table is missing OR last_tick_at is NULL — both read the same to the caller
    (classify_tick_staleness treats None as stale, never distinguishing "pre-migration"
    from "dead": this watchdog existing IS the migration having landed)."""
    try:
        cur.execute("SELECT extract(epoch from (now()-last_tick_at)), last_tick_reason "
                    "FROM hub_self_recovery_settings LIMIT 1")
        row = cur.fetchone()
    except Exception:  # noqa: BLE001 — table missing (pre-migration) reads as "never ticked"
        cur.connection.rollback()
        return None, None
    if row is None:
        return None, None
    age, reason = row
    return (float(age) if age is not None else None), reason


def _already_paged(cur, cooldown_min: int) -> bool:
    cur.execute("SELECT 1 FROM agent_messages WHERE from_agent=%s AND subject LIKE 'HSR TICK-STALE:%%' "
                "AND created_at > now() - make_interval(mins => %s) LIMIT 1", (SRE_FROM, cooldown_min))
    return cur.fetchone() is not None


def _page(cur, last_tick_age_s: "float | None", reason: str, last_tick_reason: "str | None") -> int:
    """NON-DESTRUCTIVE P2 page to RECIPIENTS. Returns count sent."""
    age_desc = "NEVER" if last_tick_age_s is None else f"{last_tick_age_s / 60.0:.1f}m ago"
    subject = f"HSR TICK-STALE: hub_self_recovery_settings.last_tick_at is stale ({age_desc})"
    try:
        from nervous_system.alert_format import format_alert
        body = format_alert(
            icon="\U0001f6d1",
            title="Hub self-recovery timer's liveness stamp has gone stale",
            what=(f"hub_self_recovery_settings.last_tick_at last advanced {age_desc} "
                  f"(threshold {STALE_S / 60.0:.1f}m). {reason}"
                  + (f" Its last recorded reason was: {last_tick_reason!r}." if last_tick_reason else "")),
            why=("The gzb systemd timer (~2min cadence) stamps this on EVERY real tick, by design (hc#175's "
                 "own 'audit #43169 finding 6') — a stale stamp means the timer stopped reaching even that far "
                 "(e.g. a DB-unreachable connect fails closed before the stamp), which previously left ZERO "
                 "trace. This watchdog is the CAI-RESP-1439 pre-arming precondition: an armed-but-silently-dead "
                 "recovery process is worse than an observe-only one going quiet, so this must be live+verified "
                 "before HSR_MODE=act is ever considered (hc#177)."),
            do=("Check gzb's hub-self-recovery systemd timer/unit directly (systemctl status/journalctl) for why "
                 "it stopped ticking — DB connectivity, a crash, the unit disabled, etc. HSR itself is correctly "
                 "staying observe-only/unarmed regardless — this page is diagnostic, not an active-danger alarm."),
            detail=f"last_tick_age={age_desc} threshold={STALE_S:.0f}s; DETECT-ONLY, no action taken.",
            ref="HSR-TICK-STALENESS-WATCHDOG (hc#175)",
        )
    except Exception:  # noqa: BLE001 — alert_format optional; fall back to a plain body
        body = (f"\U0001f6d1 HSR tick-stale: last_tick_at {age_desc} (> {STALE_S / 60.0:.1f}m threshold). "
                f"{reason}. Check gzb's hub-self-recovery systemd timer. DETECT-ONLY.")
    sent = 0
    for to_agent in RECIPIENTS:
        if DRY:
            print(f"[hsr-staleness DRY] would page {to_agent} :: {subject}")
        else:
            cur.execute(
                "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,requires_response,priority) "
                "VALUES (%s,%s,'update',%s,%s,false,'P2')", (SRE_FROM, to_agent, subject, body))
        sent += 1
    return sent


def run_once() -> int:
    """One detect pass. Prints the verdict; pages (deduped) on a stale tick. Returns 0
    (detect-only — never a non-zero exit for a genuine stale finding; this never gates
    a build/CI step, only informs humans), 2 on a genuine can't-even-connect failure
    ('could not measure', distinct from a measured stale reading)."""
    import psycopg
    dsn = _dsn()
    if not dsn:
        print("[hsr-staleness] no DATABASE_URL — could not measure (not a finding)", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(dsn, connect_timeout=15) as c, c.cursor() as cur:
            age_s, last_tick_reason = _read_last_tick_age(cur)
            verdict, reason = classify_tick_staleness(age_s)
            print(f"[hsr-staleness] {verdict.upper()}: {reason}")
            if verdict == "stale" and ENABLED:
                if _already_paged(cur, PAGE_COOLDOWN_MIN):
                    print(f"[hsr-staleness] stale but already paged within {PAGE_COOLDOWN_MIN}m — cooled, no re-page")
                else:
                    sent = _page(cur, age_s, reason, last_tick_reason)
                    if not DRY:
                        c.commit()
                    print(f"[hsr-staleness{' DRY' if DRY else ''}] paged {sent} recipient(s) on tick-staleness")
            elif verdict == "stale":
                print("[hsr-staleness] stale but HSR_STALENESS_WATCHDOG_ENABLED!=1 — INERT (detect-only, no page)")
    except Exception as e:  # noqa: BLE001 — a genuine connect/query failure: report honestly, never silently "ok"
        print(f"[hsr-staleness] COULD NOT MEASURE (DB error, not a finding): {e!r}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(run_once())
