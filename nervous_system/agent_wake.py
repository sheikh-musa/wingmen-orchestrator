"""#111 wake mechanism — resolve a live agent to its tmux session and send an
OS-level wake SIGNAL (CAI-RESP-255 #2: signal only, never message content).

INERT until wired into the realtime subscriber — building the mechanism is
non-contingent; activation (the wake policy: which messages wake which agents)
is gated on cai. See docs/superpowers/specs/2026-06-17-realtime-wake-111.md.

Resolution: each `claude` lane inherits TMUX_PANE from its pane; we read it from
the process env and ask tmux for the session name, then match the pane's cwd to
the agent's repo_scope. v1 derives via cwd; target state is a self-registered
agent_status.tmux_session column (follow-up).
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import subprocess
import time

import psycopg
from dotenv import load_dotenv

ORCH = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ORCH / ".env")

# launchd hands the orchestrator a minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin)
# that OMITS Homebrew (/usr/local/bin), where `tmux` lives. Without this, every
# tmux shell-out below fails under launchd, no lane resolves, and the wake can
# never be delivered (it silently returns 'no live session'). Additive only.
for _d in ("/usr/local/bin", "/opt/homebrew/bin"):
    if os.path.isdir(_d) and _d not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + _d
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
_WAKE_DIR = ORCH / "scripts" / ".agent_wake"
_DEBOUNCE_S = 45
_CAP_LIMIT = 5            # CAI-RESP-259 Q4: hard cap...
_CAP_WINDOW_S = 300      # ...of 5 wakes per 5 min per agent; cap-hit must fail LOUD.
_SIGNAL = "[wake] new inbox item — read your agent_messages inbox and act"
_SUBTAG_RE = re.compile(r"-\d+$")

# CAI-RESP-259 Q1 trigger policy (the wake doorbell fires iff this is true).
_WAKE_TYPES = frozenset(
    {"blocker", "question", "review_request", "decision", "challenge"}
)


def auto_wake_enabled() -> bool:
    """Kill-switch (CAI-RESP-259 activation cond.1). Default OFF until the
    activation diff is cai-reviewed and the operator flips AUTO_WAKE_ENABLED=1."""
    return os.environ.get("AUTO_WAKE_ENABLED", "0").strip().lower() in ("1", "true", "yes")


def is_wake_eligible_recipient(
    to_agent: str | None,
    priority: str = "P2",
    requires_response: bool = False,
) -> bool:
    """RECIPIENT policy shared by the realtime doorbell AND the backstop sweep
    (op#11297). Factored out so realtime and the sweep enforce ONE recipient
    invariant — only the TRIGGER differs (realtime = urgent-now; sweep =
    nothing-rots-unread), never who is eligible.

    Full-eligibility recipients — live CC WORKER lanes, cai, and the console
    (orch-console, wake-A) — are eligible whenever the caller's trigger fires.

    The HUB (cc-orchestrator) is operator-attended and eligible ONLY on the CAI-451
    NARROW FLOOR: priority P0/P1 AND requires_response. This is why the predicate
    takes priority + requires_response — the floor lives HERE so realtime and the
    sweep apply it identically. A prior op#11297 refactor dropped it ('NEVER
    cc-orchestrator'); cai ruled that a REGRESSION (CAI-RESP-786), not a policy, and
    that op#11297's uniform principle itself requires including the hub. The operator
    is never woken. Recipient-only / default context => the hub is NOT eligible
    (fail-safe: never wake the hub unless a P0/P1+rr row is proven)."""
    if not to_agent:
        return False
    if to_agent == "cc-orchestrator":
        # CAI-451 narrow floor — carried forward per CAI-RESP-786.
        return priority in ("P0", "P1") and bool(requires_response)
    is_worker = to_agent.startswith("cc-")  # cc-orchestrator already handled above
    return is_worker or to_agent in ("cai", "orch-console")


def should_auto_wake(
    to_agent: str | None,
    message_type: str,
    requires_response: bool,
    priority: str,
    is_test: bool,
) -> bool:
    """CAI-RESP-259 Q1 (REALTIME doorbell trigger): wake iff recipient is eligible
    (is_wake_eligible_recipient), not a test, not P3, and (requires_response OR an
    actionable type OR P0/P1 floor). The backstop sweep reuses the SAME recipient
    gate but a BROADER trigger — see wake_backstop_sweep (op#11297): a passive
    update/rr=false to an eligible lane is correctly NOT realtime-urgent here, yet
    must not rot unread, so the sweep re-wakes it after a grace."""
    if is_test or priority == "P3":
        return False
    if not is_wake_eligible_recipient(to_agent, priority, requires_response):
        return False
    return requires_response or message_type in _WAKE_TYPES or priority in ("P0", "P1")


def should_backstop_wake(
    to_agent: str | None,
    message_type: str,
    requires_response: bool,
    priority: str,
    is_test: bool,
) -> bool:
    """CANONICAL backstop-sweep trigger (op#11297, cc-quality #16848) — the WIDER
    of the two wake predicates, and the authoritative per-row gate the sweep applies
    (the sweep's SQL is only a coarse prefilter; the policy lives HERE, never forked
    into SQL). Job: 'no directed message rots unread'. Wakes iff the recipient is
    eligible, not a test, not P3 — it DROPS should_auto_wake's urgent-type / rr /
    P0-P1 requirement, so a passive update/rr=false to a live lane (the #16838 miss)
    is still swept. Same recipient invariant as realtime; only the trigger widens.
    (message_type/requires_response are accepted for a uniform signature but do not
    gate the backstop; unread + past-grace are the sweep's query concerns.)"""
    if is_test or priority == "P3":
        return False
    return is_wake_eligible_recipient(to_agent, priority, requires_response)


def _base_family(agent_id: str) -> str:
    """cc-ihsanos-1 -> cc-ihsanos; cai -> cai."""
    return _SUBTAG_RE.sub("", agent_id)


def _expected_cwd_tokens(agent_id: str) -> list[str]:
    """Path segments that identify this agent's working tree(s)."""
    base = _base_family(agent_id)
    if base == "cai":
        return ["wingmen-cai"]
    if not _DSN:
        return []
    with psycopg.connect(_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT repo_scope FROM agents WHERE id=%s", (base,))
        row = cur.fetchone()
    scope = (row[0] if row else []) or []
    return [r for r in scope if r and r != "*"]


def _ppid(pid: int) -> int:
    try:
        return int(subprocess.check_output(
            ["ps", "-o", "ppid=", "-p", str(pid)], text=True, stderr=subprocess.DEVNULL).strip())
    except Exception:
        return 0


def _session_for_pid(pid: int, sess_by_pane: dict) -> str | None:
    """Walk the parent chain until a pid matches a tmux pane pid -> its session."""
    cur = pid
    for _ in range(12):
        if cur in sess_by_pane:
            return sess_by_pane[cur]
        cur = _ppid(cur)
        if cur <= 1:
            return None
    return None


def _live_claude_panes() -> list[dict]:
    """[{pid, cwd, session}] for every running dangerous-CC lane.

    Robust under launchd: maps each claude pid -> tmux session via tmux's OWN
    pane->pid table (`tmux list-panes -a`) + a parent-pid walk — NOT by reading
    the lane's TMUX_PANE env. Cross-process `ps eww` env reads are restricted
    under the launchd sandbox, which silently broke resolution and the wake.
    """
    out = []
    try:
        pids = subprocess.check_output(
            ["pgrep", "-f", "claude --dangerously-skip-permissions"],
            text=True, stderr=subprocess.DEVNULL).split()
    except Exception:
        return out
    sess_by_pane: dict[int, str] = {}
    try:
        panes = subprocess.check_output(
            ["tmux", "list-panes", "-a", "-F", "#{pane_pid} #{session_name}"],
            text=True, stderr=subprocess.DEVNULL)
        for ln in panes.splitlines():
            parts = ln.split(None, 1)
            if len(parts) == 2 and parts[0].isdigit():
                sess_by_pane[int(parts[0])] = parts[1].strip()
    except Exception:
        return out
    for pid in pids:
        try:
            cwd = subprocess.check_output(
                ["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"],
                text=True, stderr=subprocess.DEVNULL)
            cwd = next((ln[1:] for ln in cwd.splitlines() if ln.startswith("n")), "")
            session = _session_for_pid(int(pid), sess_by_pane)
            if session:
                out.append({"pid": pid, "cwd": cwd, "session": session})
        except Exception:
            continue
    logging.getLogger("wingmen.agent_wake").info(
        f"wake-resolve: pgrep={len(pids)} panes={len(sess_by_pane)} matched={len(out)} "
        f"panes_map={sess_by_pane} out={[(o['session'], o['cwd'][-20:]) for o in out]}")
    return out


def _tmux_bin() -> str:
    """The lane tmux binary. The Mini runs TWO tmux servers on different sockets —
    lanes live on /usr/local/bin/tmux (tmux-501/default), NOT /opt/homebrew/bin/tmux
    — so resolve/has-session must not depend on PATH order (reference_mini_tmux_two
    _binaries_socket)."""
    for c in (os.environ.get("TMUX_BIN"), "/usr/local/bin/tmux"):
        if c and os.path.exists(c):
            return c
    return "tmux"


def _tmux_has_session(session: str) -> bool:
    """True iff the named session exists on the lane tmux server (exact match)."""
    if not session:
        return False
    try:
        return subprocess.run(
            [_tmux_bin(), "has-session", "-t", f"={session}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except Exception:
        return False


def rank_candidates(rows, agent_id: str) -> list[str]:
    """Order candidate sessions for `agent_id`: its OWN row first, then the family.

    Pure, so the ordering that decides WHICH BODY GETS WOKEN is testable without a DB.

    THE BUG THIS FIXES (cc-irsyad-3 reported it from the inside, bus #22646, after being
    woken 5 consecutive times by traffic addressed to its siblings). The old query threw the
    exact agent_id away and selected `WHERE base_agent_id=%s`, so a wake for cc-irsyad-2
    resolved to whichever of the SIX cc-irsyad instances happened to be live first. The
    per-instance rows exist and are correct — cc-irsyad-3 -> irsyad-prog2, cc-irsyad-4 ->
    irsyad-prog1 — the resolver simply never looked at them.

    It is not merely a misroute, it is a TOKEN LEAK, and it is the one that undoes recycling:
    a lane cleared to free 635k gets re-woken by unrelated sibling traffic and re-inflates on
    messages it will read, find nothing in, and discard. Stood-down lanes are hit hardest
    because they have nothing else to spend context on. prog2 measured five in a few minutes.

    Family fallback is KEPT and deliberately so: an instance with no row of its own, or whose
    own pane is dead, must still reach a live sibling — that is the op#11297 coverage
    property, and narrowing to exact-only would trade this bug for missed wakes. Preference,
    not restriction.
    """
    exact, family = [], []
    for row in rows or []:
        rid, sess = (row[0], row[1]) if len(row) > 1 else (None, row[0])
        if not sess:
            continue
        (exact if rid == agent_id else family).append(sess)
    out, seen = [], set()
    for s in exact + family:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _candidate_sessions(agent_id: str) -> list[str]:
    """Registered tmux sessions for the agent, ITS OWN FIRST then its base family, freshest
    first, with a mild preference for non-offline rows. NOTE (op#11297 #16880): does NOT
    filter on status — an on-demand body that self-marks offline WHILE its pane is alive
    still yields its session; liveness is decided by the pane, not the status field."""
    base = _base_family(agent_id)
    if not _DSN:
        return []
    try:
        with psycopg.connect(_DSN) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT agent_id, tmux_session FROM agent_status "
                "WHERE (agent_id=%s OR base_agent_id=%s) AND tmux_session IS NOT NULL "
                "ORDER BY (status<>'offline') DESC, last_heartbeat DESC NULLS LAST LIMIT 8",
                (agent_id, base))
            return rank_candidates(cur.fetchall(), agent_id)
    except Exception:
        return []


def _first_live_session(candidates, has_session=None) -> str | None:
    """Pure: the first candidate whose pane is actually live. Injectable has_session
    for testing."""
    hs = has_session or _tmux_has_session
    for s in candidates:
        if hs(s):
            return s
    return None


def resolve_tmux_session(agent_id: str) -> str | None:
    """Resolve on the LIVE-SESSION FACT (op#11297 #16880): a registered tmux_session
    whose pane is actually live — DECOUPLED from the status field AND from cwd-intro-
    spection. This closes all three miss-classes at once: offline-while-alive (status
    filtered the row), {*}-scoped fleet-wide roles (repo_scope={*} -> no cwd tokens ->
    the old fallback could never resolve them), and brand-new agents. Coverage is a
    pure function of (registered session AND live pane), never a curated list."""
    live = _first_live_session(_candidate_sessions(agent_id))
    if live:
        return live
    # FALLBACK: cwd introspection — only helps scoped agents from a login shell (not
    # {*} roles, not under the launchd sandbox). Kept for non-launchd scoped callers.
    tokens = _expected_cwd_tokens(agent_id)
    if not tokens:
        return None
    for pane in _live_claude_panes():
        if any(f"/{t}" in pane["cwd"] for t in tokens):
            return pane["session"]
    return None


_LANE_NUDGE = ORCH / "scripts" / "lane_nudge.sh"


def _verified_submit(session: str, signal: str) -> int:
    """Submit `signal` into `session`'s composer via the fleet's ONE verified-submit
    (scripts/lane_nudge.sh) and return its exit code. lane_nudge does the whole
    clear->type->Enter->confirm-it-left-the-composer->extra-Enter retry AND the
    ghost-aware composer guard (never clobbers the lane's own staged next-step).

    Returns lane_nudge's exit code so the caller can report honestly:
      0 = verified submitted (pane entered a working/queued state)
      3 = could NOT verify after retries, OR refused to clobber real staged text
      2 = no such session (raced) / usage
    A shell-out failure (missing script, exec error) maps to a non-zero rc so the
    caller treats it as a failed wake — never a silent success (charter #1)."""
    try:
        proc = subprocess.run(
            [str(_LANE_NUDGE), session, signal],
            check=False, capture_output=True, text=True,
            timeout=120,  # lane_nudge worst case ~3 tries * ~9s + margin
        )
        return proc.returncode
    except Exception as e:  # noqa: BLE001 — never let a submit failure look like success
        logging.getLogger("wingmen.agent_wake").error(
            "verified-submit shell-out failed for %s: %s", session, e)
        return 1


def _pane_busy(session: str) -> bool:
    """True if the lane is mid-turn (don't interrupt; debounce will retry)."""
    try:
        pane = subprocess.check_output(
            ["tmux", "capture-pane", "-t", session, "-p"],
            text=True, stderr=subprocess.DEVNULL)
        return "esc to interrupt" in pane
    except Exception:
        return False


def _load(agent_id: str) -> dict:
    try:
        return json.loads((_WAKE_DIR / f"{agent_id}.json").read_text())
    except Exception:
        return {}


def _save(agent_id: str, state: dict) -> None:
    _WAKE_DIR.mkdir(parents=True, exist_ok=True)
    (_WAKE_DIR / f"{agent_id}.json").write_text(json.dumps(state))


def _read_wakes(agent_id: str) -> list[float]:
    return list(_load(agent_id).get("wakes", []))


def _record_wake(agent_id: str, now: float) -> None:
    state = _load(agent_id)
    state["wakes"] = [t for t in state.get("wakes", []) if now - t < _CAP_WINDOW_S] + [now]
    _save(agent_id, state)


def cap_alert_due(agent_id: str, now: float) -> bool:
    """CAI-RESP-262 fast-follow #1: a cap-hit alert fires at most once per agent
    per cap-window — fail LOUD, not spammy."""
    return (now - _load(agent_id).get("cap_alerted", 0)) >= _CAP_WINDOW_S


def _record_cap_alert(agent_id: str, now: float) -> None:
    state = _load(agent_id)
    state["cap_alerted"] = now
    _save(agent_id, state)


def cap_state(agent_id: str, now: float) -> dict:
    """Pure: classify the wake request against debounce + the 5/5min cap."""
    recent = [t for t in _read_wakes(agent_id) if now - t < _CAP_WINDOW_S]
    if recent and (now - max(recent)) < _DEBOUNCE_S:
        return {"allow": False, "why": "debounced"}
    if len(recent) >= _CAP_LIMIT:
        return {"allow": False, "why": "wake-cap", "cap_hit": True, "count": len(recent)}
    return {"allow": True}


def wake_agent(agent_id: str, reason: str = "", dry_run: bool = False, now: float | None = None) -> dict:
    """Send the fixed wake signal to agent_id's lane. Returns a status dict.

    On a cap hit returns {cap_hit: True} so the caller can fail LOUD (notify the
    operator) per CAI-RESP-259 Q4 — never silently drop. The message itself is
    never lost; the bus is the durable channel and the agent sees it next cycle.
    """
    now = time.time() if now is None else now
    session = resolve_tmux_session(agent_id)
    if not session:
        return {"woke": False, "why": "no live session"}
    gate = cap_state(agent_id, now)
    if not gate["allow"]:
        if gate.get("cap_hit"):
            # Loud-but-not-spammy: tell the caller to alert only once per window.
            gate["alert_due"] = cap_alert_due(agent_id, now)
            if gate["alert_due"]:
                _record_cap_alert(agent_id, now)
        return {"woke": False, "session": session, **gate}
    if _pane_busy(session):
        return {"woke": False, "why": "busy (mid-turn)", "session": session}
    if dry_run:
        return {"woke": False, "why": "dry-run", "session": session, "signal": _SIGNAL}
    # CAI-817: a raw `send-keys -l SIGNAL` + a single unverified `Enter` was the bug —
    # the lone Enter can fail to commit (TUI focus / dim composer), the wake sits
    # STAGED-UNSUBMITTED, the body wedges idle, and we used to still return woke=True
    # (false confidence). Delegate the keystroke submit to the fleet's ONE verified
    # submit (scripts/lane_nudge.sh: ghost-aware composer guard -> C-u clear -> type
    # -> Enter -> confirm-it-left-the-composer -> extra-Enter retry) and REPORT the
    # real outcome. Fail LOUD, never silent (charter #1).
    rc = _verified_submit(session, _SIGNAL)
    if rc == 0:
        _record_wake(agent_id, now)
        return {"woke": True, "session": session, "reason": reason}
    # rc 3 = could not verify submission (staged/wedged/at a dialog) OR the ghost-aware
    # guard REFUSED because the body has its OWN real unsent text (which we must not
    # clobber). Either way the wake did NOT land: report it honestly + flag it, and
    # RECORD the attempt so repeated failures trip the 5/5min cap -> alert_due -> the
    # existing loud telegram escalation (self-wiring dead-man's switch, no policy change).
    logging.getLogger("wingmen.agent_wake").error(
        "wake NOT verified for %s (session=%s, lane_nudge rc=%s) — body may be "
        "staged-unsubmitted or wedged; bus row is durable, escalation path armed.",
        agent_id, session, rc)
    if rc == 3:
        _record_wake(agent_id, now)
        return {"woke": False, "session": session, "why": "submit-unverified",
                "submit_failed": True, "rc": rc}
    # rc 2 = lane_nudge found no such session (raced away after we resolved it); other
    # rc = unexpected. Nothing was delivered into any live pane, so do NOT burn a cap
    # slot — just report the failure.
    return {"woke": False, "session": session,
            "why": "session-gone (raced)" if rc == 2 else f"submit-error rc={rc}",
            "submit_failed": True, "rc": rc}


if __name__ == "__main__":
    import sys
    targets = sys.argv[1:] or ["cc-ihsanos-1", "cc-cosem-1", "cai", "cc-reviewer"]
    for a in targets:
        print(f"{a:16} -> session={resolve_tmux_session(a)!r}  wake(dry)={wake_agent(a, dry_run=True)}")
