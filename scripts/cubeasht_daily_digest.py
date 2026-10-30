#!/usr/bin/env python3
"""cubeasht_daily_digest.py — a once-daily roll-up of all 3 fleet machines'
health for the prior complete UTC day (originally cubeasht-only, operator
ask#2207, Nazim bus #64620/#64628; extended to mini + gzb per op#35975,
orch-console #83733 -- Musa wants the new-Mac-mini sizing decision to rest
on a week of real data across all 3 boxes, not cubeasht alone). The 5-min
monitors (cubeasht_monitor.py, mini_monitor.py, gzb_monitor.py) PAGE only on
threshold breaches; this digest is the opposite end — a calm daily "here's
how the fleet's machines were yesterday" so nothing quietly drifts (temps
creeping, memory pressure, CI contention) between pages. The filename stays
as-is (cubeasht_daily_digest.py) to avoid unnecessary launchd label/plist
churn on an already-scheduled job -- its scope has simply grown.

Design mirrors scripts/asks_daily_digest.py (the fleet's proven daily-digest
pattern), deliberately SIMPLE — NOT cubeasht_monitor's dedup/backoff/paging
machinery:

  * Reuses each monitor's fetch_samples()/summarize() — the digest and the
    monitors compute the exact same numbers from the exact same host_metrics
    rows; no re-derivation (memory ddl-detect-vendored-shared-module).
    build_summary_all_hosts() fetches all 3 independently: one host's failure
    (e.g. gzb ssh down) does not block the other two's section, it just shows
    "could not measure" inline for that host -- unless ALL 3 fail (a real
    outage, not a one-host blip), which stays loud (raises, nothing sent).
  * Summarizes the PRIOR complete UTC day by default (so a morning fire has a
    full day of data), overridable with --date YYYY-MM-DD.
  * Sends AT MOST ONE digest per summarized date — a persisted date-stamp in
    $ORCH/logs/cubeasht_daily_digest_state.json is the only dedup, so a launchd
    misfire or a manual re-run can never double-send by construction.
  * Posts to the BUS (orch-console) via scripts/bus_send.send() — cc-fleet-health
    is on the Mini and does NOT hold the hub's Telegram pen (CLAUDE.md §5 /
    ORCH-TOPOLOGY-001); the bus is its attributable, durable escalation path.
    Nazim surfaces it to the operator. NOT nazim_send.sh (that is the operator
    TG voice, which fail-closes for a non-orch_lease body anyway).
  * Fail-LOUD: any DB/send error exits non-zero so launchd records it — a
    silently-failing digest is worse than none (CLAUDE.md §2.1). An unreachable
    desktop is DATA (a "0 samples" digest), not an error.

Scheduled via launchd/dev.wingmen.cubeasht-daily-digest.plist at 09:00 Abu
Dhabi (the operator's morning, same slot as asks-daily-digest) = 13:00 SGT =
05:00 UTC; see that plist's comment + tests/test_launchd_uae_calendar_hours.py
for the host-TZ-hour conversion. Landing this file does NOT wire it into
launchd; a plist must still be separately `launchctl load`ed at go-live.

Usage:
  cubeasht_daily_digest.py [--date YYYY-MM-DD] [--tz TZ] [--dry-run] [--force]
    --date     the UTC day to summarize (default: yesterday UTC)
    --tz       timezone the day is cut in (default UTC, matching the monitor)
    --dry-run  print the digest, send nothing, stamp nothing (safe any time)
    --force    send even if already sent for that date (manual re-run / testing)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
ORCH = SCRIPTS_DIR.parent
# scripts/ on sys.path so `import cubeasht_monitor` / `bus_send` work whether
# run as `python3 scripts/cubeasht_daily_digest.py` or imported by pytest.
sys.path.insert(0, str(SCRIPTS_DIR))

STATE_FILE = ORCH / "logs" / "cubeasht_daily_digest_state.json"
TO_AGENT = "orch-console"
FROM_AGENT = "cc-fleet-health"
PRIORITY = "P3"  # a calm informational roll-up, never a page

try:
    from dotenv import load_dotenv
    load_dotenv(ORCH / ".env")
except Exception:
    pass  # env may already be sourced by the launchd wrapper / caller


class DigestError(Exception):
    """Loud, caught in main() -> non-zero exit."""


def default_date(now: "datetime | None" = None) -> date:
    """Yesterday (UTC) — the last day guaranteed to be complete at any fire
    time after 00:00 UTC (the 09:00-UAE / 05:00-UTC slot is well clear)."""
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=1)).date()


def _fmt_stat(s: "dict | None", unit: str = "") -> str:
    if not s:
        return "n/a"
    return f"min {s['min']}{unit} / avg {s['avg']}{unit} / max {s['max']}{unit} (n={s['n']})"


def render_digest(summary: dict) -> tuple[str, str]:
    """Build (subject, body) for the bus row from a cubeasht_monitor.summarize()
    dict. ELI5 TL;DR first (fleet doctrine), then the per-metric lines, then the
    raw JSON so the exact numbers are preserved and machine-readable."""
    d = summary["date"]
    tz = summary.get("tz", "UTC")
    n = summary.get("samples", 0)
    ok = summary.get("reachable_samples", 0)
    unreach = summary.get("unreachable_samples", 0)
    gpu = summary.get("gpu_temp_c")
    nvme = summary.get("nvme_temp_c")
    ram = summary.get("ram_used_pct")
    cpu = (summary.get("cpu_load_pct") or {})
    cpu_overall = cpu.get("overall")
    cpu_busy = cpu.get("ci_busy")
    cpu_idle = cpu.get("ci_idle")
    cpu_temp = summary.get("cpu_temp_c")
    ci_busy_n = summary.get("ci_busy_samples", 0)

    subject = f"cubeasht daily health digest — {d} ({tz})"

    lines: list[str] = [f"🖥️ cubeasht daily health digest — {d} ({tz})"]

    if n == 0:
        lines.append(
            "TL;DR: ⚠️ ZERO samples logged for this day — the 5-min monitor "
            "recorded nothing (desktop down all day, or the monitor wasn't "
            "running). Worth a look; a real outage also pages via the monitor's "
            "own alerts, so this is the quiet backstop."
        )
        lines.append("")
        lines.append("(raw summary JSON)")
        lines.append(json.dumps(summary, indent=2))
        return subject, "\n".join(lines)

    tl = [f"{ok}/{n} samples reached the box"]
    if unreach:
        tl.append(f"{unreach} unreachable")
    if gpu:
        tl.append(f"peak GPU {gpu['max']}°C")
    if nvme:
        tl.append(f"peak NVMe {nvme['max']}°C")
    if ram:
        tl.append(f"RAM peaked {ram['max']}% (avg {ram['avg']}%)")
    if cpu_overall:
        tl.append(f"CPU peaked {cpu_overall['max']}%")
    tl.append(f"CI busy in {ci_busy_n} sample-windows")
    lines.append("TL;DR: " + "; ".join(tl) + ".")
    lines.append("")

    lines.append(f"GPU temp:  {_fmt_stat(gpu, '°C')}")
    lines.append(f"NVMe temp: {_fmt_stat(nvme, '°C')}")
    if cpu_temp:
        lines.append(f"CPU temp:  {_fmt_stat(cpu_temp, '°C')}")
    lines.append(f"CPU load:  overall {_fmt_stat(cpu_overall, '%')}")
    lines.append(f"           during CI {_fmt_stat(cpu_busy, '%')}")
    lines.append(f"           idle      {_fmt_stat(cpu_idle, '%')}")
    lines.append(f"RAM used:  {_fmt_stat(ram, '%')}")
    lines.append("")
    lines.append("(raw summary JSON)")
    lines.append(json.dumps(summary, indent=2))
    return subject, "\n".join(lines)


def _fmt_stat_line(label: str, s: "dict | None", unit: str = "") -> str:
    return f"{label}: {_fmt_stat(s, unit)}"


def render_generic_section(summary: dict) -> str:
    """A short, host-agnostic section for mini_monitor/gzb_monitor summaries
    (op#35975, orch-console #83733) -- deliberately plainer than cubeasht's
    temp-heavy render_digest(), since neither host has GPU/NVMe temps and
    cubeasht's own render stays untouched (existing tests keep passing)."""
    host, n = summary["host"], summary.get("samples", 0)
    if n == 0:
        return (f"### {host}\nZERO samples logged for this day -- the 5-min monitor recorded "
                f"nothing (box down, or the monitor wasn't running).")
    lines = [f"### {host} ({summary.get('reachable_samples', n)}/{n} samples reached the box)"]
    if summary.get("unreachable_samples"):
        lines.append(f"- unreachable samples: {summary['unreachable_samples']}")
    lines.append("- " + _fmt_stat_line("real memory used", summary.get("mem_real_used_pct"), "%"))
    lines.append("- " + _fmt_stat_line("disk free", summary.get("disk_free_pct"), "%"))
    lines.append("- " + _fmt_stat_line("CPU load", summary.get("cpu_load_pct"), "%"))
    if summary.get("cpu_temp_c"):
        lines.append("- " + _fmt_stat_line("CPU temp", summary.get("cpu_temp_c"), "°C"))
    if summary.get("swap_used_pct"):
        lines.append("- " + _fmt_stat_line("swap used", summary.get("swap_used_pct"), "%"))
    lines.append("- " + _fmt_stat_line("claude sessions", summary.get("claude_sessions")))
    return "\n".join(lines)


def build_summary_mini(day: date, tz: str) -> dict:
    import mini_monitor as mm  # local import: needs env (DATABASE_URL) ready

    lo, hi = mm.cm.day_bounds(day, tz)
    conn = mm.cm.connect()
    try:
        samples = mm.cm.fetch_samples(conn, mm.HOST, lo, hi)
    finally:
        conn.close()
    return mm.summarize(samples, day, tz)


def build_summary_gzb(day: date, tz: str) -> dict:
    import gzb_monitor as gzm  # local import: needs env (DATABASE_URL) ready

    lo, hi = gzm.cm.day_bounds(day, tz)
    conn = gzm.cm.connect()
    try:
        samples = gzm.cm.fetch_samples(conn, gzm.HOST, lo, hi)
    finally:
        conn.close()
    return gzm.summarize(samples, day, tz)


def build_summary_all_hosts(day: date, tz: str) -> dict:
    """{"cubeasht": <build_summary() dict>, "mini": {...}, "gzb": {...}} --
    one host's fetch failing does not stop the others; its slot holds a
    "could not measure" string instead (fail loud per-host, not all-or-nothing
    for the whole digest)."""
    out: dict = {}
    for host, fn in (("cubeasht", lambda: build_summary(day, tz)),
                      ("mini", lambda: build_summary_mini(day, tz)),
                      ("gzb", lambda: build_summary_gzb(day, tz))):
        try:
            out[host] = fn()
        except (Exception, SystemExit) as e:  # noqa: BLE001
            # bus_send.dburl() deliberately raises SystemExit (not Exception) as
            # a CLI-style sentinel when no DATABASE_URL resolves -- a known
            # pattern in this codebase, not a real process-exit request here.
            out[host] = f"could not measure -- {type(e).__name__}: {e}"
    if all(isinstance(v, str) for v in out.values()):
        # every host failed -- a real outage (e.g. the DB itself is down), not
        # a one-host blip. Stay loud: raise rather than silently send (and
        # dedup-stamp!) an all-"could not measure" digest as if it were a
        # normal day.
        raise DigestError(f"ALL 3 hosts failed to summarize: {out}")
    return out


def render_digest_all_hosts(summaries: dict, day: date, tz: str) -> tuple[str, str]:
    """(subject, body) for the 3-host digest -- cubeasht's section reuses the
    existing render_digest() body verbatim (byte-identical to its own
    standalone digest); mini/gzb get render_generic_section()."""
    date_str = day.isoformat()
    subject = f"fleet 3-host daily health digest — {date_str} ({tz})"
    lines = [f"🖥️ fleet 3-host daily health digest — {date_str} ({tz})", ""]

    tl: list[str] = []
    for host in ("cubeasht", "mini", "gzb"):
        s = summaries.get(host)
        if isinstance(s, str):
            tl.append(f"{host}: {s}")
        else:
            tl.append(f"{host}: {s.get('reachable_samples', 0)}/{s.get('samples', 0)} samples")
    lines.append("TL;DR: " + "; ".join(tl) + ".")
    lines.append("")

    for host in ("cubeasht", "mini", "gzb"):
        s = summaries.get(host)
        lines.append(f"## {host}")
        if isinstance(s, str):
            lines.append(s)
        elif host == "cubeasht":
            _, cubeasht_body = render_digest(s)
            lines.append(cubeasht_body)
        else:
            lines.append(render_generic_section(s))
        lines.append("")

    return subject, "\n".join(lines)


def build_summary(day: date, tz: str) -> dict:
    """Fetch the day's host_metrics and summarize — reusing cubeasht_monitor so
    the digest's numbers are byte-identical to the monitor's own --summary."""
    import cubeasht_monitor as cm  # local import: needs env (DATABASE_URL) ready

    lo, hi = cm.day_bounds(day, tz)
    conn = cm.connect()
    try:
        samples = cm.fetch_samples(conn, cm.HOST, lo, hi)
    finally:
        conn.close()
    return cm.summarize(samples, day, tz)


def _already_sent(date_str: str) -> bool:
    try:
        state = json.loads(STATE_FILE.read_text())
    except Exception:
        return False
    return state.get("last_sent_date") == date_str


def _mark_sent(date_str: str) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({"last_sent_date": date_str}))
    except Exception as e:  # loud: a failed stamp means tomorrow double-sends
        raise DigestError(f"could not persist dedup stamp to {STATE_FILE}: {e}")


