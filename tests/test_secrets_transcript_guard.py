"""Tests for scripts/hooks/secrets_transcript_guard.py (Musa op#24408).

Corpus taken directly from the three real incidents and orch-console's own BLOCK/ALLOW
examples (bus #48293, #48312) -- not invented after the fact.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).parent.parent / "scripts" / "hooks" / "secrets_transcript_guard.py"


def run_hook(tool_name: str, tool_input: dict, env: dict | None = None) -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
    full_env = dict(os.environ)
    full_env.pop("CC_BASE_AGENT_ID", None)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload, text=True, capture_output=True, env=full_env,
    )


def assert_blocked(tool_name: str, tool_input: dict, env: dict | None = None):
    r = run_hook(tool_name, tool_input, env=env)
    assert r.returncode == 2, f"expected BLOCK for {tool_input!r}, got exit {r.returncode}, stderr={r.stderr!r}"
    assert "secret would enter the transcript" in r.stderr


def assert_allowed(tool_name: str, tool_input: dict, env: dict | None = None):
    r = run_hook(tool_name, tool_input, env=env)
    assert r.returncode == 0, f"expected ALLOW for {tool_input!r}, got exit {r.returncode}, stderr={r.stderr!r}"


# ---- must BLOCK: the three real incidents ----------------------------------------

def test_blocks_the_gzb_forced_command_ssh_invocation():
    assert_blocked("Bash", {"command": "ssh -i ~/.ssh/gzb_to_mini gazzai@mini-tailscale-ip"})


def test_blocks_raw_db_password_into_a_bus_message_shape():
    assert_blocked("Bash", {"command": 'echo "DB password is $DATABASE_URL" | bus_send.py --to x'})


def test_blocks_raw_database_url_echo():
    assert_blocked("Bash", {"command": "echo $DATABASE_URL"})


# ---- must BLOCK: additional direct-exposure shapes --------------------------------

def test_blocks_cat_dot_env():
    assert_blocked("Bash", {"command": "cat .env"})


def test_blocks_cat_write_dsn():
    assert_blocked("Bash", {"command": "cat ~/.wingmen/private/write_dsn.env"})


def test_blocks_read_tool_on_env_file():
    assert_blocked("Read", {"file_path": "/Users/sheikhmusa/wingmen/orchestrator/.env"})


def test_blocks_read_tool_on_oauth_token():
    assert_blocked("Read", {"file_path": "/Users/sheikhmusa/.wingmen/keys/musa-oauth-token"})


def test_blocks_sed_print_of_secret_file():
    assert_blocked("Bash", {"command": "sed 's/x/y/' ~/.wingmen/private/write_dsn.env"})


def test_blocks_printenv_without_sink():
    assert_blocked("Bash", {"command": "printenv | grep TOKEN"})


def test_blocks_ps_eww_dump_without_sink():
    assert_blocked("Bash", {"command": "ps eww -p 123"})


def test_blocks_proc_environ_without_sink():
    assert_blocked("Bash", {"command": "cat /proc/1234/environ"})


def test_blocks_bare_set_dump():
    assert_blocked("Bash", {"command": "set"})


def test_blocks_export_dash_p():
    assert_blocked("Bash", {"command": "export -p | grep SECRET"})


def test_blocks_hash_sink_followed_by_more_than_trimming():
    # a sink is present, but the pipeline keeps going past it with something that
    # isn't pure trimming of the hash (grep here could re-surface raw content upstream
    # of the sink in a more complex real pipeline -- conservatively blocked).
    assert_blocked("Bash", {"command": 'printf %s "$DATABASE_URL" | shasum | grep abc'})


# ---- must ALLOW: orch-console's own examples (bus #48312), verbatim --------------

def test_allows_sourced_env_hashed_database_url():
    assert_allowed("Bash", {"command": 'set -a; . ./.env; set +a; printf %s "$DATABASE_URL" | shasum'})


def test_allows_ps_eww_piped_to_hash():
    assert_allowed("Bash", {
        "command": "ps eww -p 123 | tr ' ' '\\n' | grep '^X=' | cut -d= -f2- | shasum"
    })


def test_allows_grep_o_name_only():
    assert_allowed("Bash", {"command": "grep -o '^[A-Z_]*TOKEN' file"})


# ---- must ALLOW: additional safe shapes found via the real-transcript replay -----

def test_allows_grep_o_name_equals_trailing():
    # `^[A-Z_]+=` only ever emits "NAME=", never a value -- same safety as name-only
    assert_allowed("Bash", {"command": "grep -oE '^[A-Z_]+=' .env"})


def test_blocks_grep_o_name_equals_value_suffix():
    # but `^[A-Z_]+=.*` captures the value too -- must still block
    assert_blocked("Bash", {"command": "grep -oE '^[A-Z_]+=.*' .env"})


def test_allows_grep_o_literal_identifier_pattern():
    # a fixed literal pattern under -o can only ever echo itself back -- never a value
    assert_allowed("Bash", {"command": "grep -oE 'GOUMLYNE_RO_DATABASE_URL' .env"})


def test_allows_grep_o_prefix_plus_name_charset():
    assert_allowed("Bash", {"command": "grep -oE 'GOUMLYNE[A-Z_]*' .env"})


def test_blocks_grep_o_wildcard_pattern():
    # '.' / '.*' can reach into actual value characters -- must still block
    assert_blocked("Bash", {"command": "grep -oE 'GOUMLYNE.*' .env"})


def test_allows_grep_rl_files_with_matches_on_env():
    # -l/-L/-c never print line content, only filenames/counts
    assert_allowed("Bash", {"command": 'grep -rl "GOUMLYNE_RO" .env ~/wingmen/projects/'})


def test_allows_sed_mask_of_dsn_password():
    assert_allowed("Bash", {"command": 'echo "$DATABASE_URL" | sed -E \'s/:[^:@]+@/:***@/\''})


def test_allows_env_dump_piped_to_sed_redact():
    assert_allowed("Bash", {"command": "env | grep -i SUPABASE | sed -E 's/=.*/=<redacted>/'"})


def test_allows_curl_header_use_piped_to_unrelated_print():
    # bus #48312 replay false positive: sensitive var used (not printed) in one
    # segment, unrelated `print(...)` in a later segment -- must not cross-correlate
    assert_allowed("Bash", {
        "command": 'curl -H "apikey: ${SUPABASE_SERVICE_KEY}" https://x | python3 -c "print(1)"'
    })


def test_allows_echo_label_containing_the_word_env():
    # bare substring "env" inside an echoed label is not an `env` command invocation
    assert_allowed("Bash", {"command": 'echo "--- env ---"'})


def test_allows_independent_safe_statements_sharing_a_command():
    assert_allowed("Bash", {
        "command": 'echo "a: $(echo x | shasum | cut -c1-10)"; set -a; . ./.env; set +a'
    })


# ---- must ALLOW: USE (not PRINT) of a secret, per bus #48312 concern 1 -----------

def test_allows_psql_with_database_url():
    assert_allowed("Bash", {"command": 'psql "$DATABASE_URL" -c "select 1"'})


def test_allows_a_script_invocation_using_the_var():
    assert_allowed("Bash", {"command": "scripts/deploy.sh --dsn $DATABASE_URL"})


def test_allows_python_reading_environ():
    assert_allowed("Bash", {"command": "python3 -c \"import os; conn(os.environ['DATABASE_URL'])\""})


def test_allows_sourcing_a_dotenv_file():
    assert_allowed("Bash", {"command": "set -a; source .env; set +a"})


def test_allows_dotenv_load_in_code():
    assert_allowed("Read", {"file_path": "/Users/sheikhmusa/wingmen/orchestrator/scripts/boot.py"})


def test_allows_set_minus_a_toggle_not_a_dump():
    assert_allowed("Bash", {"command": "set -a"})


def test_allows_unrelated_command():
    assert_allowed("Bash", {"command": "git status --short"})


# ---- Rule D: lane-scoped human-owner cloud login / IAM mutation block (bus #48386) -
# Corpus is the ask's own three required cases, verbatim.

LANE_ENV = {"CC_BASE_AGENT_ID": "cc-cosem-tdu-coord"}


def assert_cloud_blocked(tool_input: dict, env: dict):
    r = run_hook("Bash", tool_input, env=env)
    assert r.returncode == 2, f"expected BLOCK for {tool_input!r}, got exit {r.returncode}, stderr={r.stderr!r}"
    assert "owner-level cloud action" in r.stderr


def test_blocks_lane_iam_mutation_with_human_owner_login():
    # the 2026-10-01 18:14Z incident shape: a lane, Musa's owner account, an IAM mutation
    assert_cloud_blocked(
        {"command": "gcloud projects add-iam-policy-binding cosem-prod "
                     "--member=user:foo@example.com --role=roles/owner "
                     "--account=musa@cosem.org.sg"},
        env=LANE_ENV,
    )


def test_allows_consoles_own_use_of_the_same_command():
    # no CC_BASE_AGENT_ID -- the console/hub's own use is exempt
    assert_allowed(
        "Bash",
        {"command": "gcloud projects add-iam-policy-binding cosem-prod "
                     "--member=user:foo@example.com --role=roles/owner "
                     "--account=musa@cosem.org.sg"},
        env={},
    )


def test_allows_lane_firebase_deploy_with_service_account():
    assert_allowed(
        "Bash",
        {"command": "firebase deploy --project my-proj "
                     "--account=cosem-deployer@my-proj.iam.gserviceaccount.com"},
        env=LANE_ENV,
    )


def test_blocks_lane_gcloud_auth_login_switching_to_human_account():
    assert_cloud_blocked(
        {"command": "gcloud auth login musa@cosem.org.sg"},
        env=LANE_ENV,
    )


def test_blocks_lane_print_access_token_with_no_account():
    # fail-closed: can't confirm it's a service account
    assert_cloud_blocked({"command": "gcloud auth print-access-token"}, env=LANE_ENV)


def test_allows_lane_print_access_token_with_service_account():
    assert_allowed(
        "Bash",
        {"command": "gcloud auth print-access-token "
                     "--account=cosem-deployer@my-proj.iam.gserviceaccount.com"},
        env=LANE_ENV,
    )


def test_blocks_lane_services_disable_regardless_of_account():
    assert_cloud_blocked(
        {"command": "gcloud services disable compute.googleapis.com "
                     "--account=cosem-deployer@my-proj.iam.gserviceaccount.com"},
        env=LANE_ENV,
    )


# ---- cc-quality PR#245 review fixes (bus #48441) -----------------------------------

def test_blocks_env_local_as_secret_file():
    # LOW #2: `.env` end-anchor missed `.env.local` / `.env.production`
    assert_blocked("Bash", {"command": "cat .env.local"})


def test_blocks_env_production_as_secret_file():
    assert_blocked("Bash", {"command": "cat .env.production"})


def test_blocks_base64_dump_of_secret_file():
    # MED #1: binary-dump readers bypassed the file-print blocklist entirely
    assert_blocked("Bash", {"command": "base64 .env"})


def test_blocks_strings_dump_of_oauth_token():
    assert_blocked("Bash", {"command": "strings ~/.wingmen/keys/musa-oauth-token"})


def test_blocks_dd_dump_of_write_dsn():
    assert_blocked("Bash", {"command": "dd if=~/.wingmen/private/write_dsn.env"})


def test_blocks_python_open_print_of_secret_file():
    assert_blocked("Bash", {"command": "python3 -c \"print(open('.env').read())\""})


def test_allows_python_open_of_non_secret_file():
    assert_allowed("Bash", {"command": "python3 -c \"print(open('README.md').read())\""})


def test_blocks_python_environ_print_of_non_dsn_secret():
    # LOW #3: non-shell print-by-name bypassed Rule B's $VAR-shaped trigger entirely
    assert_blocked("Bash", {"command": "python3 -c \"import os; print(os.environ['SUPABASE_SERVICE_KEY'])\""})


def test_blocks_python_environ_get_print_of_token():
    assert_blocked("Bash", {"command": "python3 -c \"import os; print(os.environ.get('API_KEY'))\""})


def test_blocks_awk_environ_print_of_secret():
    assert_blocked("Bash", {"command": "awk 'BEGIN{print ENVIRON[\"GOUMLYNE_RO_DATABASE_URL\"]}'"})


def test_allows_python_environ_use_without_print():
    # unchanged: USE (not PRINT) of os.environ stays allowed
    assert_allowed("Bash", {"command": "python3 -c \"import os; conn(os.environ['DATABASE_URL'])\""})


def test_blocks_tee_leak_before_hash_sink():
    # LOW #4: a hash sink downstream doesn't un-leak what tee already duplicated
    assert_blocked("Bash", {"command": 'printf %s "$DATABASE_URL" | tee /tmp/leak | shasum'})


def test_allows_grep_on_env_local_through_sed_mask_sink():
    # widening .env.local into SECRET_FILE_RE (LOW #2) must not re-break the
    # already-accepted sed-mask-sink exception for a grep match line on that file
    assert_allowed("Bash", {
        "command": 'grep -iE "SUPABASE" /home/gazzai/wingmen/projects/ihsanos/.env.local | sed -E \'s/=.*/=<redacted>/\''
    })


def test_blocks_grep_on_env_local_without_a_sink():
    assert_blocked("Bash", {"command": 'grep -iE "SUPABASE" .env.local'})


def test_allows_hash_sink_with_no_tee_in_between():
    # regression guard: the tee check must not fire on pipelines without tee
    assert_allowed("Bash", {
        "command": "ps eww -p 123 | tr ' ' '\\n' | grep '^X=' | cut -d= -f2- | shasum"
    })


# ---- cc-quality PR#245 Rule D re-review fixes (bus #48466) -------------------------

def test_blocks_dd_if_equals_dotenv_operand():
    # cc-quality #48499 LOW: `if=.env` is one shlex token; the value-half of a
    # key=value operand needs checking too, not just whole-token matches
    assert_blocked("Bash", {"command": "dd if=.env"})


def test_blocks_lane_sudo_gcloud_iam_mutation():
    assert_cloud_blocked(
        {"command": "sudo gcloud projects add-iam-policy-binding cosem-prod "
                     "--member=user:foo@example.com --role=roles/owner"},
        env=LANE_ENV,
    )


def test_blocks_lane_absolute_path_gcloud_iam_mutation():
    assert_cloud_blocked(
        {"command": "/usr/bin/gcloud projects add-iam-policy-binding cosem-prod "
                     "--member=user:foo@example.com --role=roles/owner"},
        env=LANE_ENV,
    )


def test_blocks_lane_secrets_version_access_with_no_account():
    # the "sharp edge" cc-quality flagged: a lane reading a client prod secret via
    # the shared owner login, with no explicit SA
    assert_cloud_blocked(
        {"command": "gcloud secrets versions access latest --secret=client-db-password"},
        env=LANE_ENV,
    )


def test_allows_lane_secrets_version_access_with_service_account():
    assert_allowed(
        "Bash",
        {"command": "gcloud secrets versions access latest --secret=client-db-password "
                     "--account=cosem-deployer@my-proj.iam.gserviceaccount.com"},
        env=LANE_ENV,
    )


# ---- fail-closed on unparseable input ---------------------------------------------

def test_fails_closed_on_bad_json():
    r = subprocess.run([sys.executable, str(HOOK)], input="not json", text=True, capture_output=True)
    assert r.returncode == 2
