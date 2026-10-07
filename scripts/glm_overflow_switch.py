#!/usr/bin/env python3
"""glm_overflow_switch.py — move every GLM lane onto Musa's Claude token when the
GLM Coding Plan quota is nearly spent (Musa op#27156, orch-console #58025/#58028).

Musa's rule: "once glm hits 95% shift all of its lanes to musa".

HOW A LANE IS ON GLM: its per-lane model pointer `.<session>_model` says `glm-*`.
The launcher routes a glm-* model to z.ai; the lane's Claude token is unchanged. So
"move to Musa" = move the GLM pointer ASIDE (renamed `.<session>_model.bak-glmoverflow-
<UTC>`, restorable) so the lane resolves to its family/fleet default Claude model, then
relaunch it with `switch_lane_token.sh --model-apply --drain` onto Musa's token, and
verify the live argv no longer says glm-*.

TRIGGER RULE (orch-console #58028, implemented exactly):
  * GLM weekly  >= 95%                      -> SWITCH
  * GLM 5-hour  >= 95% AND weekly >= 90%    -> SWITCH
  * GLM 5-hour  >= 95% with weekly < 90%    -> PAGE ONLY (5h resets itself; wait is cheaper)
  * Musa 5h or 7d >= 90% when a switch is due -> PAGE INSTEAD of switching
  * NEVER switch back automatically (switch-back is orch-console's call). Each run only
    acts while the trigger condition holds NOW; lanes are only ever moved GLM -> Musa.

FAIL LOUD: an unreadable GLM or Musa reading raises (non-zero exit). A monitor that
silently reads "fine" because its probe failed is worse than none. --dry-run (the
DEFAULT) changes nothing; --apply acts.
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

WK_SWITCH = 95.0
FIVE_H_SWITCH = 95.0
FIVE_H_NEEDS_WK = 90.0
MUSA_MAX = 90.0
MUSA_TOKEN = str(Path.home() / ".wingmen" / "keys" / "musa-oauth-token")
STATE_FILE = Path.home() / "wingmen" / "fleet-health" / "state" / "glm_overflow_switch.json"
PAGE_COOLDOWN_S = 3600


# ── pure decision ─────────────────────────────────────────────────────────────
def decide(glm_wk: float, glm_5h: float, musa_7d: float, musa_5h: float) -> Dict[str, str]:
    """Return {"action": none|page_5h_only|page_musa_high|switch, "reason": str}.
    All inputs are percentages 0-100."""
    if glm_wk >= WK_SWITCH:
        why = f"GLM weekly {glm_wk:.1f}% >= {WK_SWITCH:.0f}%"
    elif glm_5h >= FIVE_H_SWITCH and glm_wk >= FIVE_H_NEEDS_WK:
        why = f"GLM 5h {glm_5h:.1f}% >= {FIVE_H_SWITCH:.0f}% and weekly {glm_wk:.1f}% >= {FIVE_H_NEEDS_WK:.0f}%"
    elif glm_5h >= FIVE_H_SWITCH:
        return {"action": "page_5h_only",
                "reason": f"GLM 5h {glm_5h:.1f}% >= {FIVE_H_SWITCH:.0f}% but weekly only {glm_wk:.1f}% "
                          f"(< {FIVE_H_NEEDS_WK:.0f}%): waiting for the 5h reset, not switching"}
    else:
        return {"action": "none", "reason": f"GLM weekly {glm_wk:.1f}%, 5h {glm_5h:.1f}%: below triggers"}
    if musa_5h >= MUSA_MAX or musa_7d >= MUSA_MAX:
        return {"action": "page_musa_high",
                "reason": f"{why}, BUT Musa is high (5h {musa_5h:.0f}%, 7d {musa_7d:.0f}%, limit "
                          f"{MUSA_MAX:.0f}%): paging instead of switching"}
    return {"action": "switch", "reason": f"{why}; Musa headroom ok (5h {musa_5h:.0f}%, 7d {musa_7d:.0f}%)"}


def glm_lanes(orch_dir: Path) -> List[str]:
    """Sessions whose per-lane model pointer currently says glm-*."""
    out = []
    for f in sorted(glob.glob(str(orch_dir / ".*_model"))):
        name = os.path.basename(f)
        if ".bak" in name:
            continue
        try:
            model = Path(f).read_text().strip()
        except OSError:
            continue
        if model.startswith("glm-"):
            out.append(name[1:-len("_model")])
    return out


def pct(v: Optional[float]) -> float:
    """Normalise a utilisation to percent (Anthropic headers are 0-1 fractions)."""
    if v is None:
        raise RuntimeError("missing utilisation value")
    v = float(v)
    return v * 100.0 if v <= 1.0 else v


# ── readings (fail loud) ──────────────────────────────────────────────────────
def read_glm() -> Dict[str, float]:
    from nervous_system.console import glm_usage
    glm_usage._reset_for_tests()          # force a fresh read, never a cached one
    r = glm_usage.get_glm_usage()
    if not r.get("available"):
        raise RuntimeError("GLM usage unavailable from z.ai quota endpoint")
    by = {w.get("label"): w for w in r.get("windows", [])}
    if "wk" not in by or "5h" not in by:
        raise RuntimeError(f"GLM usage missing a window: got {sorted(by)}")
    return {"wk": float(by["wk"]["pct"]), "5h": float(by["5h"]["pct"]),
            "wk_reset": by["wk"].get("resets_at"), "5h_reset": by["5h"].get("resets_at")}


def read_musa() -> Dict[str, float]:
    from nervous_system import weekly_limit_monitor as wlm
    p = wlm.probe_pool("Musa")
    if p.get("idle_no_data"):
        # Parked/idle token returns no fresh header; treat as ample headroom only if
        # the request itself succeeded (http 200), which probe_pool guarantees here.
        return {"7d": 0.0, "5h": 0.0, "idle": 1.0}
    return {"7d": pct(p.get("u7d")), "5h": pct(p.get("u5h") or 0.0), "idle": 0.0}


# ── actions ───────────────────────────────────────────────────────────────────
def _now_tag() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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


def switch_lane(session: str, orch_dir: Path, apply: bool, run=subprocess.run) -> Dict[str, str]:
    ptr = orch_dir / f".{session}_model"
    up = tmux_up(session)
    if not apply:
        return {"session": session, "result": "DRY", "detail": f"would move {ptr.name} aside"
                + (" and relaunch via switch_lane_token --model-apply --drain" if up else " (lane down: pointer only)")}
    bak = orch_dir / f".{session}_model.bak-glmoverflow-{_now_tag()}"
    if ptr.exists():
        ptr.rename(bak)
    if not up:
        return {"session": session, "result": "POINTER-ONLY", "detail": f"lane down; pointer moved to {bak.name}; boots on Musa default next launch"}
    env = dict(os.environ, CC_BASE_AGENT_ID="cc-fleet-health")
    cp = run([str(orch_dir / "scripts" / "switch_lane_token.sh"), "--model-apply", "--drain", session, MUSA_TOKEN],
             capture_output=True, text=True, timeout=420, env=env, cwd=str(orch_dir))
    model = lane_argv_model(session)
    if model and not model.startswith("glm-"):
        return {"session": session, "result": "OK", "detail": f"live argv --model {model}"}
    # Relaunch refused (busy lane did not drain) or did not take: the pointer stays moved,
    # so the NEXT run retries the relaunch, and any other relaunch path lands on Musa too.
    tail = (cp.stdout + cp.stderr).strip().splitlines()[-1:] if (cp.stdout or cp.stderr) else []
    return {"session": session, "result": "PENDING",
            "detail": f"still on {model or 'unknown'} (switch rc={cp.returncode}: {tail[0][:160] if tail else ''}); retry next run"}


def stamp_lane_note(session: str, text: str) -> None:
    import psycopg2
    from dotenv import load_dotenv
    load_dotenv(ORCH_DIR / ".env")
    with psycopg2.connect(os.environ["DATABASE_URL"]) as c, c.cursor() as cur:
        cur.execute("UPDATE fleet_lanes SET notes = coalesce(notes,'') || %s WHERE lane = %s",
                    (f" | {text}", session))


def page(subject: str, body: str, apply: bool) -> None:
    if not apply:
        print(f"[DRY] would page orch-console: {subject}")
        return
    os.environ.setdefault("CC_BASE_AGENT_ID", "cc-fleet-health")
    from scripts import bus_send
    bus_send.send(from_agent="cc-fleet-health", to="orch-console", mtype="update", subject=subject,
                  body=body, priority="P1", req=True)


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

    glm = read_glm()
    musa = read_musa()
    d = decide(glm["wk"], glm["5h"], musa["7d"], musa["5h"])
    held = [s for s in glm_lanes(ORCH_DIR) if s in set(a.exclude)]
    lanes = [s for s in glm_lanes(ORCH_DIR) if s not in set(a.exclude)]
    print(f"{now:%H:%M:%SZ} GLM wk={glm['wk']:.1f}% 5h={glm['5h']:.1f}% | Musa 7d={musa['7d']:.0f}% "
          f"5h={musa['5h']:.0f}% | glm lanes={len(lanes)} | {d['action']}: {d['reason']}")
    if held:
        print(f"  HELD this run (--exclude): {', '.join(held)}")

    state = load_state()
    if d["action"] in ("page_5h_only", "page_musa_high"):
        last = state.get(f"page_{d['action']}", 0)
        if now.timestamp() - last >= PAGE_COOLDOWN_S:
            page(f"GLM overflow: {d['action'].replace('_', ' ')}: {d['reason'][:120]}",
                 f"TL;DR: {d['reason']}.\n\nGLM lanes still on GLM ({len(lanes)}): {', '.join(lanes) or 'none'}.\n"
                 f"GLM resets: weekly {glm['wk_reset']}, 5h {glm['5h_reset']}.\n"
                 "Rule: Musa op#27156 / orch-console #58028. Nothing was switched.", a.apply)
            if a.apply:
                state[f"page_{d['action']}"] = now.timestamp()
                save_state(state)
        return 0
    if d["action"] != "switch" or not lanes:
        return 0

    results = [switch_lane(s, ORCH_DIR, a.apply) for s in lanes]
    for r in results:
        print(f"  {r['session']}: {r['result']} — {r['detail']}")
    if not a.apply:
        return 0
    for r in results:
        if r["result"] in ("OK", "POINTER-ONLY"):
            stamp_lane_note(r["session"], f"GLM-OVERFLOW moved to Musa by cc-fleet-health {now:%Y-%m-%d %H:%MZ}: "
                                          f"{d['reason']} (op#27156); {r['detail']}")
    ok = [r for r in results if r["result"] == "OK"]
    ptr = [r for r in results if r["result"] == "POINTER-ONLY"]
    pend = [r for r in results if r["result"] == "PENDING"]
    lines = "\n".join(f"- {r['session']}: {r['result']}: {r['detail']}" for r in results)
    page(f"GLM overflow: moved {len(ok)} live lane(s) to Musa ({len(ptr)} down lanes re-pointed, {len(pend)} pending)",
         f"TL;DR: {d['reason']}, so per Musa's rule (op#27156) GLM lanes are moving to Musa's token.\n\n{lines}\n\n"
         "PENDING lanes were busy and didn't drain; their GLM pointer is already moved aside, so the next run "
         "(5 min) retries, and any other relaunch also lands on Musa. Nothing switches back automatically: "
         "restore a lane by renaming its .bak-glmoverflow-* pointer back and model-applying it.", True)
    return 1 if pend else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 — fail LOUD: non-zero + stderr, never silent
        print(f"glm_overflow_switch FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
