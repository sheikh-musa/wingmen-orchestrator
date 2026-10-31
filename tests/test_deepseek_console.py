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
from datetime import timedelta, timezone

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
    for sub in ("add-client", "usage-export", "split-key", "rename-key"):
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

def test_help_lists_every_subcommand():
    rc, out, err = _run(["--help"])
    assert rc == 0
    for sub in ("login", "add-client", "split-key", "list-clients", "usage-export",
                "rename-key"):
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
    untouched = {k: v for k, v in mapping.items() if k != "zzz-test-only"}
    mapping["zzz-test-only"] = "DEEPSEEK_KEY_ZZZ_TEST_ONLY"
    mod._write_map(mapping)

    reread = mod._load_map()
    assert reread["zzz-test-only"] == "DEEPSEEK_KEY_ZZZ_TEST_ONLY"
    # Every pre-existing entry survives byte-identically. Written against the
    # map loaded at the top of the test rather than against a literal, because
    # the map legitimately changes as families are split one PR at a time --
    # pinning "bariq is shared" would fail on the PR that splits bariq and
    # tell us nothing about round-tripping.
    assert {k: v for k, v in reread.items() if k != "zzz-test-only"} == untouched


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


def _budget_jsonl(tmp_path, rows):
    path = str(tmp_path / "usage.jsonl")
    with open(path, "w") as f:
        for day, key, usd in rows:
            f.write(
                json.dumps(
                    {"date": day, "api_key_name": key, "model": "deepseek-flash", "cost_usd": usd}
                )
                + "\n"
            )
    return path


def test_budget_status_is_ok_warn_alert_on_the_calendar_day(tmp_path):
    """bus #87142 / Musa op#36779: WARN 5 USD, ALERT 7 USD, per calendar day."""
    mod = _load_module()
    rows = [
        ("2026-11-01", "cosem", 2.0),
        ("2026-11-01", "bariq", 2.99),  # 4.99 -> still ok
        ("2026-11-01", "fleet", 0.01),  # 5.00 -> warn exactly on the line
        ("2026-11-02", "cosem", 3.0),   # 7.00 -> alert exactly on the line
        ("2026-11-02", "bariq", 4.0),
        ("2026-10-31", "cosem", 99.0),  # a different day must not leak in
    ]
    path = _budget_jsonl(tmp_path, rows)
    assert mod.budget_status(path, day="2026-11-01")["level"] == "warn"
    assert mod.budget_status(path, day="2026-11-02")["level"] == "alert"
    assert mod.budget_status(path, day="2026-10-30")["level"] == "ok"


def test_budget_status_reports_the_top_three_keys_by_spend(tmp_path):
    mod = _load_module()
    path = _budget_jsonl(
        tmp_path,
        [
            ("2026-11-01", "a", 1.0),
            ("2026-11-01", "b", 4.0),
            ("2026-11-01", "c", 2.0),
            ("2026-11-01", "d", 0.5),
        ],
    )
    st = mod.budget_status(path, day="2026-11-01")
    assert [t["key"] for t in st["top_keys"]] == ["b", "c", "a"]
    assert st["total_usd"] == 7.5 and st["level"] == "alert"


def test_budget_status_on_a_missing_file_is_ok_not_a_crash(tmp_path):
    mod = _load_module()
    st = mod.budget_status(str(tmp_path / "nope.jsonl"), day="2026-11-01")
    assert st["level"] == "ok" and st["total_usd"] == 0.0


