"""reset_auditor.sh — the auditor singletons' OWN in-place recycle (CAI-1392 C).

WHY THIS EXISTS. cc-quality / cc-storefront / cc-finance are CAI-500 singletons
(SINGLETON_BODIES, CAI-1392 A), so the worker recycler refuses them by design — and the
promised per-auditor reset was never built. On 2026-10-02 cc-storefront sat idle at ~832k
with a fresh handoff written and NO sanctioned way to recycle it (orch-console #49338 ->
SRE blocker #49348 -> decision #49350).

Contract under test (all against the tmux STUB — no real pane is touched):
  * only the three auditors are accepted; anything else refuses (exit 2), nothing sent
  * CALLER-IDENTITY (CAI-RESP-1442): fires only for a caller sharing the target's own
    pane tty (a detached runner the target itself spawns) -> exit 5 otherwise; no
    --caller argument or env knob can assert identity instead
  * busy -> exit 5; stale / missing handoff -> exit 3; nothing sent
  * the handoff is found by the shared finder (cc-<short>-HANDOFF-* counts)
  * STANDING ARMED (cai's sign, bus #49648, CAI-RESP-1442 closed by PR #268): a real run
    with no arm env set now fires by default; RESET_AUDITOR_ARMED=0 force-disarms one run
    (exit 4); RESET_DRYRUN evaluates every gate regardless of arm state
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
TTY_STUB = REPO / "tests" / "fixtures" / "stub_tty.sh"

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


def _arm_stub(tmp_path: Path, log: Path, check_rc=0, consume_rc=0) -> Path:
    """Stub for RESET_AUDITOR_ARM_VERIFY_CMD -- logs each call (mode + args) to
    the shared keys.log (so ordering vs the audit row / send-keys is assertable),
    exits check_rc for --mode check, consume_rc for --mode consume."""
    s = tmp_path / "arm_verify_stub.sh"
    s.write_text(
        '#!/usr/bin/env bash\n'
        f'LOG="{log}"\n'
        'mode=""; prev=""; for a in "$@"; do if [ "$prev" = "--mode" ]; then mode="$a"; fi; prev="$a"; done\n'
        'mode_upper="$(printf "%s" "$mode" | tr "[:lower:]" "[:upper:]")"\n'  # bash 3.2 (macOS) has no ${var^^}
        'printf "ARM_%s %s\\n" "$mode_upper" "$*" >> "$LOG"\n'
        'if [ "$mode" = check ]; then echo "check stub says ok"; exit ' + str(check_rc) + '; fi\n'
        'if [ "$mode" = consume ]; then echo "consume stub says ok"; exit ' + str(consume_rc) + '; fi\n'
        'exit 9\n'
    )
    s.chmod(0o755)
    return s


def _run(tmp_path, target, pane=IDLE_PANE, extra=None, arm_row=None):
    log = tmp_path / "keys.log"
    log.touch()
    env = {
        "TM": str(STUB),
        "TMUX_PANE": "%99",
        "STUB_CAPTURE_PANE_TEXT": pane,
        "STUB_SENDKEYS_LOG": str(log),
        "RESET_AUDITOR_TTY_STUB": str(TTY_STUB),
        "FIRE_WINDOW_DIR": str(tmp_path / "fw"),
        "RESET_AUDITOR_REPORTS_DIR": str(tmp_path / "reports"),
        "RESET_AUDITOR_AUDIT_CMD": str(_audit_stub(tmp_path, log)),
        "RESET_AUDITOR_LOGDIR": str(tmp_path / "logs"),
        "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin",
        "HOME": str(Path.home()),
    }
    env.update(extra or {})
    argv = [target] if target else []
    if arm_row is not None:
        argv += ["--arm-row", str(arm_row)]
    r = subprocess.run(["bash", str(SCRIPT), *argv],
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


def test_target_self_call_matching_tty_is_allowed(tmp_path):
    """The target's own detached runner shares its pane's controlling tty -- proven
    via dry-run so no real pane is touched (CAI-RESP-1442 'target self-call -> allowed')."""
    _handoff(tmp_path / "reports")
    r, keys = _run(tmp_path, "storefront", extra={
        "RESET_DRYRUN": "1", "STUB_TARGET_TTY": "ttysABC", "STUB_CALLER_TTY": "ttysABC"})
    assert r.returncode == 0, r.stderr
    assert keys == ""


def test_caller_identity_mismatch_is_refused(tmp_path):
    """A caller whose controlling tty differs from the target's own pane tty is
    refused -- this is what stops cc-fleet-health (or anyone else) from firing
    reset_auditor.sh directly; only a detached runner the TARGET itself spawns,
    sharing its pane's tty, may pass (CAI-RESP-1442 'fleet-health caller -> refused',
    and equally 'a runner whose parent isn't the target's process -> refused' --
    ancestry doesn't survive the detached runner's reparenting, so tty is the
    substitute OS-enforced identity; see the gate's own comment for why)."""
    _handoff(tmp_path / "reports")
    r, keys = _run(tmp_path, "storefront", extra={"STUB_CALLER_TTY": "ttys999"})
    assert r.returncode == 5 and "CALLER-IDENTITY REFUSED" in r.stderr
    assert keys == ""


