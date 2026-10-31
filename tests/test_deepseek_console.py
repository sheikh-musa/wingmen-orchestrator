"""deepseek_console.py (op#35851 Phase 1, op#36768 Phase 2).

Phase 1 shipped PLACEHOLDER browser selectors because nothing had ever driven
the live console. Phase 2 replaced them with selectors verified against the
real authenticated session (2026-10-31) and rewired usage-export around the
console's own Export download. Browser-driving itself is still not unit-
testable here -- there is no live session in CI -- so these tests cover
everything around it: CLI wiring, the slug/collision guardrails ahead of any
browser or vault call, the map file read/write round-trip, the export zip
parser and its JSONL merge, and the no-capture-API constraint (grepped
directly from the shipped source, not re-implemented here, so a later edit
that adds a screenshot/trace call fails this test immediately instead of only
being caught by someone reading the diff).
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
    for sub in ("add-client", "usage-export", "split-key"):
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

def test_help_lists_all_five_subcommands():
    rc, out, err = _run(["--help"])
    assert rc == 0
    for sub in ("login", "add-client", "split-key", "list-clients", "usage-export"):
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


# --- usage export: zip parsing + JSONL merge (op#36768 Phase 2) ------------

def _load_module(script_path=_SCRIPT, name="deepseek_console_export"):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_AMOUNT_HEADER = (
    "user_id,start_time_iso,end_time_iso,model,name,api_key_name,api_key,type,price,amount"
)
_COST_HEADER = "user_id,start_time_iso,end_time_iso,model,name,wallet_type,cost,currency"
_USER = "Google-aaaa-bbbb"


def _build_export_zip(amount_lines, cost_lines):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "amount-2026-10-01_2026-11-01.csv",
            "\n".join([_AMOUNT_HEADER, *amount_lines]) + "\n",
        )
        zf.writestr(
            "cost-2026-10-01_2026-11-01.csv",
            "\n".join([_COST_HEADER, *cost_lines]) + "\n",
        )
    return buf.getvalue()


def test_parse_export_zip_groups_per_key_per_day_and_sums_cost():
    mod = _load_module()
    zip_bytes = _build_export_zip(
        [
            # 2 days x 2 keys x 3 usage types, one row each
            f"{_USER},2026-10-30T00:00:00+08:00,2026-10-31T00:00:00+08:00,deepseek-flash,"
            f"deepseek-flash,bariq,sk-MASKED*****aa,output_tokens,0.0000012,1000",
            f"{_USER},2026-10-30T00:00:00+08:00,2026-10-31T00:00:00+08:00,deepseek-flash,"
            f"deepseek-flash,bariq,sk-MASKED*****aa,input_cache_hit_tokens,0.0000001,50",
            f"{_USER},2026-10-30T00:00:00+08:00,2026-10-31T00:00:00+08:00,deepseek-flash,"
            f"deepseek-flash,bariq,sk-MASKED*****aa,input_cache_miss_tokens,0.0000005,200",
            f"{_USER},2026-10-30T00:00:00+08:00,2026-10-31T00:00:00+08:00,deepseek-flash,"
            f"deepseek-flash,cosem,sk-MASKED*****bb,output_tokens,0.0000012,500",
            f"{_USER},2026-10-31T00:00:00+08:00,2026-11-01T00:00:00+08:00,deepseek-flash,"
            f"deepseek-flash,bariq,sk-MASKED*****aa,output_tokens,0.0000012,10",
        ],
        [
            f"{_USER},2026-10-30T00:00:00+08:00,2026-10-31T00:00:00+08:00,deepseek-flash,"
            f"deepseek-flash,Paid,0.001905,USD",
            f"{_USER},2026-10-31T00:00:00+08:00,2026-11-01T00:00:00+08:00,deepseek-flash,"
            f"deepseek-flash,Paid,0.000012,USD",
        ],
    )

    records, meta = mod.parse_export_zip(zip_bytes)
    idx = {(r["date"], r["api_key_name"]): r for r in records}

    bariq_30 = idx[("2026-10-30", "bariq")]
    assert bariq_30["usage"] == {
        "output_tokens": 1000,
        "input_cache_hit_tokens": 50,
        "input_cache_miss_tokens": 200,
    }
    assert bariq_30["cost_usd"] == round(1000 * 0.0000012 + 50 * 0.0000001 + 200 * 0.0000005, 10)
    assert bariq_30["currency"] == "USD"
    assert bariq_30["source"] == "console_export"

    # one row per (day, key), not one per usage type
    assert ("2026-10-30", "cosem") in idx
    assert idx[("2026-10-30", "cosem")]["cost_usd"] == round(500 * 0.0000012, 10)
    assert len(records) == 3

    # the console's own per-day cost is reproduced by amount x price
    assert meta["max_abs_diff_usd"] < 1e-9
    assert meta["keys"] == ["bariq", "cosem"]


def test_parse_export_zip_never_emits_key_material_or_user_id():
    mod = _load_module()
    zip_bytes = _build_export_zip(
        [
            f"{_USER},2026-10-30T00:00:00+08:00,2026-10-31T00:00:00+08:00,deepseek-flash,"
            f"deepseek-flash,bariq,sk-9b3bf***********************ff74,output_tokens,"
            f"0.0000012,1000",
        ],
        [],
    )
    records, _meta = mod.parse_export_zip(zip_bytes)
    blob = json.dumps(records)
    assert "sk-" not in blob
    assert _USER not in blob
    assert "ff74" not in blob
    assert records[0]["api_key_name"] == "bariq"


def test_parse_export_zip_since_filters_days():
    mod = _load_module()
    zip_bytes = _build_export_zip(
        [
            f"{_USER},2026-10-30T00:00:00+08:00,2026-10-31T00:00:00+08:00,deepseek-flash,"
            f"deepseek-flash,bariq,sk-MASKED,output_tokens,0.0000012,10",
            f"{_USER},2026-10-31T00:00:00+08:00,2026-11-01T00:00:00+08:00,deepseek-flash,"
            f"deepseek-flash,bariq,sk-MASKED,output_tokens,0.0000012,20",
        ],
        [],
    )
    records, _meta = mod.parse_export_zip(zip_bytes, since="2026-10-31")
    assert [r["date"] for r in records] == ["2026-10-31"]


def test_parse_export_zip_rejects_a_bad_since_date():
    mod = _load_module()
    zip_bytes = _build_export_zip([], [])
    try:
        mod.parse_export_zip(zip_bytes, since="31-10-2026")
    except mod.LayoutMismatch as exc:
        assert "YYYY-MM-DD" in str(exc)
    else:
        raise AssertionError("expected LayoutMismatch for a malformed --since")


def test_parse_export_zip_fails_closed_when_the_export_shape_changes():
    import io
    import zipfile

    mod = _load_module()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("something-else.csv", "a,b\n1,2\n")
    try:
        mod.parse_export_zip(buf.getvalue())
    except mod.LayoutMismatch as exc:
        assert "amount-*.csv" in str(exc)
    else:
        raise AssertionError("expected LayoutMismatch when amount-*.csv is absent")


def test_merge_jsonl_is_idempotent_and_keeps_first_seen_at(tmp_path):
    mod = _load_module()
    out = str(tmp_path / "state" / "deepseek_usage_by_key.jsonl")

    first, _ = mod.parse_export_zip(
        _build_export_zip(
            [
                f"{_USER},2026-10-30T00:00:00+08:00,2026-10-31T00:00:00+08:00,deepseek-flash,"
                f"deepseek-flash,bariq,sk-MASKED,output_tokens,0.0000012,1000",
            ],
            [],
        )
    )
    s1 = mod.merge_jsonl(out, first, currency="USD")
    assert s1["records_added"] == 1 and s1["records_total"] == 1
    original_first_seen = json.loads(open(out).read().strip())["first_seen_at"]

    s2 = mod.merge_jsonl(out, first, currency="USD")
    assert s2["records_added"] == 0 and s2["records_updated"] == 1
    assert s2["records_total"] == 1
    row = json.loads(open(out).read().strip())
    assert row["first_seen_at"] == original_first_seen
    assert row["usage"]["output_tokens"] == 1000


def test_merge_jsonl_refreshes_a_changed_day_without_duplicating(tmp_path):
    mod = _load_module()
    out = str(tmp_path / "deepseek_usage_by_key.jsonl")

    def run(amount):
        recs, _ = mod.parse_export_zip(
            _build_export_zip(
                [
                    f"{_USER},2026-10-30T00:00:00+08:00,2026-10-31T00:00:00+08:00,deepseek-flash,"
                    f"deepseek-flash,bariq,sk-MASKED,output_tokens,0.0000012,{amount}",
                ],
                [],
            )
        )
        return mod.merge_jsonl(out, recs, currency="USD")

    run(100)
    run(250)
    lines = [json.loads(l) for l in open(out) if l.strip()]
    assert len(lines) == 1
    assert lines[0]["usage"]["output_tokens"] == 250


# --- split-key: migrate one family off the shared key (bus #87091 GO) ------
#
# The property that matters most is ORDER: create -> vault -> flip map ->
# check. provider_routing.sh fails closed when the resolved vault key is
# absent, so a map flip that lands before the secret exists stops that
# family's lanes from booting. These tests assert the order directly by
# reading the map file from inside the vault.put() call, not by trusting the
# source to stay in the right order.

import argparse  # noqa: E402


def _rewrite_map(script_path, updates):
    map_file = script_path.parent / "lib" / "deepseek_key_map.json"
    mapping = json.loads(map_file.read_text())
    mapping.update(updates)
    map_file.write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n")
    return map_file


def _install_fake_vault(monkeypatch, on_put=None):
    import types

    calls = []

    class _FakeVault:
        def put(self, name, value, reason):
            if on_put is not None:
                on_put()
            calls.append((name, value, reason))

    parent = types.ModuleType("nervous_system")
    child = types.ModuleType("nervous_system.vault")
    child.vault = _FakeVault()
    monkeypatch.setitem(sys.modules, "nervous_system", parent)
    monkeypatch.setitem(sys.modules, "nervous_system.vault", child)
    return calls


def _split_args(slug, **kw):
    base = dict(headed=False, flip_only=False, dry_run=False, skip_boot_check=True)
    base.update(kw)
    return argparse.Namespace(slug=slug, **base)


# guardrails -- all fire before any playwright/vault import or DB access

def test_split_key_rejects_invalid_slug():
    rc, out, err = _run(["split-key", "Upper Case"])
    assert rc == 1
    assert "invalid slug" in err


def test_split_key_refuses_a_slug_that_is_not_mapped(tmp_path):
    script = _isolated_scripts_copy(tmp_path)
    before = (script.parent / "lib" / "deepseek_key_map.json").read_text()

    rc, out, err = _run_isolated(script, ["split-key", "never-mapped-before"])
    assert rc == 1
    assert "not in the map" in err
    assert (script.parent / "lib" / "deepseek_key_map.json").read_text() == before


def test_split_key_refuses_an_already_split_slug(tmp_path):
    script = _isolated_scripts_copy(tmp_path)
    _rewrite_map(script, {"bariq": "DEEPSEEK_KEY_BARIQ"})

    rc, out, err = _run_isolated(script, ["split-key", "bariq"])
    assert rc == 1
    assert "already split" in err


def test_split_key_refuses_to_repoint_a_row_this_script_did_not_set(tmp_path):
    """A row pointing anywhere other than the shared key was set by hand --
    repointing it could strand a family that was deliberately routed."""
    script = _isolated_scripts_copy(tmp_path)
    before = _rewrite_map(script, {"bariq": "DEEPSEEK_KEY_SOMETHING_ELSE"}).read_text()

    rc, out, err = _run_isolated(script, ["split-key", "bariq"])
    assert rc == 1
    assert "not the shared" in err
    assert (script.parent / "lib" / "deepseek_key_map.json").read_text() == before


# state-machine refusals (need the vault presence bit, so patch it)

def test_split_key_refuses_when_a_previous_run_already_vaulted(tmp_path, monkeypatch):
    script = _isolated_scripts_copy(tmp_path)
    mod = _load_module(script, "deepseek_split_partial")
    monkeypatch.setattr(mod, "_vault_has_key", lambda name: True)

    rc = mod.cmd_split_key(_split_args("bariq"))
    assert rc == 1


def test_split_key_flip_only_refuses_when_the_vault_has_no_secret(tmp_path, monkeypatch):
    """Flipping without the secret is exactly the state that makes
    provider_routing.sh refuse to boot the family."""
    script = _isolated_scripts_copy(tmp_path)
    mod = _load_module(script, "deepseek_split_fliponly")
    map_file = script.parent / "lib" / "deepseek_key_map.json"
    before = map_file.read_text()

    monkeypatch.setattr(mod, "_vault_has_key", lambda name: False)
    rc = mod.cmd_split_key(_split_args("bariq", flip_only=True))

    assert rc == 1
    assert map_file.read_text() == before


def test_split_key_dry_run_changes_nothing(tmp_path, monkeypatch, capsys):
    script = _isolated_scripts_copy(tmp_path)
    mod = _load_module(script, "deepseek_split_dry")
    map_file = script.parent / "lib" / "deepseek_key_map.json"
    before = map_file.read_text()

    monkeypatch.setattr(mod, "_vault_has_key", lambda name: False)
    calls = _install_fake_vault(monkeypatch)

    def _explode(*_a, **_k):
        raise AssertionError("dry run must not create a console key")

    monkeypatch.setattr(mod, "_create_console_key", _explode)

    rc = mod.cmd_split_key(_split_args("bariq", dry_run=True))
    assert rc == 0
    assert calls == []
    assert map_file.read_text() == before
    assert "DEEPSEEK_KEY_BARIQ" in capsys.readouterr().out


# the real command

def test_split_key_vaults_before_flipping_the_map_row(tmp_path, monkeypatch, capsys):
    script = _isolated_scripts_copy(tmp_path)
    mod = _load_module(script, "deepseek_split_order")
    map_file = script.parent / "lib" / "deepseek_key_map.json"

    monkeypatch.setattr(mod, "_vault_has_key", lambda name: False)
    monkeypatch.setattr(
        mod, "_create_console_key", lambda slug, headed: "sk-ORDERING-SENTINEL-VALUE"
    )
    monkeypatch.setattr(mod, "_run_key_map_check", lambda: (0, "ok"))

    seen = {}

    def on_put():
        seen["map_during_put"] = json.loads(map_file.read_text())["bariq"]

    calls = _install_fake_vault(monkeypatch, on_put=on_put)

    rc = mod.cmd_split_key(_split_args("bariq"))

    assert rc == 0
    assert seen["map_during_put"] == "DEEPSEEK_CODING_KEY", (
        "map row was flipped BEFORE the vault held the secret -- that family "
        "would fail closed at launch"
    )
    assert json.loads(map_file.read_text())["bariq"] == "DEEPSEEK_KEY_BARIQ"
    assert len(calls) == 1
    assert calls[0][0] == "DEEPSEEK_KEY_BARIQ"
    # the whole point of the no-print rule
    assert "sk-ORDERING-SENTINEL-VALUE" not in capsys.readouterr().out


def test_split_key_leaves_the_map_alone_when_key_creation_fails(tmp_path, monkeypatch):
    script = _isolated_scripts_copy(tmp_path)
    mod = _load_module(script, "deepseek_split_createfail")
    map_file = script.parent / "lib" / "deepseek_key_map.json"
    before = map_file.read_text()

    monkeypatch.setattr(mod, "_vault_has_key", lambda name: False)
    calls = _install_fake_vault(monkeypatch)

    def _boom(slug, headed):
        raise mod.LayoutMismatch("simulated dialog change")

    monkeypatch.setattr(mod, "_create_console_key", _boom)

    try:
        mod.cmd_split_key(_split_args("bariq"))
    except mod.LayoutMismatch:
        pass
    else:
        raise AssertionError("expected LayoutMismatch to propagate to main()")

    assert calls == []
    assert map_file.read_text() == before


def test_the_console_allowlist_still_has_no_delete_or_revoke_action():
    """orch-console #87091: revoking the shared key 'needs my explicit go
    after every family is verified'. The allowlist is what makes that
    structurally true -- there is no page or action here that could revoke
    anything, so no later edit can route around the gate by accident."""
    mod = _load_module(name="deepseek_allowlist")
    assert set(mod._SEL) == {"api_keys_page", "usage_page", "login_page"}
    blob = json.dumps(mod._SEL).lower()
    for forbidden in ("delete", "revoke", "remove"):
        assert forbidden not in blob
