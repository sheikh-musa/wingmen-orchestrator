"""The BASH provider-routing cascade launch_dangerous_cc.sh uses to send a
lane to a non-Anthropic model provider (Musa op#26108 A/B harness, bus
#53237/#53267). Extracted into scripts/lib/provider_routing.sh so the SHIPPED
path is testable (gate-test != shipped-path) — same pattern as
model_precedence.sh / tests/test_model_precedence.py.

This is the regression guard orch-console's GO (#53267) required: proof that
(1) every claude-* lane (today's only case) resolves BYTE-IDENTICAL to before
this change (no vars touched at all), (2) the already-shipped glm-* arm keeps
its exact existing behavior, and (3) a vault-fetch failure fails CLOSED
(exports nothing, non-zero exit) rather than silently falling back to
Anthropic/metered billing.

Drives the REAL bash function via subprocess, with a stub fetch_script (never
the real nervous_system.vault) standing in for the vault read.
"""
import os
import stat
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LIB = os.path.join(_ROOT, "scripts", "lib", "provider_routing.sh")

_TRACKED_VARS = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE",
    "PROVIDER_ROUTING_LABEL",
    "PROVIDER_ROUTING_SMALL",
)


def _write_fetch_stub(tmp_path, *, key_by_vault_key=None, always=None):
    """A stub provider_vault_fetch.py replacement. `always` prints a fixed
    key regardless of input (vault succeeds); `key_by_vault_key` maps a
    specific $1 (vault key name) to a key value, anything else -> empty
    (vault fails for that provider only); neither set -> always fails."""
    path = tmp_path / "fetch_stub.py"
    if always is not None:
        body = f'import sys\nsys.stdout.write("{always}")\n'
    elif key_by_vault_key:
        cases = "\n".join(
            f'if sys.argv[1] == "{k}":\n    sys.stdout.write("{v}")'
            for k, v in key_by_vault_key.items()
        )
        body = f"import sys\n{cases}\n"
    else:
        body = "import sys\nsys.exit(1)\n"
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


_OVERRIDE_ENV_VARS = ("GLM_SMALL_MODEL", "MOONSHOT_SMALL_MODEL", "DEEPSEEK_SMALL_MODEL", "QWEN_SMALL_MODEL")


def _run(resolved_model, orch_dir, fetch_script, *, pre_env=None):
    """Source the lib, call apply_provider_routing, print rc + every
    tracked var (pipe-joined, in _TRACKED_VARS order) so the test can assert
    on the exact post-call shell state in one subprocess round-trip.

    Scrubs every tracked/override var from the inherited environment first —
    this test process's OWN shell (and this repo's launch_dangerous_cc.sh
    convention of `set -a; source .env`) may already have e.g.
    ANTHROPIC_API_KEY set for unrelated reasons; the subprocess must start
    from a known-clean slate or a "nothing touched" assertion is meaningless.
    """
    env = dict(os.environ)
    for k in _TRACKED_VARS + _OVERRIDE_ENV_VARS:
        env.pop(k, None)
    for k, v in (pre_env or {}).items():
        env[k] = v
    var_dump = " ".join(f'"${{{v}:-}}"' for v in _TRACKED_VARS)
    script = (
        f'source "{_LIB}"\n'
        + f'apply_provider_routing "{resolved_model}" "{orch_dir}" "{sys.executable}" "test-agent" "{fetch_script}"\n'
        + "echo \"RC=$?\"\n"
        + f'printf "%s\\x1f" {var_dump}\n'
    )
    out = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, cwd=_ROOT, env=env,
    )
    assert "RC=" in out.stdout, (out.stdout, out.stderr)
    rc_line, _, rest = out.stdout.partition("\n")
    rc = int(rc_line.split("=", 1)[1])
    # Every field is emitted as "value\x1f" (one trailing delimiter each,
    # including the last) — split and drop the final empty element rather
    # than rstrip, since rstrip("\x1f") would also eat genuinely-empty
    # trailing fields when every tracked var is unset (the fail-closed case).
    values = rest.rstrip("\n").split("\x1f")[:-1]
    assert len(values) == len(_TRACKED_VARS), (values, out.stdout, out.stderr)
    env = dict(zip(_TRACKED_VARS, values))
    return rc, env, out.stderr


@pytest.fixture
def failing_fetch(tmp_path):
    return _write_fetch_stub(tmp_path)


@pytest.fixture
def glm_fetch(tmp_path):
    return _write_fetch_stub(tmp_path, always="glm-secret-key")


# ── no-match: every claude-* (and any other unrecognized) model is a pure no-op ──

@pytest.mark.parametrize("model", ["claude-opus-4-8", "claude-sonnet-5", "claude-fable-5-1", "something-else"])
def test_claude_and_unknown_models_are_byte_identical_noop(model, tmp_path, failing_fetch):
    rc, env, stderr = _run(model, str(tmp_path), failing_fetch)
    assert rc == 0
    # NOTHING is touched — not even cleared/emptied; truly absent, as if the
    # cascade were never sourced.
    assert all(v == "" for v in env.values()), env
    assert stderr == ""


# ── glm-* keeps its exact pre-existing shape ─────────────────────────────────

