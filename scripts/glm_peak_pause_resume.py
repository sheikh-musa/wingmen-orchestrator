#!/usr/bin/env python3
"""glm_peak_pause_resume.py — standing daily GLM-peak automation (orch-console
#67477/#67479/#67504/#67507, Musa op#30538/op#30543/op#30563, 2026-10-15).

Musa's rule, final form: at GLM peak (z.ai burns quota ~3x faster), a lane doing
URGENT work should MOVE to a Claude pool and keep working; a lane doing
non-urgent/backlog work should either PAUSE (0 pool burn) or move to DeepSeek
(near-0 Claude-pool burn, keeps moving). At off-peak, reverse both: urgent and
deepseek-fallback lanes move back to GLM, paused lanes resume. No human in the
loop at fire time — that is the actual ask ("i shouldnt have to remind you
everyday"). Two things a human (orch-console, NOT Musa) still does, same
coordination-judgment-call shape, neither inferable by this script:
- keep `fleet_lanes.glm_peak_urgent` current as the day's hot work shifts;
- keep today's pool/fallback CONFIG current — see GLM_PEAK_URGENT_POOL and
  GLM_PEAK_NONURGENT_ACTION below (orch-console #81291, 2026-10-29: a 2026-10-15
  build hardcoded "urgent -> Musa, else pause" as policy, not just a default, so
  when pool pacing or DeepSeek availability changed which destination was safe
  TODAY, the next scheduled run silently undid the hand correction. Fix is to
  read both as env-set config each run, never cache or hardcode them).

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

PINS + EXCLUDES (orch-console #81954, 2026-10-29): two config-driven lists in
CONFIG_FILE, read fresh every run via config_lane_set() — never code constants,
so console can edit them without a redeploy (via cc-fleet-health; "I edit pins
via you"). 'excluded_lanes': a lane whose provider this daemon must never touch
because something else manages it (e.g. the DeepSeek rollout — resuming those
lanes back onto GLM at off-peak would silently undo that decision). 'pinned_lanes':
a lane console is holding for a specific reason RIGHT NOW (e.g. mid-task, or
carrying a deploy that must not be interrupted) — lifted by editing this file,
not a standing policy. Both route through the single is_blocked() gate, checked
at every dispatch site (peak AND off-peak, every action, not just the GLM flip)
before any state change; a blocked lane gets a SKIPPED result, same as the
existing hard-exclude mechanism. Caught live 2026-10-29 (bus #81944/#81954): the
10:00Z off-peak resume was about to flip a pinned lane and reflip the DeepSeek
rollout's lanes back onto GLM with no human in the loop to stop it mid-run —
this is the fix. Every lane this daemon touches or even just considers gets its
own bus row to orch-console (log_move()), not only the end-of-run aggregate.
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
SYED_TOKEN = str(Path.home() / ".wingmen" / "keys" / "syed-oauth-token")
STATE_FILE = Path.home() / "wingmen" / "fleet-health" / "state" / "glm_peak_pause_resume.json"

# orch-console #81291 (2026-10-29): the 10-15 build hardcoded "urgent -> Musa,
# non-urgent -> pause" as the whole policy. That stopped being true the moment
# pool pacing or a new provider (DeepSeek) changes which pool is safe to land
# urgent work on TODAY — the next invocation (a fresh script run, NOT a live
# process — there is no standing daemon to hold an env var) then silently
# re-created whatever an operator had just corrected by hand. Both legs are now
# a daily, human-set knob living in a small CONFIG_FILE next to STATE_FILE —
# same "orch-console keeps it current, read fresh every run, never cached"
# shape as `fleet_lanes.glm_peak_urgent` itself and as the MODEL-env>pin-file>
# default precedence used elsewhere in this fleet. GLM_PEAK_URGENT_POOL /
# GLM_PEAK_NONURGENT_ACTION env vars still win when set — that path exists for
# tests and a one-off hand override in a single invocation, not as the durable
# knob (an export does not survive past this process).
CONFIG_FILE = Path.home() / "wingmen" / "fleet-health" / "state" / "glm_peak_pool_config.json"
_URGENT_POOL_TOKENS = {"musa": MUSA_TOKEN, "syed": SYED_TOKEN}
_NONURGENT_ACTIONS = {"pause", "deepseek"}


def _config_value(key: str, env_var: str, default: str) -> str:
    env = os.environ.get(env_var)
    if env is not None and env.strip():
        return env.strip().lower()
    try:
        cfg = json.loads(CONFIG_FILE.read_text())
        val = cfg.get(key)
        if val:
            return str(val).strip().lower()
    except FileNotFoundError:
        pass
    return default


def urgent_pool_token() -> str:
    pool = _config_value("urgent_pool", "GLM_PEAK_URGENT_POOL", "musa")
    try:
        return _URGENT_POOL_TOKENS[pool]
    except KeyError:
        raise RuntimeError(
            f"urgent_pool={pool!r} (from {CONFIG_FILE} or GLM_PEAK_URGENT_POOL) not recognised "
            f"(must be one of {sorted(_URGENT_POOL_TOKENS)}) — refusing to guess a pool for urgent lanes"
        )


def nonurgent_action() -> str:
    action = _config_value("nonurgent_action", "GLM_PEAK_NONURGENT_ACTION", "pause")
    if action not in _NONURGENT_ACTIONS:
        raise RuntimeError(
            f"nonurgent_action={action!r} (from {CONFIG_FILE} or GLM_PEAK_NONURGENT_ACTION) not "
            f"recognised (must be one of {sorted(_NONURGENT_ACTIONS)}) — refusing to guess the fallback"
        )
    return action

# glm-rollout-plan-20261013 line 35, standing doctrine: never move these onto GLM,
# whatever a pointer file says. Exact lane names, not prefixes, except irsyad* which
# is a whole family bar. cc-paperclip-spike: orch-console ruling #79984 (2026-10-28) — its live
# scope is operator/irsyad-adjacent, so Claude-only until that scope shrinks back to the
# isolated spike (this also matches provider_policy, which fails it closed: no #79888 divergence).
HARD_EXCLUDE_LANES = {"cosem-adcda-photos", "cc-fleet-health", "cc-paperclip-spike"}
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


def config_lane_set(key: str) -> set:
    """orch-console #81954 (2026-10-29): a config-driven list this daemon must
    never act on, read fresh every run (same shape as urgent_pool/nonurgent_action
    — CONFIG_FILE is the human-set knob, never cached or hardcoded). Two keys
    share this reader: 'excluded_lanes' (a provider managed elsewhere, e.g. the
    DeepSeek rollout — standing until someone changes the config) and
    'pinned_lanes' (a console-directed hold on a specific lane for a specific
    reason, e.g. mid-task — lifted by console editing this same file)."""
    try:
        cfg = json.loads(CONFIG_FILE.read_text())
    except FileNotFoundError:
        return set()
    val = cfg.get(key) or []
    if not isinstance(val, list):
        raise RuntimeError(f"{key} in {CONFIG_FILE} must be a list, got {type(val).__name__}")
    return set(val)


def is_config_excluded(session: str) -> bool:
    return session in config_lane_set("excluded_lanes")


def is_pinned(session: str) -> bool:
    return session in config_lane_set("pinned_lanes")


def is_blocked(session: str) -> Optional[str]:
    """Single gate every action/dispatch site checks before touching a lane.
    Returns the reason string if blocked, None if the daemon may act normally."""
    if is_hard_excluded(session):
        return "hard-excluded (standing never-list)"
    if is_config_excluded(session):
        return "config-excluded (provider managed elsewhere, e.g. DeepSeek rollout)"
    if is_pinned(session):
        return "pinned (console hold)"
    return None


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
    return {"window_date": today, "flipped": [], "paused": [], "deepseek": [], "resume_done": False}


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
    s.setdefault("deepseek", [])
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
def flip_to_musa(session: str, orch_dir: Path, apply: bool, run=subprocess.run,
                  token: str = MUSA_TOKEN) -> Dict[str, str]:
    """Move an urgent lane onto `token` (today's urgent pool — see urgent_pool_token();
    the name stays 'flip_to_musa' for back-compat, the destination is now a parameter)."""
    if not apply:
        return {"session": session, "result": "DRY", "detail": "would flip to musa"}
    model = read_lane_model(session) or "claude-sonnet-5"
    (orch_dir / f".{session}_model").write_text(model)
    env = dict(os.environ, CC_BASE_AGENT_ID="cc-fleet-health")
    cp = run([str(orch_dir / "scripts" / "switch_lane_token.sh"), "--model-apply", "--drain",
              session, token], capture_output=True, text=True, timeout=420, env=env, cwd=str(orch_dir))
    ok = cp.returncode == 0
    tail = (cp.stdout + cp.stderr).strip().splitlines()[-1:] if (cp.stdout or cp.stderr) else []
    return {"session": session, "result": "OK" if ok else "FAILED",
            "detail": f"model={model}; switch rc={cp.returncode}" + (f": {tail[0][:160]}" if tail and not ok else "")}


def pause_lane(session: str, apply: bool) -> Dict[str, str]:
    if not apply:
        return {"session": session, "result": "DRY", "detail": "would set desired_state=paused"}
    set_desired_state(session, "paused")
    return {"session": session, "result": "OK", "detail": "desired_state=paused"}


def check_deepseek_policy(session: str) -> None:
    """Same shape as check_glm_policy, for the deepseek non-urgent fallback (#81291)."""
    from scripts.lib import provider_policy as pp
    policy_rows, override_rows = pp._default_fetch_rows()
    pp.assert_glm_allowed(session, policy_rows=policy_rows, override_rows=override_rows, provider="deepseek")


def flip_to_deepseek(session: str, orch_dir: Path, apply: bool, run=subprocess.run,
                      token_resolver=resolve_default_token,
                      policy_check=check_deepseek_policy) -> Dict[str, str]:
    """[orch-console #81291] The non-urgent fallback when GLM_PEAK_NONURGENT_ACTION=
    deepseek: unpause (desired_state=up) and move the lane onto deepseek-flash
    instead of pausing it, so backlog work keeps moving at ~0 Claude-pool burn
    instead of stalling. Policy-gated the SAME way flip_back_to_glm is (#79897):
    the provider_policy check runs BEFORE the model pointer is ever written, so a
    refused flip leaves NOTHING changed — never a stray deepseek-flash pointer on
    a lane the policy actually bars (e.g. a future irsyad/mamadah-family lane
    landing in this non-urgent list). switch_lane_token.sh's own flip-time gate is
    a second, independent check at kill-time; this is belt-and-suspenders, same
    shape as flip_back_to_glm, not a duplicate of its authority."""
    if not apply:
        return {"session": session, "result": "DRY", "detail": "would unpause + flip to deepseek-flash"}
    from scripts.lib import provider_policy as pp
    try:
        policy_check(session)
    except (pp.ProviderNotAllowedError, pp.ProviderPolicyMisconfigured) as e:
        return {"session": session, "result": "REFUSED",
                "detail": f"provider_policy refuses deepseek, stays paused, nothing touched: {e}"}
    except Exception as e:  # noqa: BLE001 — unreadable policy = fail closed, never flip blind
        return {"session": session, "result": "FAILED",
                "detail": f"policy check failed, stays paused, nothing touched: {e}"}
    try:
        token = token_resolver(session, orch_dir)
    except Exception as e:  # noqa: BLE001 — fail loud on this lane, keep going for the rest
        return {"session": session, "result": "FAILED", "detail": f"token resolve failed, stays paused: {e}"}
    set_desired_state(session, "up")
    # Same pointer-restore discipline as flip_back_to_glm (#79897): keep the prior bytes
    # (or absence) to put back if the switch never actually moved the lane off GLM.
    ptr = orch_dir / f".{session}_model"
    prior = ptr.read_bytes() if ptr.exists() else None
    ptr.write_text("deepseek-flash")
    env = dict(os.environ, CC_BASE_AGENT_ID="cc-fleet-health")
    try:
        cp = run([str(orch_dir / "scripts" / "switch_lane_token.sh"), "--model-apply", "--drain",
                  session, token], capture_output=True, text=True, timeout=420, env=env, cwd=str(orch_dir))
    except subprocess.TimeoutExpired:
        return {"session": session, "result": "FAILED",
                "detail": "switch timed out after 420s — lane may be mid-relaunch; pointer left at "
                          "deepseek-flash, VERIFY the lane's model + pointer by hand"}
    except Exception as e:  # noqa: BLE001 — the switch never started, the lane is untouched
        _restore_pointer(ptr, prior)
        return {"session": session, "result": "FAILED", "detail": f"switch did not start ({e}); pointer restored"}
    if cp.returncode == 0:
        return {"session": session, "result": "OK",
                "detail": f"token={os.path.basename(token)}; switch rc=0"}
    out = (cp.stdout or "") + (cp.stderr or "")
    tail = out.strip().splitlines()[-1:]
    why = f"token={os.path.basename(token)}; switch rc={cp.returncode}" + (f": {tail[0][:160]}" if tail else "")
    if cp.returncode in _PRE_RELAUNCH_RCS or (cp.returncode == 7 and _GATE_REFUSED_MARK in out):
        _restore_pointer(ptr, prior)
        return {"session": session, "result": "FAILED", "detail": f"{why}; lane untouched, pointer restored"}
    return {"session": session, "result": "FAILED",
            "detail": f"{why}; failed AFTER the relaunch step — pointer left at deepseek-flash, VERIFY "
                      f"the lane's live model + pointer by hand"}


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
        with psycopg.connect(dburl(os.environ.get("DATABASE_URL"))) as conn, conn.cursor() as cur:
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


def check_glm_policy(session: str) -> None:
    """Impure: the same provider_policy resolution switch_lane_token.sh's flip-time gate
    runs, read BEFORE anything is written. Raises ProviderNotAllowedError /
    ProviderPolicyMisconfigured on a refusal, any other exception if the policy is
    unreadable; returns None if the lane may use GLM."""
    from scripts.lib import provider_policy as pp
    policy_rows, override_rows = pp._default_fetch_rows()
    pp.assert_glm_allowed(session, policy_rows=policy_rows, override_rows=override_rows)


# switch_lane_token.sh exit codes that provably fire BEFORE its kill-session (so the lane
# is untouched and the prior pointer is still the truth): 1 no session, 2 usage, 3/4 token,
# 5 composer hold, 9 orch/lib missing. Exit 7 is BOTH the pre-kill policy-gate refusal AND
# the post-kill new-session failure, so it only counts as pre-kill with the gate's message.
_PRE_RELAUNCH_RCS = {1, 2, 3, 4, 5, 9}
_GATE_REFUSED_MARK = "REFUSED by the provider_policy gate"


def _restore_pointer(ptr: Path, prior: Optional[bytes]) -> None:
    if prior is None:
        ptr.unlink(missing_ok=True)
    else:
        ptr.write_bytes(prior)


def flip_back_to_glm(session: str, orch_dir: Path, apply: bool, run=subprocess.run,
                      token_resolver=resolve_default_token,
                      divergence_check=check_hard_exclude_divergence,
                      policy_check=check_glm_policy) -> Dict[str, str]:
    divergence_check(session, apply)
    block_reason = is_blocked(session)
    if block_reason:
        return {"session": session, "result": "SKIPPED", "detail": f"{block_reason} — never flips to GLM"}
    if not apply:
        return {"session": session, "result": "DRY", "detail": "would flip back to glm-5.3"}
    # #79897: the policy check runs BEFORE the pointer write — a refused flip used to leave
    # .<session>_model=glm-5.3 behind on a lane that stayed on Claude.
    from scripts.lib import provider_policy as pp
    try:
        policy_check(session)
    except (pp.ProviderNotAllowedError, pp.ProviderPolicyMisconfigured) as e:
        return {"session": session, "result": "REFUSED",
                "detail": f"provider_policy refuses GLM, stays on Claude, pointer untouched: {e}"}
    except Exception as e:  # noqa: BLE001 — unreadable policy = fail closed, never flip blind
        return {"session": session, "result": "FAILED",
                "detail": f"policy check failed, not flipping, pointer untouched: {e}"}
    try:
        token = token_resolver(session, orch_dir)
    except Exception as e:  # noqa: BLE001 — fail loud on this lane, keep going for the rest
        return {"session": session, "result": "FAILED", "detail": f"token resolve failed: {e}"}
    # switch_lane_token.sh reads the pointer to pick the model, so it must be written first;
    # keep the prior bytes (or its absence) to put back if the switch never touched the lane.
    ptr = orch_dir / f".{session}_model"
    prior = ptr.read_bytes() if ptr.exists() else None
    ptr.write_text("glm-5.3")
    env = dict(os.environ, CC_BASE_AGENT_ID="cc-fleet-health")
    try:
        cp = run([str(orch_dir / "scripts" / "switch_lane_token.sh"), "--model-apply", "--drain",
                  session, token], capture_output=True, text=True, timeout=420, env=env, cwd=str(orch_dir))
    except subprocess.TimeoutExpired:
        return {"session": session, "result": "FAILED",
                "detail": "switch timed out after 420s — lane may be mid-relaunch; pointer left at "
                          "glm-5.3, VERIFY the lane's model + pointer by hand"}
    except Exception as e:  # noqa: BLE001 — the switch never started, the lane is untouched
        _restore_pointer(ptr, prior)
        return {"session": session, "result": "FAILED", "detail": f"switch did not start ({e}); pointer restored"}
    if cp.returncode == 0:
        return {"session": session, "result": "OK",
                "detail": f"token={os.path.basename(token)}; switch rc=0"}
    out = (cp.stdout or "") + (cp.stderr or "")
    tail = out.strip().splitlines()[-1:]
    why = f"token={os.path.basename(token)}; switch rc={cp.returncode}" + (f": {tail[0][:160]}" if tail else "")
    if cp.returncode in _PRE_RELAUNCH_RCS or (cp.returncode == 7 and _GATE_REFUSED_MARK in out):
        _restore_pointer(ptr, prior)
        return {"session": session, "result": "FAILED", "detail": f"{why}; lane untouched, pointer restored"}
    return {"session": session, "result": "FAILED",
            "detail": f"{why}; failed AFTER the relaunch step — pointer left at glm-5.3, VERIFY the "
                      f"lane's live model + pointer by hand"}


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


def log_move(phase: str, result: Dict[str, str], apply: bool) -> None:
    """orch-console #81954 (2026-10-29): every lane this daemon touches or
    considers this run gets its own bus row to orch-console, not just the
    end-of-run aggregate — a pin/exclude SKIPPED result is itself worth
    seeing (it's the proof the hold is actually working), not only a
    successful flip."""
    page(f"GLM {phase} {result['session']}: {result['result']}",
         f"TL;DR: {phase} pass, {result['session']} -> {result['result']}.\n\n{result['detail']}", apply)


# ── main ────────────────────────────────────────────────────────────────────
def validate_pin_exclude_config(orch_dir: Path, apply: bool) -> None:
    """orch-console #81954 should-fix (cc-quality #81964 review of PR#631): an
    excluded/pinned lane name that doesn't match any real .{lane}_model pointer
    silently no-ops (is_blocked() just never matches that lane) -- is_pinned/
    is_config_excluded are exact-match while lane naming is inconsistent
    (.bariq_model vs .cc-ffsfulfilment_model), so a wrong-form entry or a typo
    would otherwise flip despite the hold with zero signal. Turn that into a
    loud warning instead of a silent bypass."""
    orphans = []
    for key in ("excluded_lanes", "pinned_lanes"):
        for lane in sorted(config_lane_set(key)):
            if not (orch_dir / f".{lane}_model").exists():
                orphans.append((key, lane))
    if not orphans:
        return
    lines = "\n".join(f"- {key}: '{lane}' has no matching .{lane}_model pointer" for key, lane in orphans)
    plural = len(orphans) != 1
    page(f"GLM pin/exclude config: {len(orphans)} orphan entr{'ies' if plural else 'y'}",
         f"TL;DR: {len(orphans)} entr{'ies' if plural else 'y'} in glm_peak_pool_config.json don't match any "
         f"real lane pointer -- silently inert, won't actually block anything. Check the lane name form "
         f"(it's the pointer basename, e.g. 'cc-ffsfulfilment' not 'ffsfulfilment').\n\n{lines}", apply)


def main(argv: Optional[List[str]] = None, now: Optional[dt.datetime] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="act (default is dry-run)")
    a = ap.parse_args(argv)
    now = now or dt.datetime.now(dt.timezone.utc)
    peak = in_peak_window(now)
    state = load_state()

    print(f"{now:%Y-%m-%d %H:%M:%SZ} peak={peak} window_date={state['window_date']} "
          f"flipped={state['flipped']} paused={state['paused']} deepseek={state['deepseek']} "
          f"resume_done={state['resume_done']}")

    validate_pin_exclude_config(ORCH_DIR, a.apply)

    if peak:
        urgent_token = urgent_pool_token()        # raises loud on a bad config value — never guess
        nonurgent = nonurgent_action()
        handled = set(state["flipped"]) | set(state["paused"]) | set(state["deepseek"])
        lanes = glm_pointer_lanes(ORCH_DIR)
        cands = peak_candidates(lanes, handled)
        if not cands:
            print("  no new GLM-pointer candidates this run")
            return 0
        urgent = read_urgent_flags(cands)
        results = []
        for s in cands:
            block_reason = is_blocked(s)
            if block_reason:
                r = {"session": s, "result": "SKIPPED", "detail": f"{block_reason} — not dispatched"}
                print(f"  blocked {s}: {r['detail']}")
            elif urgent.get(s, False):
                r = flip_to_musa(s, ORCH_DIR, a.apply, token=urgent_token)
                print(f"  URGENT->{os.path.basename(urgent_token)} {s}: {r['result']} — {r['detail']}")
                if a.apply and r["result"] == "OK":
                    state["flipped"].append(s)
            elif nonurgent == "deepseek":
                r = flip_to_deepseek(s, ORCH_DIR, a.apply)
                print(f"  nonurgent->deepseek {s}: {r['result']} — {r['detail']}")
                if a.apply and r["result"] == "OK":
                    state["deepseek"].append(s)
            else:
                r = pause_lane(s, a.apply)
                print(f"  pause {s}: {r['result']} — {r['detail']}")
                if a.apply and r["result"] == "OK":
                    state["paused"].append(s)
            results.append(r)
            log_move("peak", r, a.apply)
        if a.apply:
            save_state(state)
            ok = [r for r in results if r["result"] == "OK"]
            failed = [r for r in results if r["result"] not in ("OK", "DRY", "SKIPPED")]
            lines = "\n".join(f"- {r['session']}: {r['result']}: {r['detail']}" for r in results)
            page(f"GLM peak: {len(ok)} lane(s) handled ({len(failed)} failed)" if ok or failed else "GLM peak: handled",
                 f"TL;DR: peak-start pass -- urgent lanes moved to today's urgent pool "
                 f"({os.path.basename(urgent_token)}), non-urgent lanes "
                 f"{'moved to deepseek' if nonurgent == 'deepseek' else 'paused'}.\n\n{lines}", a.apply)
        return 1 if any(r["result"] == "FAILED" for r in results) else 0

    # off-peak: reverse anything this run (today) handled and hasn't reversed yet
    if state["resume_done"] or not (state["flipped"] or state["paused"] or state["deepseek"]):
        print("  off-peak, nothing to resume")
        return 0
    results = []
    for s in state["flipped"] + state["deepseek"]:
        # Both buckets reverse the SAME way: back onto GLM, policy-gated — a deepseek-
        # fallback lane was never paused (desired_state stayed 'up' the whole time, it
        # just ran on a cheaper provider), so it needs the token-pointer reversal, not
        # resume_pause()'s desired_state flip. flip_back_to_glm() has its own is_blocked()
        # gate (returns SKIPPED), so a pinned/config-excluded lane here is handled safely
        # without a duplicate check — but check here too (belt-and-suspenders, same shape
        # as the rest of this file) so resume_pause() below gets the identical protection.
        r = flip_back_to_glm(s, ORCH_DIR, a.apply)
        print(f"  resume->glm {s}: {r['result']} — {r['detail']}")
        results.append(r)
        log_move("off-peak", r, a.apply)
    for s in state["paused"]:
        block_reason = is_blocked(s)
        if block_reason:
            r = {"session": s, "result": "SKIPPED", "detail": f"{block_reason} — not resumed"}
        else:
            r = resume_pause(s, a.apply)
        print(f"  resume-unpause {s}: {r['result']} — {r['detail']}")
        results.append(r)
        log_move("off-peak", r, a.apply)
    if a.apply:
        state["resume_done"] = True
        save_state(state)
        failed = [r for r in results if r["result"] not in ("OK", "DRY", "SKIPPED")]
        lines = "\n".join(f"- {r['session']}: {r['result']}: {r['detail']}" for r in results)
        page(f"GLM off-peak: resumed {len(results) - len(failed)} lane(s) ({len(failed)} failed)",
             f"TL;DR: off-peak pass -- urgent + deepseek-fallback lanes back on GLM, paused lanes resumed.\n\n{lines}", a.apply)
    return 1 if any(r["result"] == "FAILED" for r in results) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 — fail LOUD: non-zero + stderr, never silent
        print(f"glm_peak_pause_resume FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
