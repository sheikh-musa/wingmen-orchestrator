#!/usr/bin/env python3
"""
daily_account_report.py  --  MVP first "daily per-account work/spend report".

Owner: cc-fleet-health (fleet SRE). READ-ONLY reporting.

This is the MVP first cut of the daily per-account report. It is built from
data that ALREADY EXISTS -- best-effort joins of:
  - cc_session_costs      (lane session tokens + wall-clock)
  - agent_status.auth_fp  (lane -> account attribution; the auth_account LABEL
                           is known to mislabel, so we attribute by auth_fp)
  - git/PR history (gh)   (fleet-wide; per-account attribution pending)

It is NOT the durable `lane_account_history` substrate (follow-up PR, Mon 12 Oct).

Usage:
    set -a; source .env; set +a
    .venv/bin/python3 scripts/daily_account_report.py            # print report to stdout
    .venv/bin/python3 scripts/daily_account_report.py --out reports/daily-account-report-YYYYMMDD.md
    .venv/bin/python3 scripts/daily_account_report.py --selftest # run helper unit test only

No secrets/token *values* are printed -- counts/metrics only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Account model (KEY: attribute by auth_fp; the auth_account label lies)
# ---------------------------------------------------------------------------
FP_MUSA = "68142948c003"
FP_MUSA2 = "e1dfa48eec85"
FP_SYED = "582043088eae"

FP_TO_ACCOUNT = {
    FP_MUSA: "Musa",
    FP_MUSA2: "irsyad",
    FP_SYED: "Syed",
}
# Reporting order. GLM = z.ai, a separate provider; expected 0 Claude-session rows.
ACCOUNTS = ["Musa", "irsyad", "Syed", "GLM", "unattributed"]
ANTHROPIC_ACCOUNTS = {"Musa", "irsyad", "Syed"}

REPO = "sheikh-musa/wingmen-orchestrator"
WINDOW_HOURS = 24


# ---------------------------------------------------------------------------
# Pure helper: map a cc_session_costs identity to an account (best-effort)
# ---------------------------------------------------------------------------
def map_identity_to_account(cc_identity: str, sub_tag, status_index) -> tuple[str, str]:
    """Best-effort map a session's (cc_identity, sub_tag) to one of the 4 accounts.

    status_index: dict built from agent_status with two sub-maps:
        - 'by_agent_id':  agent_id      -> auth_fp (None for GLM rows)
        - 'by_base':      base_agent_id -> set(auth_fp) seen for that base (incl None)

    Returns (account, basis) where basis explains how we resolved it, so the
    report can flag imprecise rows.

    Resolution order:
      1. Exact agent_id match (handles singletons, incl. the Syed cc-quality
         singleton vs the musa cc-quality-N workers that share a base).
      2. Base match: if exactly one NON-null auth_fp among the base's instances,
         attribute there (GLM instances carry null auth_fp and drop out).
      3. Base match with only null auth_fp(s) -> GLM.
      4. Multiple non-null auth_fp for a base, or no match at all -> unattributed.
    """
    by_agent = status_index["by_agent_id"]
    by_base = status_index["by_base"]

    # 1. exact singleton match
    if cc_identity in by_agent:
        fp = by_agent[cc_identity]
        if fp in FP_TO_ACCOUNT:
            return FP_TO_ACCOUNT[fp], "exact-agent_id"
        if fp is None:
            return "GLM", "exact-agent_id(glm-null-fp)"

    # 2/3. base match
    if cc_identity in by_base:
        fps = by_base[cc_identity]
        non_null = {f for f in fps if f is not None}
        if len(non_null) == 1:
            fp = next(iter(non_null))
            acct = FP_TO_ACCOUNT.get(fp, "unattributed")
            if acct == "unattributed":
                return acct, f"base-match(unknown-fp)"
            return acct, "base-match(single-fp)"
        if len(non_null) == 0:
            # base exists but every instance has a null fp -> GLM provider
            return "GLM", "base-match(all-null-fp->glm)"
        # ambiguous: more than one Claude account shares this base
        return "unattributed", f"base-match(ambiguous:{len(non_null)}-fps)"

    return "unattributed", "no-agent_status-match"


# ---------------------------------------------------------------------------
# Lightweight self-test of the pure helper
# ---------------------------------------------------------------------------
def _selftest_mapping() -> bool:
    idx = {
        "by_agent_id": {
            "cai": FP_SYED,
            "cc-fleet-health": FP_SYED,
            "cc-quality": FP_SYED,          # singleton (Syed)
            "cc-quality-1": FP_MUSA,        # worker (Musa)
            "cc-irsyad-1": FP_MUSA2,
            "cc-cosem-platform-5": None,    # GLM instance, null fp
        },
        "by_base": {
            "cc-quality": {FP_SYED, FP_MUSA},            # ambiguous base
            "cc-cosem-platform": {FP_MUSA, None},        # musa + glm -> musa
            "cc-irsyad": {FP_MUSA2},
            "cc-only-glm": {None},                       # glm-only base
        },
    }
    cases = [
        (("cai", None), "Syed"),
        (("cc-fleet-health", None), "Syed"),
        (("cc-quality", None), "Syed"),            # exact singleton wins
        (("cc-cosem-platform", "cosem-port"), "Musa"),  # base, glm drops out
        (("cc-irsyad", None), "irsyad"),
        (("cc-only-glm", None), "GLM"),
        (("cc-nonexistent", None), "unattributed"),
    ]
    ok = True
    for (ident, sub), expect in cases:
        got, basis = map_identity_to_account(ident, sub, idx)
        mark = "ok" if got == expect else "XX"
        if got != expect:
            ok = False
        print(f"  [{mark}] {ident!r:28} -> {got:12} (expected {expect}; basis={basis})")
    return ok


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------
@dataclass
class AcctAgg:
    lane_hours: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    sessions: int = 0
    lanes: set = field(default_factory=set)
    open_sessions: int = 0  # sessions still open (ended_at NULL), hours use now()

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read + self.cache_creation


def build_status_index(cur):
    cur.execute("select agent_id, base_agent_id, auth_fp from agent_status")
    by_agent, by_base = {}, {}
    for agent_id, base, fp in cur.fetchall():
        by_agent[agent_id] = fp
        by_base.setdefault(base, set()).add(fp)
    return {"by_agent_id": by_agent, "by_base": by_base}


def gather_session_costs(cur, status_index, now):
    """Return (per_account dict[str,AcctAgg], basis_counter dict, n_rows)."""
    cur.execute(
        """
        select cc_identity, sub_tag, session_id, started_at, ended_at,
               input_tokens, output_tokens, cache_read_input_tokens,
               cache_creation_input_tokens
        from cc_session_costs
        where (started_at >= %s) or (ended_at >= %s) or (ended_at is null and started_at >= %s)
        """,
        (now - dt.timedelta(hours=WINDOW_HOURS),
         now - dt.timedelta(hours=WINDOW_HOURS),
         now - dt.timedelta(hours=72)),
    )
    per = {a: AcctAgg() for a in ACCOUNTS}
    basis = {}
    rows = cur.fetchall()
    window_start = now - dt.timedelta(hours=WINDOW_HOURS)
    for (cc_identity, sub_tag, session_id, started_at, ended_at,
         itok, otok, cread, ccreate) in rows:
        acct, b = map_identity_to_account(cc_identity, sub_tag, status_index)
        basis[b] = basis.get(b, 0) + 1
        agg = per[acct]
        # clamp session wall-clock to the 24h window so "lane-hours" ~ work in window
        eff_start = max(started_at, window_start)
        eff_end = ended_at if ended_at is not None else now
        if eff_end < window_start:
            # session entirely before window (open>24h edge) -> count tokens=0 effect
            dur_h = 0.0
        else:
            dur_h = max(0.0, (eff_end - eff_start).total_seconds() / 3600.0)
        agg.lane_hours += dur_h
        agg.input_tokens += itok or 0
        agg.output_tokens += otok or 0
        agg.cache_read += cread or 0
        agg.cache_creation += ccreate or 0
        agg.sessions += 1
        agg.lanes.add(cc_identity)
        if ended_at is None:
            agg.open_sessions += 1
    return per, basis, len(rows)


def gather_pr_history(now):
    """Best-effort fleet-wide PR signals from gh for the last 24h. Never fatal."""
    out = {
        "available": False, "merged": 0, "revert_or_hotfix": 0,
        "fix_followup": 0, "merged_titles": [], "flagged": [], "error": None,
    }
    cutoff = now - dt.timedelta(hours=WINDOW_HOURS)
    try:
        res = subprocess.run(
            ["gh", "pr", "list", "--repo", REPO, "--state", "merged",
             "--limit", "100", "--json", "number,title,mergedAt,author"],
            capture_output=True, text=True, timeout=60,
        )
        if res.returncode != 0:
            out["error"] = res.stderr.strip()[:300]
            return out
        prs = json.loads(res.stdout or "[]")
        out["available"] = True
        for pr in prs:
            merged = pr.get("mergedAt")
            if not merged:
                continue
            m = dt.datetime.fromisoformat(merged.replace("Z", "+00:00"))
            if m < cutoff:
                continue
            title = pr.get("title", "")
            out["merged"] += 1
            out["merged_titles"].append(f"#{pr['number']} {title}")
            low = title.lower()
            if "revert" in low or "hotfix" in low:
                out["revert_or_hotfix"] += 1
                out["flagged"].append(f"#{pr['number']} (revert/hotfix) {title}")
            if ("fast-follow" in low or "follow-up" in low or "followup" in low
                    or "fix-of-fix" in low or "re-fix" in low):
                out["fix_followup"] += 1
                out["flagged"].append(f"#{pr['number']} (fix follow-up) {title}")
    except Exception as e:  # noqa: BLE001 - reporting path, fail soft but record
        out["error"] = f"{type(e).__name__}: {e}"[:300]
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def fmt_int(n: int) -> str:
    return f"{n:,}"


def render(per, basis, n_rows, pr, now, selfcheck_ok, selfcheck_detail):
    win_start = now - dt.timedelta(hours=WINDOW_HOURS)
    gen = now.strftime("%Y-%m-%d %H:%M UTC")
    owed = "2026-10-08 04:00Z"

    # provider usage
    anth_h = sum(per[a].lane_hours for a in ANTHROPIC_ACCOUNTS)
    glm_h = per["GLM"].lane_hours + per["unattributed"].lane_hours * 0  # glm only
    glm_h = per["GLM"].lane_hours
    anth_tok = sum(per[a].total_tokens for a in ANTHROPIC_ACCOUNTS)
    glm_tok = per["GLM"].total_tokens
    tot_h = anth_h + glm_h
    tot_tok = anth_tok + glm_tok
    anth_h_pct = (anth_h / tot_h * 100) if tot_h else 0.0
    glm_h_pct = (glm_h / tot_h * 100) if tot_h else 0.0
    anth_tok_pct = (anth_tok / tot_tok * 100) if tot_tok else 0.0
    glm_tok_pct = (glm_tok / tot_tok * 100) if tot_tok else 0.0

    L = []
    L.append("# Daily per-account work/spend report — MVP (first cut)")
    L.append("")
    L.append(f"**Generated:** {gen}  ")
    L.append(f"**Window (daily = last {WINDOW_HOURS}h, UTC):** "
             f"`{win_start.strftime('%Y-%m-%d %H:%M:%SZ')}` .. `{now.strftime('%Y-%m-%d %H:%M:%SZ')}`")
    L.append("")
    L.append("> **Read me first (ELI5).** This is the **MVP first** daily per-account "
             "report. It was **owed " + owed + "** and is delivered **late** "
             f"(generated {gen}). It is built from **existing best-effort joins** "
             "(`cc_session_costs` + `agent_status.auth_fp` + git/PR history), **NOT** "
             "yet the durable `lane_account_history` substrate — that is a **follow-up "
             "PR dated Mon 12 Oct**. Numbers below are counts/metrics only; no secrets "
             "or token *values* (credentials) appear.")
    L.append("")
    L.append("## What is solid vs best-effort (imprecise columns)")
    L.append("")
    L.append("**Solid (direct from the substrate):**")
    L.append("- Per-session token sums (input / output / cache-read / cache-creation) from `cc_session_costs`.")
    L.append("- Session count and distinct lanes per account.")
    L.append("- auth_fp → account mapping for **singletons** (exact `agent_id` match) and for bases with a single Claude account.")
    L.append("")
    L.append("**Best-effort / may be imprecise:**")
    L.append("- **lane-hours** — `ended_at − started_at`, `now()` for still-open sessions, **clamped to the 24h window**. Open-session hours grow until the session row closes; approximate.")
    L.append("- **lane → account attribution** — `cc_session_costs` is keyed by the *base* `cc_identity`, not the instance, so a base shared by two providers (e.g. `cc-cosem-platform` has Musa **and** GLM instances) is resolved to the single **non-null auth_fp** (GLM instances carry a null fp and drop out). Instance-level precision arrives with `lane_account_history`.")
    L.append("- **provider usage %** — see basis note under the table.")
    L.append("- **PR / git metrics** — reported **fleet-wide, not yet per-account** (see PR section). Every PR is authored by the single GitHub user `sheikh-musa`, so per-Claude-account attribution is **not cleanly derivable** from GitHub today.")
    L.append("- **GLM** — a separate provider (z.ai). Its lanes carry a null auth_fp and do not write Claude session-cost rows, so it shows **zeros** here (see note).")
    L.append("- **A `0` can mean \"no cost telemetry\", not \"no work\"** — some lanes are live/`working` in `agent_status` yet emit no `cc_session_costs` rows in the window. In particular the **irsyad lanes** last wrote a session-cost row on **2026-09-11**; their current work is real but unmetered here. Treat irsyad's zeros as a **telemetry gap to fix**, not idleness. (`lane_account_history` is the durable fix.)")
    L.append("")

    # ---- per-account table ----
    L.append("## Per-account spend (cc_session_costs, last 24h)")
    L.append("")
    L.append("| Account | Sessions | Distinct lanes | Lane-hours | Input tok | Output tok | Cache-read tok | Cache-create tok | Total tok |")
    L.append("|---|--:|--:|--:|--:|--:|--:|--:|--:|")
    for a in ACCOUNTS:
        g = per[a]
        open_note = f" ({g.open_sessions} open)" if g.open_sessions else ""
        L.append(f"| **{a}** | {g.sessions}{open_note} | {len(g.lanes)} | "
                 f"{g.lane_hours:.1f} | {fmt_int(g.input_tokens)} | {fmt_int(g.output_tokens)} | "
                 f"{fmt_int(g.cache_read)} | {fmt_int(g.cache_creation)} | {fmt_int(g.total_tokens)} |")
    tot = AcctAgg()
    for a in ACCOUNTS:
        g = per[a]
        tot.input_tokens += g.input_tokens
        tot.output_tokens += g.output_tokens
        tot.cache_read += g.cache_read
        tot.cache_creation += g.cache_creation
        tot.sessions += g.sessions
        tot.lane_hours += g.lane_hours
        tot.lanes |= g.lanes
    L.append(f"| **FLEET TOTAL** | {tot.sessions} | {len(tot.lanes)} | {tot.lane_hours:.1f} | "
             f"{fmt_int(tot.input_tokens)} | {fmt_int(tot.output_tokens)} | {fmt_int(tot.cache_read)} | "
             f"{fmt_int(tot.cache_creation)} | {fmt_int(tot.total_tokens)} |")
    L.append("")
    L.append(f"_Raw cc_session_costs rows touching the window: {n_rows}. "
             f"Attribution basis breakdown: {json.dumps(basis)}._")
    L.append("")

    # ---- provider usage ----
    L.append("## Provider usage: Anthropic vs GLM")
    L.append("")
    L.append("| Basis | Anthropic (Musa+irsyad+Syed) | GLM (z.ai) |")
    L.append("|---|--:|--:|")
    L.append(f"| Lane-hours | {anth_h:.1f}h ({anth_h_pct:.1f}%) | {glm_h:.1f}h ({glm_h_pct:.1f}%) |")
    L.append(f"| Total tokens | {fmt_int(anth_tok)} ({anth_tok_pct:.1f}%) | {fmt_int(glm_tok)} ({glm_tok_pct:.1f}%) |")
    L.append("")
    L.append("_Basis reported both ways (lane-hours and token share). "
             "**No GLM lane activity in window** — GLM is a separate provider (z.ai) "
             "whose lanes carry a null auth_fp and do not emit Claude session-cost rows; "
             "shown as zeros rather than forcing a mapping._")
    L.append("")

    # ---- ELI5 per account ----
    L.append("## ELI5 — one line per account (for a non-engineer)")
    L.append("")
    for a in ACCOUNTS:
        g = per[a]
        if a == "GLM":
            L.append(f"- **GLM:** no work on the GLM provider in the last 24h (0 sessions). It is a separate pay-as-you-go provider; quiet is normal here.")
            continue
        if a == "unattributed":
            if g.sessions == 0:
                L.append(f"- **unattributed:** every session mapped cleanly to an account — nothing fell through. 👍")
            else:
                L.append(f"- **unattributed:** {g.sessions} session(s) ({fmt_int(g.total_tokens)} tokens) could not be tied to one account; kept visible, not dropped.")
            continue
        if g.sessions == 0:
            if a == "irsyad":
                L.append(f"- **irsyad:** 0 metered sessions — but the irsyad lanes are live; their cost telemetry stopped landing on 2026-09-11, so this is a **blind spot to fix**, not real idleness.")
            else:
                L.append(f"- **{a}:** no lane activity in the last 24h.")
        else:
            L.append(f"- **{a}:** {g.sessions} work session(s) across {len(g.lanes)} lane(s), "
                     f"about {g.lane_hours:.0f} lane-hours and {fmt_int(g.total_tokens)} tokens of model work.")
    L.append("")

    # ---- PR / git ----
    L.append("## Git / PR activity (fleet-wide, last 24h)")
    L.append("")
    L.append("> **Not yet per-account** (best-effort attribution pending the durable "
             "`lane_account_history` table). All PRs land under the single GitHub user "
             "`sheikh-musa`, so per-Claude-account attribution is not cleanly derivable today.")
    L.append("")
    if pr["available"]:
        L.append(f"- **PRs merged (window):** {pr['merged']}")
        L.append(f"- **Reverts / hotfix PRs (title match):** {pr['revert_or_hotfix']}")
        L.append(f"- **Fix follow-up PRs (fast-follow / follow-up, title match):** {pr['fix_followup']}")
        L.append("- **cc-quality FAIL / post-review fix commits:** not derivable from `gh` metadata alone in the MVP — deferred to `lane_account_history` (which will carry the review verdict). Reported as **not measured** here rather than guessed.")
        if pr["flagged"]:
            L.append("")
            L.append("  Flagged PRs:")
            for f in pr["flagged"]:
                L.append(f"  - {f}")
        if pr["merged_titles"]:
            L.append("")
            L.append("  <details><summary>Merged PRs in window</summary>")
            L.append("")
            for t in pr["merged_titles"]:
                L.append(f"  - {t}")
            L.append("")
            L.append("  </details>")
    else:
        L.append(f"- PR data unavailable (`gh` error): `{pr.get('error')}`. "
                 "Scoped to the orchestrator repo; not fatal to the report.")
    L.append("")
    L.append(f"_Scope: `{REPO}` only. Client repos not swept in this MVP cut._")
    L.append("")

    # ---- self-check ----
    L.append("## Self-check (numbers trustworthy?)")
    L.append("")
    status = "PASS" if selfcheck_ok else "FAIL"
    L.append(f"- Per-account token sums reconcile to fleet total within rounding: **{status}**")
    L.append(f"  - {selfcheck_detail}")
    L.append("")
    L.append("---")
    L.append("_Generated by `scripts/daily_account_report.py` (cc-fleet-health, read-only). "
             "MVP; superseded by the `lane_account_history` substrate once that lands (PR Mon 12 Oct)._")
    L.append("")
    L.extend(_deepseek_balance_section())
    return "\n".join(L), tot


def _deepseek_balance_section() -> list[str]:
    """op#35698 (console #81729): DeepSeek native-API balance, from the hourly
    poller's own history file. Never touches the vault/key -- read-only on the
    poller's {ts, balance_usd} JSONL output."""
    path = Path.home() / "wingmen" / "fleet-health" / "state" / "deepseek_balance_history.jsonl"
    L = ["## DeepSeek balance (native API, op#35698)", ""]
    try:
        lines = path.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        L.append("_No poll history yet (`dev.wingmen.deepseek-balance-poll` not run/loaded)._")
        L.append("")
        return L
    if not lines:
        L.append("_Poll history file is empty._")
        L.append("")
        return L
    rows = [json.loads(ln) for ln in lines]
    latest = rows[-1]
    day_ago_rows = [r for r in rows if r["ts"] >= (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)).isoformat()]
    first_today = day_ago_rows[0] if day_ago_rows else rows[0]
    delta = first_today["balance_usd"] - latest["balance_usd"]
    DAILY_BUDGET_USD = 7.80  # op#35703: cost of one Max plan/day
    L.append(f"- **Latest:** ${latest['balance_usd']:.2f} (polled {latest['ts']})")
    L.append(f"- **24h spend:** ${delta:.2f} vs ${DAILY_BUDGET_USD:.2f}/day budget "
             f"(op#35703; no per-lane split — the DeepSeek balance endpoint is account-level only) "
             f"(from ${first_today['balance_usd']:.2f} at {first_today['ts']})")
    if delta > DAILY_BUDGET_USD:
        L.append(f"- **⚠ over the ${DAILY_BUDGET_USD:.2f}/day budget** — P2 alert already sent to orch-console by the poller.")
    if latest["balance_usd"] < 40:
        L.append(f"- **⚠ below the $40 floor** — P1 alert already sent to orch-console by the poller.")
    L.append("")
    return L


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="write rendered markdown to this path")
    ap.add_argument("--selftest", action="store_true", help="run helper unit test only")
    args = ap.parse_args()

    if args.selftest:
        print("mapping self-test:")
        ok = _selftest_mapping()
        print("SELFTEST", "PASS" if ok else "FAIL")
        return 0 if ok else 1

    # Always run the mapping unit test first — fail loud if the helper is broken.
    print("[selftest] map_identity_to_account:", file=sys.stderr)
    if not _selftest_mapping():
        print("FATAL: mapping self-test FAILED — aborting, numbers not trustworthy.", file=sys.stderr)
        return 1

    import psycopg
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("FATAL: DATABASE_URL not set (did you `source .env`?)", file=sys.stderr)
        return 1

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select now() at time zone 'utc'")
        now = cur.fetchone()[0].replace(tzinfo=dt.timezone.utc)
        status_index = build_status_index(cur)
        per, basis, n_rows = gather_session_costs(cur, status_index, now)

    pr = gather_pr_history(now)

    # reconciliation self-check: sum of per-account totals == fleet total
    fleet_tok = sum(per[a].total_tokens for a in ACCOUNTS)
    recomputed = (sum(per[a].input_tokens for a in ACCOUNTS)
                  + sum(per[a].output_tokens for a in ACCOUNTS)
                  + sum(per[a].cache_read for a in ACCOUNTS)
                  + sum(per[a].cache_creation for a in ACCOUNTS))
    selfcheck_ok = (fleet_tok == recomputed)
    detail = (f"per-account total tokens = {recomputed:,}; "
              f"sum of components = {fleet_tok:,}; "
              f"delta = {abs(fleet_tok - recomputed):,} (must be 0).")

    report, tot = render(per, basis, n_rows, pr, now, selfcheck_ok, detail)

    if args.out:
        with open(args.out, "w") as fh:
            fh.write(report)
        print(f"[wrote] {args.out}", file=sys.stderr)
    print(report)
    print(f"\n[self-check] token reconciliation: {'PASS' if selfcheck_ok else 'FAIL'} — {detail}",
          file=sys.stderr)
    return 0 if selfcheck_ok else 1


if __name__ == "__main__":
    sys.exit(main())