def test_glm_routes_to_zai_with_vault_key(tmp_path, glm_fetch):
    rc, env, _ = _run("glm-5", str(tmp_path), glm_fetch)
    assert rc == 0
    assert env["ANTHROPIC_BASE_URL"] == "https://api.z.ai/api/anthropic"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "glm-secret-key"
    assert env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "glm-5"
    assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "glm-5"
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "glm-4.7"  # documented small default
    assert env["CLAUDE_CODE_SUBAGENT_MODEL"] == "glm-5"
    assert env["PROVIDER_ROUTING_LABEL"] == "z.ai GLM"
    # Anthropic creds scrubbed, never leaked to a non-Anthropic provider.
    assert env["ANTHROPIC_API_KEY"] == ""
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == ""
    assert env["CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE"] == ""


def test_glm_small_model_env_override_honored(tmp_path, glm_fetch):
    rc, env, _ = _run("glm-5", str(tmp_path), glm_fetch, pre_env={"GLM_SMALL_MODEL": "glm-4.6-custom"})
    assert rc == 0
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "glm-4.6-custom"


def test_glm_scrubs_preexisting_anthropic_creds(tmp_path, glm_fetch):
    rc, env, _ = _run(
        "glm-5", str(tmp_path), glm_fetch,
        pre_env={"ANTHROPIC_API_KEY": "sk-ant-leaky", "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-leaky"},
    )
    assert rc == 0
    assert env["ANTHROPIC_API_KEY"] == ""
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == ""


# ── new providers route correctly (op#26108) ──────────────────────────────────

@pytest.mark.parametrize("model,base_url,label", [
    ("kimi-k2.7-code", "https://api.moonshot.ai/anthropic", "Moonshot Kimi"),
    ("moonshot-v2", "https://api.moonshot.ai/anthropic", "Moonshot Kimi"),
    ("deepseek-v4-pro", "https://api.deepseek.com/anthropic", "DeepSeek"),
    ("qwen3.8-max", "https://coding-intl.dashscope.aliyuncs.com/apps/anthropic", "Qwen (DashScope Coding Plan)"),
])
def test_new_providers_route_to_their_own_endpoint(model, base_url, label, tmp_path):
    fetch = _write_fetch_stub(tmp_path, always="provider-secret")
    rc, env, _ = _run(model, str(tmp_path), fetch)
    assert rc == 0
    assert env["ANTHROPIC_BASE_URL"] == base_url
    assert env["ANTHROPIC_AUTH_TOKEN"] == "provider-secret"
    assert env["PROVIDER_ROUTING_LABEL"] == label
    assert env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == model
    assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == model
    assert env["CLAUDE_CODE_SUBAGENT_MODEL"] == model
    assert env["ANTHROPIC_API_KEY"] == ""
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == ""


def test_deepseek_small_default_is_deepseek_flash(tmp_path):
    fetch = _write_fetch_stub(tmp_path, always="k")
    rc, env, _ = _run("deepseek-v4-pro", str(tmp_path), fetch)
    assert rc == 0
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "deepseek-flash"


def test_qwen_small_default_is_qwen_flash(tmp_path):
    fetch = _write_fetch_stub(tmp_path, always="k")
    rc, env, _ = _run("qwen3.8-max", str(tmp_path), fetch)
    assert rc == 0
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "qwen3.6-flash"


def test_kimi_has_no_known_small_default_falls_back_to_main_model(tmp_path):
    """No doc-confirmed cheap Kimi variant at generalization time — the
    fallback must be the resolved model itself, never a guessed id."""
    fetch = _write_fetch_stub(tmp_path, always="k")
    rc, env, _ = _run("kimi-k2.7-code", str(tmp_path), fetch)
    assert rc == 0
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "kimi-k2.7-code"


def test_provider_small_model_override_env_honored_per_provider(tmp_path):
    fetch = _write_fetch_stub(tmp_path, always="k")
    rc, env, _ = _run("deepseek-v4-pro", str(tmp_path), fetch, pre_env={"DEEPSEEK_SMALL_MODEL": "deepseek-chat"})
    assert rc == 0
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "deepseek-chat"


# ── fail-closed: a vault-fetch failure must export NOTHING and exit non-zero ──

@pytest.mark.parametrize("model", ["glm-5", "kimi-k2.7-code", "deepseek-v4-pro", "qwen3.8-max"])
def test_vault_fetch_failure_is_fail_closed_no_export_nonzero_exit(model, tmp_path, failing_fetch):
    rc, env, stderr = _run(model, str(tmp_path), failing_fetch)
    assert rc == 1
    assert env["ANTHROPIC_BASE_URL"] == "", "a failed vault fetch must never leave ANTHROPIC_BASE_URL pointed at a non-Anthropic host with no credential behind it"
    assert env["ANTHROPIC_AUTH_TOKEN"] == ""
    assert "FATAL" in stderr
    assert "vault" in stderr.lower()


def test_one_providers_vault_outage_does_not_affect_another(tmp_path):
    """A stub that only knows GLM's key — DeepSeek must still fail closed,
    not silently reuse GLM's (wrong) credential."""
    fetch = _write_fetch_stub(tmp_path, key_by_vault_key={"GLM_CODING_KEY": "glm-only-key"})
    rc, env, _ = _run("deepseek-v4-pro", str(tmp_path), fetch)
    assert rc == 1
    assert env["ANTHROPIC_AUTH_TOKEN"] == ""
