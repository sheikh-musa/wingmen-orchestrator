"""Tests for scripts/lib/lane_env_allowlist.sh — the least-privilege lane env
(op#29348 part b). Proves a planted secret in the parent env does NOT survive the
scrub, while the fleet-wide keepers do, and that the per-lane opt-in map works.
Fixture values are plain sentinels (never secret-shaped) on purpose.
"""
import os
import subprocess
import tempfile

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LIB = os.path.join(_REPO, "scripts", "lib", "lane_env_allowlist.sh")


def _scrub(base, env, orch_dir=None):
    """Run lane_env_scrubbed_pairs0 under a controlled env; return {NAME: VALUE}."""
    script = f'. "{_LIB}"; lane_env_scrubbed_pairs0 "{base}"'
    run_env = dict(env)
    run_env["ORCH_DIR"] = orch_dir or _REPO  # map lives at <ORCH_DIR>/scripts/lib/lane_env_optin.map
    out = subprocess.run(
        ["/bin/bash", "-c", script],
        env=run_env, capture_output=True, check=True,
    ).stdout
    pairs = {}
    for chunk in out.split(b"\x00"):
        if not chunk:
            continue
        name, _, val = chunk.partition(b"=")
        pairs[name.decode()] = val.decode()
    return pairs


def _base_env(**extra):
    e = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
         "HOME": os.environ.get("HOME", "/tmp")}
    e.update(extra)
    return e


def test_planted_secret_is_dropped():
    env = _base_env(
        DATABASE_URL="KEEP-DB-sentinel",
        GH_TOKEN="KEEP-GH-sentinel",
        CLAUDE_CODE_OAUTH_TOKEN="KEEP-OAUTH-sentinel",
        SECRET_BOT_TOKEN="DROP-telegram-sentinel",   # not allow-listed -> dropped
        SUPABASE_ACCESS_TOKEN="DROP-supa-sentinel",   # not allow-listed -> dropped
        MUSA_TELEGRAM_ID="DROP-123456",               # not allow-listed -> dropped
    )
    pairs = _scrub("cc-anything", env)
    assert pairs.get("DATABASE_URL") == "KEEP-DB-sentinel"
    assert pairs.get("GH_TOKEN") == "KEEP-GH-sentinel"
    assert pairs.get("CLAUDE_CODE_OAUTH_TOKEN") == "KEEP-OAUTH-sentinel"
    assert "SECRET_BOT_TOKEN" not in pairs
    assert "SUPABASE_ACCESS_TOKEN" not in pairs
    assert "MUSA_TELEGRAM_ID" not in pairs


def test_safe_essentials_and_part_a_vars_pass():
    env = _base_env(
        GOOGLE_APPLICATION_CREDENTIALS="/lane/.no-prod-adc",   # Part-A sentinel path
        CLOUDSDK_CONFIG="/lane/gcloud",
        CC_BASE_AGENT_ID="cc-x",
        TMUX="SENTINEL-tmux-ctx",
    )
    pairs = _scrub("cc-x", env)
    assert pairs.get("PATH")
    assert pairs.get("HOME")
    assert pairs.get("GOOGLE_APPLICATION_CREDENTIALS") == "/lane/.no-prod-adc"
    assert pairs.get("CLOUDSDK_CONFIG") == "/lane/gcloud"
    assert pairs.get("CC_BASE_AGENT_ID") == "cc-x"
    assert pairs.get("TMUX")  # claude needs tmux context


def test_unset_allowlisted_name_is_absent_not_empty():
    env = _base_env(DATABASE_URL="d")
    pairs = _scrub("cc-x", env)
    assert "SUPABASE_SERVICE_KEY" not in pairs  # not set in parent -> absent, not ""


def test_optin_map_adds_extra_for_named_lane_only():
    with tempfile.TemporaryDirectory() as d:
        libdir = os.path.join(d, "scripts", "lib")
        os.makedirs(libdir)
        with open(os.path.join(libdir, "lane_env_optin.map"), "w") as f:
            f.write("cc-deploy VERCEL_TOKEN\n")
        env = _base_env(VERCEL_TOKEN="DROP-unless-optin", DATABASE_URL="d")
        p_in = _scrub("cc-deploy", env, orch_dir=d)
        assert p_in.get("VERCEL_TOKEN") == "DROP-unless-optin"   # opted-in lane keeps it
        p_out = _scrub("cc-other", env, orch_dir=d)
        assert "VERCEL_TOKEN" not in p_out                        # other lane drops it


def test_values_with_spaces_and_equals_survive_intact():
    env = _base_env(DATABASE_URL="val with spaces = and = equals", GH_TOKEN="a b c")
    pairs = _scrub("cc-x", env)
    assert pairs.get("DATABASE_URL") == "val with spaces = and = equals"
    assert pairs.get("GH_TOKEN") == "a b c"
