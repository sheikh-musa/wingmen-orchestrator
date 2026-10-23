#!/usr/bin/env python3
"""glm_peak_pause_resume.py — standing daily GLM-peak automation (orch-console
#67477/#67479/#67504/#67507, Musa op#30538/op#30543/op#30563, 2026-10-15).

Musa's rule, final form: at GLM peak (z.ai burns quota ~3x faster), a lane doing
URGENT work should MOVE to the Musa/Anthropic pool and keep working; a lane doing
non-urgent/backlog work should PAUSE instead (0 pool burn). At off-peak, reverse
both: urgent lanes move back to GLM, paused lanes resume. No human in the loop at
fire time — that is the actual ask ("i shouldnt have to remind you everyday").
The one thing a human (orch-console, NOT Musa) still does is keep
`fleet_lanes.glm_peak_urgent` current as the day's hot work shifts — that is a
coordination judgment call, not something this script can infer.

PEAK WINDOW: 06:00-10:00 UTC (confirmed live 2026-10-13/#65625 and reconfirmed
2026-10-15/#67507; z.ai peak = 14:00-18:00 UTC+8). A PERIODIC check against this
window (not a brittle exact-minute timer) — safe to run every few minutes via
launchd StartInterval; a missed single firing just gets caught on the next one.

SCOPE (which lanes this ever touches): only sessions whose per-lane model pointer
`.<session>_model` currently says `glm-*` (i.e. genuinely on GLM right now) are
candidates — same signal glm_overflow_switch.py uses, so a lane that was never on
GLM (irsyad, the hub, orch-console, cc-fleet-health itself) is never a candidate
by construction. HARD_EXCLUDE below is a second, independent belt-and-suspenders
check against the exact standing-doctrine never-list (glm-rollout-plan-20261013
line 35) — in particular cosem-adcda-photos (image work, HARD-barred from GLM by
the original rollout's own image/vision-MCP-off condition) must never be flipped
TO glm by the resume-at-off-peak branch even if some future bug ever put a glm-*
pointer there. Caught 2026-10-15: cosem-adcda-photos was observed on glm:z.ai
despite this standing bar — see the FYI this build was reported alongside; this
exclude list is what stops this specific daemon from ever re-creating that state.

IDEMPOTENT + STATE-TRACKED (like glm_overflow_switch.py): this script only ever
reverses actions IT took (recorded in STATE_FILE, keyed by UTC calendar date —
the peak window never crosses midnight UTC so date-keying is exact), never a
lane some other process independently parked on Musa/paused for an unrelated
reason. A new UTC day resets state fresh regardless of how the prior day ended
(fail toward re-evaluating, never toward carrying stale state forward).

FAIL LOUD: an unreadable DB or a lane that won't drain is reported, never
silently skipped and dropped from the record.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

ORCH_DIR = Path(os.environ.get("ORCH_DIR", str(Path(__file__).resolve().parent.parent)))
sys.path.insert(0, str(ORCH_DIR))

PEAK_START_HOUR = 6   # UTC, inclusive
PEAK_END_HOUR = 10    # UTC, exclusive
MUSA_TOKEN = str(Path.home() / ".wingmen" / "keys" / "musa-oauth-token")
STATE_FILE = Path.home() / "wingmen" / "fleet-health" / "state" / "glm_peak_pause_resume.json"

# glm-rollout-plan-20261013 line 35, standing doctrine: never move these onto GLM,
# whatever a pointer file says. Exact lane names, not prefixes, except irsyad* which
# is a whole family bar.
HARD_EXCLUDE_LANES = {"cosem-adcda-photos", "cc-fleet-health"}
HARD_EXCLUDE_PREFIXES = ("irsyad", "hub", "console", "orch-console")


# ── pure decisions (testable without touching tmux/DB/subprocess) ─────────────
def in_peak_window(now: dt.datetime) -> bool:
    """06:00-10:00 UTC, inclusive start / exclusive end, weekdays only.

    z.ai peak (14:00-18:00 UTC+8) is Mon-Fri only -- confirmed against z.ai's
    own docs + twice-observed burn (bus #68623). The 06:00-10:00 UTC window
    never crosses a UTC+8 midnight, so the UTC weekday and the UTC+8 weekday
    are always the same calendar day here -- no timezone conversion needed.
    """
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return PEAK_START_HOUR <= now.hour < PEAK_END_HOUR


def is_hard_excluded(session: str) -> bool:
    if session in HARD_EXCLUDE_LANES:
        return True
    return any(session.startswith(p) for p in HARD_EXCLUDE_PREFIXES)


def glm_pointer_lanes(orch_dir: Path) -> List[str]:
    """Sessions whose per-lane model pointer currently says glm-* — same signal
    glm_overflow_switch.py's glm_lanes() uses. Excludes hard-barred lanes."""
    out = []
    for f in sorted(glob.glob(str(orch_dir / ".*_model"))):
        name = os.path.basename(f)
        if ".bak" in name:
            continue
        try:
            model = Path(f).read_text().strip()
        except OSError:
            continue
        if not model.startswith("glm-"):
            continue
        session = name[1:-len("_model")]
        if is_hard_excluded(session):
            continue
        out.append(session)
    return out


def fresh_state_for(today: str) -> Dict:
    return {"window_date": today, "flipped": [], "paused": [], "resume_done": False}


def load_state() -> Dict:
    try:
        s = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        s = {}
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    if s.get("window_date") != today:
        return fresh_state_for(today)
    s.setdefault("flipped", [])
    s.setdefault("paused", [])
    s.setdefault("resume_done", False)
    return s


def save_state(s: Dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1))
    tmp.replace(STATE_FILE)


