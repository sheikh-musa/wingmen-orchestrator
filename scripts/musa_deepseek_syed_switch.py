#!/usr/bin/env python3
"""musa_deepseek_syed_switch.py — Musa's two standing spend triggers (op#36560/36562,
orch-console #86307/#86319):

  (A) Musa 7d >= 90%: move every WORKER lane currently on Musa's Claude token onto
      DeepSeek (deepseek-flash). Brains stay on Claude. EXCLUDES mamadah (Claude-only,
      op#35514/35515) and any irsyad-* lane (own key, never Musa/DeepSeek).
  (B) Musa 7d >= 95%: move the BRAINS (cc-fleet-health, orch-console, cc-quality) off
      Musa onto SYED via switch_singleton_token.sh, plus mamadah (Claude-only; moves
      WITH the brains, not to DeepSeek). SUPERSEDES glm_overflow_switch.py's old
      op#33932 90% PAGE-only rule for the brains — this script ACTS instead of just
      paging for it once (B)'s threshold is reached.

Membership is RE-DERIVED AT RUNTIME from the live token (agent_status.auth_fp ==
musa-oauth-token's fp), never from a hardcoded lane list (orch-console #86307: "never
from this list").

provider_policy is the pre-existing fail-closed gate (scripts/lib/provider_policy.py),
already wired into switch_lane_token.sh --model-apply for ANY non-Anthropic model incl.
deepseek-* (provider_for_model). This script does its OWN read-only pre-check
(resolve_client_key/resolve_policy) so an uncleared lane is reported SKIPPED up front,
in both --dry-run and --apply, rather than only discovered as a refusal escalation at
flip time. (2026-10-31: 5 of the original 8 target lanes had no provider_policy row —
added per orch-console #86319, citing Musa op#35512 + op#36560. The skip-and-report
path stays for any FUTURE lane that lands here with no row — fail-closed is correct.)

Brain/mamadah "already moved" is ground-truthed against agent_status.auth_fp, never a
`.<node>_default_token` pointer file — verified live 2026-10-31 that the pointer file
can be stale (`.nazim_default_token` already said syed-oauth-token while orch-console's
LIVE token was still Musa's). A successful brain/mamadah switch ALSO updates the
durable pointer file after the fact, so a future crash-restart (e.g. the launchd
keep-alive PATH gap, bus #86258) stays on Syed instead of silently reverting to Musa.

FAIL LOUD: an unreadable Musa reading raises (non-zero exit). --dry-run (the DEFAULT)
changes nothing; --apply acts. ONE-WAY ONLY — this script never switches a lane back;
reverting the deepseek flip is a restorable pointer-rename (`.bak-musadeepseek-*`),
reverting a singleton/mamadah Syed move is a separate switch_singleton_token.sh /
switch_lane_token.sh call back onto Musa (operator's call, never automatic).

cc-quality PR#727 review (bus #86372/#86374) + orch-console #86380 fast-follows, verified
2026-10-31:
  (3) read_musa()'s idle_no_data->0% path is fail-safe, not fail-quiet: traced
      nervous_system.weekly_limit_monitor.probe_pool — it returns idle_no_data ONLY on a
      genuine http==200 response with no fresh utilization header (a truly parked token);
      any transient network/timeout/overload error RAISES after retries, and any non-200
      non-retryable error RAISES immediately. A probe error can never surface as
      idle_no_data, so this script correctly can't read a broken probe as "Musa is at 0%".
      No code change needed; recorded here so this isn't re-litigated from scratch later.
  (4) worker_candidate_sessions()'s host filter (Sheikhs-Mini/NULL only) is NOT just a
      missing feature — confirmed live that 2 real non-protected worker lanes run on
      Musa's token on host gzbai (cc-cosem-convergence-2/cosem-convergence,
      cc-scholar-1/scholar), so they will KEEP burning Musa's quota past 90% even once
      this fires. Investigated widening it via `ssh gzb` (the checkout + script exist
      there at the same relative path) but Musa's and Syed's oauth token files do not
      exist on gzbai at all — there is no known credential path to flip a remote lane
      onto, so a widen here would be a guess, not a fix. Flagged to orch-console
      (#86393) instead of guessed at; the host filter stays Mini-only until someone who
      knows the gzbai credential layout makes that call.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

ORCH_DIR = Path(os.environ.get("ORCH_DIR", str(Path(__file__).resolve().parent.parent)))
sys.path.insert(0, str(ORCH_DIR))

WORKER_DEEPSEEK_PCT = 90.0
BRAIN_SYED_PCT = 95.0
DEEPSEEK_MODEL = "deepseek-flash"
MUSA_TOKEN = Path.home() / ".wingmen" / "keys" / "musa-oauth-token"
SYED_TOKEN = Path.home() / ".wingmen" / "keys" / "syed-oauth-token"
STATE_FILE = Path.home() / "wingmen" / "fleet-health" / "state" / "musa_deepseek_syed_switch.json"
PAGE_COOLDOWN_S = 3600
DEEPSEEK_BALANCE_FILE = Path.home() / "wingmen" / "fleet-health" / "state" / "deepseek_balance_history.jsonl"
# FALLBACK ONLY (pool_usage.resets_at for Musa as of 2026-10-31 11:15Z) — used when the
# live read below fails or is unset. musa_reset_at() reads the real value every run; a
# hardcoded reset date silently expires (orch-console #86380 fast-follow 2) and then the
# balance-insufficient page never fires again (hours_until floors at 0 -> always "sufficient").
MUSA_RESET_AT_FALLBACK = dt.datetime(2026, 11, 4, 8, 0, tzinfo=dt.timezone.utc)


def musa_reset_at(fallback: dt.datetime = MUSA_RESET_AT_FALLBACK) -> dt.datetime:
    """Live pool_usage.resets_at for Musa. A read failure or missing row falls back to
    the constant above with a LOUD warning (never silent) rather than raising — this
    feeds an advisory balance projection, not the real worker/brain switch actions, so a
    transient DB hiccup here must not abort the whole run."""
    try:
        with _db() as c, c.cursor() as cur:
            cur.execute("SELECT resets_at FROM pool_usage WHERE pool = 'Musa'")
            row = cur.fetchone()
        if row and row[0]:
            ts = row[0]
            return ts if ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)
        print(f"  WARNING: pool_usage has no resets_at for Musa; using fallback {fallback:%Y-%m-%d %H:%MZ}")
    except Exception as e:
        print(f"  WARNING: could not read live pool_usage.resets_at for Musa "
              f"({type(e).__name__}: {e}); using fallback {fallback:%Y-%m-%d %H:%MZ}")
    return fallback

# Brain/singleton -> its durable default-token pointer file (the filename does NOT
# match the node name 1:1 — e.g. orch-console's is .nazim_default_token, matching its
# tmux session, not its agent_id). switch_singleton_token.sh's own NODE registry is the
# source of truth for which node names are valid; this is strictly a subset (no cai —
# Musa's rule names only these 3 as "brains").
BRAIN_NODES = {
    "cc-fleet-health": ".fleet-health_default_token",
    "orch-console": ".nazim_default_token",
    "cc-quality": ".quality_default_token",
}
MAMADAH_SESSION = "mamadah"
EXCLUDE_WORKER_SUBSTR = ("mamadah", "irsyad")


# ── pure-ish decision ──────────────────────────────────────────────────────────
def pct(v: Optional[float]) -> float:
    """Normalise a utilisation to percent (Anthropic headers are 0-1 fractions)."""
    if v is None:
        raise RuntimeError("missing utilisation value")
    v = float(v)
    return v * 100.0 if v <= 1.0 else v


def decide(musa_7d: float) -> Dict[str, bool]:
    """PURE. The two triggers are independent and both can be true at once (95%
    implies 90%) — callers run the worker step and the brain step as two separate
    blocks, not a single exclusive branch."""
    return {"workers_due": musa_7d >= WORKER_DEEPSEEK_PCT, "brains_due": musa_7d >= BRAIN_SYED_PCT}


# ── readings (fail loud) ──────────────────────────────────────────────────────
def read_musa() -> Dict[str, float]:
    from nervous_system import weekly_limit_monitor as wlm
    p = wlm.probe_pool("Musa")
    if p.get("idle_no_data"):
        return {"7d": 0.0, "5h": 0.0, "idle": 1.0}
    return {"7d": pct(p.get("u7d")), "5h": pct(p.get("u5h") or 0.0), "idle": 0.0}


def _fp_of(path: Path) -> str:
    raw = path.read_bytes()
    if raw.endswith(b"\n"):
        raw = raw[:-1]  # match $(cat) / boot-script fingerprint convention
    return hashlib.sha256(raw).hexdigest()[:12]


def _db():
    import psycopg2
    return psycopg2.connect(os.environ["DATABASE_URL"])


# ── worker-lane membership + provider_policy pre-check ───────────────────────
def worker_candidate_sessions(musa_fp: str) -> List[Tuple[str, str]]:
    """[(agent_id, tmux_session), ...] for every lane currently on Musa's Claude
    token, excluding singletons/brains and the standing mamadah/irsyad exclusions.
    Re-derived live every run — never a hardcoded lane list (orch-console #86307)."""
    from nervous_system.protected_agents import protected_agent_ids
    protected = protected_agent_ids()
    out = []
    with _db() as c, c.cursor() as cur:
        cur.execute("""SELECT agent_id, tmux_session FROM agent_status
                       WHERE auth_fp = %s AND status IN ('working', 'active')
                         AND (host = 'Sheikhs-Mini' OR host IS NULL)
                         AND tmux_session IS NOT NULL
                       ORDER BY agent_id""", (musa_fp,))
        for agent_id, session in cur.fetchall():
            if agent_id in protected:
                continue
            low = f"{agent_id} {session}".lower()
            if any(x in low for x in EXCLUDE_WORKER_SUBSTR):
                continue
            out.append((agent_id, session))
    return out


def provider_policy_rows():
    with _db() as c, c.cursor() as cur:
        cur.execute("SELECT client_key, allowed_providers, text_only FROM provider_policy")
        cols = [d[0] for d in cur.description]
        policy_rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.execute("SELECT lane, allowed_providers, text_only FROM provider_policy_lane_override")
        cols = [d[0] for d in cur.description]
        override_rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return policy_rows, override_rows


def deepseek_cleared(session: str, policy_rows, override_rows) -> bool:
    """Read-only PREVIEW of the same gate switch_lane_token.sh --model-apply enforces
    at flip time (scripts.lib.provider_policy). Never the enforcement itself — just
    lets this script report a SKIP up front instead of attempting a flip it knows
    will be refused."""
    from scripts.lib.provider_policy import (is_narrower_or_equal, lookup_lane_override,
                                              resolve_client_key, resolve_policy)
    keys = [r.get("client_key") for r in policy_rows]
    key = resolve_client_key(session, keys)
    client_level = resolve_policy(policy_rows, key)
    override = lookup_lane_override(override_rows, session)
    effective = client_level
    if override is not None and is_narrower_or_equal(override, client_level):
        effective = override
    return "deepseek" in effective["allowed_providers"]


# ── tmux / argv helpers (self-contained; mirrors glm_overflow_switch.py's) ────
def tmux_up(session: str) -> bool:
    return subprocess.run(["tmux", "has-session", "-t", f"={session}"],
                          capture_output=True).returncode == 0


def lane_argv_model(session: str) -> Optional[str]:
    """The --model value of the claude process running in the session's pane."""
    pane = subprocess.run(["tmux", "list-panes", "-t", f"={session}", "-F", "#{pane_pid}"],
                          capture_output=True, text=True)
    if pane.returncode != 0 or not pane.stdout.strip():
        return None
    root = pane.stdout.split()[0]
    ps = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True)
    rows = [l.split(None, 2) for l in ps.stdout.splitlines() if l.strip()]
    kids: Dict[str, List[List[str]]] = {}
    for r in rows:
        if len(r) == 3:
            kids.setdefault(r[1], []).append(r)
    stack = [root]
    while stack:
        p = stack.pop()
        for r in kids.get(p, []):
            cmd = r[2]
            if "claude" in cmd and "--model" in cmd:
                parts = cmd.split()
                return parts[parts.index("--model") + 1] if parts.index("--model") + 1 < len(parts) else None
            stack.append(r[0])
    return None


