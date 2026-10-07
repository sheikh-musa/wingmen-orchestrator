"""Tests for scripts/hooks/secrets_transcript_guard.py (Musa op#24408).

Corpus taken directly from the three real incidents and orch-console's own BLOCK/ALLOW
examples (bus #48293, #48312) -- not invented after the fact.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

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


def assert_blocked(tool_name: str, tool_input: dict, env: dict | None = None, expect_substr: str = "secret would enter the transcript"):
    r = run_hook(tool_name, tool_input, env=env)
    assert r.returncode == 2, f"expected BLOCK for {tool_input!r}, got exit {r.returncode}, stderr={r.stderr!r}"
    assert expect_substr in r.stderr


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
    assert_blocked("Read", {"file_path": "/Users/sheikhmusa/wingmen/orchestrator/.env"},
                   expect_substr="this path is a secret file")


def test_blocks_read_tool_on_oauth_token():
    assert_blocked("Read", {"file_path": "/Users/sheikhmusa/.wingmen/keys/musa-oauth-token"},
                   expect_substr="this path is a secret file")


def test_blocks_sed_print_of_secret_file():
    assert_blocked("Bash", {"command": "sed 's/x/y/' ~/.wingmen/private/write_dsn.env"})


def test_blocks_printenv_without_sink():
    assert_blocked("Bash", {"command": "printenv | grep TOKEN"})


# ---- must BLOCK: .ssh dir under a home that isn't the hook's own $HOME ------------
# found via synthetic tool-path replay (scripts/scratch_toolpath_replay.py), not the
# real 1200 corpus -- os.path.expanduser("~/.ssh/") only ever resolves against THIS
# process's own home, so the tilde-prefix check alone misses e.g. /root/.ssh/ (exactly
# where LOCK 1 relocates gzb_to_mini) or another user's home.

def test_blocks_edit_tool_under_root_dot_ssh():
    assert_blocked("Edit", {"file_path": "/root/.ssh/gzb_to_mini_wrapper_notes",
                             "old_string": "a", "new_string": "b"},
                   expect_substr="this path is a secret file")


def test_blocks_edit_tool_under_another_users_dot_ssh():
    assert_blocked("Edit", {"file_path": "/home/gazzai/.ssh/config",
                             "old_string": "a", "new_string": "b"},
                   expect_substr="this path is a secret file")


# ---- must BLOCK: .wingmen/private and .wingmen/keys under a home that isn't the
# hook's own $HOME (cc-quality bus #48712 -- same gap as .ssh, found on review) --------

def test_blocks_edit_tool_under_root_dot_wingmen_keys():
    assert_blocked("Edit", {"file_path": "/root/.wingmen/keys/some_credential.txt",
                             "old_string": "a", "new_string": "b"},
                   expect_substr="this path is a secret file")


def test_blocks_edit_tool_under_another_users_dot_wingmen_private():
    assert_blocked("Edit", {"file_path": "/home/gazzai/.wingmen/private/some_credential.txt",
                             "old_string": "a", "new_string": "b"},
                   expect_substr="this path is a secret file")


# ---- must BLOCK: Rule E -- a secret VALUE typed literally into a Bash command -----
# cc-fleet-health's real-leak-shapes sweep (bus #48685), confirmed by orch-console
# (bus #48695): shape 1 (literal password DSN) and shape 2 (literal Bearer/bot token)
# were typed directly into Bash commands, not referenced via $VAR -- already in the
# tool_use INPUT by the time PreToolUse fires, so this must be unconditional (no sink
# exception can un-leak an argv that's already in the transcript).

def test_blocks_literal_password_dsn_in_psql():
    assert_blocked(
        "Bash",
        {"command": 'psql "postgres://orchuser:FakeSyntheticPass123@db.example.internal:5432/orch" -c "select 1"'},
        expect_substr="types a secret VALUE literally",
    )


def test_blocks_literal_dsn_assigned_to_a_shell_var():
    assert_blocked(
        "Bash",
        {"command": 'DB="postgres://orchuser:FakeSyntheticPass123@db.example.internal:5432/orch"; echo "$DB" | head -c0'},
        expect_substr="types a secret VALUE literally",
    )


def test_blocks_literal_bearer_token_in_curl():
    assert_blocked(
        "Bash",
        {"command": 'curl -H "Authorization: Bearer FakeSyntheticToken1234567890abcdef" https://api.example.com/x'},
        expect_substr="types a secret VALUE literally",
    )


def test_blocks_literal_bot_token_in_curl_url():
    assert_blocked(
        "Bash",
        {"command": "curl https://api.telegram.org/bot123456789:AAFakeSyntheticTokenNotReal123456/sendMessage"},
        expect_substr="types a secret VALUE literally",
    )


def test_allows_dsn_referenced_by_variable_not_literal():
    # a bare $VAR reference passed to a non-print, non-postgres-CLI command (USE, not
    # PRINT, per Rule B's own carve-out) must stay allowed -- Rule E only fires on the
    # LITERAL value shape, never a bare $VAR reference, and Rule F (below) is scoped
    # to pg_dump/pg_restore/pg_dumpall/psql specifically.
    assert_allowed("Bash", {"command": "scripts/notify.sh --dsn $DATABASE_URL"})


# ---- must BLOCK: Rule F -- pg_dump/pg_restore/pg_dumpall/psql given a DSN var as argv -
# Real incident (bus #52114/#52348/#52387, 2026-10-05): the command text never had a
# literal secret (Rule E doesn't fire), but the exec'd process's shell-expanded argv
# did, and a background-task status read on a timed-out pg_dump surfaced it.
# orch-console's #52393 explicitly overruled an earlier draft that carved psql out as
# a "pre-existing sanctioned idiom for quick queries": any of the four can time out
# into a background task and hit the same argv-exposure path.

def test_blocks_pg_dump_with_dsn_var_positional_arg():
    assert_blocked(
        "Bash",
        {"command": 'pg_dump "$DATABASE_URL" --schema-only --no-owner --no-privileges'},
        expect_substr="pg_dump given a DSN-shaped variable as a positional argument",
    )


def test_blocks_pg_restore_with_dsn_var_and_absolute_path():
    assert_blocked(
        "Bash",
        {"command": '/usr/local/opt/postgresql@17/bin/pg_restore "$WRITE_DSN" -d orch'},
        expect_substr="pg_restore given a DSN-shaped variable as a positional argument",
    )


def test_blocks_pg_dumpall_with_dsn_var_positional_arg():
    assert_blocked(
        "Bash",
        {"command": 'pg_dumpall "$DATABASE_URL" --globals-only'},
        expect_substr="pg_dumpall given a DSN-shaped variable as a positional argument",
    )


def test_blocks_psql_with_dsn_var_positional_arg_supersedes_old_carveout():
    # supersedes the old test_allows_dsn_referenced_by_variable_not_literal psql case
    # (#52393) -- psql is no longer exempt.
    assert_blocked(
        "Bash",
        {"command": 'psql "$DATABASE_URL" -c "select 1"'},
        expect_substr="psql given a DSN-shaped variable as a positional argument",
    )


def test_blocks_psql_with_db_url_named_var():
    # orch-console named the \\w*_DB_URL form explicitly (#52393), e.g. CONSOLE_DB_URL.
    assert_blocked(
        "Bash",
        {"command": 'psql "$CONSOLE_DB_URL" -c "select 1"'},
        expect_substr="psql given a DSN-shaped variable as a positional argument",
    )


def test_allows_pg_dump_with_pgpassword_env_and_component_flags():
    # the sanctioned fix: PGPASSWORD + --host/--port/--username/--dbname, so the
    # password never reaches pg_dump's own argv regardless of who lists it later.
    # PGPASSWORD="$DB_PASSWORD" is the sanctioned decomposition idiom itself -- it is
    # the leading VAR=val PREFIX, not part of pg_dump's own argv, so it must not trip
    # Rule F (which scopes SENSITIVE_VAR_RE to the argv tokens only).
    assert_allowed(
        "Bash",
        {"command": 'PGPASSWORD="$DB_PASSWORD" pg_dump --host=db.example.internal --port=5432 '
                    '--username=orchuser --dbname=orch --schema-only'},
    )


def test_allows_psql_with_pgpassword_env_and_component_flags():
    assert_allowed(
        "Bash",
        {"command": 'PGPASSWORD="$DB_PASSWORD" psql --host=db.example.internal --port=5432 '
                    '--username=orchuser --dbname=orch -c "select 1"'},
    )


# ---- must BLOCK: Rule E extended to Write/Edit/MultiEdit/NotebookEdit content ------
# bus #48903/#48922 LOCK2 follow-up: real incident (cc-substrate op#24409) was a
# fixture DSN typed into a Write'd file -- Rule E previously only scanned Bash command
# text, so this only got caught post-hoc by secrets_output_scanner.py, never pre-empted.

def test_blocks_literal_dsn_in_write_content():
    assert_blocked(
        "Write",
        {"file_path": "/Users/sheikhmusa/wingmen/orchestrator/reports/notes.md",
         "content": 'psql "postgres://orchuser:FakeSyntheticPass123@db.example.internal:5432/orch"'},
        expect_substr="types a secret VALUE literally",
    )


def test_blocks_literal_bearer_token_in_edit_new_string():
    assert_blocked(
        "Edit",
        {"file_path": "/Users/sheikhmusa/wingmen/orchestrator/reports/notes.md",
         "old_string": "x", "new_string": "Authorization: Bearer FakeSyntheticToken1234567890abcdef"},
        expect_substr="types a secret VALUE literally",
    )


def test_blocks_literal_dsn_in_one_of_several_multiedit_edits():
    assert_blocked(
        "MultiEdit",
        {"file_path": "/Users/sheikhmusa/wingmen/orchestrator/reports/notes.md",
         "edits": [
             {"old_string": "a", "new_string": "harmless change"},
             {"old_string": "b", "new_string": 'postgres://orchuser:FakeSyntheticPass123@db.example.internal:5432/orch'},
         ]},
        expect_substr="types a secret VALUE literally",
    )


def test_blocks_literal_dsn_in_notebookedit_new_source():
    assert_blocked(
        "NotebookEdit",
        {"notebook_path": "/Users/sheikhmusa/wingmen/orchestrator/reports/scratch.ipynb",
         "new_source": 'postgres://orchuser:FakeSyntheticPass123@db.example.internal:5432/orch'},
        expect_substr="types a secret VALUE literally",
    )


def test_allows_write_content_with_no_secret_shape():
    assert_allowed(
        "Write",
        {"file_path": "/Users/sheikhmusa/wingmen/orchestrator/reports/notes.md",
         "content": "just some ordinary notes, no secrets here"},
    )


def test_allows_edit_new_string_referencing_var_by_name():
    # the agent writing code that references $DATABASE_URL by name (not a literal
    # value) must stay allowed -- same Rule E boundary as the Bash case.
    assert_allowed(
        "Edit",
        {"file_path": "/Users/sheikhmusa/wingmen/orchestrator/scripts/foo.py",
         "old_string": "a", "new_string": "dsn = os.environ['DATABASE_URL']"},
    )


def test_secret_path_block_takes_priority_over_content_scan_on_env_file():
    # a secret PATH is blocked by Rule A's path check before content is ever scanned --
    # confirms the new content check doesn't change or duplicate that existing message.
    assert_blocked(
        "Write",
        {"file_path": "/Users/sheikhmusa/wingmen/orchestrator/.env",
         "content": "ORDINARY_KEY=not-secret-shaped"},
        expect_substr="this path is a secret file",
    )


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


def test_blocks_echo_of_override_suffixed_token_var_with_no_sink():
    # bus #49026/#49029/#49030 real leak: SENSITIVE_VAR_RE's old trailing \b meant
    # CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE (and any other _TOKEN/_KEY/_DSN name with a
    # qualifier suffix) never matched at all, so Rule B never even recognized this as a
    # secret-printing command -- it was silently allowed outright, with no block AND no
    # sink requirement.
    name = "CLAUDE_CODE_OAUTH_TOKEN" + "_OVERRIDE"
    assert_blocked("Bash", {"command": f'echo "${{{name}}}"'})


def test_allows_echo_of_override_suffixed_token_var_through_hash_sink():
    name = "CLAUDE_CODE_OAUTH_TOKEN" + "_OVERRIDE"
    assert_allowed("Bash", {"command": f'echo "${{{name}}}" | shasum'})


def test_blocks_echo_of_write_dsn_override_suffixed_var_with_no_sink():
    assert_blocked("Bash", {"command": 'echo "$WRITE_DSN_OVERRIDE"'})


def test_blocks_env_dump_piped_to_sed_redact():
    # bus #49026/#49029/#49030 real leak: a sed-mask sink is no longer accepted for an
    # UNBOUNDED "dumps the environment" trigger -- only a hash/length sink is, since a
    # hand-written sed pattern can't be trusted to enumerate every sensitive name a dump
    # might contain (this exact shape, with a specific-var-name sed, is what let
    # CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE through raw while masking CLAUDE_CODE_OAUTH_TOKEN).
    assert_blocked("Bash", {"command": "env | grep -i SUPABASE | sed -E 's/=.*/=<redacted>/'"})


def test_allows_env_dump_piped_to_hash_sink():
    assert_allowed("Bash", {"command": "env | grep -i SUPABASE | shasum"})


def test_blocks_proc_environ_read_piped_to_sed_redact_real_incident_shape():
    # bus #49026/#49029 real incident, reproduced: masking one named var in a
    # /proc/<pid>/environ dump by sed left a DIFFERENT, suffixed var name
    # (CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE) unmasked. The dump-level sink must now be a
    # real hash/length sink, not a per-name sed pattern.
    assert_blocked("Bash", {
        "command": (
            "cat /proc/668620/environ | tr '\\0' '\\n' | "
            "sed -E 's/CLAUDE_CODE_OAUTH_TOKEN=.*/CLAUDE_CODE_OAUTH_TOKEN=***/'"
        )
    })


def test_allows_proc_environ_read_piped_to_hash_sink():
    assert_allowed("Bash", {"command": "cat /proc/668620/environ | tr '\\0' '\\n' | shasum"})


# ---- bus #49064 (cc-fleet-health root-cause probe, orch-console #49062 condition B):
# the real Sep-24 at-rest leak (tool-results byz7cuxa2.txt) came from a command of
# exactly this shape -- `ps eww` on a remote host, nested behind `sshpass ... ssh`,
# itself nested behind an outer `ssh`, piped to a grep that kept whole matching lines
# (every secret env var of every matching process) with no sink at all. Safe
# reconstruction: same command SHAPE, fake host/user, no real secrets anywhere.

def test_blocks_sep24_nested_ssh_sshpass_ps_eww_incident_shape():
    assert_blocked("Bash", {
        "command": (
            "ssh hub-vps \"sshpass -p x ssh gazzai@gzb-host "
            "'ps eww -u gazzai -o pid,command'\" | "
            "grep -i 'musa2\\|CLAUDE_CODE_OAUTH' | head -20"
        )
    })


def test_blocks_ssh_unquoted_remote_ps_eww_dump():
    # bus #49064 gap #3: the remote command arrives as separate unquoted words rather
    # than one quoted string -- must resolve the same way.
    assert_blocked("Bash", {"command": "ssh gzb-host ps eww -u gazzai -o pid,command"})


def test_blocks_ps_dash_capital_e_flag():
    # bus #49064 gap #4: macOS's dash-prefixed `-E` env-display flag, distinct from the
    # lowercase `-e`/`-ef` "all processes" flag (which must stay allowed -- see
    # test_allows_ps_dash_e_all_processes_flag below).
    assert_blocked("Bash", {"command": "ps -E -p 123"})


def test_blocks_ps_env_keyletter_not_leading_in_cluster():
    # bus #49064 gap #4 (second half): BSD keyletters can combine in any order, so the
    # env-dump letter `e` need not be first (`ps eww`, already caught) -- `ps auxe` is
    # the same dump, letter last.
    assert_blocked("Bash", {"command": "ps auxe"})


def test_allows_ps_dash_e_all_processes_flag():
    # the ubiquitous GNU/BSD `-e` ("select all processes") must stay allowed -- it is
    # not an env dump, and this shape is far too common to regress.
    assert_allowed("Bash", {"command": "ps -ef"})


def test_allows_ps_aux_no_env_letter():
    assert_allowed("Bash", {"command": "ps aux | grep myproc"})


def test_allows_ssh_remote_command_with_no_dump_trigger():
    assert_allowed("Bash", {"command": "ssh gzb-host 'cat /etc/hostname'"})


def test_blocks_bash_dash_c_wrapped_ps_eww():
    assert_blocked("Bash", {"command": "bash -c 'ps eww -u gazzai'"})


# ---- cc-fleet-health's own #49064/#49090 probe corpus, verbatim (orch-console #49085:
# "add them ALL as regression tests ... so the wider ps-e/ssh recursion doesn't start
# over-blocking normal work"). These are their exact case strings (hosts/paths/users
# already anonymised by them), not reconstructions -- kept alongside the equivalent
# tests above rather than replacing them.

def test_fleet_health_probe_sep24_exact_shape_anonymised():
    assert_blocked("Bash", {
        "command": (
            "ssh hostA \"sshpass -f /dev/stdin ssh -o StrictHostKeyChecking=no "
            "user@10.0.0.1 'ps eww -u user -o pid,command' \" < /tmp/x 2>&1 | "
            "grep -i \"musa2\\|CLAUDE_CODE_OAUTH\" | head -20"
        )
    })


def test_fleet_health_probe_sep24_shape_no_ssh_wrapper():
    assert_blocked("Bash", {
        "command": "ps eww -u user -o pid,command | grep -i CLAUDE_CODE_OAUTH | head -20"
    })


def test_fleet_health_probe_ps_eww_alone():
    assert_blocked("Bash", {"command": "ps eww -p 123"})


def test_fleet_health_probe_ps_dash_capital_e():
    assert_blocked("Bash", {"command": "ps -E -p 123"})


def test_fleet_health_probe_environ_grep_sed_mask_missed_override():
    assert_blocked("Bash", {
        "command": (
            "tr '\\0' '\\n' < /proc/123/environ | "
            "grep -E '^(CLAUDE_CODE_OAUTH_TOKEN|AGENT_ID)' | sed -E 's/(TOKEN=).*/\\1<r>/'"
        )
    })


def test_fleet_health_probe_verbatim_for_loop_environ_grep_sed_redacted():
    # the exact shape from the second real incident -- a for-loop over child PIDs,
    # each /proc/$p/environ dumped and sed-masked (missed CLAUDE_CODE_OAUTH_TOKEN
    # *_OVERRIDE while masking the base name).
    assert_blocked("Bash", {
        "command": (
            "for p in $(pgrep -P 1); do tr '\\0' '\\n' < /proc/$p/environ 2>/dev/null | "
            "grep -E '^(CLAUDE_CODE_OAUTH_TOKEN|WINGMEN_AUTH|AGENT_ID)' | "
            "sed -E 's/(TOKEN=).{0,}/\\1<redacted>/'; done"
        )
    })


def test_fleet_health_probe_ps_eww_via_ssh_unquoted():
    assert_blocked("Bash", {"command": "ssh host ps eww -p 1 | grep TOKEN"})


def test_fleet_health_probe_cat_dotenv():
    assert_blocked("Bash", {"command": "cat ~/wingmen/orchestrator/.env"})


def test_fleet_health_probe_env_pipe_grep_token():
    assert_blocked("Bash", {"command": "env | grep TOKEN"})


def test_fleet_health_probe_tmux_show_environment():
    assert_blocked("Bash", {"command": "tmux show-environment -t lane"})


def test_fleet_health_probe_safe_environ_hashed_as_last_step():
    assert_allowed("Bash", {
        "command": (
            "tr '\\0' '\\n' < /proc/123/environ | grep '^CLAUDE_CODE_OAUTH_TOKEN=' | "
            "cut -d= -f2- | tr -d '\\n' | sha256sum | cut -c1-12"
        )
    })


def test_fleet_health_probe_safe_plain_ps_without_e():
    assert_allowed("Bash", {"command": "ps -o pid,args -p 123"})


def test_fleet_health_probe_safe_git_status():
    assert_allowed("Bash", {"command": "git status --porcelain"})


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

def test_blocks_psql_with_database_url_superseded_by_rule_f():
    # superseded by Rule F (bus #52393): psql is no longer exempt from the
    # DSN-as-positional-argv block -- see test_blocks_psql_with_dsn_var_positional_arg_
    # supersedes_old_carveout above for the full rationale.
    assert_blocked(
        "Bash",
        {"command": 'psql "$DATABASE_URL" -c "select 1"'},
        expect_substr="psql given a DSN-shaped variable as a positional argument",
    )


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


# ---- tool-path coverage (bus #48639/#48642, real incident 2026-10-01 14:37Z) -----
# An Edit/MultiEdit/Write/NotebookEdit tool_result snippet can echo a secret file's
# EXISTING content into the transcript the same way a `cat` would -- these are
# PATH-ONLY tools, gated unconditionally on the target path, no sink exception.

def test_blocks_edit_tool_on_env_file():
    # the exact 14:37Z incident shape: an Edit on orchestrator/.env.
    assert_blocked(
        "Edit",
        {"file_path": "/Users/sheikhmusa/wingmen/orchestrator/.env",
         "old_string": "ANTHROPIC_API_KEY=old", "new_string": "ANTHROPIC_API_KEY=new"},
        expect_substr="this path is a secret file",
    )


def test_blocks_multiedit_tool_on_env_file():
    assert_blocked(
        "MultiEdit",
        {"file_path": "/Users/sheikhmusa/wingmen/orchestrator/.env",
         "edits": [{"old_string": "A=1", "new_string": "A=2"}]},
        expect_substr="this path is a secret file",
    )


def test_blocks_write_tool_on_env_file():
    assert_blocked(
        "Write",
        {"file_path": "/Users/sheikhmusa/wingmen/orchestrator/.env", "content": "A=1\n"},
        expect_substr="this path is a secret file",
    )


def test_blocks_notebookedit_tool_on_env_file():
    assert_blocked(
        "NotebookEdit",
        {"notebook_path": "/Users/sheikhmusa/wingmen/orchestrator/.env", "new_source": "x"},
        expect_substr="this path is a secret file",
    )


def test_blocks_edit_tool_under_dev_shm_wingmen_secrets():
    assert_blocked(
        "Edit",
        {"file_path": "/dev/shm/wingmen-secrets/private/write_dsn.env",
         "old_string": "a", "new_string": "b"},
        expect_substr="this path is a secret file",
    )


def test_blocks_write_tool_under_wingmen_keys():
    assert_blocked(
        "Write",
        {"file_path": os.path.expanduser("~/.wingmen/keys/some-new-key"), "content": "x"},
        expect_substr="this path is a secret file",
    )


def test_blocks_edit_tool_under_dot_ssh():
    assert_blocked(
        "Edit",
        {"file_path": os.path.expanduser("~/.ssh/gzb_to_mini"), "old_string": "a", "new_string": "b"},
        expect_substr="this path is a secret file",
    )


def test_blocks_write_tool_on_service_account_json():
    assert_blocked(
        "Write",
        {"file_path": "/Users/sheikhmusa/creds/cosem-service-account.json", "content": "{}"},
        expect_substr="this path is a secret file",
    )


def test_allows_edit_tool_on_ordinary_source_file():
    assert_allowed(
        "Edit",
        {"file_path": "/Users/sheikhmusa/wingmen/orchestrator/scripts/foo.py",
         "old_string": "a", "new_string": "b"},
    )


def test_allows_write_tool_on_ordinary_file():
    assert_allowed(
        "Write",
        {"file_path": "/Users/sheikhmusa/wingmen/orchestrator/reports/notes.md", "content": "hi"},
    )


def test_allows_env_set_sh_as_the_sanctioned_escape_hatch():
    # scripts/env_set.sh only ever prints a sha1 fingerprint -- structurally a hash
    # sink -- so feeding it a sensitively-named var via printf must resolve the same
    # way `| shasum` does, not get blamed as an unresolved print trigger.
    assert_allowed(
        "Bash",
        {"command": 'printf \'%s\' "$NEW_API_KEY" | scripts/env_set.sh orchestrator/.env API_KEY'},
    )


# ---- Rule G: raw DDL against a PRODUCTION_SILOS store, bypassing apply_migration.py's
# --gate (bus #44135, re-raised live by #53758/#53764) -----------------------------

# ywrpttpxwfcoodovxhsr is the real cosem-platform PRODUCTION_SILOS member, already a
# hardcoded literal in scripts/apply_migration.py and docs/data-store-registry.md --
# a project ref, not a credential, same convention as those two files.
_PROD_REF = "ywrpttpxwfcoodovxhsr"
_NONPROD_REF = "some-dev-ref-not-production"
# every env var check_rule_g might resolve, forced empty -- a test asserting ALLOW
# must not accidentally inherit a real production DSN from the host running the suite.
_NEUTRAL_PG_ENV = {"DATABASE_URL": "", "PGHOST": "", "PGDATABASE": "", "PGUSER": "", "PGSERVICE": "", "PROD_DSN": ""}


def test_blocks_ddl_against_production_silo_via_sensitive_var_name_resolution():
    # a non-psql-CLI shape (Rule F only covers pg_dump/pg_restore/pg_dumpall/psql) that
    # still references a $VAR by name and mentions psycopg -- exercises the
    # SENSITIVE_VAR_RE env-resolution path in _resolved_values_for_silo_check directly,
    # independent of the PG_CONNECT_ENV_VARS and os.environ[...] paths covered below.
    assert_blocked(
        "Bash",
        {"command": "TARGET=$PROD_DSN python3 -c \"import psycopg; psycopg.connect(TARGET).execute('CREATE TABLE x(y int)')\""},
        env={**_NEUTRAL_PG_ENV, "PROD_DSN": "host=db-" + _PROD_REF + "-pooler.example"},
        expect_substr="raw DDL against a PRODUCTION_SILOS store",
    )


def test_blocks_psql_ddl_against_production_silo_via_ambient_pghost():
    # psql connects via PGHOST with NO var named anywhere in the command text at all --
    # the shape Rule F's own argv-exposure fix pushed everyone toward.
    assert_blocked(
        "Bash",
        {"command": 'psql -c "DROP TABLE foo"'},
        env={**_NEUTRAL_PG_ENV, "PGHOST": "db-" + _PROD_REF + "-pooler.example"},
        expect_substr="raw DDL against a PRODUCTION_SILOS store",
    )


def test_blocks_psycopg_ddl_via_os_environ_reference():
    assert_blocked(
        "Bash",
        {"command": "python3 -c \"import os,psycopg; psycopg.connect(os.environ['DATABASE_URL']).execute('ALTER TABLE x ADD COLUMN y int')\""},
        env={**_NEUTRAL_PG_ENV, "DATABASE_URL": "host=db-" + _PROD_REF + "-pooler.example"},
        expect_substr="raw DDL against a PRODUCTION_SILOS store",
    )


def test_allows_apply_migration_py_invocation_even_with_ddl_and_prod_ref():
    # the sanctioned, gated path -- must never be the thing this rule blocks, even
    # with a prod-silo DSN ambiently set and a DDL-shaped silo ref in the command.
    assert_allowed(
        "Bash",
        {"command": "python3 scripts/apply_migration.py 100 --silo " + _PROD_REF + " --gate 123"},
        env={**_NEUTRAL_PG_ENV, "DATABASE_URL": "host=db-" + _PROD_REF + "-pooler.example"},
    )


def test_allows_readonly_psql_against_production_silo():
    # ambient PGHOST resolves to a production silo, but there's no DDL keyword --
    # a plain read is not what apply_migration.py's gate exists to protect.
    assert_allowed(
        "Bash",
        {"command": 'psql -c "SELECT * FROM foo"'},
        env={**_NEUTRAL_PG_ENV, "PGHOST": "db-" + _PROD_REF + "-pooler.example"},
    )


def test_allows_ddl_against_nonproduction_ref():
    assert_allowed(
        "Bash",
        {"command": 'psql -c "CREATE TABLE foo (id int)"'},
        env={**_NEUTRAL_PG_ENV, "PGHOST": "db-" + _NONPROD_REF},
    )


def test_allows_ddl_keyword_with_no_pg_tool_or_psycopg_reference():
    # a DDL-shaped word with no psql/psycopg involvement at all isn't this rule's job.
    assert_allowed(
        "Bash",
        {"command": 'echo "CREATE TABLE foo (id int)"'},
        env=_NEUTRAL_PG_ENV,
    )


# ---- Rule G exemption #2: exec_prod + --gate (irsyad's sanctioned path, bus
# #56599/#56607/#56672/#56684/#56752) -- mirrors the apply_migration.py exemption
# above, but CLASSIFIES the SQL rather than blocking every ungated call outright:
# a flat block would also wrongly catch exec_prod's DML-only calls (backfills,
# dry-runs), which never need a gate under exec_prod's own contract (#56684).
# Real invocation shape per cc-irsyad-coord (#56672): the SQL is a FILE
# (--inner-file), never inline -- these tests write real temp files and point
# real exec_prod-shaped commands at them, not synthetic inline SQL.

_EXEC_PROD_BIN = "python3 wingmen-irsyad/scripts/db/exec_prod.py --project-ref " + _PROD_REF


def _exec_prod_cmd(inner_file: str, gate: str = "") -> str:
    return (_EXEC_PROD_BIN + ' --inner-file ' + str(inner_file)
            + ' --reason "test" --pr 1051 --migration 435' + gate)


def test_allows_exec_prod_ddl_file_with_gate_flag(tmp_path):
    f = tmp_path / "mig435.sql"
    f.write_text("ALTER TABLE foo ADD COLUMN bar int;")
    assert_allowed("Bash", {"command": _exec_prod_cmd(f, " --gate 56599")}, env=_NEUTRAL_PG_ENV)


def test_allows_exec_prod_ddl_file_with_gate_flag_equals_form(tmp_path):
    # argparse's `type=int` also accepts `--gate=123`.
    f = tmp_path / "mig435.sql"
    f.write_text("CREATE TABLE foo (id int);")
    assert_allowed("Bash", {"command": _exec_prod_cmd(f, " --gate=56599")}, env=_NEUTRAL_PG_ENV)


def test_blocks_exec_prod_ddl_file_without_gate_flag(tmp_path):
    # THE regression this suite exists to pin: no DDL keyword appears anywhere on
    # the COMMAND LINE (the SQL is in --inner-file) -- the block must come from
    # actually reading and classifying the file's content, not from scanning argv.
    f = tmp_path / "mig435.sql"
    f.write_text("ALTER TABLE foo ADD COLUMN bar int;")
    assert_blocked("Bash", {"command": _exec_prod_cmd(f)}, env=_NEUTRAL_PG_ENV,
                   expect_substr="exec_prod needs --gate")


def test_allows_exec_prod_dml_only_file_without_gate(tmp_path):
    # the exact case a flat "block every ungated exec_prod call" would have broken
    # -- a DML-only backfill/dry-run needs no gate under exec_prod's own contract.
    f = tmp_path / "backfill.sql"
    f.write_text("UPDATE foo SET bar = 1 WHERE id = 2;")
    assert_allowed("Bash", {"command": _exec_prod_cmd(f)}, env=_NEUTRAL_PG_ENV)


def test_blocks_exec_prod_missing_inner_file_without_gate(tmp_path):
    missing = tmp_path / "does-not-exist.sql"
    assert_blocked("Bash", {"command": _exec_prod_cmd(missing)}, env=_NEUTRAL_PG_ENV,
                   expect_substr="exec_prod needs --gate")


def test_blocks_exec_prod_with_no_inner_file_flag_at_all():
    # inline SQL via some other flag (or a malformed invocation) -- no --inner-file
    # to read at all -- fail-closed, same posture as every other branch here.
    assert_blocked(
        "Bash",
        {"command": _EXEC_PROD_BIN + ' --sql "ALTER TABLE foo ADD COLUMN bar int" --reason "x"'},
        env=_NEUTRAL_PG_ENV,
        expect_substr="exec_prod needs --gate",
    )


def test_blocks_bare_exec_prod_basename_ddl_file_without_gate(tmp_path):
    # basename invocation (no .py), per orch-console's exact phrasing.
    f = tmp_path / "x.sql"
    f.write_text("DROP TABLE foo;")
    assert_blocked(
        "Bash",
        {"command": "exec_prod --project-ref " + _PROD_REF + " --inner-file " + str(f) + ' --reason "x"'},
        env=_NEUTRAL_PG_ENV,
        expect_substr="exec_prod needs --gate",
    )


_DDL_VECTORS_PATH = Path(__file__).parent.parent / "scripts" / "hooks" / "ddl_detect_vectors.json"
_DDL_VECTORS = json.loads(_DDL_VECTORS_PATH.read_text())


@pytest.mark.parametrize("vector", _DDL_VECTORS, ids=lambda v: v["label"])
def test_exec_prod_matches_the_shared_ddl_detect_vectors(tmp_path, vector):
    # the SAME 14 vectors exec_prod.py's own test suite loads (cc-irsyad-2, #56752)
    # -- proves the hook's classification can never drift from exec_prod's, because
    # it IS the same module, not a second implementation that happens to agree today.
    f = tmp_path / "v.sql"
    f.write_text(vector["sql"])
    cmd = _exec_prod_cmd(f)
    if vector["expected"]:   # DDL-shaped -> needs a gate
        assert_blocked("Bash", {"command": cmd}, env=_NEUTRAL_PG_ENV, expect_substr="exec_prod needs --gate")
    else:                    # DML-only -> no gate needed
        assert_allowed("Bash", {"command": cmd}, env=_NEUTRAL_PG_ENV)


def test_vendored_ddl_detect_matches_its_own_pinned_hash():
    # cc-quality's non-blocking hardening suggestion (#56797): nothing previously
    # caught a hand-edit of the vendored copy drifting from the hash recorded next
    # to the import. Extracts the pinned hash straight out of the comment (never
    # hardcoded here a second time, so this test can't itself go stale against a
    # legitimate re-sync) and asserts the LIVE vendored file still matches it.
    import hashlib

    hook_src = HOOK.read_text()
    m = re.search(r"sha256 ([0-9a-f]{64})\)", hook_src)
    assert m, "no sha256 pin comment found near the ddl_detect import -- did it move?"
    pinned_hash = m.group(1)

    vendored_path = HOOK.parent / "ddl_detect.py"
    live_hash = hashlib.sha256(vendored_path.read_bytes()).hexdigest()
    assert live_hash == pinned_hash, (
        f"scripts/hooks/ddl_detect.py has drifted from its pinned hash "
        f"(live={live_hash}, pinned={pinned_hash}) -- re-copy verbatim from ihsanos "
        f"and update the pin comment, never hand-edit the vendored copy"
    )


def test_apply_migration_exemption_unchanged_when_exec_prod_also_mentioned():
    # defensive: apply_migration.py's own exemption must still short-circuit first,
    # even if the command text also happens to mention exec_prod (e.g. in a comment).
    assert_allowed(
        "Bash",
        {"command": "python3 scripts/apply_migration.py 100 --silo " + _PROD_REF
                     + " --gate 123  # supersedes the old exec_prod rehearsal"},
        env=_NEUTRAL_PG_ENV,
    )


# ---- fail-closed on unparseable input ---------------------------------------------

def test_fails_closed_on_bad_json():
    r = subprocess.run([sys.executable, str(HOOK)], input="not json", text=True, capture_output=True)
    assert r.returncode == 2
