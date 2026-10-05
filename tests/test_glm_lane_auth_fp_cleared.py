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

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "launch_dangerous_cc.sh"

_GLM_OVERRIDE_RE = re.compile(
    r'glm-\*\)\s*\n\s*CC_AUTH_FP=""\s*\n\s*CC_AUTH_LABEL="[^"]+"',
)
_BOOT_STAMP_RE = re.compile(r"agent_status_stamp\.py.*--mode\s+boot")
_PROVIDER_CREDENTIAL_UNSET_RE = re.compile(
    r"unset\s+CLAUDE_CODE_OAUTH_TOKEN\s+CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE\s+ANTHROPIC_API_KEY"
)


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


def test_glm_auth_override_runs_before_credentials_are_unset():
    """Sanity: the override reads CC_AUTH_FP (derived from
    CLAUDE_CODE_OAUTH_TOKEN) which must still be the ORIGINAL value at that
    point -- it doesn't matter whether the credential unset happens before or
    after for correctness of the override itself, but this pins that the
    override block and the later credential-unset block are the same branch
    (glm-*) so they can never drift out of sync silently."""
    src = _SCRIPT.read_text()
    override_m = _GLM_OVERRIDE_RE.search(src)
    unset_m = _PROVIDER_CREDENTIAL_UNSET_RE.search(src)
    assert override_m and unset_m, "both blocks must exist"
    assert override_m.start() < unset_m.start()
