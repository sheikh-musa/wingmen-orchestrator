"""The BASH provider-routing cascade launch_dangerous_cc.sh uses to send a
lane to a non-Anthropic model provider (Musa op#26108 A/B harness, bus
#53237/#53267/#53278). Extracted into scripts/lib/provider_routing.sh so the
SHIPPED path is testable (gate-test != shipped-path) — same pattern as
model_precedence.sh / tests/test_model_precedence.py.

This is the regression guard orch-console's GO (#53267) required: proof that
(1) every claude-* lane (today's only case) resolves BYTE-IDENTICAL to before
this change (no vars touched at all), (2) the already-shipped glm-* arm keeps
its exact existing behavior, and (3) a vault-fetch failure fails CLOSED
(exports nothing, non-zero exit) rather than silently falling back to
Anthropic/metered billing. bus #53278 re-scoped the A/B TEST phase to
OpenRouter (one key, not three) and added a critical, easy-to-miss gotcha:
the OpenRouter arm must EXPORT ANTHROPIC_API_KEY="" (set to empty), never
leave it unset, or Claude Code can fall back to api.anthropic.com directly.
"unset" and "exported empty" both read back as "" through a plain
${VAR:-} dump, so this file also probes `${VAR+SET}` specifically for that
var to prove the distinction actually holds.

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
    "ANTHROPIC_SMALL_FAST_MODEL",
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


_OVERRIDE_ENV_VARS = (
    "GLM_SMALL_MODEL",
    "MOONSHOT_SMALL_MODEL",
    "DEEPSEEK_SMALL_MODEL",
    "QWEN_SMALL_MODEL",
    "OPENROUTER_SMALL_MODEL",
)


def _run(resolved_model, orch_dir, fetch_script, *, pre_env=None):
    """Source the lib, call apply_provider_routing, print rc + every
    tracked var (pipe-joined, in _TRACKED_VARS order) so the test can assert
    on the exact post-call shell state in one subprocess round-trip. Also
    appends one extra probe, `${ANTHROPIC_API_KEY+SET}`, which is "SET" when
    the var is set (even to "") and "" when it is unset entirely — the plain
    ${VAR:-} dump used for every other var cannot tell those two apart, but
    the OpenRouter arm's correctness hinges on exactly that distinction.

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
        + f'printf "%s\\x1f" {var_dump} "${{ANTHROPIC_API_KEY+SET}}"\n'
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
    assert len(values) == len(_TRACKED_VARS) + 1, (values, out.stdout, out.stderr)
    env = dict(zip(_TRACKED_VARS, values))
    env["ANTHROPIC_API_KEY_IS_SET"] = values[-1] == "SET"
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
    # cascade were never sourced. ANTHROPIC_API_KEY must be truly UNSET, not
    # merely read back as "" (which an exported empty string would also do).
    assert env.pop("ANTHROPIC_API_KEY_IS_SET") is False, "no-op must leave ANTHROPIC_API_KEY untouched/unset, never export it empty"
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
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "glm-5.3"  # small default = glm-5.3 (1M), NOT glm-4.7 (131K) — op#29665
    # BOTH small-slot names route to $small: newer CC reads HAIKU, older reads
    # the legacy SMALL_FAST (bus #65856 — unset SMALL_FAST wedged substrate-1).
    assert env["ANTHROPIC_SMALL_FAST_MODEL"] == "glm-5.3"
    assert env["CLAUDE_CODE_SUBAGENT_MODEL"] == "glm-5"
    assert env["PROVIDER_ROUTING_LABEL"] == "z.ai GLM"
    # Anthropic creds scrubbed, never leaked to a non-Anthropic provider. A
    # native arm UNSETS ANTHROPIC_API_KEY (unlike the OpenRouter arm, which
    # must export it empty — see test_openrouter_exports_empty_string_...).
    assert env["ANTHROPIC_API_KEY"] == ""
    assert env["ANTHROPIC_API_KEY_IS_SET"] is False
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == ""
    assert env["CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE"] == ""


