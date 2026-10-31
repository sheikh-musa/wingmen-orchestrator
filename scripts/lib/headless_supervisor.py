"""headless_supervisor.py — minimal headless-agent supervisor (op#36531/36535).

Moved here from scratchpad/headless-spike-op36531/supervisor.py (bus #87654/#87739):
the canary was running out of a lane worktree that can be pruned out from under a
live launchd job, so this -- the actual Adapter/Supervisor library code, not the
angullia-specific entrypoint -- lives in the tracked live checkout now.

Demonstrates the adapter interface the headless-spike design doc (section 3)
describes, backed by the raw-CLI mechanics proven in that spike (items 1-7 +
the hand-replicated control_request wire format for interrupt).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_ORCH_DIR = Path(__file__).resolve().parent.parent.parent
if str(_ORCH_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCH_DIR))
from scripts.lib.lane_token_resolver import resolve_lane_token_path  # noqa: E402
from scripts.lib.token_file_guard import check as token_check  # noqa: E402


# ---------------------------------------------------------------------------
# Adapter interface (design doc section 3, "Harness interface (ours, harness-
# agnostic)"). Only the Claude-CLI adapter is implemented here; the OpenHands
# adapter proven separately (no-lockin-proof-and-inventory.md) would implement
# the same four methods, minus interrupt (flagged gap, needs the ACP path).
# ---------------------------------------------------------------------------
class Adapter:
    def start(self, cwd: str, boot_prompt: Optional[str] = None, resume_session_id: Optional[str] = None) -> None:
        raise NotImplementedError

    def send(self, text: str) -> None:
        raise NotImplementedError

    def interrupt(self) -> dict:
        raise NotImplementedError

    def events(self):
        """Yields parsed JSON event dicts until the current turn's result event."""
        raise NotImplementedError

    def health(self) -> dict:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


def _lane_env(lane_name: str) -> dict:
    """Claude-Max-subscription path: resolve the lane's own pinned OAuth token."""
    path = resolve_lane_token_path(lane_name, orch_dir=str(_ORCH_DIR))
    ok, reason = token_check(path)
    if not ok:
        raise RuntimeError(reason)
    with open(path, "r", encoding="utf-8") as fh:
        tok = fh.read().strip()
    env = dict(os.environ)
    env["CLAUDE_CODE_OAUTH_TOKEN"] = tok
    for v in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        env.pop(v, None)
    return env


_SENTINEL = "___ROUTED_ENV_START___"


def _routed_env(model: str, agent_id: str) -> dict:
    """Non-Anthropic routed path (e.g. deepseek-flash): drive the SAME
    scripts/lib/provider_routing.sh every real lane uses (apply_provider_routing),
    via bash (its indirect-expansion syntax needs bash, not zsh/sh). Captures the
    resulting env in-process; never printed or logged anywhere a transcript
    could pick it up."""
    orch_dir = str(_ORCH_DIR)
    venv_py = str(_ORCH_DIR / ".venv" / "bin" / "python3")
    script = f'''
source {orch_dir}/scripts/lib/provider_routing.sh
apply_provider_routing "{model}" "{orch_dir}" "{venv_py}" "{agent_id}" || exit 1
echo "{_SENTINEL}"
env -0
'''
    proc = subprocess.run(["bash", "-c", script], capture_output=True, env=dict(os.environ))
    if proc.returncode != 0:
        raise RuntimeError(f"apply_provider_routing failed for model={model} agent_id={agent_id}: "
                            f"{proc.stderr.decode(errors='replace')[-500:]}")
    out = proc.stdout.decode(errors="replace")
    marker = out.find(_SENTINEL)
    if marker == -1:
        raise RuntimeError("provider routing: sentinel not found, cannot trust partial env capture")
    env_blob = out[marker + len(_SENTINEL):].lstrip("\n")
    env = {}
    for pair in env_blob.split("\0"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            env[k] = v
    return env


class ClaudeCliAdapter(Adapter):
    """Wraps a persistent `claude -p --input-format stream-json --output-format
    stream-json` process. Proven mechanics: raw_cli_items34.py (queue + interrupt),
    scratch-resume-test (kill -9 + --resume), scratch-resume-test turn7 (/compact).
    """

    def __init__(self, lane_name: str, agent_id: Optional[str] = None, model: Optional[str] = None):
        self.lane_name = lane_name
        self.agent_id = agent_id
        self.model = model  # None = Claude Max subscription path; else routed (e.g. "deepseek-flash")
        self.proc: Optional[subprocess.Popen] = None
        self._req_counter = 0
        self.session_id: Optional[str] = None

    def start(self, cwd, boot_prompt=None, resume_session_id=None):
        os.makedirs(cwd, exist_ok=True)
        args = [
            "claude", "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", "bypassPermissions",
            "--dangerously-skip-permissions",
        ]
        if resume_session_id:
            args += ["--resume", resume_session_id]
        if self.model:
            env = _routed_env(self.model, self.agent_id or self.lane_name)
        else:
            env = _lane_env(self.lane_name)
        self.proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, cwd=cwd, text=True, bufsize=1,
        )
        if boot_prompt:
            self.send(boot_prompt)

    def send(self, text: str) -> None:
        line = json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}) + "\n"
        self.proc.stdin.write(line)
        self.proc.stdin.flush()

    def interrupt(self) -> dict:
        self._req_counter += 1
        req_id = f"sup_{self._req_counter}"
        line = json.dumps({"type": "control_request", "request_id": req_id, "request": {"subtype": "interrupt"}}) + "\n"
        self.proc.stdin.write(line)
        self.proc.stdin.flush()
        for ev in self.events(stop_on_result=False):
            if ev.get("type") == "control_response" and ev.get("response", {}).get("request_id") == req_id:
                return ev
        return {}

    def events(self, stop_on_result: bool = True):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("session_id"):
                self.session_id = d["session_id"]
            yield d
            if stop_on_result and d.get("type") == "result":
                return

    def health(self) -> dict:
        alive = self.proc is not None and self.proc.poll() is None
        return {"alive": alive, "session_id": self.session_id}

    def stop(self) -> None:
        if self.proc:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            self.proc.terminate()


