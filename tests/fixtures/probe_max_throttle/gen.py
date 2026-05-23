"""Generate probe_max_throttle fixture: 30 jsonls matching the historical
probe daemon pattern — 54KB no-op sessions, prompt='ok', 300s exact cadence.

Per CAI-RESP-164 R1: fixture must produce 3-of-3 content-shape match → SIGTERM.

Why 30 (not 10): signal_b requires span > 7200s. 10 sessions at 300s span
only 2700s (fails). 30 sessions at 300s span 8700s (passes). signal_a +
signal_c sample first 10 paths; signal_b uses all paths for span.
"""
import json
import os
from pathlib import Path

OUT = Path(__file__).parent
BASE_TIME = 1700000000.0
SIZE = 54 * 1024


def make_session(idx: int):
    msgs = [
        {"type": "user", "message": {"role": "user", "content": "ok"}},
        {"type": "assistant", "message": {"role": "assistant", "content": "ok"}},
    ]
    body = "\n".join(json.dumps(m) for m in msgs)
    pad = SIZE - len(body) - 2
    if pad > 0:
        body += "\n" + ("x" * pad)
    p = OUT / f"session-{idx:02d}.jsonl"
    p.write_text(body + "\n")
    mt = BASE_TIME + idx * 300
    os.utime(p, (mt, mt))


if __name__ == "__main__":
    for i in range(30):
        make_session(i)
    print(f"generated 30 probe fixtures in {OUT}")