def test_glm53_resolves_main_and_small_both_to_glm53(tmp_path, glm_fetch):
    # op#29665 / #65656: a lane pointed at glm-5.3 must route BOTH the main slot
    # (opus/sonnet/subagent) AND the small/fast slot (haiku) to glm-5.3. The small
    # slot is what claude-code uses for summarisation/auto-compaction; if it stayed
    # the 131K glm-4.7, compaction would overflow and wedge ("Not logged in") even
    # with a 1M main pointer. glm-5.3 is 1M on the Coding Plan, so no cliff.
    rc, env, _ = _run("glm-5.3", str(tmp_path), glm_fetch)
    assert rc == 0
    assert env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "glm-5.3"
    assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "glm-5.3"
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "glm-5.3"   # the compaction small slot — MUST be 1M too
    assert env["ANTHROPIC_SMALL_FAST_MODEL"] == "glm-5.3"       # legacy name for the same slot
    assert env["CLAUDE_CODE_SUBAGENT_MODEL"] == "glm-5.3"


def test_glm_small_model_env_override_honored(tmp_path, glm_fetch):
    rc, env, _ = _run("glm-5", str(tmp_path), glm_fetch, pre_env={"GLM_SMALL_MODEL": "glm-4.6-custom"})
    assert rc == 0
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "glm-4.6-custom"
    # the override drives BOTH small-slot names, not just the newer one
    assert env["ANTHROPIC_SMALL_FAST_MODEL"] == "glm-4.6-custom"


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
    ("kimi-k2.7-code", "https://api.moonshot.ai/anthropic", "Moonshot Kimi (native)"),
    ("moonshot-v2", "https://api.moonshot.ai/anthropic", "Moonshot Kimi (native)"),
    ("deepseek-v4-pro", "https://api.deepseek.com/anthropic", "DeepSeek (native)"),
    ("qwen3.8-max", "https://coding-intl.dashscope.aliyuncs.com/apps/anthropic", "Qwen (DashScope Coding Plan, native)"),
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
    assert env["ANTHROPIC_API_KEY_IS_SET"] is False, "native arms must UNSET, not export-empty"
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


# ── OpenRouter arm (bus #53278, Musa op#26140 re-scope): the active A/B TEST
# path, one key for every vendor, selected by OpenRouter's own vendor/model
# namespaced id strings rather than each vendor's native bare model id ──────

@pytest.mark.parametrize("model", [
    "moonshotai/kimi-k2",
    "deepseek/deepseek-v3",
    "qwen/qwen3-coder",
    "z-ai/glm-4.7",
])
def test_openrouter_routes_every_vendor_through_one_endpoint_and_key(model, tmp_path):
    fetch = _write_fetch_stub(tmp_path, always="or-secret-key")
    rc, env, _ = _run(model, str(tmp_path), fetch)
    assert rc == 0
    assert env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "or-secret-key"
    assert env["PROVIDER_ROUTING_LABEL"] == "OpenRouter"
    assert env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == model
    assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == model
    assert env["CLAUDE_CODE_SUBAGENT_MODEL"] == model


def test_openrouter_exports_empty_string_api_key_never_unset(tmp_path):
    """The one gotcha this whole arm hinges on, per OpenRouter's own docs
    (bus #53278): ANTHROPIC_API_KEY must be the EMPTY STRING, not absent —
    an unset/absent key lets Claude Code fall back to authenticating against
    api.anthropic.com directly, silently defeating the whole A/B test."""
    fetch = _write_fetch_stub(tmp_path, always="or-secret-key")
    rc, env, _ = _run("moonshotai/kimi-k2", str(tmp_path), fetch)
    assert rc == 0
    assert env["ANTHROPIC_API_KEY"] == ""
    assert env["ANTHROPIC_API_KEY_IS_SET"] is True, "OpenRouter arm must EXPORT ANTHROPIC_API_KEY=\"\", not leave it unset"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == ""
    assert env["CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE"] == ""


def test_openrouter_scrubs_preexisting_anthropic_creds_to_empty_not_unset(tmp_path):
    fetch = _write_fetch_stub(tmp_path, always="or-secret-key")
    rc, env, _ = _run(
        "qwen/qwen3-coder", str(tmp_path), fetch,
        pre_env={"ANTHROPIC_API_KEY": "sk-ant-leaky", "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-leaky"},
    )
    assert rc == 0
    assert env["ANTHROPIC_API_KEY"] == ""
    assert env["ANTHROPIC_API_KEY_IS_SET"] is True
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == ""


def test_openrouter_small_model_override_env_honored(tmp_path):
    fetch = _write_fetch_stub(tmp_path, always="or-secret-key")
    rc, env, _ = _run(
        "deepseek/deepseek-v3", str(tmp_path), fetch,
        pre_env={"OPENROUTER_SMALL_MODEL": "deepseek/deepseek-v3-haiku"},
    )
    assert rc == 0
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "deepseek/deepseek-v3-haiku"


