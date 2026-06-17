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


def should_auto_wake(
    to_agent: str | None,
    message_type: str,
    requires_response: bool,
    priority: str,
    is_test: bool,
) -> bool:
    """CAI-RESP-259 Q1: wake iff recipient is a live CC WORKER lane or cai (NOT
    cc-orchestrator — it stays operator-attended — and never the operator), not a
    test, not P3, and (requires_response OR an actionable type OR P0/P1 floor)."""
    if is_test or priority == "P3":
        return False
    is_worker = bool(to_agent) and to_agent.startswith("cc-") and to_agent != "cc-orchestrator"
    if not (is_worker or to_agent == "cai"):
        return False
    return requires_response or message_type in _WAKE_TYPES or priority in ("P0", "P1")


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


def _live_claude_panes() -> list[dict]:
    """[{pid, cwd, session}] for every running dangerous-CC lane."""
    out = []
    try:
        pids = subprocess.check_output(
            ["pgrep", "-f", "claude --dangerously-skip-permissions"],
            text=True, stderr=subprocess.DEVNULL).split()
    except Exception:
        return out
    for pid in pids:
        try:
            cwd = subprocess.check_output(
                ["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"],
                text=True, stderr=subprocess.DEVNULL)
            cwd = next((ln[1:] for ln in cwd.splitlines() if ln.startswith("n")), "")
            env = subprocess.check_output(["ps", "eww", pid], text=True, stderr=subprocess.DEVNULL)
            m = re.search(r"TMUX_PANE=(\S+)", env)
            if not m:
                continue
            session = subprocess.check_output(
                ["tmux", "display-message", "-p", "-t", m.group(1), "#{session_name}"],
                text=True, stderr=subprocess.DEVNULL).strip()
            out.append({"pid": pid, "cwd": cwd, "session": session})
        except Exception:
            continue
    return out


def resolve_tmux_session(agent_id: str) -> str | None:
    tokens = _expected_cwd_tokens(agent_id)
    if not tokens:
        return None
    for pane in _live_claude_panes():
        cwd = pane["cwd"]
        if any(f"/{t}" in cwd for t in tokens):
            return pane["session"]
    return None


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
    subprocess.run(["tmux", "send-keys", "-t", session, "-l", _SIGNAL], check=False)
    subprocess.run(["tmux", "send-keys", "-t", session, "Enter"], check=False)
    _record_wake(agent_id, now)
    return {"woke": True, "session": session, "reason": reason}


if __name__ == "__main__":
    import sys
    targets = sys.argv[1:] or ["cc-ihsanos-1", "cc-cosem-1", "cai", "cc-reviewer"]
    for a in targets:
        print(f"{a:16} -> session={resolve_tmux_session(a)!r}  wake(dry)={wake_agent(a, dry_run=True)}")
