"""synthetic_feed_angullia.py — op#36531/36535 condition 2: synthetic traffic
generator for the angullia headless-agent canary.

Moved from scratchpad/headless-spike-op36531/synthetic_feed.py into the
tracked live checkout (bus #87654/#87739) -- same reason as
headless_canary_angullia.py: a lane worktree can be pruned out from under a
live launchd job. TEMPORARY, scoped to this canary; remove with the plist
when the window ends.

Sends ONE small, no-file-write test bus row to cc-angullia per invocation, with a
randomized priority (mixed P1-P3) and a tiny one-line task. Driven by its own
launchd StartInterval job (dev.wingmen.headless-canary-angullia-feed), independent
of any single conversational session's lifetime. Every send is appended to
state/synthetic_feed_angullia_sent.jsonl (id, priority, sent_at, task kind) --
cross-referenced against the supervisor's own canary log by msg_id, this is
exactly condition 5's missed/duplicate reconciliation measurement.
"""
import json
import random
import subprocess
import sys
import time
from pathlib import Path

_ORCH_DIR = Path(__file__).resolve().parent.parent
SENT_LOG = str(_ORCH_DIR / "state" / "synthetic_feed_angullia_sent.jsonl")

# Tiny, strictly no-file-write, no-command tasks (token cost stays trivial, and
# nothing here can touch angullia's real repo state).
TASKS = [
    ("instant", "Reply with exactly the single word: SYN-{tag}"),
    ("instant", "What is 2+2? Reply with just the number."),
    ("short-delay", "Count slowly from 1 to 3, one short line each, nothing else, labelled SYN-{tag}."),
]

PRIORITIES = ["P1", "P2", "P2", "P2", "P3"]  # mixed, P2-weighted (routine), some P1/P3


def send_one(tag: str):
    priority = random.choice(PRIORITIES)
    kind, template = random.choice(TASKS)
    body = template.format(tag=tag)
    subject = f"op#36531 synthetic feed #{tag} ({kind})"
    proc = subprocess.run(
        [
            str(_ORCH_DIR / ".venv" / "bin" / "python3"),
            str(_ORCH_DIR / "scripts" / "bus_send.py"),
            "--to", "cc-angullia", "--type", "update",
            "--subject", subject, "--priority", priority,
        ],
        input=body + "\n", capture_output=True, text=True,
        cwd=str(_ORCH_DIR),
    )
    msg_id = None
    for line in (proc.stdout + proc.stderr).splitlines():
        if line.startswith("SENT id="):
            msg_id = int(line.split("id=")[1].split()[0])
    record = {
        "tag": tag, "priority": priority, "kind": kind, "msg_id": msg_id,
        "sent_at": time.time(), "ok": proc.returncode == 0,
        "stderr": proc.stderr[-300:] if proc.returncode != 0 else None,
    }
    with open(SENT_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")
    return record


def send_mid_turn_pair(tag: str):
    """Send a 'short-delay' task, then IMMEDIATELY a second instant task, so the
    second row genuinely arrives while the first is likely still being processed
    (the real 'some arriving mid-turn' requirement) rather than relying on luck."""
    r1 = send_one(f"{tag}a")
    r2 = send_one(f"{tag}b")
    return [r1, r2]


if __name__ == "__main__":
    tag = sys.argv[1] if len(sys.argv) > 1 else str(int(time.time()))
    # Roughly 1 in 4 invocations fires a deliberate mid-turn pair instead of a
    # single message, per condition 2's "some arriving mid-turn" requirement.
    if random.random() < 0.25:
        results = send_mid_turn_pair(tag)
    else:
        results = [send_one(tag)]
    for r in results:
        print(json.dumps(r))