def send_digest(subject: str, body: str) -> int:
    from bus_send import send

    row_id, _thread = send(
        from_agent=FROM_AGENT, to=TO_AGENT, mtype="update",
        subject=subject, body=body, priority=PRIORITY,
    )
    return row_id


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="UTC day to summarize (YYYY-MM-DD); default yesterday UTC")
    ap.add_argument("--tz", default="UTC", help="timezone the day is cut in (default UTC)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the digest; send nothing, stamp nothing")
    ap.add_argument("--force", action="store_true",
                    help="send even if already sent for that date")
    args = ap.parse_args(argv)

    try:
        day = date.fromisoformat(args.date) if args.date else default_date()
    except ValueError as e:
        print(f"cubeasht_daily_digest: FAIL LOUD — bad --date: {e}", file=sys.stderr)
        return 2

    date_str = day.isoformat()

    if not args.force and not args.dry_run and _already_sent(date_str):
        print(f"cubeasht_daily_digest: already sent for {date_str} — skipping "
              "(use --force to resend)")
        return 0

    try:
        summaries = build_summary_all_hosts(day, args.tz)
        subject, body = render_digest_all_hosts(summaries, day, args.tz)
    except Exception as e:
        print(f"cubeasht_daily_digest: FAIL LOUD — {type(e).__name__}: {e}",
              file=sys.stderr)
        return 3

    if args.dry_run:
        print(f"[DRY-RUN] would post to {TO_AGENT} ({PRIORITY}):")
        print(f"subject: {subject}")
        print(body)
        print("[DRY-RUN: nothing sent, nothing stamped]")
        return 0

    try:
        row_id = send_digest(subject, body)
    except Exception as e:
        print(f"cubeasht_daily_digest: FAIL LOUD — send failed: {type(e).__name__}: {e}",
              file=sys.stderr)
        return 3

    _mark_sent(date_str)
    now_utc = datetime.now(timezone.utc)
    print(f"cubeasht_daily_digest: SENT bus #{row_id} for {date_str} "
          f"(fired {now_utc:%Y-%m-%d %H:%M UTC})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