def test_unresolvable_target_pane_fails_closed(tmp_path):
    _handoff(tmp_path / "reports")
    r, keys = _run(tmp_path, "storefront", extra={"STUB_PANE_PID": ""})
    assert r.returncode == 5 and "CALLER-IDENTITY REFUSED" in r.stderr
    assert keys == ""


def test_no_caller_override_knob_exists(tmp_path):
    """No --caller flag or env knob lets a caller ASSERT its own identity -- only the
    OS-level tty comparison decides (CAI-RESP-1442 'spoofing the target via an
    argument -> refused': there must be no argument to spoof in the first place).

    --arm-row (added for cc-quality's self-arm path, #83292) is deliberately NOT
    an exception to this rule: it never lets a caller assert its own identity by
    argument -- the identity check is still independent OS-level env state
    (ORCH_BODY_ROLE/CC_BASE_AGENT_ID), and the row's authenticity is itself
    verified server-side (DB-checked, single-use, 30-min-windowed), never taken
    on the caller's word. See test_arm_row_* above for that path's own coverage."""
    text = SCRIPT.read_text()
    assert "--caller" not in text
    assert "RESET_AUDITOR_CALLER" not in text


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


def test_standing_armed_by_default_now_fires(tmp_path):
    """Post-cai-sign (bus #49648): a real run with NO arm env set fires by default."""
    h = _handoff(tmp_path / "reports")
    r, keys = _run(tmp_path, "storefront")
    assert r.returncode == 0, r.stderr + r.stdout
    assert any("/clear" in l for l in keys.splitlines()), "no /clear sent"
    assert str(h.resolve()) in keys


def test_explicit_disarm_refuses_a_real_run(tmp_path):
    _handoff(tmp_path / "reports")
    r, keys = _run(tmp_path, "storefront", extra={"RESET_AUDITOR_ARMED": "0"})
    assert r.returncode == 4, r.stderr
    assert "arm" in r.stderr.lower()
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


# ── RESET_FORCE is audited and still requires ARMED (orch-console #49369) ───────
# FORCE bypasses the busy + queued gates, i.e. it can discard an in-flight money audit.
# It must never work in a disarmed run, and the pre-clear audit row must say it was used,
# which gate(s) it overrode, and why the body was busy.
BUSY_IDLE_RENDER = IDLE_PANE + "\n  esc to interrupt"          # busy marker + readable composer
QUEUED_RENDER = IDLE_PANE + "\nPress up to edit queued messages"


def _audit_line(keys):
    return next(l for l in keys.splitlines() if l.startswith("AUDIT "))


