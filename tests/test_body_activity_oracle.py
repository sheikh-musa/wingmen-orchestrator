"""Phase-0 (op#11774) — body-activity ORACLE, DETECT-ONLY, TDD.

The oracle is the one primitive the wedge/wake fixes all consume: a fresh,
confidence-tagged verdict — WORKING / IDLE_EMPTY / STAGED / GHOST_WEDGED / UNSURE
— read from a body's real tmux pane (local OR the cross-host hub). It reuses the
composer_capture.sh signals (CC_BUSY/CC_EMPTY/CC_GHOST/CC_PARTIAL/CC_UNSURE/CC_N).

Guardrails locked here:
  * fail-CLOSED: any busy evidence -> WORKING (never act on a maybe-working body),
    even under a partial capture.
  * UNSURE -> do nothing, never guess: capture failure, low-confidence capture, or
    an unreachable remote host all resolve to UNSURE.
  * the MUTATING probe (fork-2, cai-gated) is BUILT-BUT-INERT: disarmed it returns
    UNSURE and NEVER touches a live pane.
"""
import importlib

import pytest

oracle = importlib.import_module("nervous_system.body_activity_oracle")

W, IDLE, STAGED, GHOST, UNSURE = (
    oracle.WORKING, oracle.IDLE_EMPTY, oracle.STAGED, oracle.GHOST_WEDGED, oracle.UNSURE,
)


def sig(**kw):
    """A signals dict with safe defaults (clean capture, idle-empty)."""
    base = dict(capture_ok=True, busy=False, partial_ok=True, unsure=False,
                ghost=False, empty=True, n=0)
    base.update(kw)
    return base


# ── classify: the pure verdict mapping ──────────────────────────────────────
def test_busy_is_working_even_under_partial_capture():
    # fail-closed: activity always wins, so we never act on a maybe-working body.
    assert oracle.classify(sig(busy=True, partial_ok=False, empty=False)).state == W


def test_low_confidence_nonbusy_capture_is_unsure():
    assert oracle.classify(sig(partial_ok=False)).state == UNSURE
    assert oracle.classify(sig(unsure=True)).state == UNSURE


def test_ghost_is_ghost_wedged():
    assert oracle.classify(sig(ghost=True, empty=False, n=1)).state == GHOST


def test_clean_empty_is_idle_empty():
    assert oracle.classify(sig(empty=True, n=0)).state == IDLE


def test_clean_content_is_staged():
    assert oracle.classify(sig(empty=False, n=2)).state == STAGED


def test_capture_failed_is_unsure():
    assert oracle.classify(None).state == UNSURE
    assert oracle.classify(sig(capture_ok=False)).state == UNSURE


# ── activity(): orchestration fail-safes ────────────────────────────────────
def test_activity_unsure_when_local_capture_returns_none():
    v = oracle.activity("cc-quality", capture=lambda host, sess: None,
                        resolve_host=lambda a: oracle.LOCAL_HOST,
                        resolve_session=lambda a: "quality")
    assert v.state == UNSURE


def test_activity_returns_fresh_remote_published_verdict():
    # cross-host G-b (VPS-instance, DB-mediated): a fresh published verdict for the
    # hub is returned directly (the VPS instance already classified it).
    v = oracle.activity("cc-orchestrator",
                        resolve_host=lambda a: "vps",
                        read_verdict=lambda a: oracle.Verdict(oracle.WORKING, "published"))
    assert v.state == W


def test_activity_unsure_when_remote_verdict_stale_or_missing():
    # VPS publisher dead / row stale -> read_verdict returns None -> UNSURE, never a
    # stale coverage-guess. This is the RemoteUnreachable-equivalent fail-safe.
    v = oracle.activity("cc-orchestrator",
                        resolve_host=lambda a: "vps",
                        read_verdict=lambda a: None)
    assert v.state == UNSURE


