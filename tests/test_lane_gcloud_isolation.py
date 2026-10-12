"""PART A of the lane prod-data wall (op#29348).

Drives the REAL bash function scripts/lib/lane_gcloud_isolation.sh that
launch_dangerous_cc.sh sources + calls — the SHIPPED path, not a
transcription (same tested-cascade pattern as test_model_precedence.py /
test_provider_routing.py).

The isolation must, FOR A WORKER LANE ONLY:
  - SET GOOGLE_APPLICATION_CREDENTIALS to a guaranteed-non-existent sentinel
    path (NOT unset it). WHY: Node's ADC resolution, when GAC is UNSET, falls
    back to the well-known per-user ~/.config/gcloud ADC (and IGNORES
    CLOUDSDK_CONFIG). So unsetting leaves the prod ADC reachable. Pointing GAC
    at a non-existent sentinel makes ADC FAIL CLOSED (credentials error) instead
    of silently resolving the well-known prod creds. And
  - export CLOUDSDK_CONFIG pointing at a per-lane gcloud config that has NO
    project and NO account (so gcloud/ADC has no implicit cosem-adcda prod
    project).
It must be a NO-OP for the console (base == orch-console) and for a non-lane
(empty base == CI / bare shell), leaving the ambient prod default untouched.
"""
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LIB = os.path.join(_ROOT, "scripts", "lib", "lane_gcloud_isolation.sh")

# A fake pre-existing ambient prod-key path, so we can PROVE the lane path
# actually unsets it (and the console path does not).
_AMBIENT_GAC = "/Users/sheikhmusa/.wingmen/keys/cosem-sa.json"


def _drive(base, lane_id, config_root):
    """Source the lib, seed the ambient GAC, call the function, print the
    resulting env + the function's exit code. Returns parsed dict."""
    script = (
        'export GOOGLE_APPLICATION_CREDENTIALS="$GAC_SEED"; '
        'unset CLOUDSDK_CONFIG; '
        'source "$1"; '
        'apply_lane_gcloud_isolation "$2" "$3" "$4"; '
        'rc=$?; '
        'echo "RC=$rc"; '
        'echo "GAC=${GOOGLE_APPLICATION_CREDENTIALS:-<UNSET>}"; '
        'echo "CLOUDSDK=${CLOUDSDK_CONFIG:-<UNSET>}"'
    )
    env = dict(os.environ)
    env.pop("CLOUDSDK_CONFIG", None)
    env["GAC_SEED"] = _AMBIENT_GAC
    out = subprocess.run(
        ["bash", "-c", script, "_", _LIB, base, lane_id, config_root],
        capture_output=True, text=True, env=env,
    )
    assert out.returncode == 0, out.stderr
    parsed = {}
    for line in out.stdout.splitlines():
        k, _, v = line.partition("=")
        parsed[k] = v
    return parsed


# ── worker lane: isolated ─────────────────────────────────────────────────────

def test_worker_lane_sets_sentinel_gac_and_points_cloudsdk_at_no_project_config(tmp_path):
    root = str(tmp_path / "cfg")
    res = _drive("cc-cosem-platform", "cc-cosem-platform-3", root)
    assert res["RC"] == "0", res
    # (2) ambient prod-key ADC dropped AND replaced with a FAIL-CLOSED sentinel:
    # GAC points at a guaranteed-non-existent path under the per-lane dir, so
    # ADC errors rather than falling back to the well-known prod ADC.
    assert res["GAC"] != "<UNSET>", "worker lane must SET a sentinel GAC (not unset — unset lets Node ADC fall back to the well-known prod creds)"
    assert res["GAC"] != _AMBIENT_GAC, "worker lane must not keep the ambient prod GAC"
    assert res["GAC"].endswith("/.no-prod-adc"), res["GAC"]
    assert res["GAC"].startswith(root), res["GAC"]
    assert not os.path.exists(res["GAC"]), "the sentinel GAC path must NOT exist, so ADC fails closed"
    # (1) CLOUDSDK_CONFIG points at a per-lane dir under our root (NOT the
    # per-user ~/.config/gcloud prod default).
    assert res["CLOUDSDK"].startswith(root), res["CLOUDSDK"]
    assert res["CLOUDSDK"].endswith("cc-cosem-platform-3"), res["CLOUDSDK"]
    # The seeded active config has an empty [core] — NO project, NO account.
    cfg = os.path.join(res["CLOUDSDK"], "configurations", "config_default")
    assert os.path.isfile(cfg)
    body = open(cfg).read()
    assert "[core]" in body
    assert "project" not in body.lower(), "seeded config must name NO project"
    assert "account" not in body.lower(), "seeded config must name NO account"


def test_worker_lane_falls_back_to_base_when_no_instance_id(tmp_path):
    root = str(tmp_path / "cfg")
    res = _drive("cc-cosem-platform", "", root)
    assert res["RC"] == "0", res
    assert res["GAC"].endswith("/.no-prod-adc"), res["GAC"]
    assert not os.path.exists(res["GAC"]), res["GAC"]
    assert res["CLOUDSDK"].endswith("cc-cosem-platform"), res["CLOUDSDK"]


# ── console / CI: untouched (no-op) ───────────────────────────────────────────

def test_console_base_is_untouched(tmp_path):
    # base == orch-console: the console's own prod-ops + config-write path
    # (trainer-strip, 2026-10-10) must be UNAFFECTED.
    res = _drive("orch-console", "orch-console", str(tmp_path / "cfg"))
    assert res["RC"] == "0", res
    assert res["GAC"] == _AMBIENT_GAC, "console must keep its ambient prod creds"
    assert res["CLOUDSDK"] == "<UNSET>", "console must not get a per-lane CLOUDSDK_CONFIG"


def test_empty_base_ci_or_bare_shell_is_untouched(tmp_path):
    # No CC_BASE_AGENT_ID == CI / a bare shell -> no-op, ambient env intact.
    res = _drive("", "", str(tmp_path / "cfg"))
    assert res["RC"] == "0", res
    assert res["GAC"] == _AMBIENT_GAC
    assert res["CLOUDSDK"] == "<UNSET>"


# ── fail-loud: GAC fail-closed even if the config dir cannot be seeded ─────────

def test_unseedable_config_root_still_fails_gac_closed_and_returns_nonzero(tmp_path):
    # Point config_root at a path whose parent is a FILE -> mkdir -p fails.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a dir")
    root = str(blocker / "cfg")  # cannot mkdir under a file
    res = _drive("cc-cosem-platform", "cc-cosem-platform-3", root)
    # Returns non-zero so the launcher surfaces it LOUDLY (not swallowed)...
    assert res["RC"] == "1", res
    # ...but the primary vector (ambient prod-key ADC) is STILL closed: GAC is
    # set to the non-existent sentinel BEFORE any seeding can fail, so ADC fails
    # closed rather than keeping the ambient prod key OR falling back to the
    # well-known prod ADC.
    assert res["GAC"] != "<UNSET>", "GAC must be the fail-closed sentinel, not unset"
    assert res["GAC"] != _AMBIENT_GAC, "the ambient prod GAC must be overwritten even on seed failure"
    assert res["GAC"].endswith("/.no-prod-adc"), res["GAC"]
    assert not os.path.exists(res["GAC"]), "sentinel must not exist (fail closed)"
    # And we did NOT point CLOUDSDK at an uncontrolled / prod-default dir.
    assert res["CLOUDSDK"] == "<UNSET>", res["CLOUDSDK"]
