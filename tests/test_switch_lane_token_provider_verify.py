"""switch_lane_token.sh's re-token verify loop must not false-FAIL (and auto-page
P1) on a HEALTHY model-apply to a provider-routed model (bus #53357, filed by
cc-fleet-health after a real false alarm: cosem-exams -> glm-5.3 succeeded
cleanly but the script declared exit 8 anyway).

Root cause: launch_dangerous_cc.sh's CC_AUTH_FP-clearing override (bus #53191,
generalized #53237/#53267/#53278) clears CC_AUTH_FP to "" for ANY provider-
routed model (glm-*, kimi-*, moonshot-*, deepseek-*, qwen*, and the OpenRouter
A/B arm) -- agent_status_stamp.py's NULLIF(auth_fp, '') then stores that as
NULL. So for a provider-routed model-apply, AFTER_FP can NEVER equal NEW_FP
(the real Anthropic-token hash) -- that is the override working as designed,
not a wedge. The old verify loop only knew the single auth_fp-flip criterion,
so it false-FAILed (and the cc-fleet-health audit's res=="FAIL" branch loudly
P1-paged orch-console) on every single provider-routed model-apply.

Fix: detect up front whether the applying model is provider-routed (via the
REAL scripts/lib/provider_routing.sh _provider_for_model -- the single source
of truth already tested by tests/test_provider_routing.py, not a re-implemented
copy here -- same sync-invariant lesson as bus #53303/#53278). If so, the
identity-flip criterion becomes "auth_account got a non-generic provider label"
(the SAME boot-stamp call's CC_AUTH_LABEL) instead of the auth_fp comparison,
gated through a single _FP_CHECK_OK the rest of the script's PASS/FAIL logic
already used _before_ this fix pointed only at auth_fp.

Layers (mirrors tests/test_switch_lane_token_resume_menu.py):
  * UNIT: the provider-detection snippet, run against the REAL provider_routing.sh
    (so this test breaks if that table's shape ever changes incompatibly).
  * STATIC: switch_lane_token.sh wires the detection + the account-based fallback
    + routes every PASS/FAIL gate through the one _FP_CHECK_OK flag.
"""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROUTING_LIB = REPO / "scripts" / "lib" / "provider_routing.sh"
SCRIPT = REPO / "scripts" / "switch_lane_token.sh"

# The exact detection snippet switch_lane_token.sh runs (kept identical to the
# script's own lines so a drift between this test and the shipped script shows
# up as a diff reviewers can spot, not as a silently-diverged reimplementation).
_DETECT = (
    f'source "{ROUTING_LIB}"; '
    '_APPLY_MODEL="$1"; _EXPECT_PROVIDER=0; '
    'if [ -n "$_APPLY_MODEL" ]; then '
    '_provider_for_model "$_APPLY_MODEL" >/dev/null 2>&1 && _EXPECT_PROVIDER=1; '
    'fi; '
    'echo "$_EXPECT_PROVIDER"'
)


def _expect_provider(model: str) -> str:
    return subprocess.run(
        ["bash", "-c", _DETECT, "_", model], capture_output=True, text=True
    ).stdout.strip()


def test_provider_routed_models_are_detected():
    for model in ("glm-5.3", "kimi-k2", "moonshot-v2", "deepseek-flash",
                   "qwen-max", "moonshotai/kimi-k2", "z-ai/glm-5"):
        assert _expect_provider(model) == "1", f"{model} must be detected as provider-routed"


def test_anthropic_and_empty_models_are_not_flagged():
    assert _expect_provider("claude-sonnet-5") == "0"
    assert _expect_provider("claude-opus-5-5") == "0"
    assert _expect_provider("") == "0"


# ── STATIC: switch_lane_token.sh wires the fix ───────────────────────────────
def test_script_detects_provider_routed_models():
    src = SCRIPT.read_text()
    assert "_EXPECT_PROVIDER" in src, "must detect a provider-routed model-apply"
    assert "_provider_for_model" in src, "must classify via the REAL provider_routing.sh table"
    assert '. "$_LIB/provider_routing.sh"' in src, "must source the single-source-of-truth table, not reimplement it"


def test_script_falls_back_to_auth_account_for_provider_lanes():
    src = SCRIPT.read_text()
    assert "_account_for_session" in src, "must have a way to read the freshest auth_account"
    assert "AFTER_ACCOUNT" in src, "the poll loop must track auth_account for provider lanes"
    assert '!= "unlabelled"' in src, "a provider lane's auth_account must be checked against the generic fallback label"


def test_script_gates_every_pass_fail_branch_through_one_flag():
    src = SCRIPT.read_text()
    assert "_FP_CHECK_OK" in src, "PASS/FAIL must route through one identity-check flag"
    # None of the final RESULT branches may compare AFTER_FP to NEW_FP directly any
    # more -- that comparison is categorically wrong for a provider-routed lane. Only
    # the one line computing _FP_CHECK_OK itself (and the loop's own non-provider
    # branch) may still do that raw comparison.
    direct_compares = [
        line for line in src.splitlines()
        if 'AFTER_FP" = "$NEW_FP' in line or 'AFTER_FP" != "$NEW_FP' in line
    ]
    assert len(direct_compares) == 2, (
        f"expected exactly 2 raw AFTER_FP/NEW_FP comparisons (the loop's non-provider "
        f"branch + the _FP_CHECK_OK assignment), found {len(direct_compares)} -- a "
        "PASS/FAIL gate is bypassing _FP_CHECK_OK and will mis-fire for a provider lane"
    )


def test_script_exit8_message_is_provider_aware():
    src = SCRIPT.read_text()
    assert "never got a provider label" in src, (
        "the exit-8 FAIL message must distinguish the provider case (auth_account "
        "never stamped) from the Anthropic case (auth_fp never flipped) -- an "
        "operator reading the FAIL text needs to know which one actually happened"
    )


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS: {name}")