def test_budget_defaults_to_the_console_calendar_not_the_host_calendar(tmp_path, monkeypatch):
    """The console says GMT+8 on /usage; the day label must follow that,
    otherwise spend lands on the wrong side of midnight every evening."""
    mod = _load_module()
    seen = {}

    class _FixedDatetime(mod.datetime):
        @classmethod
        def now(cls, tz=None):
            seen["tz"] = tz
            # 16:30Z is still 2026-10-31 on a UTC host, but the console's
            # GMT+8 calendar has already rolled over to 2026-11-01.
            return mod.datetime(2026, 10, 31, 16, 30, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(mod, "datetime", _FixedDatetime)
    st = mod.budget_status(str(tmp_path / "nope.jsonl"))
    assert seen["tz"] is not None and seen["tz"] == timezone(timedelta(hours=8))
    assert st["date"] == "2026-11-01"


def test_budget_posts_only_when_notify_is_on_and_the_level_is_not_ok(monkeypatch):
    """A manual run must never page the fleet; the launchd runner opts in."""
    mod = _load_module()
    calls = []
    monkeypatch.setattr(mod, "_post_budget_row", lambda st: calls.append(st) or 0)
    alert = {"level": "alert", "bus_rc": None}
    warn = {"level": "warn", "bus_rc": None}
    ok = {"level": "ok", "bus_rc": None}

    class A:
        pass

    off = A()
    off.budget_notify = False
    mod._maybe_post_budget(off, dict(alert))
    assert calls == []  # notify off, even at alert

    on = A()
    on.budget_notify = True
    mod._maybe_post_budget(on, dict(ok))
    assert calls == []  # notify on, but nothing to say
    mod._maybe_post_budget(on, dict(warn))
    assert len(calls) == 1

    no_attr = A()
    mod._maybe_post_budget(no_attr, dict(alert))
    assert len(calls) == 1  # args without the flag default to silent


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


def _force_shared(script_path, slug="bariq"):
    """Put one row back on the shared key before a split test runs.

    The split tests must not depend on how far the real migration has got.
    The map legitimately flips families one PR at a time, so a test that
    assumed 'bariq is still shared' would fail on the very PR that splits
    bariq -- and, worse, quietly start testing the 'already split' refusal
    instead of the behaviour it names. Forcing the precondition states the
    test's real requirement instead of inheriting it from production state.
    """
    return _rewrite_map(script_path, {slug: "DEEPSEEK_CODING_KEY"})


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
    base = dict(
        headed=False, flip_only=False, dry_run=False, skip_boot_check=True,
        rollback_on_failure=True,
    )
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
    _force_shared(script)
    monkeypatch.setattr(mod, "_vault_has_key", lambda name: True)

    rc = mod.cmd_split_key(_split_args("bariq"))
    assert rc == 1


def test_split_key_flip_only_refuses_when_the_vault_has_no_secret(tmp_path, monkeypatch):
    """Flipping without the secret is exactly the state that makes
    provider_routing.sh refuse to boot the family."""
    script = _isolated_scripts_copy(tmp_path)
    mod = _load_module(script, "deepseek_split_fliponly")
    _force_shared(script)
    map_file = script.parent / "lib" / "deepseek_key_map.json"
    before = map_file.read_text()

    monkeypatch.setattr(mod, "_vault_has_key", lambda name: False)
    rc = mod.cmd_split_key(_split_args("bariq", flip_only=True))

    assert rc == 1
    assert map_file.read_text() == before


def test_split_key_dry_run_changes_nothing(tmp_path, monkeypatch, capsys):
    script = _isolated_scripts_copy(tmp_path)
    mod = _load_module(script, "deepseek_split_dry")
    _force_shared(script)
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
    _force_shared(script)
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
    _force_shared(script)
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


# --- rename-key: ONE key, its NAME, nothing else (bus #87201 / #87881) ------
# The shared console key is itself named `substrate`, and usage-export
# attributes spend by that name, so it must be renamed before that family is
# split. These tests cover everything that is checkable without a live
# session: input validation, the second confirmation gate, and the pure
# before/after table comparison that makes "renamed exactly one row"
# verifiable rather than intended.

def _rename_mod():
    return _load_module(name="deepseek_rename")


def test_rename_key_dry_run_prints_the_plan_without_touching_anything():
    rc, out, err = _run(
        ["rename-key", "substrate", "shared-legacy", "--confirm", "substrate", "--dry-run"]
    )
    assert rc == 0, err
    assert "would rename" in out
    assert "substrate" in out and "shared-legacy" in out


def test_rename_key_refuses_when_confirm_does_not_repeat_the_old_name():
    """The gate that stops a mistyped target. Fires before any browser work."""
    rc, out, err = _run(["rename-key", "substrate", "shared-legacy", "--confirm", "substrat"])
    assert rc == 1
    assert out == ""
    assert "repeat the current name exactly" in err


def test_rename_key_confirm_is_required():
    rc, out, err = _run(["rename-key", "substrate", "shared-legacy"])
    assert rc != 0
    assert out == ""


def test_rename_key_refuses_to_rename_a_key_onto_its_own_name():
    rc, out, err = _run(
        ["rename-key", "substrate", "substrate", "--confirm", "substrate", "--dry-run"]
    )
    assert rc == 1
    assert "identical" in err


def test_rename_key_rejects_anything_that_is_not_a_plain_key_name():
    # "-leading" is covered by the pure test below instead: argparse would
    # read it as an option and fail before our validation ever runs, which
    # would make this assertion pass for the wrong reason.
    for bad in ("", "bad name", "with/slash", "a" * 65, "tab\tname"):
        rc, out, err = _run(["rename-key", bad, "ok-name", "--confirm", bad])
        assert rc == 1, bad
        assert out == "", bad
        assert "rename-key:" in err, bad


def test_validate_rename_rejects_a_leading_punctuation_name():
    mod = _rename_mod()
    for bad in ("-leading", ".dot", "_under"):
        try:
            mod._validate_rename(bad, "ok-name")
        except ValueError as exc:
            assert "not a plain key name" in str(exc)
        else:
            raise AssertionError(f"{bad!r} must be rejected")
    # and the shape we actually intend to use is accepted
    mod._validate_rename("substrate", "substrate-shared-legacy")


def _verify_raises(names_before, names_after, old, new, needle):
    mod = _rename_mod()
    try:
        mod._verify_rename(names_before, names_after, old, new)
    except mod.LayoutMismatch as exc:
        assert needle in str(exc), str(exc)
    else:
        raise AssertionError(f"expected LayoutMismatch containing {needle!r}")


def test_verify_rename_accepts_exactly_one_name_change():
    _rename_mod()._verify_rename(
        ["substrate", "bariq", "oeh"], ["shared-legacy", "bariq", "oeh"],
        "substrate", "shared-legacy",
    )


def test_verify_rename_refuses_a_target_that_is_not_there():
    _verify_raises(["bariq"], ["bariq"], "substrate", "shared-legacy", "no key named")


def test_verify_rename_refuses_a_name_collision():
    """Renaming onto an existing name is the exact failure this action exists
    to prevent -- two different spend buckets collapsing onto one label."""
    _verify_raises(["substrate", "shared-legacy"], ["substrate", "shared-legacy"],
                   "substrate", "shared-legacy", "ALREADY EXISTS")


def test_verify_rename_refuses_a_duplicated_target():
    _verify_raises(["substrate", "substrate"], ["shared-legacy", "substrate"],
                   "substrate", "shared-legacy", "refusing to guess")


def test_verify_rename_refuses_when_the_old_name_survives():
    """A save that ADDED the new name without dropping the old one would look
    like a success from the dialog and leave two rows claiming one key."""
    _verify_raises(["substrate"], ["substrate", "shared-legacy"],
                   "substrate", "shared-legacy", "STILL named")


def test_verify_rename_refuses_when_the_new_name_never_appeared():
    _verify_raises(["substrate"], ["something-else"], "substrate", "shared-legacy",
                   "did not take")


def test_verify_rename_refuses_when_another_row_changed_too():
    """The 'touch no other field' guarantee, expressed as something that can
    actually be checked: the after-list must be the before-list with exactly
    one substitution."""
    _verify_raises(["substrate", "bariq"], ["shared-legacy", "oeh"],
                   "substrate", "shared-legacy", "more than the one key")


class _FakeNode:
    """Enough of a Playwright Locator for _read_key_table: children-as-text."""

    def __init__(self, text="", children=None):
        self.text = text
        self.children = children or []

    def all_inner_texts(self):
        return [c.text for c in self.children]

    def count(self):
        return len(self.children)

    def nth(self, i):
        return self.children[i]

    def inner_text(self):
        return self.text

    @property
    def first(self):
        return self.children[0] if self.children else self

    def locator(self, _sel):
        return _FakeNode(children=self.children)


class _FakePage:
    """Serves successive table states, because the real page renders <table>
    before it fills the rows -- the bug that made a first probe read a
    19-row table as 4 empty rows."""

    def __init__(self, header, frames):
        self.header = header
        self.frames = list(frames)
        self.waits = 0

    def locator(self, sel):
        if sel == "table th":
            return _FakeNode(children=[_FakeNode(text=h) for h in self.header])
        if sel == "table tr":
            frame = self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]
            return _FakeNode(children=frame)
        raise AssertionError(sel)

    def wait_for_timeout(self, _ms):
        self.waits += 1


