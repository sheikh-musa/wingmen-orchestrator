"""Synthetic replay of cc-fleet-health's 5 real-leak SHAPES (bus #48685/#48695), with
masked/fake synthetic values only -- never real secrets. Each shape must be either
BLOCKED (PreToolUse guard refuses before execution) or CAUGHT (PostToolUse scanner
redacts it after the fact). Run after any change to secrets_transcript_guard.py,
secrets_output_scanner.py, or secret_shape_patterns.py.
"""
import json
import subprocess
import sys
import tempfile
import os

GUARD = "scripts/hooks/secrets_transcript_guard.py"
SCANNER = "scripts/hooks/secrets_output_scanner.py"

FAKE_DSN = "postgres://orchuser:FakeSyntheticPassNotReal123@db.example.internal:5432/orch"
FAKE_BEARER = "Bearer FakeSyntheticTokenNotReal1234567890"
FAKE_BOT_TOKEN = "123456789:AAFakeSyntheticTokenNotReal123456"


def run_guard(tool_name, tool_input):
    payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
    return subprocess.run([sys.executable, GUARD], input=payload, text=True, capture_output=True)


def run_scanner(tool_name, tool_input, tool_response, transcript_path):
    payload = json.dumps({
        "tool_name": tool_name, "tool_input": tool_input,
        "tool_response": tool_response, "transcript_path": transcript_path,
    })
    # bus #48740: this is a synthetic replay, not a real agent session -- tag the
    # scanner's auto-redact page as [DEMO]/P3 so it doesn't dilute real P1 pages.
    env = dict(os.environ, SECRETS_SCANNER_DEMO="1")
    return subprocess.run([sys.executable, SCANNER], input=payload, text=True, capture_output=True, env=env)


results = []

# SHAPE 1 -- literal password DSN typed inline into psql (~84x, mostly cai)
r = run_guard("Bash", {"command": f'psql "{FAKE_DSN}" -c "select 1"'})
results.append(("shape1: literal DSN in psql", r.returncode == 2, r.stderr))

# SHAPE 2 -- literal Bearer token typed inline into curl (~55x, cosem-adcda)
r = run_guard("Bash", {"command": f'curl -H "Authorization: {FAKE_BEARER}" https://api.example.com/x'})
results.append(("shape2: literal Bearer token in curl", r.returncode == 2, r.stderr))

# SHAPE 2b -- literal bot token inline in a curl URL
r = run_guard("Bash", {"command": f'curl https://api.telegram.org/bot{FAKE_BOT_TOKEN}/sendMessage'})
results.append(("shape2b: literal bot token in curl URL", r.returncode == 2, r.stderr))

# SHAPE 3 -- source .env && env (env-dump trigger, pre-existing Rule B coverage)
r = run_guard("Bash", {"command": "source .env && env"})
results.append(("shape3: source .env && env", r.returncode == 2, r.stderr))

# SHAPE 4 -- Edit tool on .env (pre-existing PATH_ONLY_TOOLS coverage)
r = run_guard("Edit", {"file_path": ".env", "old_string": "a", "new_string": "b"})
results.append(("shape4: Edit tool on .env", r.returncode == 2, r.stderr))

# SHAPE 5 -- ToolResult echoing a secret (scanner must CATCH/redact, not guard-block --
# this is after-the-fact by design, the command itself ran before PostToolUse fires)
with tempfile.TemporaryDirectory() as d:
    transcript = os.path.join(d, "session.jsonl")
    with open(transcript, "w") as f:
        f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "some_script_that_leaks_in_stdout.sh"}}
        ]}}) + "\n")
        f.write(json.dumps({"type": "tool_result", "toolUseResult": {"output": FAKE_DSN}}) + "\n")
    r = run_scanner("Bash", {"command": "some_script_that_leaks_in_stdout.sh"}, FAKE_DSN, transcript)
    with open(transcript) as f:
        after = f.read()
    caught = "secret-shaped content" in r.stderr and FAKE_DSN not in after and "REDACTED" in after
    results.append(("shape5: ToolResult echoes a secret -> scanner redacts on disk", caught, r.stderr))

passed = sum(1 for _, ok, _ in results if ok)
for label, ok, detail in results:
    print(f"[{'OK' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"    detail: {detail!r}")

print(f"\n{passed}/{len(results)} shapes blocked/caught as expected", file=sys.stderr)
if passed != len(results):
    sys.exit(1)
