"""Every boot-script heartbeat must resolve the DSN at USE time, file-first.

WHY (2026-11-01 pooler outage). A boot launcher sources `.env` once and then starts a
background heartbeat loop whose Python heredoc reads `os.environ["DATABASE_URL"]`. That
value was captured at boot. When the substrate password is rotated the `.env` FILE moves
on but the long-lived loop keeps the DEAD password, so every beat fails auth.

The failure is not cosmetic and it is not local. Supavisor counts auth failures; enough
of them trip `ECIRCUITBREAKER`, which then refuses NEW connections *fleet-wide* — every
lane, every daemon, the bus itself. On 2026-11-01 that is exactly what happened, and the
two loops hammering it were cc-quality and cc-quality-2, whose boot scripts still read
the inherited value while boot_cai.sh and boot_fleet_health.sh had already been fixed.

`scripts/lib/dsn_at_use.py` exists precisely for this: it re-reads the env FILE on every
use, so a rotated `.env` is live within one beat and neither a restart nor killing the
subshell is needed. These tests pin that invariant across every launcher so the next
boot script cannot silently reintroduce the env-first read.

SCOPE NOTE: the invariant is asserted on the HEARTBEAT function body only. The one-shot
boot-registration and clean-exit heredocs also read the inherited value, but they run
once, immediately after `.env` is sourced, so they cannot accumulate failures — they are
not the bug being fixed here and are deliberately not asserted on.
"""
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO_ROOT / "scripts"

# Every launcher that runs a periodic heartbeat loop against the substrate, mapped to
# the bash function that holds that loop. boot_orch's heartbeat is not a `while true`
# loop inside the function — the function is the per-beat DB writer, called from the
# supervisor loop below it — but it is the heredoc that runs on every beat, so it is
# the thing that must be file-first.
HEARTBEAT_FUNCTIONS = {
    "boot_orch.sh": "_hub_db",
    "boot_cai.sh": "_heartbeat_loop",
    "boot_quality.sh": "_quality_heartbeat_loop",
    "boot_fleet_health.sh": "_heartbeat_loop",
    # boot_nazim.sh was already file-first when this test was written, so it was left out
    # and therefore UNPINNED — the rotation runbook (§2) and dsn_at_use.py's docstring both
    # name it as carrying the heartbeat trap, but nothing failed if that function were
    # reverted to the inherited read. Pinning it closes the last hole in this class
    # (cc-quality on #806/#809, orch-console bus #89532).
    "boot_nazim.sh": "_console_heartbeat_loop",
}

# A direct assignment of the INHERITED value to the DSN the heartbeat connects with.
# This is the bug shape: it is captured once, at boot, and never re-read.
_INHERITED_DSN_RE = re.compile(
    r'^[ \t]*dsn[ \t]*=[ \t]*os\.environ\.get\("DATABASE_URL"\)', re.MULTILINE
)


def _text(name):
    path = _SCRIPTS / name
    assert path.is_file(), f"expected boot launcher {name} to exist at {path}"
    return path.read_text()


def _function_body(text, name):
    """Return the bash body of `name() {` … closing `}` at column 0.

    These launchers close functions with a bare `}` on its own line; Python heredoc
    lines are indented, so a stripped `== "}"` match is unambiguous enough here.
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^{re.escape(name)}\(\)\s*\{{", line):
            start = i
            break
    assert start is not None, f"expected bash function {name}() to exist"
    for j in range(start + 1, len(lines)):
        if lines[j].strip() == "}":
            return "\n".join(lines[start : j + 1])
    raise AssertionError(f"function {name}() never closed with a bare '}}'")


def test_heartbeat_launchers_resolve_dsn_at_use():
    """Each heartbeat must go through dsn_at_use, not a boot-time snapshot."""
    for name in HEARTBEAT_FUNCTIONS:
        text = _text(name)
        assert "dsn_at_use" in text, (
            f"{name} runs a heartbeat loop but never uses scripts/lib/dsn_at_use.py. "
            "After a password rotation its beats fail auth against the pooler, which "
            "trips ECIRCUITBREAKER fleet-wide (2026-11-01). Resolve the DSN at use time."
        )


def test_no_heartbeat_assigns_the_inherited_dsn_directly():
    """The env-first read may survive only as a fallback, never as the primary value."""
    offenders = []
    for name, fn in HEARTBEAT_FUNCTIONS.items():
        text = _text(name)
        body = _function_body(text, fn)
        offset = text.index(body)
        for match in _INHERITED_DSN_RE.finditer(body):
            line_no = text[: offset + match.start()].count("\n") + 1
            offenders.append(f"{name}:{line_no} ({fn})")
    assert not offenders, (
        "in the heartbeat, the DSN must come from dsn_at_use.dsn(), not from the "
        "environment this process inherited at boot; found a direct inherited read at "
        + ", ".join(offenders)
    )


def test_heartbeat_uses_the_file_first_resolver():
    """The heartbeat must actually CALL _dsn_now(), not merely import it."""
    for name, fn in HEARTBEAT_FUNCTIONS.items():
        body = _function_body(_text(name), fn)
        assert "_dsn_now" in body, (
            f"{name}:{fn}() does not call _dsn_now(); the import alone changes nothing"
        )


def test_dsn_at_use_fallback_is_still_present():
    """File-first must still DEGRADE safely: the env read stays as the except-branch.

    Deleting the fallback would turn a missing/unreadable .env into a silent dead
    heartbeat, which is worse than the bug being fixed (it is the exact 'monitor fails
    silently' failure mode). The fallback is allowed only INSIDE the except block.
    """
    for name, fn in HEARTBEAT_FUNCTIONS.items():
        body = _function_body(_text(name), fn)
        assert "except Exception" in body, (
            f"{name}:{fn}() imports dsn_at_use without a fallback branch; an unreadable "
            ".env would leave the heartbeat dead instead of degrading to the inherited value"
        )
        assert 'return os.environ.get("DATABASE_URL")' in body, (
            f"{name}:{fn}() lost the environment fallback inside its except branch"
        )
