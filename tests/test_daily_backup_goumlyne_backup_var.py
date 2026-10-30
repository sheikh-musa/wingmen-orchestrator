"""The goumlyne backup must stay cross-org: it prefers GOUMLYNE_BACKUP_RO_DATABASE_URL and only
falls back to GOUMLYNE_RO_DATABASE_URL, which is moving to the narrowed irsyad_monitor_ro role
(console #81213 item 4, sequence #82789 STEP 0). No network, no DSN values."""
import re
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "daily_backup.sh"


def _goumlyne_call() -> str:
    lines = [l for l in SCRIPT.read_text().splitlines() if l.startswith('backup_client_silo "irsyad-goumlyne"')]
    assert len(lines) == 1, lines
    return lines[0]


def test_goumlyne_backup_prefers_backup_var_with_fallback():
    assert _goumlyne_call().endswith('"${GOUMLYNE_BACKUP_RO_DATABASE_URL:-${GOUMLYNE_RO_DATABASE_URL:-}}"')


def _expand(env: dict) -> str:
    expr = re.search(r'"(\$\{GOUMLYNE_BACKUP_RO_DATABASE_URL.*\})"$', _goumlyne_call()).group(1)
    out = subprocess.run(["bash", "-c", f'printf %s "{expr}"'], env=env, capture_output=True, text=True, check=True)
    return out.stdout


def test_expansion_precedence():
    assert _expand({"GOUMLYNE_BACKUP_RO_DATABASE_URL": "B", "GOUMLYNE_RO_DATABASE_URL": "R"}) == "B"
    assert _expand({"GOUMLYNE_RO_DATABASE_URL": "R"}) == "R"
    assert _expand({}) == ""


def test_backup_var_sourced_from_dedicated_file_after_env():
    """console #83193 ruling B: the broad DSN lives in its own 0600 file, never in the bundle-feeding .env."""
    text = SCRIPT.read_text()
    assert 'BACKUP_RO_ENV_FILE="$HOME/.config/wingmen/goumlyne_backup_ro.env"' in text
    guarded = 'if [ -r "$BACKUP_RO_ENV_FILE" ]; then source "$BACKUP_RO_ENV_FILE"; fi'
    assert guarded in text
    assert text.index('source "$ENV_FILE"') < text.index(guarded) < text.index('backup_client_silo "irsyad-goumlyne"')


def test_dedicated_file_wins_and_is_optional(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("GOUMLYNE_BACKUP_RO_DATABASE_URL=OLD\n")
    backup_file = tmp_path / "goumlyne_backup_ro.env"
    snippet = (
        f'source "{env_file}"; BACKUP_RO_ENV_FILE="{backup_file}"; '
        'if [ -r "$BACKUP_RO_ENV_FILE" ]; then source "$BACKUP_RO_ENV_FILE"; fi; '
        'printf %s "${GOUMLYNE_BACKUP_RO_DATABASE_URL:-}"'
    )
    run = lambda: subprocess.run(["bash", "-c", snippet], env={}, capture_output=True, text=True, check=True).stdout
    assert run() == "OLD"
    backup_file.write_text("GOUMLYNE_BACKUP_RO_DATABASE_URL=NEW\n")
    assert run() == "NEW"