def test_openrouter_vault_fetch_failure_is_fail_closed(tmp_path, failing_fetch):
    rc, env, stderr = _run("moonshotai/kimi-k2", str(tmp_path), failing_fetch)
    assert rc == 1
    assert env["ANTHROPIC_BASE_URL"] == ""
    assert env["ANTHROPIC_API_KEY_IS_SET"] is False, "a failed fetch must abort before the api_key_mode=empty export runs"
    assert "FATAL" in stderr


# ── fail-closed: a vault-fetch failure must export NOTHING and exit non-zero ──

@pytest.mark.parametrize("model", ["glm-5", "kimi-k2.7-code", "deepseek-v4-pro", "qwen3.8-max", "moonshotai/kimi-k2"])
def test_vault_fetch_failure_is_fail_closed_no_export_nonzero_exit(model, tmp_path, failing_fetch):
    rc, env, stderr = _run(model, str(tmp_path), failing_fetch)
    assert rc == 1
    assert env["ANTHROPIC_BASE_URL"] == "", "a failed vault fetch must never leave ANTHROPIC_BASE_URL pointed at a non-Anthropic host with no credential behind it"
    assert env["ANTHROPIC_AUTH_TOKEN"] == ""
    assert env["ANTHROPIC_API_KEY_IS_SET"] is False
    assert "FATAL" in stderr
    assert "vault" in stderr.lower()


def test_one_providers_vault_outage_does_not_affect_another(tmp_path):
    """A stub that only knows GLM's key — DeepSeek must still fail closed,
    not silently reuse GLM's (wrong) credential."""
    fetch = _write_fetch_stub(tmp_path, key_by_vault_key={"GLM_CODING_KEY": "glm-only-key"})
    rc, env, _ = _run("deepseek-v4-pro", str(tmp_path), fetch)
    assert rc == 1
    assert env["ANTHROPIC_AUTH_TOKEN"] == ""


# ── GUARD: every model-slot var claude-code reads must be ROUTED ──────────────
# The env vars claude-code consults to pick which model to call for each slot.
# If a future CC version adds a NEW slot var, add it here AND provider_routing.sh
# must export it — otherwise the new slot silently falls back to an UNROUTED
# Claude model id, called against the provider base with no Anthropic creds ->
# the "Not logged in" wedge (bus #65856). Keep this in sync with claude-code's
# documented model-slot env vars; this test is the fall-through tripwire.
_MODEL_SLOT_VARS = (
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",   # newer CC: small/background slot
    "ANTHROPIC_SMALL_FAST_MODEL",      # legacy name for the same small/background slot
    "CLAUDE_CODE_SUBAGENT_MODEL",
)


def test_every_model_slot_var_is_routed_on_a_native_arm(tmp_path, glm_fetch):
    """No model slot may fall through to an unrouted Claude id on a routed lane."""
    rc, env, _ = _run("glm-5.3", str(tmp_path), glm_fetch)
    assert rc == 0
    for v in _MODEL_SLOT_VARS:
        assert env.get(v), (
            f"model-slot var {v} is UNSET on a native (glm) arm -> claude-code would "
            f"fall back to an unrouted Claude model id and wedge 'Not logged in'"
        )
        assert env[v].startswith("glm-"), f"{v}={env[v]!r} is not a routed glm model id"


# ── GUARD: the routed vars must SURVIVE the env -i least-privilege allow-list ──
# The op#29348 part-b launcher (launch_dangerous_cc.sh) exec's claude via
# `env -i` + scripts/lib/lane_env_allowlist.sh, so provider_routing's exports
# reach claude ONLY if the allow-list passes them. Before this fix the allow-list
# listed NONE of them -> a glm lane received `--model glm-5.3` with no base_url /
# no auth token / no routed slots -> "Not logged in" on the first turn
# (substrate-1, bus #65856/#65890). This test ties the two libs together exactly
# as the launcher does; the subprocess strips every VALUE in-pipeline (sed), so
# only NAMES ever reach captured output — no secret can leak.
_ALLOWLIST = os.path.join(_ROOT, "scripts", "lib", "lane_env_allowlist.sh")
_ROUTED_MUST_SURVIVE = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)


