"""credential_store_guard.py — PREVENTION for the 30 Oct client-credential leak
(orch-console bus #87265/#87268/#87276, P0, Musa op 36795).

The real incident: a body analysed a client's WooCommerce store by dumping the
`wp_options` rows matching `wc_facebook_*`. The secret came back as ordinary SELECT
output, entered the transcript, and was re-sent to the model provider every turn
after that. These tests pin the refusal BEFORE execution, using the exact command
shape of that dump, and pin that ordinary store work still runs.

RIGHT-REASON asserts (the TDD warning): every DENY asserts exit 2 AND the guard's
OWN marker in stderr — never just a non-zero exit, which a crash also gives. Every
ALLOW asserts exit 0, so the allow path is proven to have run.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "scripts" / "hooks" / "credential_store_guard.py"
MARKER = "credential_store_guard"


def run_hook(tool_name, tool_input, env=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_name": tool_name, "tool_input": tool_input}),
        text=True, capture_output=True, env=full_env,
    )


def assert_denied(tool_name, tool_input, env=None):
    r = run_hook(tool_name, tool_input, env=env)
    assert r.returncode == 2, (
        f"expected DENY for {tool_input!r}, got exit {r.returncode}, "
        f"stdout={r.stdout!r} stderr={r.stderr!r}")
    assert MARKER in r.stderr, f"denied for the WRONG reason: {r.stderr!r}"
    return r.stderr


def assert_allowed(tool_name, tool_input, env=None):
    r = run_hook(tool_name, tool_input, env=env)
    assert r.returncode == 0, (
        f"expected ALLOW for {tool_input!r}, got exit {r.returncode}, "
        f"stderr={r.stderr!r}")


def bash(cmd):
    return ("Bash", {"command": cmd})


# ── the 30 October vector, replayed ─────────────────────────────────────────────

def test_30_oct_woocommerce_options_dump_is_refused():
    """The exact shape of the leak: option_name LIKE 'wc_facebook%'."""
    assert_denied(*bash(
        'psql "$WOO_DSN" -tAc "SELECT option_name, option_value FROM wp_options '
        "WHERE option_name LIKE 'wc_facebook%'\""))


def test_secret_named_option_read_is_refused_even_when_narrow():
    """Narrowing to one row does not help when that row IS the secret."""
    stderr = assert_denied(*bash(
        'psql "$WOO_DSN" -tAc "SELECT option_value FROM wp_options '
        "WHERE option_name = 'wc_facebook_feed_url_secret'\""))
    assert "SECRET-named" in stderr


def test_select_star_on_a_client_store_table_is_refused():
    assert_denied(*bash('psql "$WOO_DSN" -c "SELECT * FROM wp_options"'))


def test_full_dump_of_a_client_database_is_refused():
    assert_denied(*bash('mysqldump -u root wordpress > /tmp/dump.sql'))
    assert_denied(*bash('pg_dump "$WOO_DSN" -t wp_options'))
    assert_denied(*bash('psql "$WOO_DSN" -c "COPY wp_options TO STDOUT"'))


def test_wp_cli_store_dumps_are_refused():
    assert_denied(*bash("wp option list --search=wc_facebook*"))
    assert_denied(*bash('wp db query "SELECT option_value FROM wp_options"'))


def test_usermeta_password_hashes_are_refused():
    assert_denied(*bash(
        'psql "$WOO_DSN" -c "SELECT user_login, user_pass FROM wp_users"'))


# ── the narrow non-secret path must keep working ────────────────────────────────

def test_narrow_named_non_secret_option_still_works():
    assert_allowed(*bash(
        'psql "$WOO_DSN" -tAc "SELECT option_value FROM wp_options '
        "WHERE option_name = 'woocommerce_currency'\""))
    assert_allowed(*bash(
        'psql "$WOO_DSN" -tAc "SELECT option_value FROM wp_options '
        "WHERE option_name = 'siteurl'\""))


def test_named_non_secret_columns_on_a_store_table_work():
    assert_allowed(*bash(
        'psql "$WOO_DSN" -c "SELECT ID, post_title FROM wp_posts LIMIT 5"'))


# ── client-store files (the gaps the sibling guard does not cover) ──────────────

def test_read_of_wp_config_is_refused():
    assert_denied("Read", {"file_path": "/srv/site/wp-config.php"})
    assert_denied("Read", {"file_path": "/srv/site/wp-config.php.bak"})


def test_read_of_a_non_dotfile_env_is_refused():
    """secrets_transcript_guard's Rule A matches `.env`, not `client.env`/`woo.env`."""
    assert_denied("Read", {"file_path": "/srv/site/client.env"})
    assert_denied("Read", {"file_path": "/srv/site/woo.env"})