def peak_candidates(lanes: List[str], handled: set) -> List[str]:
    """Currently-GLM lanes not yet handled this window — a lane that boots onto
    GLM mid-peak (after an earlier run already fired) still gets caught on the
    next periodic run, not just once at the window's first instant."""
    return [l for l in lanes if l not in handled]


# ── DB reads/writes ─────────────────────────────────────────────────────────
def read_urgent_flags(sessions: List[str]) -> Dict[str, bool]:
    if not sessions:
        return {}
    import psycopg2
    from scripts.bus_send import dburl
    conn = psycopg2.connect(dburl(os.environ.get("DATABASE_URL")))
    cur = conn.cursor()
    cur.execute("SELECT lane, glm_peak_urgent FROM fleet_lanes WHERE lane = ANY(%s)", (sessions,))
    return dict(cur.fetchall())


def read_lane_model(session: str) -> Optional[str]:
    """fleet_lanes.model — the lane's own recorded target Claude model, used when
    flipping it onto Musa (NOT guessed; read from the DB each time)."""
    import psycopg2
    from scripts.bus_send import dburl
    conn = psycopg2.connect(dburl(os.environ.get("DATABASE_URL")))
    cur = conn.cursor()
    cur.execute("SELECT model FROM fleet_lanes WHERE lane = %s", (session,))
    row = cur.fetchone()
    return row[0] if row else None


def set_desired_state(session: str, state: str) -> None:
    import psycopg2
    from scripts.bus_send import dburl
    conn = psycopg2.connect(dburl(os.environ.get("DATABASE_URL")))
    cur = conn.cursor()
    cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
    cur.execute("UPDATE fleet_lanes SET desired_state=%s WHERE lane=%s", (state, session))
    conn.commit()


def resolve_default_token(session: str, orch_dir: Path) -> str:
    """The lane's normal/baseline token (e.g. syed-oauth-token for cosem lanes) —
    what it should flip BACK to at off-peak, read via the fleet's own resolver,
    never hardcoded, so this works for any lane family the daemon ever touches."""
    from scripts.lib.lane_token_resolver import resolve_lane_token_path
    path = resolve_lane_token_path(session, orch_dir=str(orch_dir))
    if not path:
        raise RuntimeError(f"no default token resolved for {session}")
    return path


# ── actions (apply=False is a no-op describe; apply=True acts) ────────────────
def flip_to_musa(session: str, orch_dir: Path, apply: bool, run=subprocess.run) -> Dict[str, str]:
    if not apply:
        return {"session": session, "result": "DRY", "detail": "would flip to musa"}
    model = read_lane_model(session) or "claude-sonnet-5"
    (orch_dir / f".{session}_model").write_text(model)
    env = dict(os.environ, CC_BASE_AGENT_ID="cc-fleet-health")
    cp = run([str(orch_dir / "scripts" / "switch_lane_token.sh"), "--model-apply", "--drain",
              session, MUSA_TOKEN], capture_output=True, text=True, timeout=420, env=env, cwd=str(orch_dir))
    ok = cp.returncode == 0
    tail = (cp.stdout + cp.stderr).strip().splitlines()[-1:] if (cp.stdout or cp.stderr) else []
    return {"session": session, "result": "OK" if ok else "FAILED",
            "detail": f"model={model}; switch rc={cp.returncode}" + (f": {tail[0][:160]}" if tail and not ok else "")}