def test_routed_vars_survive_the_env_i_allowlist_scrub(tmp_path, glm_fetch):
    env = dict(os.environ)
    for k in _TRACKED_VARS + _OVERRIDE_ENV_VARS:
        env.pop(k, None)
    script = (
        f'export ORCH_DIR="{_ROOT}"\n'
        f'source "{_LIB}"\n'
        f'source "{_ALLOWLIST}"\n'
        f'apply_provider_routing "glm-5.3" "{_ROOT}" "{sys.executable}" "test-agent" "{glm_fetch}"\n'
        # Emit only the NAMES the launcher would pass through env -i. The sed
        # strips every value INSIDE the subprocess, so no secret reaches stdout.
        f'lane_env_scrubbed_pairs0 "test-agent" | tr "\\0" "\\n" | sed "s/=.*//"\n'
    )
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd=_ROOT, env=env)
    survivors = {ln for ln in out.stdout.splitlines() if ln}
    missing = [v for v in _ROUTED_MUST_SURVIVE if v not in survivors]
    routed_survivors = sorted(s for s in survivors if "ANTHROPIC" in s or "MODEL" in s)
    assert not missing, (
        f"these provider-routing vars are DROPPED by the env -i allow-list and never reach "
        f"claude: {missing} -> add them to scripts/lib/lane_env_allowlist.sh. "
        f"(routed NAMES that did survive: {routed_survivors}; stderr={out.stderr!r})"
    )


# ── CAI-1225 / TENANT-RESIDENCY-001: a provider-routed boot drops client-silo write-DSN
#    creds, but KEEPS DATABASE_URL (the shared substrate/bus DSN) ─────────────────────
_SILO_VARS = ("GOUMLYNE_DATABASE_URL", "IHSANOS_PROD_DATABASE_URL", "WRITE_DSN_ALLOWED")
_SEED = {"GOUMLYNE_DATABASE_URL": "x", "IHSANOS_PROD_DATABASE_URL": "y",
         "WRITE_DSN_ALLOWED": "1", "DATABASE_URL": "z"}


def _run_silo(resolved_model, orch_dir, fetch_script, pre_env):
    """Like _run but seeds + reads back the client-silo vars + DATABASE_URL via ${VAR+SET}
    (SET even when empty, "" when truly unset) so we can assert unset-vs-kept precisely."""
    keys = _SILO_VARS + ("DATABASE_URL",)
    env = dict(os.environ)
    for k in _TRACKED_VARS + _OVERRIDE_ENV_VARS + keys:
        env.pop(k, None)
    for k, v in pre_env.items():
        env[k] = v
    dump = " ".join(f'"${{{v}+SET}}"' for v in keys)
    script = (
        f'source "{_LIB}"\n'
        f'apply_provider_routing "{resolved_model}" "{orch_dir}" "{sys.executable}" "test-agent" "{fetch_script}"\n'
        'echo "RC=$?"\n'
        f'printf "%s\\x1f" {dump}\n'
    )
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd=_ROOT, env=env)
    rc_line, _, rest = out.stdout.partition("\n")
    rc = int(rc_line.split("=", 1)[1])
    vals = rest.rstrip("\n").split("\x1f")[:-1]
    assert len(vals) == len(keys), (vals, out.stdout, out.stderr)
    return rc, {k: (v == "SET") for k, v in zip(keys, vals)}, out.stderr


def test_glm_route_strips_client_silo_creds_keeps_database_url(tmp_path, glm_fetch):
    rc, is_set, stderr = _run_silo("glm-5.3", str(tmp_path), glm_fetch, _SEED)
    assert rc == 0, stderr
    assert is_set["GOUMLYNE_DATABASE_URL"] is False, "goumlyne silo DSN must be stripped on a provider route"
    assert is_set["IHSANOS_PROD_DATABASE_URL"] is False, "ihsanos silo DSN must be stripped on a provider route"
    assert is_set["WRITE_DSN_ALLOWED"] is False, "write-DSN allow-flag must be stripped on a provider route"
    assert is_set["DATABASE_URL"] is True, "DATABASE_URL (bus/substrate) must be KEPT — not a client silo"


def test_claude_noop_keeps_client_silo_creds(tmp_path, failing_fetch):
    rc, is_set, stderr = _run_silo("claude-opus-4-8", str(tmp_path), failing_fetch, _SEED)
    assert rc == 0, stderr
    assert all(is_set[k] for k in _SILO_VARS), "no-op (claude-*) path must leave silo creds untouched"
    assert is_set["DATABASE_URL"] is True