def test_read_of_a_store_settings_export_is_refused():
    assert_denied("Read", {"file_path": "/srv/site/shopify-settings.json"})
    assert_denied("Read", {"file_path": "/srv/site/woocommerce-config.json"})


def test_read_of_a_token_cache_is_refused():
    assert_denied("Read", {"file_path": "/home/x/.netrc"})
    assert_denied("Read", {"file_path": "/home/x/oauth_token.json"})


def test_catting_a_client_store_file_is_refused():
    assert_denied(*bash("cat /srv/site/wp-config.php"))
    assert_denied(*bash("grep DB_PASSWORD /srv/site/wp-config.php"))


def test_metadata_only_touches_are_allowed():
    """`ls`/`stat`/`chmod` cannot put a credential in the transcript."""
    assert_allowed(*bash("ls -l /srv/site/wp-config.php"))
    assert_allowed(*bash("chmod 600 /srv/site/wp-config.php"))


# ── sanctioned pathways: the value never reaches the transcript ─────────────────

def test_pipe_into_the_vault_helper_is_allowed():
    assert_allowed(*bash(
        'psql "$WOO_DSN" -tAc "SELECT option_value FROM wp_options '
        "WHERE option_name = 'wc_facebook_feed_url_secret'\" | "
        "scripts/put_client_secret.py MODESTY_HUT_WC_FB_FEED_SECRET --reason test"))


def test_pipe_into_a_hash_or_length_sink_is_allowed():
    assert_allowed(*bash(
        'psql "$WOO_DSN" -tAc "SELECT option_value FROM wp_options '
        "WHERE option_name = 'wc_facebook_feed_url_secret'\" | sha256sum"))
    assert_allowed(*bash(
        'psql "$WOO_DSN" -tAc "SELECT option_value FROM wp_options '
        "WHERE option_name = 'wc_facebook_feed_url_secret'\" | wc -c"))


def test_a_sink_does_not_launder_a_second_copy():
    """`| tee /tmp/x` keeps the plaintext readable by a later call."""
    assert_denied(*bash(
        'psql "$WOO_DSN" -tAc "SELECT option_value FROM wp_options '
        "WHERE option_name = 'wc_facebook_feed_url_secret'\" "
        "| tee /tmp/leak | sha256sum"))


# ── secret-reading CLIs with no fixed output shape ──────────────────────────────

def test_secret_reading_cli_is_refused_unredirected():
    assert_denied(*bash(
        "firebase functions:secrets:access SCHEDULE_PDF_S2S_SECRET --project cosem-adcda-cb6d9"))
    assert_denied(*bash("gcloud secrets versions access latest --secret=FOO"))
    assert_denied(*bash("aws secretsmanager get-secret-value --secret-id foo"))


def test_secret_reading_cli_into_the_vault_helper_is_allowed():
    assert_allowed(*bash(
        "gcloud secrets versions access latest --secret=FOO "
        "| scripts/put_client_secret.py FOO_SECRET --reason test"))


# ── fail-closed + provider-independent ─────────────────────────────────────────

def test_unreadable_input_fails_closed():
    r = subprocess.run([sys.executable, str(HOOK)], input="not json",
                       text=True, capture_output=True)
    assert r.returncode == 2
    assert MARKER in r.stderr


def test_verdict_is_identical_on_a_deepseek_routed_lane():
    """The verdict is computed from the tool call alone — no provider is consulted —
    so a DeepSeek-/GLM-routed lane gets exactly the same decision (bus #87265)."""
    dump = bash('psql "$WOO_DSN" -tAc "SELECT * FROM wp_options"')
    narrow = bash('psql "$WOO_DSN" -tAc "SELECT option_value FROM wp_options '
                  "WHERE option_name = 'siteurl'\"")
    for tool, ti in (dump, narrow):
        plain = run_hook(tool, ti)
        routed = run_hook(tool, ti, env={
            "ANTHROPIC_BASE_URL": "https://api.deepseek.example/anthropic",
            "ANTHROPIC_MODEL": "deepseek-flash",
            "CLAUDE_CODE_MODEL": "deepseek-flash",
        })
        assert plain.returncode == routed.returncode, (tool, ti)


