"""boot_orch.sh must NOT write the cc-orchestrator hub row when ORCH_BODY_ROLE=console.

WHY (audit #1B, Nazim 37658). On the Mini, dev.wingmen.cc-orch runs boot_orch.sh with
ORCH_BODY_ROLE=console (it supervises Nazim's 'nazim' session). The old code ignored the
role and self-registered AND heartbeat the HUB's row (cc-orchestrator) with
tmux_session=nazim, host=Sheikhs-Mini — a FALSE hub-liveness signal, and agent_wake then
resolved the cross-host hub to the local nazim pane. Hub liveness is the orch_lease
(singleton_liveness / hub_alive_evidence), never a Mini heartbeat. Under console role the
boot still supervises its session but writes NOTHING to the cc-orchestrator row: the
self-register is skipped and _hub_db (beat + the offline-EXIT marker) is a no-op.

These are structural guards on the shipped script (the register/heartbeat are DB-writing
heredocs — not runnable prod-clean), plus bash -n. Prod-clean: no DB.
"""
import re
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_BOOT = _ROOT / "scripts" / "boot_orch.sh"


def _code_only(text: str) -> str:
    return "\n".join("" if ln.lstrip().startswith("#") else ln for ln in text.splitlines())


def test_boot_orch_exists():
    assert _BOOT.is_file(), f"missing {_BOOT}"


def test_boot_orch_reads_body_role():
    assert "ORCH_BODY_ROLE" in _code_only(_BOOT.read_text()), (
        "boot_orch.sh must consult ORCH_BODY_ROLE to know it is a console-role boot"
    )


def test_self_register_is_skipped_in_console_role():
    code = _code_only(_BOOT.read_text())
    # A console-role guard must take a SKIP branch, and the cc-orchestrator register (the
    # INSERT heredoc) must sit in the non-console (elif) branch — never reached in console role.
    guard = re.search(r'if\s+\[\s*"\$\{ORCH_BODY_ROLE:-\}"\s*=\s*"console"\s*\]', code)
    assert guard, "the self-register must be guarded by an ORCH_BODY_ROLE=console check"
    after = code[guard.end():]
    # the register INSERT must be behind an `elif` that follows the console guard
    m_elif = re.search(r'\belif\b', after)
    m_insert = re.search(r"INSERT INTO agent_status", after)
    assert m_elif and m_insert and m_elif.start() < m_insert.start(), (
        "the cc-orchestrator self-register INSERT must be in the non-console (elif) branch"
    )


def test_hub_db_writes_are_skipped_in_console_role():
    code = _code_only(_BOOT.read_text())
    # _hub_db must early-return under console role BEFORE any UPDATE of the hub row.
    m_return = re.search(r'ORCH_BODY_ROLE:-\}"\s*=\s*"console"\s*\]\s*&&\s*return', code)
    assert m_return, "_hub_db must early-return when ORCH_BODY_ROLE=console (no beat/offline write)"
    m_update = code.find("UPDATE agent_status")
    assert m_update == -1 or m_return.start() < m_update, (
        "the console-role early-return must precede the hub-row UPDATE"
    )


def test_boot_orch_parses_clean():
    r = subprocess.run(["bash", "-n", str(_BOOT)], capture_output=True, text=True)
    assert r.returncode == 0, f"boot_orch.sh has a syntax error: {r.stderr}"