def test_force_on_a_busy_auditor_still_requires_arm(tmp_path):
    """FORCE never reaches a keystroke on an explicitly-disarmed run, even post-standing-arm."""
    _handoff(tmp_path / "reports")
    r, keys = _run(tmp_path, "storefront", pane=BUSY_IDLE_RENDER,
                   extra={"RESET_FORCE": "1", "RESET_AUDITOR_ARMED": "0"})
    assert r.returncode == 4, r.stderr
    assert keys == "", "a disarmed FORCE run wrote an audit row or typed into the pane"


def test_forced_busy_override_is_recorded_in_the_audit_row(tmp_path):
    _handoff(tmp_path / "reports")
    r, keys = _run(tmp_path, "storefront", pane=BUSY_IDLE_RENDER,
                   extra={"RESET_FORCE": "1", "RESET_AUDITOR_ARMED": "1"})
    assert r.returncode == 0, r.stderr + r.stdout
    a = _audit_line(keys)
    assert "--forced-gates busy" in a, a
    assert "esc to interrupt" in a, f"busy reason not recorded: {a}"


def test_forced_queued_override_is_recorded_in_the_audit_row(tmp_path):
    _handoff(tmp_path / "reports")
    r, keys = _run(tmp_path, "storefront", pane=QUEUED_RENDER,
                   extra={"RESET_FORCE": "1", "RESET_AUDITOR_ARMED": "1"})
    assert r.returncode == 0, r.stderr + r.stdout
    assert "queued" in _audit_line(keys).split("--forced-gates ", 1)[1].split()[0]


def test_unforced_run_records_no_forced_gates(tmp_path):
    _handoff(tmp_path / "reports")
    r, keys = _run(tmp_path, "storefront", extra={"RESET_AUDITOR_ARMED": "1"})
    assert r.returncode == 0, r.stderr
    assert "--forced-gates none" in _audit_line(keys)


def test_force_on_an_idle_body_overrides_nothing(tmp_path):
    """FORCE set but no gate actually needed overriding -> recorded as none, not 'busy'."""
    _handoff(tmp_path / "reports")
    r, keys = _run(tmp_path, "storefront", extra={"RESET_FORCE": "1", "RESET_AUDITOR_ARMED": "1"})
    assert r.returncode == 0, r.stderr
    assert "--forced-gates none" in _audit_line(keys)


# ── CC-QUALITY SELF-ARM PATH (--arm-row N; Musa op_msg 35891/ask 5849, #83292,
#      ordering fix #83304, cc-quality's review points in #83301) ───────────
CONSOLE_ENV = {"ORCH_BODY_ROLE": "console"}  # no CC_BASE_AGENT_ID set


def _arm_env(tmp_path, log, check_rc=0, consume_rc=0, console=True, extra=None):
    env = {"RESET_AUDITOR_ARM_VERIFY_CMD": str(_arm_stub(tmp_path, log, check_rc, consume_rc))}
    if console:
        env.update(CONSOLE_ENV)
    env.update(extra or {})
    return env


def test_arm_row_scoped_to_quality_only_storefront_refused(tmp_path):
    """console spec item 1: storefront/finance never reach the arm-row path,
    even with a valid-looking row and the console env set."""
    _handoff(tmp_path / "reports", "cc-storefront-HANDOFF-20261002.md")
    log = tmp_path / "keys.log"
    r, keys = _run(tmp_path, "storefront", arm_row=1,
                   extra=_arm_env(tmp_path, log), pane=IDLE_PANE)
    assert r.returncode == 2, r.stderr
    assert "scoped to cc-quality" in r.stderr
    assert "ARM_CHECK" not in keys, "the arm verifier must never even be called for storefront"


def test_arm_row_scoped_to_quality_only_finance_refused(tmp_path):
    _handoff(tmp_path / "reports", "finance-handoff-NOW.md")
    log = tmp_path / "keys.log"
    r, keys = _run(tmp_path, "finance", arm_row=1,
                   extra=_arm_env(tmp_path, log), pane=IDLE_PANE)
    assert r.returncode == 2, r.stderr
    assert "ARM_CHECK" not in keys