# ── the guard must not get in the way of ordinary fleet work ───────────────────

FLEET_COMMANDS = [
    "set -a; . ./.env; set +a",
    "source ~/wingmen/orchestrator/.env",
    "git status --short",
    "git worktree add ../orchestrator.wt-x some-branch",
    "scripts/pytest_local.sh tests/test_bus_send.py",
    '.venv/bin/python3 scripts/bus_send.py --to cc-x --priority P2 --type ack --subject s --body b',
    'psql "$DATABASE_URL" -tAc "SELECT count(*) FROM agent_messages WHERE read_at IS NULL"',
    'curl -s -H "Authorization: Bearer $TOK" http://127.0.0.1:8787/api/fleet',
    "ls -la ~/wingmen/keys/",
    "tmux send-keys -t fleet-health Enter",
]


def test_ordinary_fleet_commands_are_not_blocked():
    """A guard that blocks normal work gets disabled, and a disabled guard protects
    nothing. Every one of these is a real command from this fleet's logs."""
    for cmd in FLEET_COMMANDS:
        assert_allowed(*bash(cmd))


# ── false positives found by replaying 92,569 real fleet commands ───────────────
# Each of these DID trip an earlier revision of the guard. They are pinned here so
# the lesson (scope by the statement's own command, not by words inside a message)
# cannot be undone by a later "make it stricter" edit.

def test_a_commit_message_mentioning_woocommerce_and_secret_is_allowed():
    assert_allowed(*bash(
        'git add scripts/secret_hash_sweep.py && git commit -m '
        "'fix store option sweep: woocommerce secret handling'"))


def test_a_bus_message_quoting_a_query_is_allowed():
    """Prose ABOUT the incident must not be refused as if it were the incident."""
    assert_allowed(*bash(
        '.venv/bin/python3 scripts/bus_send.py --to cc-x --priority P1 --type blocker '
        '--subject s --body "the leak came from SELECT option_name, option_value FROM '
        'wp_options WHERE option_name LIKE wc_facebook%"'))


def test_scoped_grep_of_an_npmrc_is_allowed_but_a_whole_file_print_is_not():
    assert_allowed(*bash('grep -n "functions-shared" functions/.npmrc'))
    assert_denied(*bash("cat functions/.npmrc"))


def test_key_name_only_extraction_is_allowed_but_the_whole_env_is_not():
    """`grep -o '^[A-Z_]*='` provably stops before the value."""
    assert_allowed(*bash('grep -oh "^[A-Z_]*=" /tmp/prod-env-check.env | sort -u'))
    assert_denied(*bash("cat /tmp/prod-env-check.env"))
    assert_denied(*bash('grep -n "SUPABASE_SERVICE" /tmp/prod-env-check.env'))


def test_process_env_is_code_not_a_file():
    assert_allowed(*bash('grep -rln "process.env" tests/ | head -20'))
    assert_allowed(*bash('grep -rhoE "process\\.env\\.[A-Z_0-9]+" tests/ | sort -u'))


def test_a_pipe_stage_that_prints_its_own_output_is_not_a_file_dump():
    """`vercel env pull f | tail -3` prints tail's output, not f — but `cat f` does."""
    assert_allowed(*bash(
        "rm -f /tmp/prod-env-check.env; vercel env pull /tmp/prod-env-check.env "
        "--environment=production 2>&1 | tail -2"))
    assert_denied(*bash("cat /tmp/prod-env-check.env"))


def test_moving_a_store_file_between_hosts_is_not_a_transcript_leak():
    assert_allowed(*bash(
        "scp -p gzb:/home/x/.config/wingmen/monitor_ro.env /tmp/scratch/monitor_ro.env"))


def test_an_interpreter_heredoc_that_mentions_dot_env_is_allowed():
    """A heredoc body is not an argument: this only NAMES the file."""
    assert_allowed(*bash(
        "python3 - <<'PY'\n# reads demo.env in the fixture\nprint('ok')\nPY"))


def test_an_interpreter_one_liner_that_opens_a_store_file_is_refused():
    assert_denied(*bash(
        'python3 -c "print(open(\'/srv/site/wp-config.php\').read())"'))


# ── cc-quality 87497 findings B/D/A/E (deny tests for each real bypass) ─────────

