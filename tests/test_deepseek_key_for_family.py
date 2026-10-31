"""deepseek_key_for_family.py — resolves a lane's per-client DeepSeek vault
key NAME from its agent_id (Musa op#35814, bus #82358). Exercises the real
CLI script directly (subprocess), same "shipped path is the tested path"
principle as test_provider_routing.py.
"""
import json
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "lib", "deepseek_key_for_family.py")
_MAP_FILE = os.path.join(_ROOT, "scripts", "lib", "deepseek_key_map.json")


def _run(agent_id):
    out = subprocess.run([sys.executable, _SCRIPT, agent_id], capture_output=True, text=True, cwd=_ROOT)
    return out.returncode, out.stdout, out.stderr


def test_the_real_map_file_has_every_client_console_named():
    """Guard against a silent typo/drift in the shipped map vs. console's
    expected family set (bus #82358, interim bus #84011).

    Values are no longer all the shared key: the per-family split is in
    progress one row at a time (orch-console bus #87110/#87191), so the
    invariant is now per-row rather than global. A row may hold the shared
    DEEPSEEK_CODING_KEY, or its OWN vault key named for its own slug -- never
    some other family's key, which is exactly the kind of typo that would
    silently bill one client for another's usage. An explicit mapping is what
    keeps the fail-closed rule for a genuinely UNMAPPED family intact."""
    with open(_MAP_FILE) as f:
        mapping = json.load(f)
    assert set(mapping) == {
        "cosem", "cosem-tdu", "bariq", "oeh", "ffsfulfilment", "hc95", "fleet", "loop", "angullia",
        "coffeemedia", "finance", "second", "shipforge", "substrate", "wingmen", "paperclip",
        "scholar",
    }
    shared = "DEEPSEEK_CODING_KEY"
    for slug, vault_key in mapping.items():
        own = "DEEPSEEK_KEY_" + slug.upper().replace("-", "_")
        assert vault_key in (shared, own), (slug, vault_key)
    # The first family off the shared key, in the order orch-console set.
    assert mapping["second"] == "DEEPSEEK_KEY_SECOND"


def test_resolves_known_families():
    for agent_id, expected in [
        ("cc-bariq-1", "DEEPSEEK_CODING_KEY"),
        ("cc-oeh-1", "DEEPSEEK_CODING_KEY"),
        ("cc-ffsfulfilment-1", "DEEPSEEK_CODING_KEY"),
        ("cc-cosem-exams-1", "DEEPSEEK_CODING_KEY"),
        ("cc-cosem-platform-author", "DEEPSEEK_CODING_KEY"),
        ("cc-hc95-broker-1", "DEEPSEEK_CODING_KEY"),
        ("cc-fleet-health", "DEEPSEEK_CODING_KEY"),
        ("cc-loop-1", "DEEPSEEK_CODING_KEY"),
        ("cc-angullia", "DEEPSEEK_CODING_KEY"),
        ("cc-coffeemedia", "DEEPSEEK_CODING_KEY"),
        ("cc-finance", "DEEPSEEK_CODING_KEY"),
        ("cc-second-brain", "DEEPSEEK_KEY_SECOND"),
        ("cc-shipforge", "DEEPSEEK_CODING_KEY"),
        ("cc-substrate", "DEEPSEEK_CODING_KEY"),
        ("cc-wingmen-sites", "DEEPSEEK_CODING_KEY"),
        ("cc-paperclip-spike", "DEEPSEEK_CODING_KEY"),
        ("cc-scholar", "DEEPSEEK_CODING_KEY"),
        ("cc-scholar-1", "DEEPSEEK_CODING_KEY"),
    ]:
        rc, stdout, stderr = _run(agent_id)
        assert rc == 0, (agent_id, stderr)
        assert stdout == expected, (agent_id, stdout)


def test_compound_family_cosem_tdu_shares_the_cosem_key():
    """cosem-tdu-coord's family is the COMPOUND 'cosem-tdu' (lane_token_resolver's
    own _COMPOUND_FAMILIES), a DIFFERENT string from 'cosem' — but Musa's plan
    is one shared key across the whole cosem family, so both map rows must
    point at the SAME vault key name."""
    rc1, out1, _ = _run("cc-cosem-tdu-coord")
    rc2, out2, _ = _run("cc-cosem-exams-1")
    assert rc1 == 0 and rc2 == 0
    assert out1 == out2 == "DEEPSEEK_CODING_KEY"


def test_unmapped_family_fails_closed_nothing_on_stdout():
    rc, stdout, stderr = _run("cc-some-brand-new-client-9")
    assert rc == 1
    assert stdout == ""
    assert "no DeepSeek vault-key mapped" in stderr
    assert "DEEPSEEK_CODING_KEY" in stderr, "the error must name what it's refusing to fall back to"


def test_empty_agent_id_fails_closed():
    rc, stdout, stderr = _run("")
    assert rc == 1
    assert stdout == ""


def test_wrong_arg_count_fails_closed():
    out = subprocess.run([sys.executable, _SCRIPT], capture_output=True, text=True, cwd=_ROOT)
    assert out.returncode == 1
    assert out.stdout == ""


def test_unreadable_map_file_fails_closed(tmp_path):
    """Mirror the real scripts/lib layout (so the family_of import still
    resolves) but with the map file itself deleted — fail closed, not an
    uncaught import error and not a silent empty resolution."""
    import shutil

    shutil.copytree(os.path.join(_ROOT, "scripts"), tmp_path / "scripts")
    os.remove(tmp_path / "scripts" / "lib" / "deepseek_key_map.json")
    out = subprocess.run(
        [sys.executable, str(tmp_path / "scripts" / "lib" / "deepseek_key_for_family.py"), "cc-bariq-1"],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert out.returncode == 1
    assert out.stdout == ""
    assert "unreadable" in out.stderr.lower() or "invalid" in out.stderr.lower() or "no such file" in out.stderr.lower()