_HEADER = ["Name", "Tracking ID", "Key", "Created", "Last used"]


def _row(name):
    return _FakeNode(children=[] if name is None else [_FakeNode(text=name)])


def test_read_key_table_waits_for_an_empty_table_to_populate():
    mod = _rename_mod()
    empty = [_row(None)]
    filled = [_row(None), _row("substrate"), _row("bariq")]
    page = _FakePage(_HEADER, [empty, filled, filled])
    assert mod._read_key_table(page) == ["substrate", "bariq"]
    assert page.waits >= 1, "must have polled instead of returning an empty shell"


def test_read_key_table_does_not_trust_a_partially_filled_table():
    """The rehearsal failure, pinned: the first live run read 3 keys off an
    18-key account because a non-empty table is not a finished one."""
    mod = _rename_mod()
    partial = [_row(None), _row("a"), _row("b")]
    full = [_row(None), _row("a"), _row("b"), _row("substrate")]
    page = _FakePage(_HEADER, [partial, full, full])
    assert mod._read_key_table(page) == ["a", "b", "substrate"]


def test_read_key_table_fails_closed_on_a_reordered_or_renamed_column():
    mod = _rename_mod()
    page = _FakePage(["Name", "Tracking ID", "Key value", "Created", "Last used"],
                     [[_row(None), _row("substrate")]])
    try:
        mod._read_key_table(page)
    except mod.LayoutMismatch as exc:
        assert "table never populated" in str(exc) or "header" in str(exc)
    else:
        raise AssertionError("a changed header must not be read as a key list")


