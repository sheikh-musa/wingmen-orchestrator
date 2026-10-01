"""Tests for scripts/hooks/secrets_transcript_guard.py (Musa op#24408).

Corpus taken directly from the three real incidents and orch-console's own BLOCK/ALLOW
examples (bus #48293, #48312) -- not invented after the fact.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).parent.parent / "scripts" / "hooks" / "secrets_transcript_guard.py"


def run_hook(tool_name: str, tool_input: dict) -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload, text=True, capture_output=True,
    )


def assert_blocked(tool_name: str, tool_input: dict):
    r = run_hook(tool_name, tool_input)
    assert r.returncode == 2, f"expected BLOCK for {tool_input!r}, got exit {r.returncode}, stderr={r.stderr!r}"
    assert "secret would enter the transcript" in r.stderr


def assert_allowed(tool_name: str, tool_input: dict):
    r = run_hook(tool_name, tool_input)
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


# ---- fail-closed on unparseable input ---------------------------------------------

def test_fails_closed_on_bad_json():
    r = subprocess.run([sys.executable, str(HOOK)], input="not json", text=True, capture_output=True)
    assert r.returncode == 2