def _now_tag() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ── action: worker lane -> DeepSeek ───────────────────────────────────────────
def switch_worker_lane(session: str, orch_dir: Path, apply: bool, run=subprocess.run) -> Dict[str, str]:
    ptr = orch_dir / f".{session}_model"
    up = tmux_up(session)
    if not apply:
        return {"session": session, "result": "DRY",
                "detail": f"would point {ptr.name} -> {DEEPSEEK_MODEL}"
                + (" and relaunch via switch_lane_token --model-apply --drain" if up else " (lane down: pointer only)")}
    bak = None
    if ptr.exists():
        bak = orch_dir / f".{session}_model.bak-musadeepseek-{_now_tag()}"
        ptr.rename(bak)
    ptr.write_text(DEEPSEEK_MODEL)
    if not up:
        return {"session": session, "result": "POINTER-ONLY",
                "detail": f"lane down; pointer set to {DEEPSEEK_MODEL}"
                + (f" (prior pointer saved to {bak.name})" if bak else "") + "; boots on DeepSeek next launch"}
    env = dict(os.environ, CC_BASE_AGENT_ID="cc-fleet-health")
    cp = run([str(orch_dir / "scripts" / "switch_lane_token.sh"), "--model-apply", "--drain", session, str(MUSA_TOKEN)],
             capture_output=True, text=True, timeout=420, env=env, cwd=str(orch_dir))
    model = lane_argv_model(session)
    if model and model.startswith("deepseek-"):
        return {"session": session, "result": "OK", "detail": f"live argv --model {model}"}
    tail = (cp.stdout + cp.stderr).strip().splitlines()[-1:] if (cp.stdout or cp.stderr) else []
    return {"session": session, "result": "PENDING",
            "detail": f"still on {model or 'unknown'} (switch rc={cp.returncode}: {tail[0][:160] if tail else ''}); retry next run"}


