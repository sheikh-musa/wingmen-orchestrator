"""nazim_send_file.sh (and siblings) silently failed on filenames with commas/semicolons
(bus #44576). Seen live: nazim_send_file.sh on "Batch 20_21 Overview (op22917 fix,
DRAFT v1.0).xlsx" exited 26 with NO output and NO operator_messages row.

TWO bugs, tested separately here:

1. curl's own `-F field=@path` parser reads a literal ',' in `path` as the multi-file
   separator (`@file1,file2`), so a real filename containing one makes curl try to open a
   bogus second "file" and exit 26 (READ_ERROR). Fix (scripts/lib/tg_safe_upload.sh):
   stage the file under a syntax-safe symlink name and restore the real name via an
   explicit `;filename="..."` modifier.

2. `code=$(curl ...)` / `resp=$(curl ...)` under `set -euo pipefail` aborts the whole
   script on curl's non-zero exit, so the durable "--undelivered" log line never runs and
   the caller sees a silent failure. Fix: `... || code="curl_exit_$?"` (or the `resp=`
   equivalent) so a curl failure is captured as data instead of killing the script.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from tests.conftest import assert_dsn_is_not_production

_ROOT = Path(__file__).resolve().parent.parent
_LIB = _ROOT / "scripts" / "lib" / "tg_safe_upload.sh"
_SCRIPTS = {
    "nazim_send_file.sh": _ROOT / "scripts" / "nazim_send_file.sh",
    "nazim_send_photo.sh": _ROOT / "scripts" / "nazim_send_photo.sh",
    "tg_send_file.sh": _ROOT / "scripts" / "tg_send_file.sh",
    "irsyad_support_send_file.sh": _ROOT / "scripts" / "irsyad_support_send_file.sh",
    "angullia_send_photo.sh": _ROOT / "scripts" / "angullia_send_photo.sh",
    "cosem_tdu_support_send_photo.sh": _ROOT / "scripts" / "cosem_tdu_support_send_photo.sh",
    "reviewer_send_file.sh": _ROOT / "scripts" / "reviewer_send_file.sh",
}

_STAGE_CALL = re.compile(r"tg_safe_upload_stage\s+\w+\s+")
_CURL_FAILURE_CAPTURED = re.compile(r'\|\|\s*(code|resp)="?\{?[^\n]*curl_exit_\$\?')
_UNSAFE_FORM_VALUE = re.compile(r'-F\s+"(document|photo)=@\$')
# bus #44680: -F treats a value starting with '@' (upload) or '<' (read-file) as a file
# directive, not literal text — caption/chat_id must go through --form-string instead.
_UNSAFE_DASH_F_CAPTION_OR_CHAT = re.compile(r'-F\s+"(caption|chat_id)=')


def _code_only(text: str) -> str:
    out = []
    for line in text.splitlines():
        out.append("" if line.lstrip().startswith("#") else line)
    return "\n".join(out)


def test_lib_and_scripts_exist():
    assert _LIB.is_file(), f"missing {_LIB}"
    for name, path in _SCRIPTS.items():
        assert path.is_file(), f"missing {path} ({name})"


@pytest.mark.parametrize("name", _SCRIPTS.keys())
def test_script_sources_the_safe_upload_lib(name):
    code = _code_only(_SCRIPTS[name].read_text())
    assert "scripts/lib/tg_safe_upload.sh" in code, (
        f"{name} no longer sources tg_safe_upload.sh — a raw `-F field=@$VAR` reintroduces "
        "the comma/semicolon curl exit-26 bug (bus #44576)"
    )
    assert _STAGE_CALL.search(code), f"{name} does not call tg_safe_upload_stage"
    assert not _UNSAFE_FORM_VALUE.search(code), (
        f'{name} still builds -F "document=@$VAR"/"photo=@$VAR" directly from an unstaged path'
    )


@pytest.mark.parametrize("name", _SCRIPTS.keys())
def test_script_captures_curl_failure_instead_of_aborting(name):
    code = _code_only(_SCRIPTS[name].read_text())
    assert _CURL_FAILURE_CAPTURED.search(code), (
        f"{name}'s curl call has no `|| code/resp=...curl_exit_$?` fallback — under "
        "`set -euo pipefail` a curl failure aborts the script BEFORE the durable "
        "--undelivered log write ever runs (bus #44576)"
    )


@pytest.mark.parametrize("name", _SCRIPTS.keys())
def test_script_uses_form_string_for_caption_and_chat_id(name):
    """bus #44680: curl's -F parser reads a value starting with '@' as an upload
    directive and '<' as a read-file directive — a caption beginning with either
    either fails outright or (worse, on tg_send_file.sh's outbound path) leaks a
    local file's contents into the request. caption/chat_id must be --form-string."""
    code = _code_only(_SCRIPTS[name].read_text())
    assert not _UNSAFE_DASH_F_CAPTION_OR_CHAT.search(code), (
        f'{name} still passes caption/chat_id via raw -F — a value starting with "@" '
        "or \"<\" would be read as a file directive, not literal text (bus #44680)"
    )
    assert "--form-string" in code, f"{name} has no --form-string call at all"


def _run_bash(script: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script], cwd=cwd, capture_output=True, text=True, timeout=10,
    )


