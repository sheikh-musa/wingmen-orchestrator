"""deepseek_console.py Phase 1 scaffold (op#35851, bus #82526/#82540/#82537).

Phase 1 ships no verified browser selectors -- there is nothing live to test
against yet. These tests exercise everything that IS real at this stage:
CLI wiring, the slug/collision guardrails ahead of any browser or vault
call, the map file read/write round-trip, and the no-capture-API
constraint (grepped directly from the shipped source, not re-implemented
here, so a later edit that adds a screenshot/trace call fails this test
immediately instead of only being caught by someone reading the diff).
"""
import json
import os
import shutil
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "deepseek_console.py")


def _run(args, cwd=None):
    out = subprocess.run(
        [sys.executable, _SCRIPT, *args],
        capture_output=True, text=True, cwd=cwd or _ROOT,
    )
    return out.returncode, out.stdout, out.stderr


def _isolated_scripts_copy(tmp_path):
    """Mirror scripts/ under tmp_path so deepseek_console.py's own __file__
    -relative _MAP_FILE/_ORCH_ROOT resolution still works, but the real
    scripts/lib/deepseek_key_map.json is never touched by a test."""
    shutil.copytree(os.path.join(_ROOT, "scripts"), tmp_path / "scripts")
    return tmp_path / "scripts" / "deepseek_console.py"


def _run_isolated(script_path, args):
    out = subprocess.run(
        [sys.executable, str(script_path), *args],
        capture_output=True, text=True, cwd=str(script_path.parent.parent),
    )
    return out.returncode, out.stdout, out.stderr


# --- no-capture constraint, checked directly on the shipped source ---------

def test_source_never_calls_the_tracing_or_screenshot_api():
    """Checks actual call/kwarg sites, not the docstring/comments that
    legitimately name these APIs in prose while describing the ban."""
    with open(_SCRIPT) as f:
        src = f.read()
    assert ".tracing.start(" not in src
    assert ".tracing.stop(" not in src
    assert ".screenshot(" not in src
    assert "record_video_dir=" not in src


def test_no_screenshot_or_trace_cli_flags_exist():
    """Per-subcommand help (not the top-level --help, which also prints the
    module docstring that names --screenshot/--trace in prose while
    describing the ban) -- this checks the actual argparse option strings."""
    for sub in ("add-client", "usage-export"):
        rc, out, err = _run([sub, "--help"])
        assert rc == 0
        assert "--screenshot" not in out
        assert "--trace" not in out


# --- real Chrome, not bundled Chromium (orch-console bus #84081: DeepSeek's
#     human check flagged the bundled Chromium's automation fingerprint) ---

