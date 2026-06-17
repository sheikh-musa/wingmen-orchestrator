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
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
_WAKE_DIR = ORCH / "scripts" / ".agent_wake"
_DEBOUNCE_S = 45
_SIGNAL = "[wake] new inbox item — read your agent_messages inbox and act"
_SUBTAG_RE = re.compile(r"-\d+$")


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


def _debounced(agent_id: str) -> bool:
    f = _WAKE_DIR / f"{agent_id}.json"
    try:
        last = json.loads(f.read_text()).get("last_wake", 0)
    except Exception:
        return False
    return (time.time() - last) < _DEBOUNCE_S


def _record_wake(agent_id: str) -> None:
    _WAKE_DIR.mkdir(parents=True, exist_ok=True)
    (_WAKE_DIR / f"{agent_id}.json").write_text(json.dumps({"last_wake": time.time()}))


def wake_agent(agent_id: str, reason: str = "", dry_run: bool = False) -> dict:
    """Send the fixed wake signal to agent_id's lane. Returns a status dict."""
    session = resolve_tmux_session(agent_id)
    if not session:
        return {"woke": False, "why": "no live session"}
    if _debounced(agent_id):
        return {"woke": False, "why": "debounced", "session": session}
    if _pane_busy(session):
        return {"woke": False, "why": "busy (mid-turn)", "session": session}
    if dry_run:
        return {"woke": False, "why": "dry-run", "session": session, "signal": _SIGNAL}
    subprocess.run(["tmux", "send-keys", "-t", session, "-l", _SIGNAL], check=False)
    subprocess.run(["tmux", "send-keys", "-t", session, "Enter"], check=False)
    _record_wake(agent_id)
    return {"woke": True, "session": session, "reason": reason}


if __name__ == "__main__":
    import sys
    targets = sys.argv[1:] or ["cc-ihsanos-1", "cc-cosem-1", "cai", "cc-reviewer"]
    for a in targets:
        print(f"{a:16} -> session={resolve_tmux_session(a)!r}  wake(dry)={wake_agent(a, dry_run=True)}")
