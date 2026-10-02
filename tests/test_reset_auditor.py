"""reset_auditor.sh — the auditor singletons' OWN in-place recycle (CAI-1392 C).

WHY THIS EXISTS. cc-quality / cc-storefront / cc-finance are CAI-500 singletons
(SINGLETON_BODIES, CAI-1392 A), so the worker recycler refuses them by design — and the
promised per-auditor reset was never built. On 2026-10-02 cc-storefront sat idle at ~832k
with a fresh handoff written and NO sanctioned way to recycle it (orch-console #49338 ->
SRE blocker #49348 -> decision #49350).

Contract under test (all against the tmux STUB — no real pane is touched):
  * only the three auditors are accepted; anything else refuses (exit 2), nothing sent
  * busy -> exit 5; stale / missing handoff -> exit 3; nothing sent
  * the handoff is found by the shared finder (cc-<short>-HANDOFF-* counts)
  * DISARMED BY DEFAULT: a real run without the arm refuses (exit 4) — the first use needs
    cai's arm-sign; RESET_DRYRUN evaluates every gate without needing the arm
  * the audit row is written BEFORE the first keystroke; if it cannot be written the
    reset aborts (exit 10) with the body untouched (dead-man's switch)
  * in-place: the happy path sends /clear then a boot naming the ABSOLUTE handoff path —
    never a kill/relaunch, so the body keeps its pid, token and model
"""
import os
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "reset_auditor.sh"
STUB = REPO / "tests" / "fixtures" / "stub_tmux.sh"

NBSP = " "
IDLE_PANE = f"Claude Code v2.1.300\n────────────\n❯{NBSP}\n────────────\n  ⏵⏵ bypass permissions on"
BUSY_PANE = "some transcript line\n\n  esc to interrupt"


def _handoff(reports: Path, name="cc-storefront-HANDOFF-20261002.md", age_s=0.0) -> Path:
    reports.mkdir(exist_ok=True)
    p = reports / name
    p.write_text("# handoff\n")
    t = time.time() - age_s
    os.utime(p, (t, t))
    return p


def _audit_stub(tmp_path: Path, log: Path, rc=0) -> Path:
    s = tmp_path / f"audit_stub_rc{rc}.sh"
    s.write_text(f'#!/usr/bin/env bash\nprintf "AUDIT %s\\n" "$*" >> "{log}"\nexit {rc}\n')
    s.chmod(0o755)
    return s


def _run(tmp_path, target, pane=IDLE_PANE, extra=None):
    log = tmp_path / "keys.log"
    log.touch()
    env = {
        "TM": str(STUB),
        "TMUX_PANE": "%99",
        "STUB_CALLER_SESS": "fleet-health",
        "STUB_CAPTURE_PANE_TEXT": pane,
        "STUB_SENDKEYS_LOG": str(log),
        "FIRE_WINDOW_DIR": str(tmp_path / "fw"),
        "RESET_AUDITOR_REPORTS_DIR": str(tmp_path / "reports"),
        "RESET_AUDITOR_AUDIT_CMD": str(_audit_stub(tmp_path, log)),
        "RESET_AUDITOR_LOGDIR": str(tmp_path / "logs"),
        "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin",
        "HOME": str(Path.home()),
    }
    env.update(extra or {})
    r = subprocess.run(["bash", str(SCRIPT), *([target] if target else [])],
                       capture_output=True, text=True, cwd=str(REPO), env=env, timeout=50)
    return r, log.read_text()


@pytest.mark.parametrize("target", ["cai", "nazim", "cosem-adcda", "cc-orchestrator", "", "quality;id"])
def test_non_auditor_target_is_refused(tmp_path, target):
    _handoff(tmp_path / "reports")
    r, keys = _run(tmp_path, target, extra={"RESET_DRYRUN": "1"})
    assert r.returncode == 2, r.stderr
    assert keys == ""