def test_the_only_row_action_the_script_knows_how_to_find_is_edit():
    """rename-key added the first mutating row action. Its label is pinned and
    nothing in the allowlist names the destructive one, so a later edit cannot
    quietly point this at Revoke."""
    page = _load_module(name="deepseek_allowlist_rename")._SEL["api_keys_page"]
    assert page["edit_action_label"] == "edit api key"
    assert not any("revoke" in str(v).lower() for v in page.values())


class _FakeDialogPage:
    """Just enough page for _assert_edit_dialog: a count and a title text.

    `titles` is the inner_text of every node the selector matches, so 0, 1 or
    2 ents all behave like the real locator would.
    """

    def __init__(self, titles, title_sel="div.ds-modal-content__title"):
        self.titles = list(titles)
        self.title_sel = title_sel

    def wait_for_selector(self, sel, timeout=None, state=None):
        if sel == self.title_sel and not self.titles:
            raise TimeoutError(sel)

    def locator(self, sel):
        assert sel == self.title_sel, sel
        return _FakeNode(children=[_FakeNode(text=t) for t in self.titles])


def test_assert_edit_dialog_accepts_the_live_dialog_shape():
    """The live DOM on 2026-11-01: one div.ds-modal-content__title reading
    'Edit API key'."""
    mod = _rename_mod()
    sel = mod._SEL["api_keys_page"]
    mod._assert_edit_dialog(_FakeDialogPage(["Edit API key"]), sel)


def test_assert_edit_dialog_fails_closed_when_the_title_text_is_not_edit():
    """The rehearsal bug in reverse: a dialog opened, but not the one meant."""
    mod = _rename_mod()
    sel = mod._SEL["api_keys_page"]
    try:
        mod._assert_edit_dialog(_FakeDialogPage(["Revoke API key"]), sel)
    except mod.LayoutMismatch as exc:
        assert "wrong dialog" in str(exc)
    else:
        raise AssertionError("a differently-titled dialog must abort before typing")


def test_assert_edit_dialog_reports_no_dialog_when_no_title_matches():
    mod = _rename_mod()
    sel = mod._SEL["api_keys_page"]
    try:
        mod._assert_edit_dialog(_FakeDialogPage([]), sel)
    except mod.LayoutMismatch as exc:
        assert "did not open a dialog" in str(exc)
    else:
        raise AssertionError("a missing dialog must abort")