def test_tg_safe_upload_stage_sanitizes_comma_and_semicolon_filename(tmp_path):
    src = tmp_path / 'Batch 20_21 Overview (op22917 fix, DRAFT v1.0).xlsx'
    src.write_text("payload")

    script = f'''
set -euo pipefail
source "{_LIB}"
tg_safe_upload_stage document "{src}"
echo "FORM:$TG_SAFE_UPLOAD_FORM"
echo "TMPDIR:$TG_SAFE_UPLOAD_TMPDIR"
cat "$TG_SAFE_UPLOAD_TMPDIR"/upload.xlsx
rm -rf "$TG_SAFE_UPLOAD_TMPDIR"
'''
    r = _run_bash(script, tmp_path)
    assert r.returncode == 0, r.stderr

    form_line = next(l for l in r.stdout.splitlines() if l.startswith("FORM:"))
    tmpdir_line = next(l for l in r.stdout.splitlines() if l.startswith("TMPDIR:"))
    form = form_line[len("FORM:"):]
    tmpdir = tmpdir_line[len("TMPDIR:"):]

    # The @-path (before the first unescaped ';') must carry NO comma/semicolon —
    # that's the part curl's own parser splits on.
    at_path = form.split("=@", 1)[1].split(";filename=", 1)[0]
    assert "," not in at_path and ";" not in at_path, f"staged @-path still unsafe: {at_path!r}"
    assert tmpdir in at_path

    # The real filename must survive, quoted, in the filename= modifier.
    assert 'filename="Batch 20_21 Overview (op22917 fix, DRAFT v1.0).xlsx"' in form

    assert "payload" in r.stdout


def test_curl_exit_26_on_raw_comma_path_vs_staged_form_value(tmp_path):
    """Load-bearing proof the fix is real: curl itself, not a mock, exits 26 on a raw
    comma-containing @-path against an unreachable host, and does NOT on the staged form
    value (it instead fails on the network call, proving the -F value parsed clean)."""
    src = tmp_path / "a,b.txt"
    src.write_text("hi")
    unreachable = "http://127.0.0.1:1"  # port 1 refuses instantly, no live network needed

    raw = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "-F", f"document=@{src}", unreachable, "--max-time", "3"],
        capture_output=True, text=True,
    )
    assert raw.returncode == 26, (
        f"expected curl exit 26 (READ_ERROR) on a raw comma-path -F value — got "
        f"{raw.returncode}; if curl's own -F parsing changed, this test (not the fix) "
        "needs revisiting"
    )

    staged_dir = tmp_path / "staged"
    staged_dir.mkdir()
    safe = staged_dir / "upload.txt"
    safe.symlink_to(src)
    safe_form = f'document=@{safe};filename="a,b.txt"'

    staged = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "-F", safe_form, unreachable, "--max-time", "3"],
        capture_output=True, text=True,
    )
    assert staged.returncode != 26, (
        f"staged form value still trips curl's -F comma-parsing (exit {staged.returncode})"
    )