# ── action: brains + mamadah -> Syed ─────────────────────────────────────────
def brains_already_moved(syed_fp: str) -> Dict[str, bool]:
    """Ground truth (agent_status.auth_fp), never a `.<node>_default_token` pointer
    file — those can be stale (verified live 2026-10-31)."""
    out = {node: False for node in BRAIN_NODES}
    try:
        with _db() as c, c.cursor() as cur:
            cur.execute("SELECT agent_id FROM agent_status WHERE agent_id = ANY(%s) AND auth_fp = %s",
                        (list(BRAIN_NODES), syed_fp))
            moved = {r[0] for r in cur.fetchall()}
        for node in BRAIN_NODES:
            out[node] = node in moved
    except Exception:
        pass  # fail SAFE -> all False; an unreadable ground truth never masks a pending move
    return out


def mamadah_already_moved(syed_fp: str) -> bool:
    try:
        with _db() as c, c.cursor() as cur:
            cur.execute("SELECT auth_fp FROM agent_status WHERE tmux_session = %s", (MAMADAH_SESSION,))
            row = cur.fetchone()
        return bool(row) and row[0] == syed_fp
    except Exception:
        return False


def switch_brain(node: str, apply: bool, run=subprocess.run) -> Dict[str, str]:
    ptr_name = BRAIN_NODES[node]
    if not apply:
        return {"node": node, "result": "DRY",
                "detail": f"would switch_singleton_token.sh {node} syed-oauth-token + durable-pin {ptr_name}"}
    cp = run([str(ORCH_DIR / "scripts" / "switch_singleton_token.sh"), node, str(SYED_TOKEN)],
             capture_output=True, text=True, timeout=180, cwd=str(ORCH_DIR))
    tail = (cp.stdout + cp.stderr).strip().splitlines()[-3:] if (cp.stdout or cp.stderr) else []
    if cp.returncode != 0:
        return {"node": node, "result": "FAILED", "detail": f"rc={cp.returncode}: {' | '.join(tail)[:300]}"}
    # Durable pin so a FUTURE crash-restart (e.g. the launchd keep-alive PATH gap,
    # bus #86258) stays on Syed — the live switch's OVERRIDE only covers THIS relaunch.
    (ORCH_DIR / ptr_name).write_text(str(SYED_TOKEN) + "\n")
    return {"node": node, "result": "OK", "detail": f"switched + durable-pinned ({ptr_name} -> syed-oauth-token)"}


