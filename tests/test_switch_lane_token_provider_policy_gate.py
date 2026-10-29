"""STATIC: switch_lane_token.sh wires the provider_policy flip gate for EVERY
non-Anthropic model-apply (total since orch-console #81004; glm-* only before) (orch-console #74584/#74588, bus #74586 spec,
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


def test_script_gates_every_model_via_the_tested_cli():
    src = _src()
    # the provider decision lives in provider_policy.provider_for_model (unit tested);
    # the shell must not re-implement it with its own glm-*-style case list.
    assert "-m scripts.lib.provider_policy" in src, "must call the real, tested gate CLI"
    assert '--gate-model "$_APPLY_MODEL" "$SESS"' in src
    assert "glm-*)" not in src.split("PROVIDER FLIP GATE", 1)[1].split("DRY-RUN (op#10706 R3)", 1)[0], \
        "the gate block must not narrow itself to one provider prefix"


def test_gate_block_comes_before_the_kill_step():
    src = _src()
    gate_pos = src.index("--gate-model")
    kill_pos = src.index('echo "[switch_lane_token] killing')
    assert gate_pos < kill_pos, "the gate must run BEFORE the lane is killed/relaunched"


def test_gate_block_comes_before_dry_run_preview():
    # a dry-run preview must show the refusal, not a misleadingly clean plan.
    src = _src()
    gate_pos = src.index("--gate-model")
    dry_run_pos = src.index('if [ "$DRY" = "1" ]; then')
    assert gate_pos < dry_run_pos


def test_script_hard_refuses_on_gate_failure():
    src = _src()
    assert "_GATE_RC" in src
    assert "exit 7" in src, "a gate refusal must hard-exit, never silently proceed"


def test_gate_passes_actor_armed_breakglass_context():
    src = _src()
    assert '--actor "${ACTOR:-cli}"' in src
    assert "_GATE_ARGS+=(--armed)" in src
    assert "_GATE_ARGS+=(--break-glass)" in src


def test_gate_is_total_not_a_provider_list():
    # orch-console #81004: deepseek-* ran ungated while the gate matched glm-* only.
    src = _src()
    assert "deliberately NOT gated here" not in src
    assert "made TOTAL" in src