def test_context_launches_real_chrome_and_strips_automation_tells(tmp_path):
    """Direct call into _context() with a fake playwright.chromium, so this
    asserts the actual kwargs passed to launch_persistent_context -- not a
    source grep, which could pass on a comment alone. _PROFILE_DIR is
    redirected to tmp_path first: _context() does a real os.makedirs/chmod
    on it, and the real path holds Musa's actual persisted DeepSeek login."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("deepseek_console_ctxtest", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._PROFILE_DIR = str(tmp_path / "profile")

    captured = {}

    class _FakeChromium:
        def launch_persistent_context(self, user_data_dir, **kwargs):
            captured["user_data_dir"] = user_data_dir
            captured.update(kwargs)
            return "fake-context"

    class _FakePlaywright:
        chromium = _FakeChromium()

    result = mod._context(_FakePlaywright(), headed=True)

    assert result == "fake-context"
    assert captured["channel"] == "chrome"
    assert "--enable-automation" in captured["ignore_default_args"]
    assert "--disable-blink-features=AutomationControlled" in captured["args"]
    assert captured["headless"] is False


# --- CLI wiring --------------------------------------------------------

def test_help_lists_all_four_subcommands():
    rc, out, err = _run(["--help"])
    assert rc == 0
    for sub in ("login", "add-client", "list-clients", "usage-export"):
        assert sub in out


def test_no_subcommand_fails_closed():
    rc, out, err = _run([])
    assert rc != 0
    assert out == ""


# --- add-client guardrails (all fire BEFORE any playwright/vault import) ---

def test_uppercase_slug_is_lowered_not_rejected(tmp_path):
    """Case-insensitive convenience, not strict rejection -- 'Bariq' lowers
    to 'bariq', which then correctly fails on the DUPLICATE check (bariq is
    already mapped), proving the input reached normalization+lookup rather
    than being rejected by the regex."""
    rc, out, err = _run(["add-client", "Bariq"])
    assert rc == 1
    assert "already mapped" in err


def test_invalid_slug_rejected_space():
    rc, out, err = _run(["add-client", "upper case"])
    assert rc == 1
    assert "invalid slug" in err


def test_invalid_slug_rejected_leading_digit():
    rc, out, err = _run(["add-client", "9client"])
    assert rc == 1
    assert "invalid slug" in err


def test_invalid_slug_rejected_special_chars():
    rc, out, err = _run(["add-client", "client_name"])
    assert rc == 1
    assert "invalid slug" in err


def test_duplicate_slug_refused_map_untouched(tmp_path):
    script = _isolated_scripts_copy(tmp_path)
    map_file = script.parent / "lib" / "deepseek_key_map.json"
    before = map_file.read_text()

    rc, out, err = _run_isolated(script, ["add-client", "bariq"])
    assert rc == 1
    assert "already mapped" in err
    assert map_file.read_text() == before


def test_family_collision_refused_map_untouched(tmp_path):
    """bariq-2 reduces to the same family_of() as the already-mapped
    'bariq' (lane_token_resolver's plain split-on-first-hyphen rule) -- must
    refuse before ever opening a browser, same as an exact duplicate."""
    script = _isolated_scripts_copy(tmp_path)
    map_file = script.parent / "lib" / "deepseek_key_map.json"
    before = map_file.read_text()

    rc, out, err = _run_isolated(script, ["add-client", "bariq-2"])
    assert rc == 1
    assert "same lane-family" in err
    assert map_file.read_text() == before


def test_genuinely_new_slug_passes_the_guardrails_then_fails_on_no_display(tmp_path):
    """A slug with no family collision gets PAST the guardrails and only
    then fails -- on trying to actually drive a browser in this sandboxed
    test environment (no DISPLAY / no real DeepSeek session). This proves
    the guardrails above are the ones rejecting the earlier cases, not some
    earlier unconditional failure."""
    script = _isolated_scripts_copy(tmp_path)
    rc, out, err = _run_isolated(script, ["add-client", "brand-new-client"])
    assert rc != 0
    assert "invalid slug" not in err
    assert "already mapped" not in err
    assert "same lane-family" not in err


# --- map file round-trip (direct function test, no subprocess) -------------

def test_map_round_trip_preserves_existing_entries(tmp_path):
    import importlib.util

    script = _isolated_scripts_copy(tmp_path)
    spec = importlib.util.spec_from_file_location("deepseek_console", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    mapping = mod._load_map()
    mapping["zzz-test-only"] = "DEEPSEEK_KEY_ZZZ_TEST_ONLY"
    mod._write_map(mapping)

    reread = mod._load_map()
    assert reread["zzz-test-only"] == "DEEPSEEK_KEY_ZZZ_TEST_ONLY"
    assert reread["bariq"] == "DEEPSEEK_CODING_KEY"  # untouched original entry (interim state, bus #84011)


def test_slug_regex_boundary_lengths():
    import importlib.util

    spec = importlib.util.spec_from_file_location("deepseek_console", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod._SLUG_RE.match("ab")
    assert mod._SLUG_RE.match("a" * 41)
    assert not mod._SLUG_RE.match("a" * 42)
    assert not mod._SLUG_RE.match("")
