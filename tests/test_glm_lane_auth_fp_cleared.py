"""GLM-booted lanes must not keep a stale Claude auth_fp (cc-fleet-health bus
#53191, reply to orch-console #53189).

launch_dangerous_cc.sh computes CC_AUTH_FP/CC_AUTH_LABEL from
CLAUDE_CODE_OAUTH_TOKEN and stamps them into agent_status at BOOT (before the
GLM-provider block runs, which unsets every Anthropic credential and routes
to z.ai). Because agent_status_stamp.py's beat-mode SQL only FILLS a NULL
auth_fp and never corrects an already-stamped non-null one, a stale Claude fp
survives for the life of the GLM lane's process -- it fooled a pool
root-cause pass (5 GLM-pinned lanes all showed a Musa-key fp despite running
z.ai creds, verified only by live `ps` inspection).

Static text-scan test (same style as test_boot_scripts_strip_api_key.py):
no shell execution, just pins the override exists and runs early enough.
"""
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts" / "launch_dangerous_cc.sh"
_ROUTING_LIB = _ROOT / "scripts" / "lib" / "provider_routing.sh"

_GLM_OVERRIDE_RE = re.compile(
    r'glm-\*\)\s*\n\s*CC_AUTH_FP=""\s*\n\s*CC_AUTH_LABEL="[^"]+"',
)
_BOOT_STAMP_RE = re.compile(r"agent_status_stamp\.py.*--mode\s+boot")
# Every `case "$RESOLVED_MODEL" in ... esac` arm pattern, e.g. "glm-*" or
# "kimi-*|moonshot-*" or "moonshotai/*|deepseek/*|qwen/*|z-ai/*" -- one match per arm,
# captured as its raw `|`-joined pattern string (not yet split into individual globs).
_CASE_ARM_RE = re.compile(r'^\s*([A-Za-z0-9_*/.\-]+(?:\|[A-Za-z0-9_*/.\-]+)*)\)', re.MULTILINE)


def _auth_override_prefixes():
    """The glob patterns covered by launch_dangerous_cc.sh's CC_AUTH_FP-clearing
    `case "$RESOLVED_MODEL"` block -- as a flat set of individual (non-|-joined) globs."""
    src = _SCRIPT.read_text()
    override_start = src.index(_GLM_OVERRIDE_RE.search(src).group(0))
    start = src.rfind('case "$RESOLVED_MODEL" in', 0, override_start)
    assert start != -1, "glm-* override arm is no longer inside a case \"$RESOLVED_MODEL\" block"
    block = src[start:src.index("\nesac", start)]
    prefixes = set()
    for arm in _CASE_ARM_RE.findall(block):
        prefixes.update(arm.split("|"))
    return prefixes


def _provider_routing_prefixes():
    """The glob patterns _provider_for_model (provider_routing.sh) recognizes as a
    non-Anthropic provider -- every agent routed through ANY of these gets its
    Anthropic credentials unset, so it must ALSO get its CC_AUTH_FP cleared above."""
    src = _ROUTING_LIB.read_text()
    start = src.index('case "$1" in', src.index("_provider_for_model()"))
    block = src[start:src.index("\n}", start)]
    prefixes = set()
    for arm in _CASE_ARM_RE.findall(block):
        prefixes.update(arm.split("|"))
    # the bare `*)` arm is the catch-all "no known provider" default, not a provider.
    prefixes.discard("*")
    return prefixes


def test_glm_branch_clears_auth_fp_and_relabels_account():
    src = _SCRIPT.read_text()
    m = _GLM_OVERRIDE_RE.search(src)
    assert m, (
        "launch_dangerous_cc.sh no longer clears CC_AUTH_FP / relabels "
        "CC_AUTH_LABEL for a glm-* resolved model -- a GLM lane would stamp "
        "its stale Claude token fp into agent_status again"
    )
    label = re.search(r'CC_AUTH_LABEL="([^"]+)"', m.group(0)).group(1)
    assert label != "unlabelled", (
        "the GLM override must stamp a DISTINCT marker, not fall back to the "
        "generic 'unlabelled' (indistinguishable from a genuinely unknown "
        "Claude account)"
    )


def test_glm_auth_override_runs_before_the_boot_stamp():
    """The override is only useful if it runs before agent_status_stamp.py
    --mode boot reads CC_AUTH_FP/CC_AUTH_LABEL -- after that point the stale
    value is already committed and beat-mode will never correct it."""
    src = _SCRIPT.read_text()
    override_m = _GLM_OVERRIDE_RE.search(src)
    boot_m = _BOOT_STAMP_RE.search(src)
    assert override_m and boot_m, "both the override and the boot stamp call must exist"
    assert override_m.start() < boot_m.start(), (
        "the glm-* auth_fp/auth_account override must run BEFORE the boot "
        "stamp call, or the stale Claude fp is already written by the time "
        "it fires"
    )


def test_auth_override_table_stays_in_sync_with_provider_routing():
    """The op#26108 refactor (bus #53237/#53267/#53278) moved the credential-unset
    logic out of launch_dangerous_cc.sh into provider_routing.sh's _provider_for_model,
    so the original co-location check (both blocks textually adjacent in ONE case arm
    of ONE file) no longer applies -- they're deliberately two files now. What still
    matters (and is what #53191 was actually about): every prefix that gets Anthropic
    credentials unset by provider_routing.sh must ALSO get its CC_AUTH_FP cleared by
    launch_dangerous_cc.sh's override block above, or a provider-routed lane keeps
    stamping a stale Claude token fp into agent_status for its whole life (exactly the
    #53191 bug, now re-checked at the TABLE level instead of one hardcoded branch --
    this is what caught the OpenRouter arm (bus #53278) missing from the override
    block entirely until this same investigation added it, bus #53303)."""
    routing_prefixes = _provider_routing_prefixes()
    override_prefixes = _auth_override_prefixes()
    missing = routing_prefixes - override_prefixes
    assert not missing, (
        f"provider_routing.sh routes {missing} to a non-Anthropic provider (unsetting "
        "Anthropic credentials) but launch_dangerous_cc.sh's CC_AUTH_FP override block "
        "has no matching arm -- those lanes will keep a stale Claude auth_fp in "
        "agent_status (the #53191 bug class)"
    )


def test_glm_auth_override_runs_before_the_provider_routing_source():
    """The override must still run before scripts/lib/provider_routing.sh is sourced
    (the actual credential-unset), or CC_AUTH_FP's read of CLAUDE_CODE_OAUTH_TOKEN
    would already reflect this lane's provider creds rather than its original value."""
    src = _SCRIPT.read_text()
    override_m = _GLM_OVERRIDE_RE.search(src)
    source_idx = src.index('source "$ORCH_DIR/scripts/lib/provider_routing.sh"')
    assert override_m and override_m.start() < source_idx