def pause_lane(session: str, apply: bool) -> Dict[str, str]:
    if not apply:
        return {"session": session, "result": "DRY", "detail": "would set desired_state=paused"}
    set_desired_state(session, "paused")
    return {"session": session, "result": "OK", "detail": "desired_state=paused"}


def check_hard_exclude_divergence(session: str, apply: bool) -> None:
    """Best-effort, OBSERVATIONAL ONLY (orch-console Q5, bus #74588 — transition
    period before HARD_EXCLUDE_LANES is deleted): compares this module's OLD
    is_hard_excluded(session) verdict against what the NEW provider_policy +
    provider_policy_lane_override gate (scripts/lib/provider_policy.assert_glm_allowed)
    would decide for the 'zai' provider. Posts ONE loud warning to orch-console if
    they disagree, or if the lane override itself is misconfigured (widens rather
    than narrows). NEVER raises and NEVER changes the caller's actual decision —
    the OLD check stays authoritative this cycle; this only surfaces drift so
    HARD_EXCLUDE_LANES can be deleted once a clean cycle is observed."""
    try:
        from scripts.lib import provider_policy as pp
        import psycopg
        from scripts.bus_send import dburl
        with psycopg.connect(dburl(os.environ)) as conn, conn.cursor() as cur:
            cur.execute("SELECT client_key, allowed_providers, text_only FROM provider_policy")
            cols = [d[0] for d in cur.description]
            policy_rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur.execute("SELECT lane, allowed_providers, text_only FROM provider_policy_lane_override")
            cols = [d[0] for d in cur.description]
            override_rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        old_excluded = is_hard_excluded(session)
        try:
            pp.assert_glm_allowed(session, policy_rows=policy_rows, override_rows=override_rows)
            new_excluded = False
        except pp.ProviderNotAllowedError:
            new_excluded = True
        except pp.ProviderPolicyMisconfigured as e:
            page(f"[provider-policy-divergence] {session}: lane override misconfigured", str(e), apply)
            return
        if old_excluded != new_excluded:
            page(f"[provider-policy-divergence] {session}: old={old_excluded} new={new_excluded}",
                 f"Transition-period warn-on-divergence (orch-console Q5, bus #74588) — "
                 f"HARD_EXCLUDE_LANES (old, authoritative this cycle) and "
                 f"provider_policy/provider_policy_lane_override (new) disagree on whether "
                 f"{session!r} may use GLM: old says excluded={old_excluded}, new says "
                 f"excluded={new_excluded}. Neither check blocked this flip. Investigate before "
                 f"HARD_EXCLUDE_LANES is deleted.", apply)
    except BaseException as e:  # noqa: BLE001 — observational only, must never block a real
        # flip. BaseException (not Exception) deliberately: bus_send.dburl() raises
        # SystemExit (not a plain Exception) when DATABASE_URL is unset, which this
        # side-channel must swallow just as safely as any other lookup failure.
        print(f"glm_peak_pause_resume: divergence check failed ({e!r}) — skipped, not blocking",
              file=sys.stderr)


def flip_back_to_glm(session: str, orch_dir: Path, apply: bool, run=subprocess.run,
                      token_resolver=resolve_default_token,
                      divergence_check=check_hard_exclude_divergence) -> Dict[str, str]:
    divergence_check(session, apply)
    if is_hard_excluded(session):
        return {"session": session, "result": "SKIPPED", "detail": "hard-excluded lane — never flips to GLM"}
    if not apply:
        return {"session": session, "result": "DRY", "detail": "would flip back to glm-5.3"}
    try:
        token = token_resolver(session, orch_dir)
    except Exception as e:  # noqa: BLE001 — fail loud on this lane, keep going for the rest
        return {"session": session, "result": "FAILED", "detail": f"token resolve failed: {e}"}
    (orch_dir / f".{session}_model").write_text("glm-5.3")
    env = dict(os.environ, CC_BASE_AGENT_ID="cc-fleet-health")
    cp = run([str(orch_dir / "scripts" / "switch_lane_token.sh"), "--model-apply", "--drain",
              session, token], capture_output=True, text=True, timeout=420, env=env, cwd=str(orch_dir))
    ok = cp.returncode == 0
    tail = (cp.stdout + cp.stderr).strip().splitlines()[-1:] if (cp.stdout or cp.stderr) else []
    return {"session": session, "result": "OK" if ok else "FAILED",
            "detail": f"token={os.path.basename(token)}; switch rc={cp.returncode}" + (f": {tail[0][:160]}" if tail and not ok else "")}