def test_curl_reads_at_prefixed_dash_f_caption_as_a_file_but_not_form_string():
    """Load-bearing proof for bus #44680: curl itself, not a mock, treats a -F value
    starting with '@' as a file-upload directive (tries to open it, fails/leaks) but
    treats the identical value passed via --form-string as literal text."""
    unreachable = "http://127.0.0.1:1"  # port 1 refuses instantly, no live network needed

    via_dash_f = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-F", "caption=@/no/such/file/here",
         unreachable, "--max-time", "3"],
        capture_output=True, text=True,
    )
    assert via_dash_f.returncode == 26, (
        f"expected curl exit 26 (READ_ERROR) when -F reads an '@...' caption as a "
        f"file directive — got {via_dash_f.returncode}; if curl's own -F parsing "
        "changed, this test (not the fix) needs revisiting"
    )

    via_form_string = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "--form-string", "caption=@/no/such/file/here",
         unreachable, "--max-time", "3"],
        capture_output=True, text=True,
    )
    assert via_form_string.returncode != 26, (
        f"--form-string still tripped curl's file-directive parsing (exit "
        f"{via_form_string.returncode}) — the caption was not sent as literal text"
    )


def test_forced_curl_failure_reaches_the_undelivered_log_branch(tmp_path):
    """Proof of fix #2 in isolation from ORCH_DIR (these scripts hardcode
    ORCH_DIR="$HOME/wingmen/orchestrator" — a fixed real checkout, not this worktree/repo
    checkout — so actually invoking nazim_send_file.sh etc. here would run a DIFFERENT
    file's code, not this PR's diff; see test_script_captures_curl_failure_instead_of_aborting
    for the static proof that the SHIPPED scripts carry the fix). This test instead proves
    the underlying mechanism: under `set -euo pipefail`, a bare `code=$(curl ...)` aborts the
    script before any later line runs, while `code=$(curl ...) || code="curl_exit_$?"` — the
    exact pattern each script now uses — lets the undelivered-log branch execute."""
    script = '''
set -euo pipefail
marker=""
cleanup() { marker="reached"; }
trap cleanup EXIT
code=$(curl -s -o /dev/null http://127.0.0.1:1 --max-time 2 -w "%{http_code}") || code="curl_exit_$?"
[ "$code" = "200" ] || echo "UNDELIVERED:$code"
'''
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    assert "UNDELIVERED:curl_exit_" in r.stdout, (
        f"the undelivered branch did not run after a captured curl failure: {r.stdout!r}"
    )


# ── caption-guarded send scripts: caption-specific guards (bus #44679) ─────────────
# angullia/cosem-tdu are CLIENT channels (the client can see them) and reviewer_send_file.sh
# is a GENERIC multi-channel tool that can just as easily land in one — a caption needs the
# same guards the text-send sibling runs on its own text — a caption is just as capable of
# leaking an internal name or an arg-swap as a plain message. Separately, curl's -F treats a
# value starting with '@' or '<' as a file to read rather than literal text, so a caption
# like "@Rhaihan" would try to upload/read a local file instead of being sent — --form-string
# has no such special-casing.
_UNSAFE_CAPTION_FORM = re.compile(r'-F\s+"caption=')
_CAPTION_GUARDED_SCRIPTS = (
    "angullia_send_photo.sh",
    "cosem_tdu_support_send_photo.sh",
    "reviewer_send_file.sh",
)


@pytest.mark.parametrize("name", _CAPTION_GUARDED_SCRIPTS)
def test_caption_guarded_script_guards_the_caption_like_a_client_message(name):
    code = _code_only(_SCRIPTS[name].read_text())
    assert "_send_arg_guard" in code, (
        f"{name} doesn't run _send_arg_guard on the caption — a photo/file "
        "caption can carry the same channel/tag arg-swap footgun the text-send sibling "
        "guards against on its text (op#16353)"
    )
    assert "_client_send_leak_guard" in code, (
        f"{name} doesn't run _client_send_leak_guard on the caption — "
        "this can reach a CLIENT channel, so a caption can leak an internal name/escalation "
        "phrase exactly like the text path does without it (op#21145)"
    )


@pytest.mark.parametrize("name", _CAPTION_GUARDED_SCRIPTS)
def test_caption_guarded_script_caption_uses_form_string_not_dash_F(name):
    code = _code_only(_SCRIPTS[name].read_text())
    assert not _UNSAFE_CAPTION_FORM.search(code), (
        f'{name} still builds -F "caption=..." — curl treats a value '
        'starting with @ or < as a file to read, so a caption like "@Rhaihan" or "<3" '
        "would try to upload/read a local file instead of being sent literally. "
        "Use --form-string for the caption (and chat_id)."
    )
    assert '--form-string "caption=' in code, (
        f"expected --form-string for the caption in {name}"
    )


