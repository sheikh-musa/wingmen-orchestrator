"""scripts/fire_fix1a_grant_ping.sh was a CAI-774-specific one-shot that already
fired/expired (Musa op#34845, console's ruling part 4) -- deleted rather than
migrated, same as the other dead legacy/ writers. Confirmed before deletion:
zero code references fleet-wide and not loaded in launchd (only the leftover
~/Library/LaunchAgents/dev.wingmen.fix1a-grant-ping.plist remained, outside
this repo). This guards against it quietly reappearing.
"""
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent


def test_fire_fix1a_grant_ping_sh_does_not_exist():
    assert not (REPO / "scripts" / "fire_fix1a_grant_ping.sh").exists()


def test_no_code_references_fire_fix1a_grant_ping_remain():
    skip_dirs = ("/tests/", "/.claude/", "/worktrees/", "/.venv/", "/node_modules/", "/.git/")
    offenders = []
    for path in REPO.rglob("*"):
        if path.is_dir() or not path.is_file():
            continue
        p = str(path)
        if any(d in p for d in skip_dirs) or path.suffix not in (".py", ".sh", ".yml", ".yaml"):
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if "fire_fix1a_grant_ping" in text:
            offenders.append(p)
    assert offenders == [], f"stale references to the deleted script: {offenders}"