def test_env_prefixed_db_client_is_refused():
    """Finding B: `env VAR=val psql` is the NORMAL way to pass a DB password, and an
    `env` word defeated the leading-token anchor, so Rule 2 never fired."""
    assert_denied(*bash(
        'env PGPASSWORD=x psql "$WOO_DSN" -c "select * from wp_options"'))
    assert_denied(*bash(
        'env PGPASSWORD=x PGSSLMODE=require psql "$WOO_DSN" -tAc "select * from wp_users"'))


def test_command_and_exec_prefixed_db_client_is_refused():
    """Finding D: same root cause as B — a launcher word before the DB client."""
    assert_denied(*bash('command psql "$WOO_DSN" -c "select * from wp_options"'))
    assert_denied(*bash('exec psql "$WOO_DSN" -c "select * from wp_options"'))
    assert_denied(*bash(
        'env A=b command psql "$WOO_DSN" -c "select * from wp_options"'))


def test_launcher_prefixes_do_not_create_false_positives():
    """Stripping launchers must not turn ordinary work into a refusal."""
    assert_allowed(*bash("env FOO=bar ls -l /srv/site/wp-config.php"))
    assert_allowed(*bash("command -v psql"))
    assert_allowed(*bash('psql "$WOO_DSN" -tAc "SELECT option_value FROM wp_options '
                         "WHERE option_name = 'siteurl'\""))
    assert_allowed(*bash('env FOO=1 psql "$WOO_DSN" -tAc "SELECT ID FROM wp_posts LIMIT 1"'))


def test_verbless_command_substitution_read_of_a_store_file_is_refused():
    """Finding A: `$(< file)` reads a file with NO command verb, so the verb-anchored
    Rule 1 never saw it. `$(cat file)` was already caught."""
    assert_denied(*bash('echo "$(< wp-config.php)"'))
    assert_denied(*bash('echo "$(< /srv/site/wp-config.php)" | head -5'))
    assert_denied(*bash('X="$(< /srv/site/client.env)"; echo "$X"'))
    assert_allowed(*bash('echo "$(< /tmp/notes.txt)"'))


def test_non_wp_client_store_dsn_env_is_refused():
    """Finding E, narrow half: a Stripe/Shopify store's DSN env var is the same leak
    class as $WOO_DSN, and the fix is cheap because the env-var NAME is unambiguous."""
    assert_denied(*bash('psql "$STRIPE_DSN" -c "select * from accounts"'))
    assert_denied(*bash('psql "$SHOPIFY_DATABASE_URL" -c "select * from orders"'))
    assert_allowed(*bash('psql "$SUBSTRATE_DSN" -tAc "select 1"'))


def test_ssh_wrapped_store_reads_are_refused():
    """Rule 2 anchors on the DB client that LEADS a statement, so a read wrapped in
    `ssh host '…'` used to be invisible to it. Measured against the shipped code on
    2026-11-01: the local `wp option list --search='wc_facebook_*'` was DENIED while
    the identical command behind `ssh` was ALLOWED. The fleet reaches client hosts
    over ssh, so the wrapper is not an exotic shape — it is how this work is done."""
    assert_denied(*bash('ssh modestyhut "wp option list --search=\'wc_facebook_*\'"'))
    assert_denied(*bash('ssh modestyhut "wp db export -"'))
    assert_denied(*bash('ssh modestyhut "mysql -e \'SELECT * FROM wp_options\'"'))
    # a flag that CONSUMES the next token, and a user@host form: both must be skipped
    # as ssh arguments, not mistaken for the remote command
    assert_denied(*bash('ssh -p 2222 modesty@modestyhut "wp db export -"'))
    assert_denied(*bash('ssh -o BatchMode=yes -i ~/.ssh/id_ed25519 modest@host "wp db export -"'))
    assert_denied(*bash('sudo ssh modestyhut "wp db export -"'))


def test_ssh_wrapped_store_read_is_refused_through_each_wrapper_shape():
    """The wrapper composes: sshpass in front of ssh, `bash -c` inside it, and an
    unquoted remote command are all the same read."""
    assert_denied(*bash('sshpass -p x ssh modestyhut "wp option list"'))
    assert_denied(*bash('ssh modestyhut bash -c "wp db export -"'))
    assert_denied(*bash('ssh modestyhut wp db export -'))
    assert_denied(*bash('bash -c "wp db export -"'))