def test_form_string_sends_at_prefixed_caption_literally_not_as_file():
    """Load-bearing proof: curl's -F treats a value starting with '@' as a file to
    read (failing locally before any network call), while --form-string sends the
    same value as a literal field — the fix angullia_send_photo.sh's caption relies
    on."""
    unreachable = "http://127.0.0.1:1"  # port 1 refuses instantly, no live network needed
    caption = "@Rhaihan, does this look right?"

    dash_f = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "-F", f"caption={caption}", unreachable, "--max-time", "3"],
        capture_output=True, text=True,
    )
    assert dash_f.returncode == 26, (
        f"expected curl exit 26 (READ_ERROR) on -F with an @-prefixed value — got "
        f"{dash_f.returncode}; if curl's own -F parsing changed, this test (not the fix) "
        "needs revisiting"
    )

    form_string = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "--form-string", f"caption={caption}", unreachable, "--max-time", "3"],
        capture_output=True, text=True,
    )
    assert form_string.returncode != 26, (
        f"--form-string still trips curl's @ file-upload parsing (exit {form_string.returncode})"
    )


# ── reviewer_send_file.sh: generic bot_channels lookup + extension dispatch ─────────
# reviewer_send_file.sh (the FILE counterpart of reviewer_send.sh) resolves
# token_env_key/allowed_chat_ids from bot_channels at runtime instead of hardcoding a
# single channel's token/chat like its siblings above. These tests run the ACTUAL
# heredoc/case-statement blocks extracted verbatim from the shipped script (so a future
# edit can't silently drift from what's tested) against an ephemeral throwaway Postgres
# (never the live substrate — assert_dsn_is_not_production guards every DSN used here).
_REVIEWER_SEND_FILE = _SCRIPTS["reviewer_send_file.sh"]
_REVIEWER_SEND = _ROOT / "scripts" / "reviewer_send.sh"
_CHANNEL_LOOKUP_SCRIPTS = (_REVIEWER_SEND_FILE, _REVIEWER_SEND)
_HEREDOC_RE = re.compile(r"<<'PY'\n(.*?)\nPY\n", re.DOTALL)
_CASE_BLOCK_RE = re.compile(r"shopt -s nocasematch\n.*?\nshopt -u nocasematch", re.DOTALL)


def _extract_channel_lookup_snippet(script_path: Path = _REVIEWER_SEND_FILE) -> str:
    m = _HEREDOC_RE.search(script_path.read_text())
    assert m, f"{script_path.name}: could not find the <<'PY' ... PY channel-lookup heredoc"
    return m.group(1)


def _extract_method_dispatch_block() -> str:
    m = _CASE_BLOCK_RE.search(_REVIEWER_SEND_FILE.read_text())
    assert m, "reviewer_send_file.sh: could not find the nocasematch METHOD dispatch block"
    return m.group(0)


@pytest.fixture
def bot_channels_db(pg_dsn):
    assert_dsn_is_not_production(pg_dsn)
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("""CREATE TABLE bot_channels (
            channel_key text PRIMARY KEY, token_env_key text,
            allowed_chat_ids bigint[])""")
    return pg_dsn


@pytest.fixture
def scoped_dot_env():
    """Temporarily writes a real repo-root .env (gitignored, never committed) so
    scripts/bus_send.dburl — which resolves its .env path relative to its OWN file,
    i.e. this repo root when PYTHONPATH points here — has a file to prefer. Refuses to
    run if a real .env is already sitting there (never overwrite a real secrets file)."""
    env_path = _ROOT / ".env"
    assert not env_path.exists(), f"refusing to touch a real {env_path} — remove it or run elsewhere"

    def _write(dsn: str) -> None:
        env_path.write_text(f"DATABASE_URL={dsn}\n")

    try:
        yield _write
    finally:
        env_path.unlink(missing_ok=True)


def _run_channel_lookup(channel: str, dsn: str,
                         script_path: Path = _REVIEWER_SEND_FILE) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": dsn, "PYTHONPATH": str(_ROOT)}
    env.pop("SUPABASE_DB_URL", None)
    return subprocess.run(
        [sys.executable, "-", channel], input=_extract_channel_lookup_snippet(script_path),
        text=True, capture_output=True, env=env, cwd=str(_ROOT), timeout=20,
    )