# ---------------------------------------------------------------------------
# Minimal supervisor: poll agent_messages (30s backstop, matches design doc
# section 3 -- real LISTEN/NOTIFY is week-2 scope, not this spike), deliver
# each unread row as one turn, log every event, stamp read_at on success.
# ---------------------------------------------------------------------------
@dataclass
class SupervisorLog:
    path: str
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def write(self, record: dict):
        record["_logged_at"] = time.time()
        with self._lock:
            with open(self.path, "a") as f:
                f.write(json.dumps(record) + "\n")


class Supervisor:
    def __init__(self, agent_id: str, lane_name: str, cwd: str, db_dsn: str, log_path: str,
                 session_state_path: Optional[str] = None, model: Optional[str] = None):
        self.agent_id = agent_id
        self.lane_name = lane_name
        self.cwd = cwd
        self.db_dsn = db_dsn
        self.adapter = ClaudeCliAdapter(lane_name, agent_id=agent_id, model=model)
        self.log = SupervisorLog(log_path)
        self._started = False
        # Session persistence across restarts (incl. a launchd KeepAlive relaunch
        # after kill -9) -- without this every restart would start a fresh
        # session and silently lose the lane's accumulated context.
        self.session_state_path = session_state_path

    def _read_persisted_session_id(self) -> Optional[str]:
        if self.session_state_path and os.path.exists(self.session_state_path):
            try:
                with open(self.session_state_path) as f:
                    sid = f.read().strip()
                    return sid or None
            except OSError:
                return None
        return None

    def _persist_session_id(self):
        if self.session_state_path and self.adapter.session_id:
            with open(self.session_state_path, "w") as f:
                f.write(self.adapter.session_id)

    def _db(self):
        import psycopg
        return psycopg.connect(self.db_dsn)

    def poll_unread(self):
        with self._db() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, from_agent, subject, body, priority, created_at FROM agent_messages "
                "WHERE to_agent = %s AND read_at IS NULL ORDER BY id ASC",
                (self.agent_id,),
            )
            return cur.fetchall()

    def mark_read(self, msg_id: int):
        with self._db() as conn, conn.cursor() as cur:
            cur.execute("UPDATE agent_messages SET read_at = now() WHERE id = %s", (msg_id,))
            conn.commit()

    def deliver_one(self, row):
        msg_id, from_agent, subject, body, priority, created_at = row
        text = f"[bus #{msg_id} from {from_agent}, priority {priority}] {subject}\n\n{body}"
        t0 = time.time()
        self.log.write({"kind": "deliver_start", "msg_id": msg_id, "priority": priority})
        self.adapter.send(text)
        result_event = None
        for ev in self.adapter.events(stop_on_result=True):
            self.log.write({"kind": "runner_event", "msg_id": msg_id, "event_type": ev.get("type"), "raw": ev})
            if ev.get("type") == "result":
                result_event = ev
        latency = time.time() - t0
        self.log.write({"kind": "deliver_done", "msg_id": msg_id, "latency_s": latency,
                        "result": (result_event or {}).get("result")})
        self.mark_read(msg_id)
        return result_event

    def run_once(self):
        """One poll cycle: deliver every currently-unread row, in order."""
        if not self._started:
            resume_id = self._read_persisted_session_id()
            self.adapter.start(self.cwd, resume_session_id=resume_id)
            self.log.write({"kind": "start", "resumed_session_id": resume_id, "lane": self.lane_name})
            self._started = True
        rows = self.poll_unread()
        self.log.write({"kind": "poll", "unread_count": len(rows)})
        results = []
        for row in rows:
            results.append(self.deliver_one(row))
            self._persist_session_id()
        return results

    def run_forever(self, poll_interval_s: int = 30):
        """30s-poll backstop loop (design doc section 3) -- real LISTEN/NOTIFY is
        week-2+ scope, not this canary."""
        self.log.write({"kind": "run_forever_start", "pid": os.getpid()})
        while True:
            try:
                self.run_once()
            except Exception as e:
                self.log.write({"kind": "poll_error", "error": repr(e)})
            time.sleep(poll_interval_s)

    def health(self):
        return self.adapter.health()

    def stop(self):
        self.adapter.stop()