def test_arm_row_requires_console_caller_env_missing_role(tmp_path):
    """console spec item 3 / cc-quality review (b): a row alone is NEVER
    sufficient -- the caller must independently BE the console body."""
    _handoff(tmp_path / "reports")
    log = tmp_path / "keys.log"
    r, keys = _run(tmp_path, "quality", arm_row=1,
                   extra=_arm_env(tmp_path, log, console=False))
    assert r.returncode == 5, r.stderr
    assert "CALLER-IDENTITY REFUSED" in r.stderr
    assert "ARM_CHECK" not in keys, "no row is consulted before the console-caller check passes"


def test_arm_row_requires_console_caller_env_lane_id_set_is_refused(tmp_path):
    """console spec item 5 of the 6 required test scenarios: a console caller
    with a lane id (CC_BASE_AGENT_ID) set must still be refused."""
    _handoff(tmp_path / "reports")
    log = tmp_path / "keys.log"
    r, keys = _run(tmp_path, "quality", arm_row=1,
                   extra=_arm_env(tmp_path, log, extra={"CC_BASE_AGENT_ID": "cc-fleet-health"}))
    assert r.returncode == 5, r.stderr
    assert "CALLER-IDENTITY REFUSED" in r.stderr
    assert "ARM_CHECK" not in keys


def test_arm_row_check_failure_refuses_before_any_keystroke(tmp_path):
    _handoff(tmp_path / "reports")
    log = tmp_path / "keys.log"
    r, keys = _run(tmp_path, "quality", arm_row=1,
                   extra=_arm_env(tmp_path, log, check_rc=1))
    assert r.returncode == 5, r.stderr
    assert "did not check out" in r.stderr
    assert "ARM_CHECK" in keys
    assert "ARM_CONSUME" not in keys, "consume must never run if check already failed"
    assert "send-keys" not in keys


def test_arm_row_happy_path_fires_and_audit_cites_the_row_and_authority(tmp_path):
    h = _handoff(tmp_path / "reports", "cc-quality-handoff-x.md")
    log = tmp_path / "keys.log"
    r, keys = _run(tmp_path, "quality", arm_row=42,
                   extra=_arm_env(tmp_path, log))
    assert r.returncode == 0, r.stderr + r.stdout
    assert "ARM_CHECK" in keys and "ARM_CONSUME" in keys
    check_i = next(i for i, l in enumerate(keys.splitlines()) if l.startswith("ARM_CHECK"))
    consume_i = next(i for i, l in enumerate(keys.splitlines()) if l.startswith("ARM_CONSUME"))
    audit_i = next(i for i, l in enumerate(keys.splitlines()) if l.startswith("AUDIT "))
    first_key = next(i for i, l in enumerate(keys.splitlines()) if l.startswith("send-keys"))
    assert check_i < consume_i < audit_i < first_key, "check -> consume -> audit -> keystrokes, strictly in order"
    audit_line = _audit_line(keys)
    assert "42" in audit_line and "35891" in audit_line, f"audit row must cite the arm-row id + Musa op_msg 35891: {audit_line}"
    assert str(h.resolve()) in keys


def test_arm_row_path_attributes_to_console_even_with_reset_by_env_set(tmp_path):
    """orch-console #83363 (correction of record, after bus #83362's
    misattribution): once gate 3b has PROVEN the caller is the console body,
    every downstream mention (the audit row's --by, the boot message) must
    name orch-console -- unconditionally, overriding any RESET_BY the caller's
    env happens to carry (fleet_health_boundaries' whole point: the SRE must
    never appear to have fired a singleton reset it has no authority to
    fire)."""
    h = _handoff(tmp_path / "reports", "cc-quality-handoff-x.md")
    log = tmp_path / "keys.log"
    r, keys = _run(tmp_path, "quality", arm_row=1,
                   extra=_arm_env(tmp_path, log, extra={"RESET_BY": "cc-fleet-health"}))
    assert r.returncode == 0, r.stderr + r.stdout
    audit_line = _audit_line(keys)
    assert "--by orch-console" in audit_line, f"audit row must cite orch-console, not cc-fleet-health: {audit_line}"
    assert "cc-fleet-health" not in audit_line
    assert "reset IN-PLACE by orch-console" in keys
    assert str(h.resolve()) in keys


