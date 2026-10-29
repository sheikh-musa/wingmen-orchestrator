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