def switch_mamadah(apply: bool, run=subprocess.run) -> Dict[str, str]:
    if not apply:
        return {"session": MAMADAH_SESSION, "result": "DRY",
                "detail": "would switch_lane_token.sh mamadah syed-oauth-token (plain re-token, no model change)"}
    cp = run([str(ORCH_DIR / "scripts" / "switch_lane_token.sh"), MAMADAH_SESSION, str(SYED_TOKEN)],
             capture_output=True, text=True, timeout=180, cwd=str(ORCH_DIR))
    tail = (cp.stdout + cp.stderr).strip().splitlines()[-3:] if (cp.stdout or cp.stderr) else []
    if cp.returncode != 0:
        return {"session": MAMADAH_SESSION, "result": "FAILED", "detail": f"rc={cp.returncode}: {' | '.join(tail)[:300]}"}
    return {"session": MAMADAH_SESSION, "result": "OK", "detail": "re-tokened to syed-oauth-token"}


# ── DeepSeek balance coverage projection ──────────────────────────────────────
def deepseek_balance_points() -> List[Tuple[dt.datetime, float]]:
    pts: List[Tuple[dt.datetime, float]] = []
    if not DEEPSEEK_BALANCE_FILE.is_file():
        return pts
    with open(DEEPSEEK_BALANCE_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                ts = dt.datetime.fromisoformat(obj["ts"])
                pts.append((ts, float(obj["balance_usd"])))
            except Exception:
                continue
    pts.sort(key=lambda p: p[0])
    return pts


def current_deepseek_lane_count(orch_dir: Path) -> int:
    n = 0
    for f in glob.glob(str(orch_dir / ".*_model")):
        if ".bak" in os.path.basename(f):
            continue
        try:
            if Path(f).read_text().strip().startswith("deepseek-"):
                n += 1
        except OSError:
            continue
    return n


def project_deepseek_coverage(new_lane_count: int, until: dt.datetime, now: Optional[dt.datetime] = None,
                               points: Optional[List[Tuple[dt.datetime, float]]] = None,
                               current_lanes: Optional[int] = None) -> Dict:
    """Projects whether the current DeepSeek balance covers `new_lane_count` ADDITIONAL
    lanes (on top of however many are on DeepSeek right now) until `until`, scaling the
    observed aggregate $/hr burn by the lane-count ratio. {"measurable": False, ...} on
    <2 balance points or 0 current lanes — never fabricates a number it can't back.

    Burn is the SUM of each consecutive interval's drop (never a negative interval, i.e.
    a top-up is floored to 0 spend for that interval, not netted against surrounding
    spend) — NOT just (first point - last point). Endpoint-only would UNDERSTATE total
    spend across any top-up in between (spend $10, top up $20, net change +$10 -> an
    endpoint read reports $0 spent) and could fabricate a false SUFFICIENT, silencing the
    one page that matters (cc-quality review, PR#727, orch-console #86380 fast-follow 1)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    pts = points if points is not None else deepseek_balance_points()
    if len(pts) < 2:
        return {"measurable": False, "reason": f"only {len(pts)} balance point(s) recorded"}
    t0, _ = pts[0]
    t1, _ = pts[-1]
    hours_observed = (t1 - t0).total_seconds() / 3600.0
    if hours_observed <= 0:
        return {"measurable": False, "reason": "balance history has no time spread"}
    lanes_now = current_lanes if current_lanes is not None else current_deepseek_lane_count(ORCH_DIR)
    if lanes_now <= 0:
        return {"measurable": False, "reason": "0 lanes currently on deepseek -- cannot derive a per-lane rate"}
    total_spend = sum(max(0.0, b_prev - b_next) for (_, b_prev), (_, b_next) in zip(pts, pts[1:]))
    observed_rate = total_spend / hours_observed                # $/hr, aggregate across lanes_now
    per_lane_rate = observed_rate / lanes_now
    total_lanes_after = lanes_now + new_lane_count
    projected_rate = per_lane_rate * total_lanes_after
    hours_until = max(0.0, (until - now).total_seconds() / 3600.0)
    projected_spend = projected_rate * hours_until
    balance_now = pts[-1][1]
    return {
        "measurable": True, "balance_now": balance_now, "current_lanes": lanes_now,
        "per_lane_rate_usd_hr": per_lane_rate, "projected_rate_usd_hr": projected_rate,
        "hours_until": hours_until, "projected_spend": projected_spend,
        "sufficient": balance_now >= projected_spend,
    }


# ── paging + state ─────────────────────────────────────────────────────────────
def page(subject: str, body: str, apply: bool, priority: str = "P1", req: bool = False) -> None:
    if not apply:
        print(f"[DRY] would page orch-console ({priority}): {subject}")
        return
    os.environ.setdefault("CC_BASE_AGENT_ID", "cc-fleet-health")
    from scripts import bus_send
    bus_send.send(from_agent="cc-fleet-health", to="orch-console", mtype="update", subject=subject,
                  body=body, priority=priority, req=req)


def load_state() -> Dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(s: Dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1))
    tmp.replace(STATE_FILE)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="act (default is dry-run)")
    ap.add_argument("--exclude", action="append", default=[], metavar="SESSION",
            help="hold this lane back this run (e.g. mid-merge/deploy); repeatable")
    a = ap.parse_args(argv)
    now = dt.datetime.now(dt.timezone.utc)
    held = set(a.exclude)

    musa = read_musa()
    d = decide(musa["7d"])
    musa_fp = _fp_of(MUSA_TOKEN)
    syed_fp = _fp_of(SYED_TOKEN)
    print(f"{now:%H:%M:%SZ} Musa 7d={musa['7d']:.0f}% 5h={musa['5h']:.0f}% | "
          f"workers_due={d['workers_due']} brains_due={d['brains_due']}")

    state = load_state()
    worker_results: List[Dict] = []
    skipped: List[Tuple[str, str, str]] = []
    to_switch: List[Tuple[str, str]] = []

    if d["workers_due"]:
        candidates = worker_candidate_sessions(musa_fp)
        policy_rows, override_rows = provider_policy_rows()
        for agent_id, session in candidates:
            if session in held or agent_id in held:
                skipped.append((agent_id, session, "HELD (--exclude)"))
            elif not deepseek_cleared(session, policy_rows, override_rows):
                skipped.append((agent_id, session, "no provider_policy clearance for deepseek"))
            else:
                to_switch.append((agent_id, session))
        for agent_id, session in to_switch:
            r = switch_worker_lane(session, ORCH_DIR, a.apply)
            r["agent_id"] = agent_id
            worker_results.append(r)
            print(f"  worker {session} ({agent_id}): {r['result']} — {r['detail']}")
        for agent_id, session, why in skipped:
            print(f"  worker {session} ({agent_id}): SKIPPED — {why}")

        reset_at = musa_reset_at()
        proj = project_deepseek_coverage(len(to_switch), reset_at, now)
        if proj["measurable"]:
            print(f"  deepseek balance ${proj['balance_now']:.2f}, projected rate "
                  f"${proj['projected_rate_usd_hr']:.3f}/hr over {proj['current_lanes'] + len(to_switch)} lanes, "
                  f"projected spend ${proj['projected_spend']:.2f} by {reset_at:%Y-%m-%d %H:%MZ} "
                  f"({'SUFFICIENT' if proj['sufficient'] else 'INSUFFICIENT'})")
            if not proj["sufficient"]:
                last_bal = state.get("page_balance_insufficient", 0)
                if now.timestamp() - last_bal >= PAGE_COOLDOWN_S:
                    page("DeepSeek balance INSUFFICIENT for projected worker-lane load (op#36560)",
                         f"Balance ${proj['balance_now']:.2f} but projected spend ${proj['projected_spend']:.2f} "
                         f"by {reset_at:%Y-%m-%d %H:%MZ} across {proj['current_lanes'] + len(to_switch)} "
                         f"lanes (${proj['projected_rate_usd_hr']:.3f}/hr, scaled from the observed "
                         f"${proj['per_lane_rate_usd_hr']:.4f}/hr/lane). Top up DeepSeek or reconsider the move.",
                         a.apply, priority="P1", req=True)
                    if a.apply:
                        state["page_balance_insufficient"] = now.timestamp()
                        save_state(state)
        else:
            print(f"  deepseek balance projection: could not measure ({proj['reason']})")

    brain_results: List[Dict] = []
    mamadah_result: Optional[Dict] = None
    if d["brains_due"]:
        already = brains_already_moved(syed_fp)
        for node in BRAIN_NODES:
            if already[node]:
                print(f"  brain {node}: already on Syed (ground truth) — standing down")
                continue
            r = switch_brain(node, a.apply)
            r["node"] = node
            brain_results.append(r)
            print(f"  brain {node}: {r['result']} — {r['detail']}")
        if mamadah_already_moved(syed_fp):
            print("  mamadah: already on Syed (ground truth) — standing down")
        else:
            mamadah_result = switch_mamadah(a.apply)
            print(f"  mamadah: {mamadah_result['result']} — {mamadah_result['detail']}")

    if not d["workers_due"] and not d["brains_due"]:
        print(f"  Musa 7d {musa['7d']:.0f}% below both triggers ({WORKER_DEEPSEEK_PCT:.0f}%/{BRAIN_SYED_PCT:.0f}%) — nothing to do")
        return 0
    if not a.apply:
        return 0

    acted = bool(worker_results or brain_results or mamadah_result)
    if not acted:
        return 0
    last = state.get("page_switch", 0)
    if now.timestamp() - last < PAGE_COOLDOWN_S:
        return 0

    lines = []
    if worker_results:
        lines.append("WORKERS -> DeepSeek:")
        lines += [f"- {r['session']} ({r['agent_id']}): {r['result']}: {r['detail']}" for r in worker_results]
    if skipped:
        lines.append("SKIPPED:")
        lines += [f"- {s} ({ag}): {why}" for ag, s, why in skipped]
    if brain_results:
        lines.append("BRAINS -> Syed:")
        lines += [f"- {r['node']}: {r['result']}: {r['detail']}" for r in brain_results]
    if mamadah_result:
        lines.append(f"MAMADAH -> Syed: {mamadah_result['result']}: {mamadah_result['detail']}")

    bad = [r for r in worker_results + brain_results if r["result"] in ("FAILED", "PENDING")]
    if mamadah_result and mamadah_result["result"] == "FAILED":
        bad.append(mamadah_result)
    ok = [r for r in worker_results + brain_results if r["result"] in ("OK", "POINTER-ONLY")]
    if mamadah_result and mamadah_result["result"] == "OK":
        ok.append(mamadah_result)

    page(f"Musa spend triggers fired: {len(ok)} moved, {len(skipped)} skipped, {len(bad)} pending/failed "
         f"(op#36560/36562, Musa 7d={musa['7d']:.0f}%)",
         "\n".join(lines) + f"\n\nMusa 7d={musa['7d']:.0f}% 5h={musa['5h']:.0f}%. Never switches back automatically "
         "— restoring a worker lane is renaming its .bak-musadeepseek-* pointer back + model-apply; "
         "restoring a brain/mamadah to Musa is a separate operator-directed switch_singleton_token.sh / "
         "switch_lane_token.sh call.", True)
    state["page_switch"] = now.timestamp()
    save_state(state)
    return 1 if bad else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 — fail LOUD: non-zero + stderr, never silent
        print(f"musa_deepseek_syed_switch FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