def test_channel_lookup_resolves_token_and_chat_from_bot_channels(bot_channels_db):
    with psycopg.connect(bot_channels_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO bot_channels (channel_key, token_env_key, allowed_chat_ids) "
            "VALUES (%s, %s, %s)",
            ("t-reviewer-file", "T_REVIEWER_FILE_BOT_TOKEN", [123456]),
        )
    r = _run_channel_lookup("t-reviewer-file", bot_channels_db)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout.strip() == "T_REVIEWER_FILE_BOT_TOKEN 123456"


def test_channel_lookup_fails_closed_for_unknown_channel(bot_channels_db):
    r = _run_channel_lookup("no-such-channel", bot_channels_db)
    assert r.returncode == 2, f"stdout={r.stdout!r} stderr={r.stderr!r}"


def test_channel_lookup_fails_closed_when_no_allowed_chat_ids(bot_channels_db):
    with psycopg.connect(bot_channels_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO bot_channels (channel_key, token_env_key, allowed_chat_ids) "
            "VALUES (%s, %s, %s)",
            ("t-no-chat", "T_NO_CHAT_BOT_TOKEN", []),
        )
    r = _run_channel_lookup("t-no-chat", bot_channels_db)
    assert r.returncode == 2, f"stdout={r.stdout!r} stderr={r.stderr!r}"


@pytest.mark.parametrize("script_path", _CHANNEL_LOOKUP_SCRIPTS, ids=lambda p: p.name)
def test_channel_lookup_uses_dburl_not_the_raw_inherited_dsn_fallback(script_path):
    """bus #46184: both scripts must resolve their DSN via scripts.bus_send.dburl
    (.env-file-wins), not nervous_system.nazim_bus_notify._dsn (inherited-env-first) —
    a long-running caller holding a pre-rotation DATABASE_URL would otherwise fail auth
    and feed the pooler circuit breaker (2026-09-28 rotation incident, hit again here
    2026-09-29 before this fix)."""
    code = _code_only(script_path.read_text())
    assert "from scripts.bus_send import dburl" in code, (
        f"{script_path.name} doesn't resolve its DSN via scripts.bus_send.dburl"
    )
    assert "nazim_bus_notify" not in code, (
        f"{script_path.name} still imports nazim_bus_notify._dsn (inherited-env-first — "
        "the exact rotation trap bus #46184 flagged)"
    )


@pytest.mark.parametrize("script_path", _CHANNEL_LOOKUP_SCRIPTS, ids=lambda p: p.name)
def test_channel_lookup_prefers_dot_env_dsn_over_bogus_inherited_env(
    script_path, bot_channels_db, scoped_dot_env
):
    """Load-bearing proof for bus #46184: runs the ACTUAL heredoc extracted from the
    shipped script with a BOGUS DATABASE_URL in the process env and the real (ephemeral)
    DSN only in .env — must still connect via the .env value, proving .env truly wins
    over the inherited env instead of merely falling back to it."""
    with psycopg.connect(bot_channels_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO bot_channels (channel_key, token_env_key, allowed_chat_ids) "
            "VALUES (%s, %s, %s)",
            ("t-dotenv-wins", "T_DOTENV_WINS_BOT_TOKEN", [777]),
        )
    scoped_dot_env(bot_channels_db)
    env = {**os.environ, "DATABASE_URL": "postgresql://bogus:bogus@127.0.0.1:1/bogus",
           "PYTHONPATH": str(_ROOT)}
    env.pop("SUPABASE_DB_URL", None)
    r = subprocess.run(
        [sys.executable, "-", "t-dotenv-wins"],
        input=_extract_channel_lookup_snippet(script_path),
        text=True, capture_output=True, env=env, cwd=str(_ROOT), timeout=20,
    )
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout.strip() == "T_DOTENV_WINS_BOT_TOKEN 777"


@pytest.mark.parametrize("filename,expected", [
    ("report.pdf", "sendDocument"),
    ("screenshot.png", "sendPhoto"),
    ("photo.JPG", "sendPhoto"),
    ("archive.zip", "sendDocument"),
    ("weird,name;here.jpeg", "sendPhoto"),
])
def test_reviewer_send_file_method_dispatch_by_extension(filename, expected):
    block = _extract_method_dispatch_block()
    script = f'FILE="{filename}"\n{block}\necho "METHOD:$METHOD"'
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    assert f"METHOD:{expected}" in r.stdout
