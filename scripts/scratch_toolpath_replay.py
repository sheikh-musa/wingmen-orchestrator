"""Synthetic tool-path replay for the bus #48639/#48642 delta (Edit/MultiEdit/Write/
NotebookEdit secret-path coverage). NOT the real 1200-command corpus -- that corpus is
Bash command TEXT only (built before the tool-path gap existed) and contains no
historical Edit/Write/NotebookEdit tool calls to replay. Per orch-console's instruction
(bus #48639), I'm not opening real transcripts to mine for that data -- that's
cc-fleet-health's redaction-sweep job, not mine. This is a representative synthetic
corpus covering the realistic shapes an agent's Edit/Write/NotebookEdit/Read calls take,
built to demonstrate the new path-only gating broadly, not just the one incident shape.
"""
import json
import subprocess
import sys

HOOK = "scripts/hooks/secrets_transcript_guard.py"

CASES = [
    # (tool_name, tool_input, expect_block, label)
    ("Edit", {"file_path": "/Users/sheikhmusa/wingmen/orchestrator/.env",
              "old_string": "BOT_TOKEN=x", "new_string": "BOT_TOKEN=y"}, True, "the 14:37Z incident shape"),
    ("Edit", {"file_path": "orchestrator/.env.local", "old_string": "a", "new_string": "b"}, True, ".env.local relative path"),
    ("MultiEdit", {"file_path": "/Users/sheikhmusa/.wingmen/private/write_dsn.env",
                   "edits": [{"old_string": "a", "new_string": "b"}]}, True, "write_dsn.env via MultiEdit"),
    ("Write", {"file_path": "/dev/shm/wingmen-secrets/.env", "content": "X=1"}, True, "write under tmpfs secrets dir"),
    ("Write", {"file_path": "~/.wingmen/keys/some_key", "content": "x"}, True, "tilde path under keys dir"),
    ("Edit", {"file_path": "/home/gazzai/.ssh/gzb_to_mini", "old_string": "a", "new_string": "b"}, True, "ssh private key edit"),
    ("Edit", {"file_path": "/home/gazzai/.ssh/config", "old_string": "a", "new_string": "b"}, True, "any file under ~/.ssh/ (conservative)"),
    ("Write", {"file_path": "/Users/sheikhmusa/creds/irsyad-service-account.json", "content": "{}"}, True, "service-account json"),
    ("NotebookEdit", {"notebook_path": "/Users/sheikhmusa/wingmen/orchestrator/.env", "new_source": "x"}, True, "NotebookEdit on .env (the pre-existing gap)"),
    ("Read", {"file_path": "/Users/sheikhmusa/.wingmen/private/write_dsn.env"}, True, "Read on write_dsn.env"),
    # must stay ALLOWED -- ordinary files, not secret-shaped
    ("Edit", {"file_path": "/Users/sheikhmusa/wingmen/orchestrator/scripts/foo.py", "old_string": "a", "new_string": "b"}, False, "ordinary source file"),
    ("Write", {"file_path": "/Users/sheikhmusa/wingmen/orchestrator/reports/notes.md", "content": "hi"}, False, "ordinary report file"),
    ("MultiEdit", {"file_path": "/Users/sheikhmusa/wingmen/orchestrator/STATUS.md",
                   "edits": [{"old_string": "a", "new_string": "b"}]}, False, "STATUS.md"),
    ("Write", {"file_path": "/Users/sheikhmusa/wingmen/orchestrator/scripts/env_set.sh", "content": "#!/bin/bash"}, False, "editing env_set.sh itself (not a secret file)"),
    ("Edit", {"file_path": "/Users/sheikhmusa/wingmen/orchestrator/.env.example", "old_string": "a", "new_string": "b"}, True, ".env.example (conservative -- SECRET_FILE_RE matches any .env.* suffix)"),
    ("Read", {"file_path": "/Users/sheikhmusa/wingmen/orchestrator/README.md"}, False, "Read on README"),
    ("NotebookEdit", {"notebook_path": "/Users/sheikhmusa/wingmen/orchestrator/notebooks/analysis.ipynb", "new_source": "x"}, False, "ordinary notebook"),
    ("Edit", {"file_path": "/Users/sheikhmusa/wingmen/orchestrator/.ssh-notes.md", "old_string": "a", "new_string": "b"}, False, "filename containing .ssh as substring, not under the dir"),
]

passed = 0
failed = []
for tool_name, tool_input, expect_block, label in CASES:
    payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
    r = subprocess.run([sys.executable, HOOK], input=payload, text=True, capture_output=True)
    blocked = r.returncode == 2
    ok = blocked == expect_block
    passed += ok
    status = "OK" if ok else "MISMATCH"
    print(f"[{status}] {tool_name} expect_block={expect_block} got_block={blocked} -- {label}")
    if not ok:
        failed.append(label)

print(f"\n{passed}/{len(CASES)} as expected", file=sys.stderr)
if failed:
    print("MISMATCHES:", failed, file=sys.stderr)
    sys.exit(1)