@pytest.mark.parametrize("target,handoff", [
    ("quality", "cc-quality-handoff-x.md"),
    ("cc-storefront", "cc-storefront-HANDOFF-20261002.md"),
    ("finance", "finance-handoff-NOW.md"),
])
def test_each_auditor_passes_dryrun_with_fresh_handoff(tmp_path, target, handoff):
    h = _handoff(tmp_path / "reports", handoff)
    r, keys = _run(tmp_path, target, extra={"RESET_DRYRUN": "1"})
    assert r.returncode == 0, r.stderr
    assert str(h.resolve()) in r.stdout, "dry run must name the handoff it would boot from"
    assert keys == "", "a dry run typed into the pane"


def test_busy_auditor_is_refused(tmp_path):
    _handoff(tmp_path / "reports")
    r, keys = _run(tmp_path, "storefront", pane=BUSY_PANE)
    assert r.returncode == 5 and "BUSY" in r.stderr
    assert keys == ""


def test_self_fire_is_refused(tmp_path):
    _handoff(tmp_path / "reports")
    r, keys = _run(tmp_path, "storefront", extra={"STUB_CALLER_SESS": "storefront"})
    assert r.returncode == 5 and "SELF-FIRE" in r.stderr
    assert keys == ""


def test_missing_handoff_is_refused(tmp_path):
    (tmp_path / "reports").mkdir()
    r, keys = _run(tmp_path, "storefront", extra={"RESET_DRYRUN": "1"})
    assert r.returncode == 3, r.stderr
    assert keys == ""


def test_stale_handoff_is_refused(tmp_path):
    _handoff(tmp_path / "reports", age_s=3600)
    r, keys = _run(tmp_path, "storefront", extra={"RESET_DRYRUN": "1"})
    assert r.returncode == 3 and "old" in r.stderr
    assert keys == ""


def test_handoff_max_age_is_overridable(tmp_path):
    _handoff(tmp_path / "reports", age_s=3600)
    r, _ = _run(tmp_path, "storefront",
                extra={"RESET_DRYRUN": "1", "RESET_AUDITOR_HANDOFF_MAX_AGE": "7200"})
    assert r.returncode == 0, r.stderr


def test_disarmed_by_default_refuses_a_real_run(tmp_path):
    _handoff(tmp_path / "reports")
    r, keys = _run(tmp_path, "storefront")
    assert r.returncode == 4, r.stderr
    assert "arm" in r.stderr.lower() and "cai" in r.stderr.lower()
    assert keys == "", "a disarmed reset typed into the pane"


def test_audit_failure_aborts_before_any_keystroke(tmp_path):
    _handoff(tmp_path / "reports")
    log = tmp_path / "keys.log"
    bad = _audit_stub(tmp_path, log, rc=1)
    r, keys = _run(tmp_path, "storefront",
                   extra={"RESET_AUDITOR_ARMED": "1", "RESET_AUDITOR_AUDIT_CMD": str(bad)})
    assert r.returncode == 10, r.stderr
    assert "send-keys" not in keys, "keys sent after the audit row FAILED"


def test_armed_happy_path_is_in_place_audit_first_boot_names_handoff(tmp_path):
    h = _handoff(tmp_path / "reports")
    r, keys = _run(tmp_path, "storefront", extra={"RESET_AUDITOR_ARMED": "1"})
    assert r.returncode == 0, r.stderr + r.stdout
    lines = keys.splitlines()
    audit_i = next(i for i, l in enumerate(lines) if l.startswith("AUDIT "))
    first_key = next(i for i, l in enumerate(lines) if l.startswith("send-keys"))
    assert audit_i < first_key, "audit row must be written BEFORE the first keystroke"
    assert "cc-storefront" in lines[audit_i] and str(h.resolve()) in lines[audit_i]
    assert any("/clear" in l for l in lines), "no /clear sent"
    boot = [l for l in lines if "cc-storefront" in l and "send-keys" in l and "-l" in l]
    assert boot and str(h.resolve()) in boot[-1], "boot must name the ABSOLUTE handoff path"
    assert not any(w in keys for w in ("kill-session", "kill-server", "respawn")), "not in-place"


def test_script_never_targets_a_non_auditor_even_via_session_override(tmp_path):
    """There is no env knob that retargets the pane — the session is derived from the
    allowlist only (self_recycle's --session bypass is the cautionary tale)."""
    text = SCRIPT.read_text()
    assert "RESET_AUDITOR_SESSION" not in text