def resume_pause(session: str, apply: bool) -> Dict[str, str]:
    if not apply:
        return {"session": session, "result": "DRY", "detail": "would set desired_state=up"}
    set_desired_state(session, "up")
    return {"session": session, "result": "OK", "detail": "desired_state=up"}


def page(subject: str, body: str, apply: bool) -> None:
    if not apply:
        print(f"[DRY] would page orch-console: {subject}")
        return
    os.environ.setdefault("CC_BASE_AGENT_ID", "cc-fleet-health")
    from scripts import bus_send
    bus_send.send(from_agent="cc-fleet-health", to="orch-console", mtype="update", subject=subject,
                  body=body, priority="P2", req=False)


# ── main ────────────────────────────────────────────────────────────────────
def main(argv: Optional[List[str]] = None, now: Optional[dt.datetime] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="act (default is dry-run)")
    a = ap.parse_args(argv)
    now = now or dt.datetime.now(dt.timezone.utc)
    peak = in_peak_window(now)
    state = load_state()

    print(f"{now:%Y-%m-%d %H:%M:%SZ} peak={peak} window_date={state['window_date']} "
          f"flipped={state['flipped']} paused={state['paused']} resume_done={state['resume_done']}")

    if peak:
        handled = set(state["flipped"]) | set(state["paused"])
        lanes = glm_pointer_lanes(ORCH_DIR)
        cands = peak_candidates(lanes, handled)
        if not cands:
            print("  no new GLM-pointer candidates this run")
            return 0
        urgent = read_urgent_flags(cands)
        results = []
        for s in cands:
            if urgent.get(s, False):
                r = flip_to_musa(s, ORCH_DIR, a.apply)
                print(f"  URGENT->musa {s}: {r['result']} — {r['detail']}")
                if a.apply and r["result"] == "OK":
                    state["flipped"].append(s)
            else:
                r = pause_lane(s, a.apply)
                print(f"  pause {s}: {r['result']} — {r['detail']}")
                if a.apply and r["result"] == "OK":
                    state["paused"].append(s)
            results.append(r)
        if a.apply:
            save_state(state)
            ok = [r for r in results if r["result"] == "OK"]
            failed = [r for r in results if r["result"] not in ("OK", "DRY")]
            lines = "\n".join(f"- {r['session']}: {r['result']}: {r['detail']}" for r in results)
            page(f"GLM peak: {len(ok)} lane(s) handled ({len(failed)} failed)" if ok or failed else "GLM peak: handled",
                 f"TL;DR: peak-start pass -- urgent lanes moved to Musa, the rest paused.\n\n{lines}", a.apply)
        return 1 if any(r["result"] == "FAILED" for r in results) else 0

    # off-peak: reverse anything this run (today) handled and hasn't reversed yet
    if state["resume_done"] or not (state["flipped"] or state["paused"]):
        print("  off-peak, nothing to resume")
        return 0
    results = []
    for s in state["flipped"]:
        r = flip_back_to_glm(s, ORCH_DIR, a.apply)
        print(f"  resume->glm {s}: {r['result']} — {r['detail']}")
        results.append(r)
    for s in state["paused"]:
        r = resume_pause(s, a.apply)
        print(f"  resume-unpause {s}: {r['result']} — {r['detail']}")
        results.append(r)
    if a.apply:
        state["resume_done"] = True
        save_state(state)
        failed = [r for r in results if r["result"] not in ("OK", "DRY", "SKIPPED")]
        lines = "\n".join(f"- {r['session']}: {r['result']}: {r['detail']}" for r in results)
        page(f"GLM off-peak: resumed {len(results) - len(failed)} lane(s) ({len(failed)} failed)",
             f"TL;DR: off-peak pass -- urgent lanes back on GLM, paused lanes resumed.\n\n{lines}", a.apply)
    return 1 if any(r["result"] == "FAILED" for r in results) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 — fail LOUD: non-zero + stderr, never silent
        print(f"glm_peak_pause_resume FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
