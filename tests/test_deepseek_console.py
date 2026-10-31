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
