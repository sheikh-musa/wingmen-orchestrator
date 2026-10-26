"""Every singleton/fleet boot launcher that sources the shared .env must strip
GOOGLE_APPLICATION_CREDENTIALS (and GOOGLE_CLOUD_PROJECT).

cc-quality review (post-incident, bus cross-session #514): a string match for
the `unset` line proves the line EXISTS, not that it actually takes effect --
a later re-source, a shadowing export, or the unset simply being unreachable
would all pass a text-only check while the lane still wedges. The wet-prove
test below actually EXECUTES each script's real .env-source-through-unset
snippet (extracted verbatim from the file, not retyped) against a fake .env
carrying a sentinel GAC value, in a real bash subprocess, and asserts the
resulting environment has it gone.

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
import subprocess
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


_IF_WRAPPER_RE = re.compile(r'if\s*\[\[\s*-f\s*"\$ORCH_DIR/\.env"\s*\]\];\s*then\s*\n')


def _extract_env_setup_snippet(text: str) -> str:
    """The verbatim slice from the FIRST .env-source match through the END of
    the line containing the GAC unset -- the real artifact, not a retyped
    stand-in. Raises if either anchor is missing (keeps this honest if either
    script changes shape).

    boot_orch.sh wraps its source in `if [[ -f "$ORCH_DIR/.env" ]]; then ...
    fi` -- if such an opener appears on one of the few lines immediately
    before the source match, the slice's start is pulled back to include it
    (its closing `fi` is already inside the slice, since that always sits
    between the source and the later unset line), so the extracted snippet
    stays syntactically self-contained."""
    env_match = _ENV_SOURCE_RE.search(text)
    assert env_match, "no .env source found — update this test"
    unset_match = _UNSET_RE.search(text)
    assert unset_match, "no GAC unset found — update this test"
    line_end = text.index("\n", unset_match.end())
    start = env_match.start()
    lookback = text[max(0, start - 200):start]
    if_match = None
    for m in _IF_WRAPPER_RE.finditer(lookback):
        if_match = m  # keep the LAST (nearest) one in the lookback window
    if if_match is not None:
        start = max(0, start - 200) + if_match.start()
    return text[start:line_end]


def test_post_boot_env_actually_lacks_gac_for_each_launcher(tmp_path):
    """Wet-prove: actually RUN each script's real env-setup snippet (not just
    grep for the unset line) against a fake .env carrying a sentinel
    GOOGLE_APPLICATION_CREDENTIALS, and assert the resulting shell environment
    genuinely no longer has it. A text-only check would pass even if the unset
    were unreachable, shadowed by a later re-export, or otherwise a dead letter."""
    fake_env = tmp_path / ".env"
    fake_env.write_text(
        "DATABASE_URL=postgresql://fake-sentinel\n"
        "GOOGLE_APPLICATION_CREDENTIALS=/fake/.wingmen/keys/cosem-sa-sentinel.json\n"
        "GOOGLE_CLOUD_PROJECT=fake-cosem-project\n"
    )
    failures = []
    for name in LAUNCHERS:
        src = (_SCRIPTS / name).read_text()
        snippet = _extract_env_setup_snippet(src)
        script = (
            f'ORCH_DIR={tmp_path}\n'
            f'{snippet}\n'
            f'echo "GAC=${{GOOGLE_APPLICATION_CREDENTIALS:-<ABSENT>}}"\n'
            f'echo "GCP=${{GOOGLE_CLOUD_PROJECT:-<ABSENT>}}"\n'
        )
        result = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, timeout=10
        )
        out = result.stdout
        if "GAC=<ABSENT>" not in out or "GCP=<ABSENT>" not in out:
            failures.append(f"{name}: {out.strip()!r} (stderr: {result.stderr.strip()!r})")
    assert not failures, (
        "these launchers' REAL env-setup snippet still leaves "
        "GOOGLE_APPLICATION_CREDENTIALS/GOOGLE_CLOUD_PROJECT set after actually "
        "running it against a fake .env: " + "; ".join(failures)
    )
