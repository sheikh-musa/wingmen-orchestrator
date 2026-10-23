"""STATIC: switch_lane_token.sh wires the provider_policy flip gate for a glm-*
model-apply (orch-console #74584/#74588, bus #74586 spec,
reports/provider-policy-flip-gate-spec-20261023.md).

The actual gate logic (assert_glm_allowed, gate_or_post_refusal) is fully unit
tested in tests/test_provider_policy.py with injected fetch_rows/escalate — this
file only pins that the shell script actually CALLS that gate before any
kill/relaunch, and hard-refuses (nonzero exit) rather than proceeding on refusal,
mirroring the existing static-check style for this script
(tests/test_switch_lane_token_provider_verify.py).
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "switch_lane_token.sh"


def _src() -> str:
    return SCRIPT.read_text()


def test_script_gates_glm_models_before_kill_relaunch():
    src = _src()
    assert 'glm-*)' in src, "must branch on a glm-* model before applying it"
    assert "gate_or_post_refusal" in src, "must call the real, tested gate function (not a reimplementation)"
    assert "from scripts.lib.provider_policy import gate_or_post_refusal" in src


def test_gate_block_comes_before_the_kill_step():
    src = _src()
    gate_pos = src.index("gate_or_post_refusal")
    kill_pos = src.index('echo "[switch_lane_token] killing')
    assert gate_pos < kill_pos, "the gate must run BEFORE the lane is killed/relaunched"


def test_gate_block_comes_before_dry_run_preview():
    # a dry-run preview must show the refusal, not a misleadingly clean plan.
    src = _src()
    gate_pos = src.index("gate_or_post_refusal")
    dry_run_pos = src.index('if [ "$DRY" = "1" ]; then')
    assert gate_pos < dry_run_pos


def test_script_hard_refuses_on_gate_failure():
    src = _src()
    assert "_GATE_RC" in src
    assert "exit 7" in src, "a gate refusal must hard-exit, never silently proceed"


def test_gate_passes_actor_armed_breakglass_context():
    src = _src()
    assert "GATE_ACTOR" in src
    assert "GATE_ARMED" in src
    assert "GATE_BREAKGLASS" in src


def test_gate_is_scoped_to_glm_only_not_other_providers():
    # provider_policy's allowed_providers vocabulary today only covers "zai" —
    # a kimi-*/deepseek-*/OpenRouter model is deliberately not gated (documented
    # scope boundary in the surrounding comment, not a silent omission).
    src = _src()
    comment_idx = src.index("deliberately NOT gated here")
    assert comment_idx > 0