def test_absolute_path_ssh_binary_is_judged_like_the_bare_name():
    """cc-quality 89507 observation 2: the anchor `^(?:ssh|sshpass)\\b` missed an
    ABSOLUTE-PATH ssh binary, so `/usr/bin/ssh host 'wp option list'` was ALLOWED while
    the bare-name form was DENIED — a one-word bypass of the whole wrapper fix. The
    fleet writes absolute paths whenever a shell has a thin PATH (launchd, cron, the
    Studio breakglass), so this is a shape that shows up in practice, not a curiosity."""
    assert_denied(*bash('/usr/bin/ssh modestyhut "wp db export -"'))
    assert_denied(*bash('/usr/bin/ssh modestyhut "wp option list"'))
    assert_denied(*bash('/bin/sshpass -p x /usr/bin/ssh modestyhut "wp option list"'))
    assert_denied(*bash('sudo /usr/bin/ssh -p 2222 modest@host "wp db export -"'))
    # the ordinary-work controls must survive the wider anchor
    assert_allowed(*bash("/usr/bin/ssh gzb 'tmux ls'"))
    assert_allowed(*bash("/usr/bin/ssh-keygen -lf ~/.ssh/id_ed25519.pub"))


def test_a_subshell_that_does_ordinary_work_still_runs():
    assert_allowed(*bash('bash -c "git status --short"'))
    assert_allowed(*bash('sh -c "ls scripts | head -3"'))


def test_ssh_wrapped_store_file_print_is_refused():
    """Rule 1 was already wrapper-proof (it matches the PATH, not the leading verb),
    so this is a regression pin: the ssh fix must not disturb it."""
    assert_denied(*bash('ssh modestyhut "cat /srv/modestyhut/wp-config.php"'))
    assert_denied(*bash('ssh modestyhut "grep DB_PASSWORD /srv/modestyhut/client.env"'))


def test_ssh_wrapped_ordinary_work_still_runs():
    """The whole fleet runs its remote work over ssh; a guard that refuses `tmux ls`
    on the other host gets switched off, and a switched-off guard protects nothing."""
    assert_allowed(*bash("ssh gzb 'tmux ls'"))
    assert_allowed(*bash('ssh gzb "sudo cat /etc/hostname"'))
    assert_allowed(*bash("ssh modestyhut 'wp option get woocommerce_currency'"))
    assert_allowed(*bash(
        'ssh modestyhut \'psql "$WOO_DSN" -tAc "SELECT option_value FROM wp_options '
        "WHERE option_name = 'siteurl'\"'"))
    # interactive shells and key management are not reads of a store
    assert_allowed(*bash("ssh gzb"))
    assert_allowed(*bash("ssh-keygen -lf ~/.ssh/id_ed25519.pub"))
    assert_allowed(*bash("scp /tmp/report.pdf gzb:/tmp/"))


def test_ssh_wrapped_read_into_the_vault_sink_is_allowed():
    """The sanctioned path stays open through ssh: the value goes to the vault and
    only a hash prefix is printed, so it never enters the transcript."""
    assert_allowed(*bash(
        'ssh modestyhut \'psql "$WOO_DSN" -tAc "SELECT option_value FROM wp_options '
        "WHERE option_name='wc_facebook_app_secret'\"' | "
        "scripts/put_client_secret.py MODESTYHUT_FB_SECRET --reason 'rotate'"))


def test_documented_residuals_stay_allowed():
    """Findings C and the broad half of E are NOT fixed here, deliberately — the
    playbook's residual list names them. Pinned so the gap is stated in code, not
    just in prose: if a later change makes these deny, this test says so loudly
    rather than leaving the doc stale."""
    # C: cross-stage dataflow through xargs — the store name is in stage 1, the
    # printing verb in stage 2, so neither stage matches Rule 1's argument test.
    assert_allowed(*bash("echo wp-config.php | xargs cat"))
    # E (broad): Rule 2 identifies a CLIENT SITE by WordPress markers only, so a
    # non-WP store's bare table names are not recognised.
    assert_allowed(*bash('psql "$CLIENT_DSN" -c "select * from stripe_events"'))


def test_guard_is_registered_in_project_settings():
    settings = json.loads((REPO / ".claude" / "settings.json").read_text())
    commands = [
        h["command"]
        for entries in settings.get("hooks", {}).get("PreToolUse", [])
        for h in entries.get("hooks", [])
        if h.get("type") == "command"
    ]
    hit = [c for c in commands if "credential_store_guard.py" in c]
    assert hit, "credential_store_guard.py is not wired into .claude/settings.json"
    assert "$CLAUDE_PROJECT_DIR" in hit[0], (
        "hook commands must use $CLAUDE_PROJECT_DIR so the guard resolves on any host")
