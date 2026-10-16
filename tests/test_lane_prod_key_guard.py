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

# Well-known per-user ADC paths (the reviewer's hole): Node's ADC falls back
# here when GOOGLE_APPLICATION_CREDENTIALS is unset, and IGNORES CLOUDSDK_CONFIG.
# A worker lane must not reach these either.
_ADC_JSON = "~/.config/gcloud/application_default_credentials.json"
_ADC_JSON_ABS = "/Users/sheikhmusa/.config/gcloud/application_default_credentials.json"
_ADC_JSON_HOME = "$HOME/.config/gcloud/application_default_credentials.json"
_CRED_DB = "~/.config/gcloud/credentials.db"
_ACCESS_DB = "~/.config/gcloud/access_tokens.db"
_LEGACY = "~/.config/gcloud/legacy_credentials/cosem-adcda-bot@cosem-adcda-cb6d9.iam.gserviceaccount.com/adc.json"


def run_hook(tool_name, tool_input, env=None):
    payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
    full_env = dict(os.environ)
    full_env.pop("CC_BASE_AGENT_ID", None)
    full_env.pop("ORCH_BODY_ROLE", None)
    # Deterministic baseline for the env-STATE check (op#29348 round 2): the
    # invoking shell's OWN ambient GOOGLE_APPLICATION_CREDENTIALS/CLOUDSDK_CONFIG
    # must never leak into a test that doesn't explicitly set them.
    full_env.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    full_env.pop("CLOUDSDK_CONFIG", None)
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


# ── DENY: worker lane reaching the well-known per-user ADC (reviewer's hole) ──

def test_lane_read_wellknown_adc_json_denied():
    assert_denied("Read", {"file_path": _ADC_JSON})


def test_lane_read_wellknown_adc_json_abs_denied():
    assert_denied("Read", {"file_path": _ADC_JSON_ABS})


def test_lane_bash_cat_wellknown_adc_json_denied():
    assert_denied("Bash", {"command": f"cat {_ADC_JSON}"})


def test_lane_bash_cat_wellknown_adc_json_home_var_denied():
    assert_denied("Bash", {"command": f"cat {_ADC_JSON_HOME}"})


def test_lane_read_wellknown_credentials_db_denied():
    assert_denied("Read", {"file_path": _CRED_DB})


def test_lane_bash_cat_credentials_db_denied():
    assert_denied("Bash", {"command": f"sqlite3 {_CRED_DB} '.tables'"})


def test_lane_read_wellknown_access_tokens_db_denied():
    assert_denied("Read", {"file_path": _ACCESS_DB})


def test_lane_bash_cat_access_tokens_db_denied():
    assert_denied("Bash", {"command": f"cat {_ACCESS_DB}"})


def test_lane_read_wellknown_legacy_credentials_denied():
    # legacy_credentials/** — any file under the dir is refused.
    assert_denied("Read", {"file_path": _LEGACY})


def test_lane_bash_cat_legacy_credentials_denied():
    assert_denied("Bash", {"command": f"cat {_LEGACY}"})


def test_lane_bash_cloudsdk_config_assignment_denied():
    # A lane re-pointing CLOUDSDK_CONFIG back at the per-user prod config is the
    # mechanism we close — refused regardless of the path after it.
    assert_denied("Bash", {"command": "CLOUDSDK_CONFIG=~/.config/gcloud gcloud config list"})


def test_lane_bash_export_cloudsdk_config_any_path_denied():
    assert_denied("Bash", {"command": "export CLOUDSDK_CONFIG=/tmp/whatever"})


def test_lane_read_wellknown_adc_via_symlink_denied(tmp_path):
    # Symlink whose own spelling has no `.config/gcloud/` but points at the
    # well-known ADC under HOME — resolved via realpath.
    gdir = tmp_path / ".config" / "gcloud"
    gdir.mkdir(parents=True)
    real = gdir / "application_default_credentials.json"
    real.write_text("{}")
    link = tmp_path / "innocuous.json"
    link.symlink_to(real)
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


def test_console_read_wellknown_adc_allowed():
    # The console legitimately runs prod ops off the per-user ADC.
    assert_allowed("Read", {"file_path": _ADC_JSON}, env={"ORCH_BODY_ROLE": "console"})