def test_assert_edit_dialog_refuses_when_two_dialogs_are_open():
    mod = _rename_mod()
    sel = mod._SEL["api_keys_page"]
    try:
        mod._assert_edit_dialog(_FakeDialogPage(["Edit API key", "Edit API key"]), sel)
    except mod.LayoutMismatch as exc:
        assert "expected exactly 1" in str(exc)
    else:
        raise AssertionError("ambiguity must abort rather than pick the first")


def test_the_title_selector_is_never_confused_for_the_title_text():
    """Pins the exact bug that failed the first live rehearsal: the expected
    title string had been stored where the selector belonged.
    """
    sel = _load_module(name="deepseek_title_split")._SEL["api_keys_page"]
    assert sel["edit_dialog_title_text"] == "Edit API key"
    assert "edit_dialog_title" not in sel, "the conflated key must stay deleted"
    assert sel["dialog_title"].endswith("ds-modal-content__title"), sel["dialog_title"]
    assert "Edit API key" not in sel["dialog_title"]


# --- F1 (cc-quality, PR#744): the cost cross-check must ENFORCE, not print ---

def test_cost_cross_check_mismatch_refuses_to_write():
    """The delta against the console's own cost export is the only detector of
    a changed export shape or a double-counting per-key split. Printing it in a
    summary helps nobody downstream: the JSONL is read as authoritative."""
    mod = _load_module(name="deepseek_crosscheck")
    zip_bytes = _build_export_zip(
        [
            f"{_USER},2026-10-30T00:00:00+08:00,2026-10-31T00:00:00+08:00,deepseek-flash,"
            f"deepseek-flash,bariq,sk-MASKED,output_tokens,0.0000012,1000",
        ],
        # console says a different total for the same day/model
        [
            f"{_USER},2026-10-30T00:00:00+08:00,2026-10-31T00:00:00+08:00,deepseek-flash,"
            f"deepseek-flash,Paid,0.0050,USD",
        ],
    )
    try:
        mod.parse_export_zip(zip_bytes)
    except mod.LayoutMismatch as exc:
        assert "cross-check FAILED" in str(exc)
        assert "refusing to write" in str(exc).lower()
    else:
        raise AssertionError("expected LayoutMismatch when the cost cross-check diverges")


def test_cost_cross_check_within_tolerance_still_parses():
    mod = _load_module(name="deepseek_crosscheck_ok")
    zip_bytes = _build_export_zip(
        [
            f"{_USER},2026-10-30T00:00:00+08:00,2026-10-31T00:00:00+08:00,deepseek-flash,"
            f"deepseek-flash,bariq,sk-MASKED,output_tokens,0.0000012,1000",
        ],
        [
            f"{_USER},2026-10-30T00:00:00+08:00,2026-10-31T00:00:00+08:00,deepseek-flash,"
            f"deepseek-flash,Paid,0.0012,USD",
        ],
    )
    records, meta = mod.parse_export_zip(zip_bytes)
    assert meta["max_abs_diff_usd"] < mod._COST_CROSS_CHECK_TOLERANCE_USD
    assert len(records) == 1


def test_unattributable_amount_row_aborts_instead_of_being_skipped():
    """A skipped row does not vanish -- it moves spend into someone else's
    bucket or drops it from the total, while the file still looks complete."""
    mod = _load_module(name="deepseek_skiprow")
    zip_bytes = _build_export_zip(
        [
            f"{_USER},2026-10-30T00:00:00+08:00,2026-10-31T00:00:00+08:00,deepseek-flash,"
            f"deepseek-flash,bariq,sk-MASKED,output_tokens,0.0000012,1000",
            # no api_key_name -> cannot be attributed to any key
            f"{_USER},2026-10-30T00:00:00+08:00,2026-10-31T00:00:00+08:00,deepseek-flash,"
            f"deepseek-flash,,sk-MASKED,output_tokens,0.0000012,500",
        ],
        [],
    )
    try:
        mod.parse_export_zip(zip_bytes)
    except mod.LayoutMismatch as exc:
        assert "cannot attribute" in str(exc)
    else:
        raise AssertionError("expected LayoutMismatch for an unattributable row")