def test_tty_path_reset_by_is_unchanged_still_env_or_default(tmp_path):
    """Sanity: the attribution fix is scoped to the arm-row branch only --
    the pre-existing tty self-fire path keeps its own env-or-default
    RESET_BY (still cc-fleet-health in the normal/no-override case)."""
    h = _handoff(tmp_path / "reports", "cc-storefront-HANDOFF-20261002.md")
    r, keys = _run(tmp_path, "storefront", extra={"RESET_AUDITOR_ARMED": "1"})
    assert r.returncode == 0, r.stderr + r.stdout
    audit_line = _audit_line(keys)
    assert "--by cc-fleet-health" in audit_line
    assert str(h.resolve()) in keys


def test_arm_row_busy_gate_refusal_leaves_row_unconsumed(tmp_path):
    """console #83304's required test: a busy-gate refusal must leave the
    arm-row UNCONSUMED (check already passed at gate 3, but consume never
    runs because the busy gate aborts first)."""
    _handoff(tmp_path / "reports")
    log = tmp_path / "keys.log"
    r, keys = _run(tmp_path, "quality", arm_row=1, pane=BUSY_PANE,
                   extra=_arm_env(tmp_path, log))
    assert r.returncode == 5 and "BUSY" in r.stderr
    assert "ARM_CHECK" in keys, "gate 3's check still runs (it's what admits past caller-identity)"
    assert "ARM_CONSUME" not in keys, "a later gate's refusal must never consume a genuine request"


def test_arm_row_dryrun_leaves_row_unconsumed(tmp_path):
    """console #83304's required test: RESET_DRYRUN must never consume the row."""
    _handoff(tmp_path / "reports", "cc-quality-handoff-x.md")
    log = tmp_path / "keys.log"
    r, keys = _run(tmp_path, "quality", arm_row=1,
                   extra=_arm_env(tmp_path, log, extra={"RESET_DRYRUN": "1"}))
    assert r.returncode == 0, r.stderr
    assert "ARM_CHECK" in keys
    assert "ARM_CONSUME" not in keys, "RESET_DRYRUN must never write/consume"
    assert keys.count("send-keys") == 0 or "send-keys" not in keys


def test_arm_row_consume_failure_at_fire_time_aborts_before_audit_row(tmp_path):
    """Closes the check-to-fire race: even if check passed, a failed consume
    at fire time must abort before the audit row / any keystroke."""
    _handoff(tmp_path / "reports", "cc-quality-handoff-x.md")
    log = tmp_path / "keys.log"
    r, keys = _run(tmp_path, "quality", arm_row=1,
                   extra=_arm_env(tmp_path, log, consume_rc=1))
    assert r.returncode == 5, r.stderr
    assert "failed to consume" in r.stderr
    assert "ARM_CONSUME" in keys
    assert "AUDIT " not in keys, "no audit row once consume fails at fire time"
    assert "send-keys" not in keys


def test_arm_row_absent_tty_path_is_unaffected(tmp_path):
    """Sanity: with no --arm-row, behavior is exactly the pre-existing tty path
    (the console env being set changes nothing when --arm-row is absent --
    only the presence of --arm-row switches gate 3's branch)."""
    h = _handoff(tmp_path / "reports", "cc-storefront-HANDOFF-20261002.md")
    r, keys = _run(tmp_path, "storefront", extra=dict(CONSOLE_ENV, **{"RESET_AUDITOR_ARMED": "1"}))
    assert r.returncode == 0, r.stderr + r.stdout  # unchanged: default stub tty matches -> fires
    assert any("/clear" in l for l in keys.splitlines())
    assert str(h.resolve()) in keys
