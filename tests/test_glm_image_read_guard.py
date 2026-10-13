"""Tests scripts/hooks/glm_image_read_guard.py — the data-residency image wall.

WHY (residency): some fleet lanes are routed to a THIRD-PARTY, non-Anthropic
provider (z.ai GLM, Singapore) via scripts/lib/provider_routing.sh, which
exports ANTHROPIC_BASE_URL (e.g. https://api.z.ai/api/anthropic) +
ANTHROPIC_AUTH_TOKEN for the routed lane. A normal Claude lane has
ANTHROPIC_BASE_URL UNSET. Policy allows cosem lanes on GLM "text_only=true",
but client IMAGE/visual data (screenshots, photos, scanned IDs) must NEVER
reach a non-Anthropic provider. cosem-platform lanes Read image proofs, so a
PROVIDER-ROUTED lane must be BLOCKED from Read-ing (or cat/base64-dumping) an
image file.

RIGHT-REASON asserts (the TDD guard against a false pass): every DENY asserts
exit 2 AND the guard's OWN refusal marker ("glm_image_read_guard") in stderr —
never just a non-zero exit (which a missing module / crash would also give).
Every ALLOW asserts exit 0 so the allow path is proven to run.

No real client file is ever touched: image DENY tests use a NON-EXISTENT path.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).parent.parent / "scripts" / "hooks" / "glm_image_read_guard.py"

# A GLM-routed lane's env, as provider_routing.sh leaves it.
_GLM_ENV = {"ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic"}
# A non-existent image path — so the block test can never read a real client file.
_IMG = "/tmp/nonexistent-glm-residency-probe/screenshot_proof.png"


def run_hook(tool_name, tool_input, env=None):
    payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
    full_env = dict(os.environ)
    # Start from a clean slate for the routing signal every test controls.
    full_env.pop("ANTHROPIC_BASE_URL", None)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload, text=True, capture_output=True, env=full_env,
    )


def assert_denied(tool_name, tool_input, env=_GLM_ENV):
    r = run_hook(tool_name, tool_input, env=env)
    assert r.returncode == 2, f"expected DENY for {tool_input!r}, got exit {r.returncode}, stderr={r.stderr!r}"
    assert "glm_image_read_guard" in r.stderr, r.stderr


def assert_allowed(tool_name, tool_input, env=_GLM_ENV):
    r = run_hook(tool_name, tool_input, env=env)
    assert r.returncode == 0, f"expected ALLOW for {tool_input!r}, got exit {r.returncode}, stderr={r.stderr!r}"


# ── The three REQUIRED directions (task spec) ────────────────────────────────

def test_a_glm_lane_read_png_blocked():
    # (a) GLM-routed env Reading a .png is BLOCKED.
    assert_denied("Read", {"file_path": _IMG})


def test_b_claude_lane_read_png_allowed():
    # (b) A normal Claude lane (ANTHROPIC_BASE_URL unset) Reading the SAME .png
    #     is a pure no-op — allowed.
    assert_allowed("Read", {"file_path": _IMG}, env={})


def test_c_glm_lane_read_text_allowed():
    # (c) A GLM lane Reading a .py / .md text file is allowed (no over-block).
    assert_allowed("Read", {"file_path": "/tmp/foo.py"})
    assert_allowed("Read", {"file_path": "/tmp/notes.md"})


# ── DENY: routed lane, Read of each image extension ──────────────────────────

def test_glm_lane_read_every_image_ext_blocked():
    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic",
                ".heif", ".bmp", ".tiff", ".tif", ".pdf"):
        assert_denied("Read", {"file_path": f"/tmp/nonexistent-probe/proof{ext}"})


def test_glm_lane_read_image_uppercase_ext_blocked():
    # Case-insensitive extension match (a .PNG is still an image).
    assert_denied("Read", {"file_path": "/tmp/nonexistent-probe/PROOF.PNG"})


# ── DENY: routed lane, Bash dump of image bytes into the transcript ──────────

def test_glm_lane_bash_cat_image_blocked():
    assert_denied("Bash", {"command": f"cat {_IMG}"})


def test_glm_lane_bash_base64_image_blocked():
    assert_denied("Bash", {"command": f"base64 {_IMG}"})


def test_glm_lane_bash_xxd_image_blocked():
    assert_denied("Bash", {"command": f"xxd {_IMG} | head"})


# ── Routing-signal precision (fail-closed on any non-anthropic host) ─────────

def test_openrouter_routed_lane_read_image_blocked():
    assert_denied("Read", {"file_path": _IMG},
                  env={"ANTHROPIC_BASE_URL": "https://openrouter.ai/api"})


def test_unparseable_base_url_treated_as_routed_blocked():
    # Fail-closed: a set-but-unparseable base URL is treated as routed.
    assert_denied("Read", {"file_path": _IMG},
                  env={"ANTHROPIC_BASE_URL": "not a url"})


def test_anthropic_host_is_not_routed_allowed():
    # An explicit api.anthropic.com base URL is NOT a third-party route.
    assert_allowed("Read", {"file_path": _IMG},
                   env={"ANTHROPIC_BASE_URL": "https://api.anthropic.com"})


def test_anthropic_subdomain_is_not_routed_allowed():
    assert_allowed("Read", {"file_path": _IMG},
                   env={"ANTHROPIC_BASE_URL": "https://foo.anthropic.com/v1"})


def test_empty_base_url_is_not_routed_allowed():
    # An EMPTY ANTHROPIC_BASE_URL is effectively unset — a normal Claude lane.
    assert_allowed("Read", {"file_path": _IMG}, env={"ANTHROPIC_BASE_URL": ""})


# ── ALLOW: routed lane, no over-block of non-image work ──────────────────────

def test_glm_lane_read_text_extensions_allowed():
    for p in ("/tmp/x.py", "/tmp/x.md", "/tmp/x.json", "/tmp/x.ts",
              "/tmp/x.txt", "/tmp/x.sh", "/tmp/x.sql", "/tmp/x.yaml"):
        assert_allowed("Read", {"file_path": p})


def test_glm_lane_bash_non_reading_image_command_allowed():
    # Moving / listing / git-adding an image does NOT ingest its bytes into the
    # transcript, so it must not be blocked (no over-block).
    assert_allowed("Bash", {"command": "ls /tmp/*.png"})
    assert_allowed("Bash", {"command": "mv /tmp/a.png /tmp/b.png"})
    assert_allowed("Bash", {"command": "git add /tmp/proof.png"})


def test_glm_lane_bash_cat_text_file_allowed():
    assert_allowed("Bash", {"command": "cat /tmp/foo.py"})


def test_glm_lane_other_tool_out_of_scope_allowed():
    assert_allowed("Grep", {"pattern": "x", "path": _IMG})
    assert_allowed("Glob", {"pattern": "*.png"})


# ── Claude lane (unset): pure no-op even on a Bash image dump ─────────────────

def test_claude_lane_bash_cat_image_allowed():
    assert_allowed("Bash", {"command": f"cat {_IMG}"}, env={})


# ── DENY: fail-closed on unreadable input (only for a routed lane) ───────────

def test_glm_lane_unparseable_stdin_fail_closed():
    r = subprocess.run(
        [sys.executable, str(HOOK)],
        input="not json", text=True, capture_output=True,
        env={**{k: v for k, v in os.environ.items()}, **_GLM_ENV},
    )
    assert r.returncode == 2, r.stderr
    assert "glm_image_read_guard" in r.stderr


def test_claude_lane_unparseable_stdin_is_noop():
    # A normal Claude lane is a pure no-op even on garbage stdin.
    env = {k: v for k, v in os.environ.items()}
    env.pop("ANTHROPIC_BASE_URL", None)
    r = subprocess.run(
        [sys.executable, str(HOOK)],
        input="not json", text=True, capture_output=True, env=env,
    )
    assert r.returncode == 0, r.stderr


# ── Crash-hardening: _run() outer wrapper fails CLOSED on a routed lane and stays
#    a pure no-op on a Claude lane (a PreToolUse exit != 0/2 is NON-blocking in CC,
#    so an unhandled crash on a routed lane would otherwise let the image through).
def _load_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("glm_guard_mod", str(HOOK))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_crash_on_routed_lane_fails_closed(monkeypatch):
    mod = _load_module()

    def boom():
        raise RuntimeError("unexpected guard failure")

    monkeypatch.setattr(mod, "main", boom)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
    assert mod._run() == 2  # routed + crash -> BLOCK (fail-closed)


def test_crash_on_claude_lane_is_noop(monkeypatch):
    mod = _load_module()

    def boom():
        raise RuntimeError("unexpected guard failure")

    monkeypatch.setattr(mod, "main", boom)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    assert mod._run() == 0  # claude + crash -> NO-OP (never block a normal lane)