def test_activity_unsure_on_remote_verdict_read_error():
    def boom(a):
        raise RuntimeError("db down")
    v = oracle.activity("cc-orchestrator",
                        resolve_host=lambda a: "vps",
                        read_verdict=boom)
    assert v.state == UNSURE


def test_activity_maps_signals_to_verdict():
    v = oracle.activity("cc-quality",
                        capture=lambda host, sess: sig(busy=True, empty=False),
                        resolve_host=lambda a: oracle.LOCAL_HOST,
                        resolve_session=lambda a: "quality")
    assert v.state == W


# ── the mutating probe: BUILT-BUT-INERT until fork-2 (cai) ───────────────────
def test_probe_disarmed_returns_unsure_and_never_touches_the_pane():
    touched = {"n": 0}
    def sendkeys(*a, **k):
        touched["n"] += 1
    v = oracle.probe_confirm_empty("quality", armed=False, sendkeys=sendkeys)
    assert v.state == UNSURE
    assert touched["n"] == 0, "disarmed probe must NEVER send keys to a live pane"


def test_probe_armed_flag_defaults_disarmed():
    # the module-level arm flag ships FALSE (fork-2 not signed); a fresh import must
    # not have an armed probe.
    assert oracle.PROBE_ARMED is False


# ── CC_* env parsing (the shell-out boundary) ───────────────────────────────
def test_parse_cc_env_normalizes_raw_strings():
    s = oracle.signals_from_cc({
        "CC_BUSY": "1", "CC_EMPTY": "0", "CC_GHOST": "0",
        "CC_PARTIAL": "ok", "CC_UNSURE": "0", "CC_N": "3",
    })
    assert s["busy"] is True and s["empty"] is False and s["partial_ok"] is True
    assert s["n"] == 3 and s["capture_ok"] is True


# ── Musa op#34916: the probe arms on MUSA's verified word, never on a cai sign ─
class _Auth:
    def __init__(self, ok, reason="", row=None):
        self.ok, self.reason, self.row = ok, reason, row


def test_probe_arm_status_true_only_on_a_musa_verified_authorization():
    seen = {}
    def verify(op_id, **kw):
        seen.update(op_id=op_id, **kw)
        return _Auth(True, "ok", {"id": 4242})
    armed, why = oracle.probe_arm_status(verify=verify)
    assert armed is True and "4242" in why
    assert seen["op_id"] == "oracle-probe-arm"
    assert oracle.PROBE_ARM_PHRASE in seen["approval_phrases"]
    assert set(seen["op_tokens"]) >= {"oracle", "probe"}


def test_probe_arm_status_false_when_no_verified_authorization():
    armed, why = oracle.probe_arm_status(verify=lambda op_id, **kw: _Auth(False, "no matching row"))
    assert armed is False and "no matching row" in why


def test_probe_arm_status_fails_closed_on_any_error():
    def boom(op_id, **kw):
        raise RuntimeError("db down")
    armed, why = oracle.probe_arm_status(verify=boom)
    assert armed is False and "RuntimeError" in why


def test_default_probe_consults_the_verified_arm_and_stays_inert_without_it():
    touched = {"n": 0}
    v = oracle.probe_confirm_empty("quality", sendkeys=lambda *a, **k: touched.__setitem__("n", 1),
                                   arm_check=lambda: (False, "no arm"))
    assert v.state == UNSURE and touched["n"] == 0


def test_armed_but_unbuilt_probe_fails_safe_never_raises_never_touches_pane():
    # Arming is now Musa's call, but the mutating probe itself is still unbuilt:
    # an arm must NOT crash the oracle or type into a live pane.
    touched = {"n": 0}
    v = oracle.probe_confirm_empty("quality", armed=True,
                                   sendkeys=lambda *a, **k: touched.__setitem__("n", 1))
    assert v.state == UNSURE and "unbuilt" in v.reason
    assert touched["n"] == 0
