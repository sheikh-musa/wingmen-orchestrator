"""PART B of the lane prod-data wall (op#29348).

Tests scripts/hooks/lane_prod_key_guard.py: a worker LANE is refused any Bash or
Read tool call that reaches the cosem-adcda PROD service-account key, or that
re-points ADC at prod creds (GOOGLE_APPLICATION_CREDENTIALS= / --key-file). The
console (ORCH_BODY_ROLE=console, no CC_BASE_AGENT_ID) and CI (no
CC_BASE_AGENT_ID) are unaffected.

RIGHT-REASON asserts (the TDD warning): every DENY asserts exit 2 AND the
guard's OWN refusal marker ("lane_prod_key_guard") in stderr — never just a
non-zero exit (which a missing module / crash would also give). Every ALLOW
asserts exit 0 so the allow path is proven to run.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).parent.parent / "scripts" / "hooks" / "lane_prod_key_guard.py"

_LANE_ENV = {"CC_BASE_AGENT_ID": "cc-cosem-platform"}
_KEY = "~/.wingmen/keys/cosem-sa.json"
_KEY_ABS = "/Users/sheikhmusa/.wingmen/keys/cosem-sa.json"
_KEY_HOME = "$HOME/.wingmen/keys/cosem-sa.json"
_KEY_REL = ".wingmen/keys/cosem-sa.json"
_BOT = "~/.wingmen/keys/cosem-adcda-bot.env"


def run_hook(tool_name, tool_input, env=None):
    payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
    full_env = dict(os.environ)
    full_env.pop("CC_BASE_AGENT_ID", None)
    full_env.pop("ORCH_BODY_ROLE", None)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload, text=True, capture_output=True, env=full_env,
    )


def assert_denied(tool_name, tool_input, env=_LANE_ENV):
    r = run_hook(tool_name, tool_input, env=env)
    assert r.returncode == 2, f"expected DENY for {tool_input!r}, got exit {r.returncode}, stderr={r.stderr!r}"
    # RIGHT reason: the guard's own refusal, not an incidental crash.
    assert "lane_prod_key_guard" in r.stderr, r.stderr


def assert_allowed(tool_name, tool_input, env=_LANE_ENV):
    r = run_hook(tool_name, tool_input, env=env)
    assert r.returncode == 0, f"expected ALLOW for {tool_input!r}, got exit {r.returncode}, stderr={r.stderr!r}"


# ── DENY: worker lane reaching the prod SA key ───────────────────────────────

def test_lane_bash_cat_prod_key_denied():
    assert_denied("Bash", {"command": f"cat {_KEY}"})


def test_lane_bash_cat_prod_key_abs_denied():
    assert_denied("Bash", {"command": f"cat {_KEY_ABS}"})


def test_lane_bash_cat_prod_key_home_var_denied():
    assert_denied("Bash", {"command": f"cat {_KEY_HOME}"})


def test_lane_bash_cat_prod_key_relative_denied():
    assert_denied("Bash", {"command": f"cat {_KEY_REL}"})


def test_lane_bash_cat_prod_bot_env_denied():
    assert_denied("Bash", {"command": f"cat {_BOT}"})


def test_lane_read_prod_key_denied():
    assert_denied("Read", {"file_path": _KEY})


def test_lane_read_prod_key_abs_denied():
    assert_denied("Read", {"file_path": _KEY_ABS})


def test_lane_bash_export_gac_denied():
    assert_denied("Bash", {"command": f"export GOOGLE_APPLICATION_CREDENTIALS={_KEY}"})


def test_lane_bash_export_gac_any_path_denied():
    # Condition (ii): the GAC= mechanism itself, regardless of the path after it.
    assert_denied("Bash", {"command": "export GOOGLE_APPLICATION_CREDENTIALS=/tmp/whatever.json"})


def test_lane_bash_keyfile_flag_cosem_denied():
    assert_denied("Bash", {"command": f"gcloud auth activate-service-account --key-file {_KEY}"})


def test_lane_bash_keyfile_flag_any_path_denied():
    # Condition (ii): --key-file activation is refused for a lane regardless of path.
    assert_denied("Bash", {"command": "gcloud auth activate-service-account --key-file=/tmp/x.json"})


def test_lane_read_prod_key_via_symlink_denied(tmp_path):
    # A symlink whose own spelling has no `.wingmen/keys/` but points at a file
    # named like a prod key under the keys dir — resolved via realpath.
    keys = tmp_path / ".wingmen" / "keys"
    keys.mkdir(parents=True)
    real = keys / "cosem-sa.json"
    real.write_text("{}")
    link = tmp_path / "innocuous.json"
    link.symlink_to(real)
    # Point the guard's keys dir at our tmp tree via HOME override.
    env = dict(_LANE_ENV)
    env["HOME"] = str(tmp_path)
    assert_denied("Read", {"file_path": str(link)}, env=env)


# ── ALLOW: console / CI unaffected ───────────────────────────────────────────

def test_console_body_cat_prod_key_allowed():
    # Console: ORCH_BODY_ROLE=console, NO CC_BASE_AGENT_ID -> not a worker lane.
    assert_allowed("Bash", {"command": f"cat {_KEY}"}, env={"ORCH_BODY_ROLE": "console"})


def test_no_base_agent_id_ci_allowed():
    # CI / bare shell: no CC_BASE_AGENT_ID at all -> allowed.
    assert_allowed("Read", {"file_path": _KEY}, env={})


def test_console_export_gac_allowed():
    # The console's own config-write / prod-ops path stays UNAFFECTED.
    assert_allowed(
        "Bash", {"command": f"export GOOGLE_APPLICATION_CREDENTIALS={_KEY}"},
        env={"ORCH_BODY_ROLE": "console"},
    )


# ── ALLOW: worker lane, no over-block ────────────────────────────────────────

def test_lane_unrelated_path_allowed():
    assert_allowed("Bash", {"command": "cat /etc/hosts"})


def test_lane_read_unrelated_path_allowed():
    assert_allowed("Read", {"file_path": "~/wingmen/orchestrator/README.md"})


def test_lane_non_cosem_keyfile_in_keys_dir_allowed():
    # A non-prod token file under keys (e.g. an OAuth token) is NOT a cosem prod
    # SA key -> this guard must not over-block it (token_file_guard owns those).
    assert_allowed("Bash", {"command": "cat ~/.wingmen/keys/syed-oauth-token"})


def test_lane_benign_gcloud_without_keyfile_allowed():
    assert_allowed("Bash", {"command": "gcloud config configurations list"})


def test_lane_other_tool_out_of_scope_allowed():
    assert_allowed("Grep", {"pattern": "cosem", "path": _KEY})


# ── DENY: fail-closed on unreadable input (worker lane) ──────────────────────

def test_lane_unparseable_stdin_fail_closed():
    r = subprocess.run(
        [sys.executable, str(HOOK)],
        input="not json", text=True, capture_output=True,
        env={**{k: v for k, v in os.environ.items()}, **_LANE_ENV},
    )
    assert r.returncode == 2, r.stderr
    assert "lane_prod_key_guard" in r.stderr
