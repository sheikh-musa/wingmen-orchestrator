"""Singleton-boot provider-routing (op#29782 part 3).

boot_cai.sh and boot_fleet_health.sh must route to a non-Anthropic provider when
their MODEL pin is a provider id (e.g. glm-5.3), mirroring the lane path
(launch_dangerous_cc.sh -> scripts/lib/provider_routing.sh). The load-bearing
safety claim: for a claude-* MODEL, apply_provider_routing is a pure no-op
(exports nothing, rc 0) so the Anthropic boot is byte-identical to before.

Two checks per script:
  (1) STATIC: the script sources provider_routing.sh and calls
      apply_provider_routing BEFORE its `claude --dangerously-skip-permissions
      --model` launch line (ordering is load-bearing — routing must export into
      the same shell that then launches claude).
  (2) BEHAVIORAL: drive the REAL apply_provider_routing via subprocess with a
      STUB fetch_script (never touches the vault): claude-* -> no-op; glm-5.3 ->
      routed to z.ai with the small/background slot set.
"""
import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "scripts" / "lib" / "provider_routing.sh"
BOOTS = {
    "boot_cai.sh": ROOT / "scripts" / "boot_cai.sh",
    "boot_fleet_health.sh": ROOT / "scripts" / "boot_fleet_health.sh",
}
# boot_fleet_health.sh resolves CLAUDE_BIN (bus #86258/#86793 PATH fix) and
# launches via the resolved binary instead of a bare `claude`; boot_cai.sh is
# untouched by that fix and still launches bare.
LAUNCH = {
    "boot_cai.sh": 'claude --dangerously-skip-permissions --model "$MODEL"',
    "boot_fleet_health.sh": '"$CLAUDE_BIN" --dangerously-skip-permissions --model "$MODEL"',
}


# ── (1) STATIC: sources lib + calls apply_provider_routing BEFORE the launch ──
def test_boots_route_before_launch():
    for name, path in BOOTS.items():
        text = path.read_text()
        assert 'scripts/lib/provider_routing.sh' in text, f"{name}: does not source provider_routing.sh"
        i_src = text.index('scripts/lib/provider_routing.sh')
        i_call = text.index('apply_provider_routing "$MODEL"')
        i_launch = text.index(LAUNCH[name])
        assert i_src < i_launch, f"{name}: sources lib AFTER the claude launch"
        assert i_call < i_launch, f"{name}: calls apply_provider_routing AFTER the claude launch"
        # routing call must pass the script's own AGENT_ID (vault keying)
        assert 'apply_provider_routing "$MODEL" "$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" "$AGENT_ID"' in text, \
            f"{name}: apply_provider_routing call signature/AGENT_ID mismatch"
        # fail-closed: a routing failure must refuse the boot, not fall through
        seg = text[i_call:i_launch]
        assert 'exit 1' in seg, f"{name}: routing failure does not fail-closed before launch"


def _write_fetch_stub(tmp_path, key="FAKE-GLM-KEY-xyz"):
    p = tmp_path / "fetch_stub.py"
    p.write_text(f'import sys\nsys.stdout.write("{key}")\n')
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def _apply(resolved_model, fetch_script, tmp_path):
    """Source the real lib, call apply_provider_routing, dump rc + the vars."""
    vars_ = ["ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_DEFAULT_OPUS_MODEL",
             "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL",
             "CLAUDE_CODE_OAUTH_TOKEN", "PROVIDER_ROUTING_LABEL"]
    env = dict(os.environ)
    for k in vars_:
        env.pop(k, None)
    env.pop("ANTHROPIC_API_KEY", None)
    # a non-routed lane keeps its OAuth token; seed one so we can prove routing clears it for glm
    env["CLAUDE_CODE_OAUTH_TOKEN"] = "seed-oauth"
    dump = " ".join(f'"${{{v}:-}}"' for v in vars_)
    script = (
        f'source "{LIB}"\n'
        f'apply_provider_routing "{resolved_model}" "{ROOT}" "{sys.executable}" "test-agent" "{fetch_script}"\n'
        'echo "RC=$?"\n'
        f'printf "%s\\x1f" {dump}\n'
    )
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd=str(ROOT), env=env)
    assert "RC=" in out.stdout, (out.stdout, out.stderr)
    rc = int(out.stdout.split("RC=", 1)[1].split("\n", 1)[0])
    rest = out.stdout.split("\n", 1)[1]
    vals = rest.rstrip("\n").split("\x1f")[:-1]
    return rc, dict(zip(vars_, vals))


# ── (2) BEHAVIORAL ────────────────────────────────────────────────────────────
def test_claude_model_is_noop(tmp_path):
    rc, v = _apply("claude-sonnet-5", _write_fetch_stub(tmp_path), tmp_path)
    assert rc == 0
    assert v["ANTHROPIC_BASE_URL"] == "", v            # no routing
    assert v["ANTHROPIC_AUTH_TOKEN"] == ""
    assert v["PROVIDER_ROUTING_LABEL"] == ""
    assert v["CLAUDE_CODE_OAUTH_TOKEN"] == "seed-oauth"  # OAuth preserved for the Anthropic boot


def test_glm_model_routes_to_zai(tmp_path):
    rc, v = _apply("glm-5.3", _write_fetch_stub(tmp_path), tmp_path)
    assert rc == 0
    assert v["ANTHROPIC_BASE_URL"] == "https://api.z.ai/api/anthropic", v
    assert v["ANTHROPIC_AUTH_TOKEN"] == "FAKE-GLM-KEY-xyz"
    assert v["ANTHROPIC_SMALL_FAST_MODEL"] == "glm-5.3"   # small/background slot routed (the wedge fix)
    assert v["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "glm-5.3"
    assert v["CLAUDE_CODE_OAUTH_TOKEN"] == ""             # OAuth cleared so no Anthropic cred reaches z.ai
    assert "GLM" in v["PROVIDER_ROUTING_LABEL"]


def test_glm_vault_fetch_failure_fails_closed(tmp_path):
    # empty-key stub -> apply_provider_routing must return 1 (refuse), export no base url
    p = tmp_path / "empty_stub.py"
    p.write_text("import sys\nsys.exit(1)\n")
    rc, v = _apply("glm-5.3", str(p), tmp_path)
    assert rc == 1, (rc, v)
    assert v["ANTHROPIC_BASE_URL"] == ""