def test_non_numeric_amount_aborts():
    mod = _load_module(name="deepseek_nonnum")
    zip_bytes = _build_export_zip(
        [
            f"{_USER},2026-10-30T00:00:00+08:00,2026-10-31T00:00:00+08:00,deepseek-flash,"
            f"deepseek-flash,bariq,sk-MASKED,output_tokens,0.0000012,not-a-number",
        ],
        [],
    )
    try:
        mod.parse_export_zip(zip_bytes)
    except mod.LayoutMismatch as exc:
        assert "non-numeric" in str(exc)
    else:
        raise AssertionError("expected LayoutMismatch for a non-numeric amount")


def test_usage_export_validates_since_before_driving_a_browser():
    """A typo in --since should cost a second, not a full export download.
    Runs in-process: the LayoutMismatch must fire before playwright is even
    imported, so this passes with no browser and no display."""
    mod = _load_module(name="deepseek_since_early")
    args = argparse.Namespace(since="31-10-2026", out=None, headed=False)
    try:
        mod.cmd_usage_export(args)
    except mod.LayoutMismatch as exc:
        assert "YYYY-MM-DD" in str(exc)
    else:
        raise AssertionError("expected LayoutMismatch for a malformed --since")


# --- F1 (cc-quality, PR#745): a post-flip failure must not strand a family ---

def _split_with_failures(tmp_path, monkeypatch, *, boot_ok, check_rc, rollback=True):
    script = _isolated_scripts_copy(tmp_path)
    mod = _load_module(script, "deepseek_split_rollback_%s_%s" % (boot_ok, check_rc))
    _force_shared(script)
    map_file = script.parent / "lib" / "deepseek_key_map.json"

    monkeypatch.setattr(mod, "_vault_has_key", lambda name: False)
    monkeypatch.setattr(mod, "_create_console_key", lambda slug, headed: "sk-X")
    monkeypatch.setattr(mod, "_run_key_map_check", lambda: (check_rc, "check output"))
    monkeypatch.setattr(mod, "_boot_check", lambda slug, key: (boot_ok, "boot detail"))
    _install_fake_vault(monkeypatch)

    rc = mod.cmd_split_key(
        _split_args("bariq", skip_boot_check=boot_ok is None,
                    rollback_on_failure=rollback)
    )
    return rc, json.loads(map_file.read_text())["bariq"]


def test_boot_check_failure_rolls_the_map_row_back(tmp_path, monkeypatch):
    """The row is on a key the boot check just proved will not load; leaving
    it there is a family that cannot start until a human reads stderr."""
    rc, row = _split_with_failures(tmp_path, monkeypatch, boot_ok=False, check_rc=0)
    assert rc == 2
    assert row == "DEEPSEEK_CODING_KEY"


def test_map_check_failure_rolls_the_map_row_back(tmp_path, monkeypatch):
    rc, row = _split_with_failures(tmp_path, monkeypatch, boot_ok=True, check_rc=1)
    assert rc == 2
    assert row == "DEEPSEEK_CODING_KEY"


def test_no_rollback_flag_leaves_the_row_flipped_for_inspection(tmp_path, monkeypatch):
    rc, row = _split_with_failures(
        tmp_path, monkeypatch, boot_ok=False, check_rc=0, rollback=False
    )
    assert rc == 2
    assert row == "DEEPSEEK_KEY_BARIQ"


def test_successful_split_stays_flipped(tmp_path, monkeypatch):
    rc, row = _split_with_failures(tmp_path, monkeypatch, boot_ok=True, check_rc=0)
    assert rc == 0
    assert row == "DEEPSEEK_KEY_BARIQ"


def test_redact_strips_key_shaped_text_from_diagnostics():
    """_boot_check prints captured stderr, so it is treated as untrusted even
    though provider_vault_fetch.py only writes the value to stdout."""
    mod = _load_module(name="deepseek_redact")
    out = mod._redact("boom sk-abcdef0123456789abcdef0123456789 tail")
    assert "sk-" not in out
    assert "abcdef0123456789" not in out
    assert "[REDACTED-KEY]" in out
    assert mod._redact("plain SecretNotFoundError") == "plain SecretNotFoundError"
    assert mod._redact(None) == ""
