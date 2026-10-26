"""Every singleton/fleet boot launcher that sources the shared .env must strip
GOOGLE_APPLICATION_CREDENTIALS (and GOOGLE_CLOUD_PROJECT).

.env carries a permanent GOOGLE_APPLICATION_CREDENTIALS for cosem-adcda-specific
tooling (op#29348 prod-wall). `set -a; . .env; set +a` auto-exports it straight
into whichever launcher sources it -- unlike an engineer-lane launch
(launch_dangerous_cc.sh calls apply_lane_gcloud_isolation to isolate this
per-lane), the singleton launchers below had no such step, so the raw prod SA
key pointer survived into the launched `claude` session's own process env.

Live incident (2026-10-26): cc-quality inherited this var, and the PreToolUse
lane_prod_key_guard (correctly, by design) then refused EVERY subsequent Bash
call for that lane -- a total self-inflicted wedge, caught via a cross-session
report from cc-quality itself. None of these singletons have any cosem-adcda
business, so the fix is a bare `unset` right after the .env source, same idiom
already used for ANTHROPIC_API_KEY.
"""
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO_ROOT / "scripts"

# Every singleton launcher that sources the shared $ORCH_DIR/.env with `set -a`
# and then launches an interactive `claude` session.
LAUNCHERS = [
    "boot_orch.sh",
    "boot_nazim.sh",
    "boot_cai.sh",
    "boot_fleet_health.sh",
    "boot_quality.sh",
]

_UNSET_RE = re.compile(r"^\s*unset\s+GOOGLE_APPLICATION_CREDENTIALS\s+GOOGLE_CLOUD_PROJECT\s*$", re.MULTILINE)
_ENV_SOURCE_RE = re.compile(r'source\s+"\$ORCH_DIR/\.env"|\.\s+"\$ORCH_DIR/\.env"')


def test_all_launchers_unset_gac():
    missing = []
    for name in LAUNCHERS:
        path = _SCRIPTS / name
        assert path.exists(), f"launcher {name} not found at {path}"
        if not _UNSET_RE.search(path.read_text()):
            missing.append(name)
    assert not missing, (
        "these launchers source .env (which carries a cosem-adcda prod "
        "GOOGLE_APPLICATION_CREDENTIALS) but never unset it, so the launched "
        "lane inherits a prod GCP credential and gets hard-wedged by "
        "lane_prod_key_guard on its very first Bash call: " + ", ".join(missing)
    )


def test_unset_comes_after_the_env_source_in_each_launcher():
    """The unset must come AFTER the .env source, or the source re-sets it and
    the strip is a no-op (same trap as the ANTHROPIC_API_KEY regression test)."""
    broken = []
    for name in LAUNCHERS:
        src = (_SCRIPTS / name).read_text()
        env_match = list(_ENV_SOURCE_RE.finditer(src))
        assert env_match, f"{name} no longer sources .env the expected way — update this test"
        last_env_idx = env_match[-1].start()
        unset_match = _UNSET_RE.search(src)
        if not unset_match or unset_match.start() < last_env_idx:
            broken.append(name)
    assert not broken, (
        "these launchers unset GOOGLE_APPLICATION_CREDENTIALS BEFORE (or never "
        "after) sourcing .env, so the source re-populates it and the strip is "
        "a no-op: " + ", ".join(broken)
    )