def test_console_cloudsdk_config_assignment_allowed():
    assert_allowed(
        "Bash", {"command": "export CLOUDSDK_CONFIG=~/.config/gcloud"},
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


def test_lane_gcloud_configurations_dir_not_overblocked():
    # The per-lane gcloud config dir (Part A) lives under .config/gcloud-style
    # paths but is NOT a credential file — the guard must not over-block a lane
    # reading its OWN seeded config.
    assert_allowed("Read", {"file_path": "~/.config/gcloud/configurations/config_default"})


def test_lane_bash_mention_cloudsdk_config_var_read_allowed():
    # Reading the var (not assigning it) is fine — only the `=` assignment that
    # re-points it is the mechanism we close.
    assert_allowed("Bash", {"command": "echo $CLOUDSDK_CONFIG"})


def test_lane_other_tool_out_of_scope_allowed():
    assert_allowed("Grep", {"pattern": "cosem", "path": _KEY})


# ── DENY: ambient ADC env state at tool-call time (op#29348 reviewer's hole,
# round 2 — platform-1 #69530): a library-based GCP client inside an
# interpreter invocation (no gcloud/firebase/credential text in the visible
# command) silently picks up a REAL prod credential already present in the
# inherited env. The guard must catch the ENV STATE, not just command text. ──

def test_lane_bash_benign_python_with_ambient_prod_gac_denied():
    # The exact shape of the incident: no telltale text in the command at all.
    assert_denied(
        "Bash", {"command": "python3 scripts/appissues_bus_poller.py --once --dry-run"},
        env={**_LANE_ENV, "GOOGLE_APPLICATION_CREDENTIALS": _KEY},
    )


def test_lane_bash_benign_node_with_ambient_prod_gac_abs_denied():
    assert_denied(
        "Bash", {"command": "node some_unrelated_script.js"},
        env={**_LANE_ENV, "GOOGLE_APPLICATION_CREDENTIALS": _KEY_ABS},
    )


def test_lane_bash_ambient_wellknown_adc_denied():
    # GAC unset, but the well-known per-user ADC file itself is ambiently
    # reachable (the ORIGINAL reviewer's hole) — same env-state class.
    assert_denied(
        "Bash", {"command": "python3 unrelated.py"},
        env={**_LANE_ENV, "GOOGLE_APPLICATION_CREDENTIALS": _ADC_JSON},
    )


def test_lane_bash_ambient_sentinel_gac_allowed():
    # Part A's own fail-closed sentinel (guaranteed non-existent, non-prod) must
    # NOT trip this check — that is the CORRECT isolated state, not a danger.
    assert_allowed(
        "Bash", {"command": "python3 unrelated.py"},
        env={**_LANE_ENV, "GOOGLE_APPLICATION_CREDENTIALS": "/Users/sheikhmusa/.wingmen/lane-gcloud/cc-cosem-platform-1/.no-prod-adc"},
    )


def test_lane_bash_ambient_gac_unset_allowed():
    # No ambient GAC at all (the ordinary/expected case for a lane that never
    # had Part A misfire) is fine.
    assert_allowed("Bash", {"command": "python3 unrelated.py"}, env=_LANE_ENV)


def test_lane_bash_ambient_gac_nonprod_path_allowed():
    # A real, EXISTING file that simply isn't a cosem prod key / well-known ADC
    # path must not false-positive (e.g. a lane's own unrelated creds file).
    assert_allowed(
        "Bash", {"command": "python3 unrelated.py"},
        env={**_LANE_ENV, "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/some-other-unrelated-file.json"},
    )


def test_console_unaffected_by_ambient_gac_check():
    # The console legitimately runs prod ops — this new check must stay
    # worker-lane-scoped like every other check in this guard.
    r = run_hook(
        "Bash", {"command": "python3 unrelated.py"},
        env={"GOOGLE_APPLICATION_CREDENTIALS": _KEY},
    )
    assert r.returncode == 0, r.stderr


# ── DENY: fail-closed on unreadable input (worker lane) ──────────────────────

def test_lane_unparseable_stdin_fail_closed():
    r = subprocess.run(
        [sys.executable, str(HOOK)],
        input="not json", text=True, capture_output=True,
        env={**{k: v for k, v in os.environ.items()}, **_LANE_ENV},
    )
    assert r.returncode == 2, r.stderr
    assert "lane_prod_key_guard" in r.stderr
