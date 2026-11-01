"""DeepSeek pool card (op#35897): per-client-key balance, delta since the last
poll, honest failure.

Locks:
  * one row per DISTINCT vault key name in deepseek_key_map.json, families that
    share a key grouped onto that one row (never duplicated);
  * a good read returns a 'balance' figure (never called 'spend') plus the
    delta against the last persisted good read, surviving a process restart
    via a small on-disk state file;
  * a failed read (missing vault secret, HTTP error, malformed body) ->
    available=False / "could not read", NEVER 0, NEVER the last-known number
    presented as current;
  * the raw key never appears in any returned payload or log line.
"""
import json
import logging
import threading
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from nervous_system.console import app as console_app
from nervous_system.console import db
from nervous_system.console import deepseek_usage as ds
from nervous_system.console import panes

ROOT = Path(__file__).resolve().parents[2]

FAKE_KEY = "fake-deepseek-key-0123456789abcdef.SENTINEL"

BALANCE_OK = {
    "is_available": True,
    "balance_infos": [
        {"currency": "CNY", "total_balance": "0.00", "granted_balance": "0.00", "topped_up_balance": "0.00"},
        {"currency": "USD", "total_balance": "46.87", "granted_balance": "0.00", "topped_up_balance": "46.87"},
    ],
}


@pytest.fixture
def fake_key(monkeypatch):
    reads = []

    def _read(name):
        reads.append(name)
        return FAKE_KEY
    monkeypatch.setattr(ds, "read_key", _read)
    return reads


def _fake_http(calls, body=BALANCE_OK):
    def _get(url, key):
        calls.append((url, key))
        return json.loads(json.dumps(body))
    return _get


# ---- key grouping -----------------------------------------------------------

def test_distinct_key_rows_groups_families_sharing_a_key():
    # One row per distinct vault key name in the shipped map. That map is
    # mid-migration from the shared DEEPSEEK_CODING_KEY to one key per family
    # (orch-console bus #87091/#87110), so the expected grouping is DERIVED
    # from the file (op#36531/36535, bus #86506) rather than written out: the
    # test is about grouping, not about how far the split has got this week.
    expected = {}
    for family, key_name in ds._key_map().items():
        expected.setdefault(key_name, []).append(family)
    rows = {r["key_name"]: sorted(r["families"]) for r in ds.distinct_key_rows()}
    assert rows == {k: sorted(v) for k, v in expected.items()}
    assert all(v for v in rows.values())  # sanity: no row with zero families
    assert len(rows) == len(set(rows))   # one row per distinct key name, no dupes


# ---- model label --------------------------------------------------------

def test_model_label_maps_known_model_to_display_name():
    assert ds.model_label("deepseek-flash") == "DeepSeek-V4.1-Flash-1017"


def test_model_label_falls_back_to_raw_string_for_unknown_model():
    assert ds.model_label("deepseek-v4-pro") == "deepseek-v4-pro"


# ---- parse_balance ------------------------------------------------------

def test_parse_balance_reads_usd_total():
    assert ds.parse_balance(BALANCE_OK) == 46.87


def test_parse_balance_rejects_malformed_body():
    with pytest.raises(ds.DeepseekUsageError):
        ds.parse_balance({"no": "balance_infos"})


# ---- per-key fetch: good / bad --------------------------------------------

def test_good_read_reports_balance_never_called_spend(fake_key, monkeypatch):
    calls = []
    monkeypatch.setattr(ds, "http_get_json", _fake_http(calls))
    out = ds._fetch_one("DEEPSEEK_KEY_BARIQ", now=1000.0)
    assert calls == [(ds.BALANCE_URL, FAKE_KEY)]
    assert out["available"] is True
    assert out["balance_usd"] == 46.87
    assert "spend" not in json.dumps(out)
    assert FAKE_KEY not in json.dumps(out)


def test_missing_vault_secret_says_key_not_created_yet_never_could_not_read(monkeypatch):
    """orch-console #83397: a key MAPPED in deepseek_key_map.json but never
    provisioned in the vault (SecretNotFoundError) is a DIFFERENT state from a
    key that exists but fails to read -- the row must say so distinctly."""
    def _missing(name):
        raise ds.DeepseekKeyNotProvisioned(f"vault: no secret named {name!r}.")
    monkeypatch.setattr(ds, "read_key", _missing)
    out = ds._fetch_one("DEEPSEEK_KEY_COSEM", now=1000.0)
    assert out["available"] is False
    assert out["balance_usd"] is None
    assert out["reason"] == "key not created yet"


def test_real_vault_read_translates_secret_not_found_to_not_provisioned(monkeypatch):
    """The real vault path (not the read_key test seam): a genuine
    SecretNotFoundError (vault_secrets has no row) must surface as
    DeepseekKeyNotProvisioned, not the generic DeepseekUsageError."""
    from nervous_system.vault import SecretNotFoundError

    class _V:
        def get(self, name, reason):
            raise SecretNotFoundError(f"vault: no secret named {name!r}.")
    monkeypatch.setitem(__import__("sys").modules, "nervous_system.vault",
                         types.SimpleNamespace(vault=_V(), SecretNotFoundError=SecretNotFoundError))
    import scripts.lib.substrate_dsn as sd
    monkeypatch.setattr(sd, "dsn_from_env_file", lambda: "postgresql://file-first.invalid/db")
    with pytest.raises(ds.DeepseekKeyNotProvisioned):
        ds._read_key_from_vault("DEEPSEEK_KEY_COSEM")


def test_http_failure_is_could_not_read_never_zero(fake_key, monkeypatch):
    def _boom(url, key):
        raise ds.DeepseekUsageError("deepseek balance HTTP 500")
    monkeypatch.setattr(ds, "http_get_json", _boom)
    out = ds._fetch_one("DEEPSEEK_KEY_BARIQ", now=1000.0)
    assert out == {"available": False, "balance_usd": None, "delta_usd": None,
                    "reason": "could not read", "fetched_at": out["fetched_at"]}


def test_key_never_appears_in_any_failure_payload_or_log(fake_key, monkeypatch, caplog):
    def _leaky(url, key):
        raise ValueError("bad thing near " + key)
    monkeypatch.setattr(ds, "http_get_json", _leaky)
    with caplog.at_level(logging.DEBUG):
        out = ds._fetch_one("DEEPSEEK_KEY_BARIQ", now=1.0)
    assert out["available"] is False
    assert FAKE_KEY not in json.dumps(out)
    assert all(FAKE_KEY not in r.getMessage() for r in caplog.records)


# ---- cache + delta-since-last-poll -----------------------------------------

def test_cache_reuses_good_read_for_the_ttl(fake_key, monkeypatch):
    calls = []
    monkeypatch.setattr(ds, "http_get_json", _fake_http(calls))
    ds.get_deepseek_usage(now=1000.0)
    ds.get_deepseek_usage(now=1000.0 + ds.CACHE_TTL_S - 1)
    assert len(calls) == len(ds.distinct_key_rows())   # one HTTP call per key, not per get_*() call


def test_delta_is_none_on_the_first_ever_read(fake_key, monkeypatch):
    monkeypatch.setattr(ds, "http_get_json", _fake_http([]))
    out = ds.get_deepseek_usage(now=1000.0)
    row = next(k for k in out["keys"] if k["key_name"] == "DEEPSEEK_CODING_KEY")
    assert row["delta_usd"] is None


def test_delta_reflects_change_since_the_last_good_read(fake_key, monkeypatch):
    monkeypatch.setattr(ds, "http_get_json", _fake_http([], body=BALANCE_OK))
    ds.get_deepseek_usage(now=1000.0)
    lower = json.loads(json.dumps(BALANCE_OK))
    lower["balance_infos"][1]["total_balance"] = "44.37"
    monkeypatch.setattr(ds, "http_get_json", _fake_http([], body=lower))
    out = ds.get_deepseek_usage(now=1000.0 + ds.CACHE_TTL_S + 1)
    row = next(k for k in out["keys"] if k["key_name"] == "DEEPSEEK_CODING_KEY")
    assert row["balance_usd"] == 44.37
    assert round(row["delta_usd"], 2) == -2.50


def test_delta_baseline_survives_a_process_restart_via_persisted_state(fake_key, monkeypatch):
    monkeypatch.setattr(ds, "http_get_json", _fake_http([]))
    ds.get_deepseek_usage(now=1000.0)
    ds._reset_for_tests(clear_state_file=False)   # simulate a fresh process (in-memory cache gone)
    lower = json.loads(json.dumps(BALANCE_OK))
    lower["balance_infos"][1]["total_balance"] = "45.00"
    monkeypatch.setattr(ds, "http_get_json", _fake_http([], body=lower))
    out = ds.get_deepseek_usage(now=2000.0)
    row = next(k for k in out["keys"] if k["key_name"] == "DEEPSEEK_CODING_KEY")
    assert round(row["delta_usd"], 2) == -1.87


def test_a_failed_refresh_never_serves_the_old_balance_as_current(fake_key, monkeypatch):
    monkeypatch.setattr(ds, "http_get_json", _fake_http([]))
    ds.get_deepseek_usage(now=1000.0)

    def _boom(url, key):
        raise ds.DeepseekUsageError("deepseek balance HTTP 500")
    monkeypatch.setattr(ds, "http_get_json", _boom)
    out = ds.get_deepseek_usage(now=1000.0 + ds.CACHE_TTL_S + 1)
    row = next(k for k in out["keys"] if k["key_name"] == "DEEPSEEK_CODING_KEY")
    assert row["available"] is False and row["balance_usd"] is None


# ---- shared key (op#35897/orch-console #83397, revised #83560 2026-10-30): --
# DEEPSEEK_CODING_KEY is vault leak_flagged (confirmed live 2026-10-29) --
# _read_key_from_vault's own leak-flag refusal (tested above) means the
# shared row can never read it directly without the console server either
# holding a flagged key or unilaterally bypassing the refusal. Instead the
# shared row reads scripts/deepseek_balance_poll.py's own hourly-polled
# history file (cc-fleet-health, already reads this key successfully every
# hour) -- never the vault, never an HTTP call, no secret in this process.

def _write_history(path, *rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_shared_key_usage_reads_the_pollers_history_file(monkeypatch):
    def _boom(name):
        raise AssertionError("shared row must never call read_key")
    monkeypatch.setattr(ds, "read_key", _boom)
    monkeypatch.setattr(ds, "http_get_json", lambda *a: (_ for _ in ()).throw(AssertionError("no HTTP")))
    _write_history(ds._SHARED_HISTORY_PATH,
                    {"ts": "2026-10-30T04:05:07+00:00", "balance_usd": 50.00},
                    {"ts": "2026-10-30T05:05:03+00:00", "balance_usd": 46.68})
    now = datetime.fromisoformat("2026-10-30T05:10:00+00:00").timestamp()
    out = ds.get_shared_key_usage(now=now)
    assert out["key_name"] == ds.SHARED_KEY_NAME
    assert out["available"] is True
    assert out["balance_usd"] == 46.68
    assert out["delta_usd"] == pytest.approx(46.68 - 50.00)
    assert out["fetched_at"] == "2026-10-30T05:05:03+00:00"
    assert out["age_s"] is not None


def test_shared_key_usage_first_ever_poll_omits_delta(monkeypatch):
    _write_history(ds._SHARED_HISTORY_PATH, {"ts": "2026-10-30T05:05:03+00:00", "balance_usd": 46.68})
    now = datetime.fromisoformat("2026-10-30T05:10:00+00:00").timestamp()
    out = ds.get_shared_key_usage(now=now)
    assert out["available"] is True and out["balance_usd"] == 46.68
    assert out["delta_usd"] is None


def test_shared_key_usage_missing_history_file_is_could_not_read(monkeypatch):
    # _SHARED_HISTORY_PATH points at a tmp path that was never written (conftest default).
    out = ds.get_shared_key_usage(now=1000.0)
    assert out["available"] is False
    assert out["reason"] == "could not read"
    assert out["balance_usd"] is None and out["delta_usd"] is None
    assert out["age_s"] is None


def test_shared_key_usage_stale_entry_is_could_not_read(monkeypatch):
    # poller runs hourly; a reading hours old (job stalled/dead) must never be
    # shown as current -- same "never serve stale as current" rule as a
    # failed vault/HTTP read.
    _write_history(ds._SHARED_HISTORY_PATH, {"ts": "2026-10-30T00:00:00+00:00", "balance_usd": 46.68})
    now = datetime.fromisoformat("2026-10-30T05:10:00+00:00").timestamp()
    out = ds.get_shared_key_usage(now=now)
    assert out["available"] is False and out["reason"] == "could not read"
    assert out["balance_usd"] is None


def test_shared_key_usage_malformed_last_line_is_could_not_read(monkeypatch):
    ds._SHARED_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    ds._SHARED_HISTORY_PATH.write_text("not valid json\n", encoding="utf-8")
    out = ds.get_shared_key_usage(now=1000.0)
    assert out["available"] is False and out["reason"] == "could not read"


# ---- /api/fleet end to end: numbers present, key absent from body + logs -----

_TEST_BREAKGLASS_TOKEN = "test" + "-console-" + "token"   # local test fixture, not a real credential


@pytest.fixture
def fleet_server(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSOLE_ALLOWED_IPS", "203.0.113.9")
    monkeypatch.setenv("CONSOLE_BREAKGLASS_TOKEN", _TEST_BREAKGLASS_TOKEN)
    monkeypatch.setenv("CONSOLE_ACCESS_LOG", str(tmp_path / "console_access.log"))
    for name in ("fetch_lanes", "fetch_deploys", "fetch_needs_you", "fetch_coordinators",
                 "fetch_backlog", "fetch_pane_context", "fetch_pool_usage", "fetch_queue",
                 "fetch_inbox_backlog", "fetch_asks", "fetch_fleet_asks"):
        monkeypatch.setattr(db, name, lambda: [])
    monkeypatch.setattr(console_app.panes, "live_sessions", lambda: [])
    monkeypatch.setattr(console_app, "_proc_models", lambda: {})
    monkeypatch.setattr(console_app, "_proc_accounts", lambda: {})
    monkeypatch.setattr(console_app, "_deepseek_lanes", lambda: [
        {"session": "bariq", "model": "deepseek-flash", "model_label": "DeepSeek-V4.1-Flash-1017"},
    ])
    monkeypatch.setattr(console_app, "_family_of", lambda sess: sess)
    # op#37213: get_spend() reads a REAL export file out of the SRE state dir,
    # so it is stubbed for every /api/fleet test -- these tests must not depend
    # on this host's spend data (and must not read it either).
    monkeypatch.setattr(ds, "get_spend", lambda now=None: {
        "available": True, "day": "2026-11-01", "day_label": "provider day (UTC+8)",
        "total_today_usd": 0.0, "total_7d_usd": 0.0, "budget_line_usd": 7.0,
        "budget_warn_usd": 5.0, "updated_at": None, "age_s": 1, "stale": False,
        "by_family": {}})
    srv = console_app.make_server(host="127.0.0.1", port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    for _ in range(50):
        try:
            httpx.get(base + "/healthz", timeout=1.0)
            break
        except Exception:
            time.sleep(0.05)
    yield base, tmp_path
    srv.shutdown()


AUTH = {"Authorization": "Bearer " + _TEST_BREAKGLASS_TOKEN}


def test_api_fleet_carries_deepseek_shared_row_per_client_rows_lanes_never_a_key(
        fleet_server, fake_key, monkeypatch, caplog):
    base, tmp = fleet_server
    monkeypatch.setattr(ds, "http_get_json", _fake_http([]))
    recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    _write_history(ds._SHARED_HISTORY_PATH, {"ts": recent, "balance_usd": 46.68})
    with caplog.at_level(logging.DEBUG):
        r = httpx.get(base + "/api/fleet", headers=AUTH, timeout=10)
    assert r.status_code == 200
    body = r.json()
    du = body["deepseek_usage"]
    assert du["shared"]["available"] is True and du["shared"]["balance_usd"] == 46.68
    # A lane appears on the shared row only if its family still maps to the
    # shared key (or its family is unknown); a lane with its own key must NOT
    # also be listed under shared (op#37222: the duplicate lane listing).
    shared_fams = {f for f, kn in ds._key_map().items() if kn == ds.SHARED_KEY_NAME}
    expected_shared_lanes = ["bariq"] if "bariq" in shared_fams else []
    assert du["shared"]["lanes"] == expected_shared_lanes
    # bariq's row is whatever key the shipped map names for its family -- the
    # split is one row per PR (orch-console bus #87091/#87110), so pinning
    # "bariq is on the shared key" would go red on bariq's own flip PR.
    bariq = next(k for k in du["keys"] if k["key_name"] == ds._key_map()["bariq"])
    assert bariq["available"] is True and bariq["balance_usd"] == 46.87
    assert bariq["lanes"] == ["bariq"]
    assert FAKE_KEY not in r.text
    assert all(FAKE_KEY not in rec.getMessage() for rec in caplog.records)
    access = tmp / "console_access.log"
    assert not access.exists() or FAKE_KEY not in access.read_text()


def test_api_fleet_deepseek_per_client_key_not_provisioned_is_distinct_from_shared_read(
        fleet_server, monkeypatch):
    """orch-console #83397/#83560: tonight's REAL state -- the shared row
    reads fine (from the poller's history file, never the vault -- its vault
    key is leak_flagged) while every per-client key says 'key not created
    yet', not 'could not read'."""
    base, _ = fleet_server

    def _per_client_missing(name):
        raise ds.DeepseekKeyNotProvisioned(f"vault has no secret named {name}")
    monkeypatch.setattr(ds, "read_key", _per_client_missing)
    monkeypatch.setattr(ds, "http_get_json", _fake_http([]))
    recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    _write_history(ds._SHARED_HISTORY_PATH, {"ts": recent, "balance_usd": 46.68})
    r = httpx.get(base + "/api/fleet", headers=AUTH, timeout=10)
    du = r.json()["deepseek_usage"]
    assert du["shared"]["available"] is True and du["shared"]["balance_usd"] == 46.68
    # The row's name/families come from the shipped map (op#36531/36535, bus
    # #86506) rather than a literal, so neither a newly added family nor one
    # family's move to its own key can silently break this.
    bariq_key = ds._key_map()["bariq"]
    expected_families = sorted(
        fam for fam, key_name in ds._key_map().items() if key_name == bariq_key
    )
    bariq = next(k for k in du["keys"] if k["key_name"] == bariq_key)
    assert bariq == {"available": False, "balance_usd": None, "delta_usd": None,
                      "reason": "key not created yet", "fetched_at": bariq["fetched_at"],
                      "key_name": bariq_key,
                      "families": expected_families,
                      "age_s": None, "lanes": ["bariq"],
                      "spend": None}


# ---- get_spend: the provider's per-key export (op#37213, console #90015) -----

def _use_export(monkeypatch, tmp_path, rows, raw=None):
    """Point the spend reader at a temp export file (never the live one)."""
    p = tmp_path / "usage.jsonl"
    p.write_text(raw if raw is not None else
                 "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(ds, "_USAGE_EXPORT_PATH", p)
    return p


def _export_row(name, day, cost, requests=10, updated_at="2026-11-01T14:20:00+00:00"):
    return {"api_key_name": name, "date": day, "cost_usd": cost, "currency": "USD",
            "updated_at": updated_at, "model": "deepseek-flash", "source": "export",
            "usage": {"request_count": requests, "input_cache_hit_tokens": 1,
                      "input_cache_miss_tokens": 2, "output_tokens": 3}}


def test_provider_day_flips_at_1600Z_not_midnight_utc():
    """The export's day is the provider's UTC+8 calendar day (deepseek_console.py
    _CONSOLE_TZ), and it is NOT the operator's local day -- Musa is UTC+4
    (console #90026). 15:59Z is still the 1st, 16:00Z is the 2nd."""
    def at(iso):
        return ds.provider_day(datetime.fromisoformat(iso).timestamp())
    assert at("2026-11-01T09:00:00+00:00") == "2026-11-01"
    assert at("2026-11-01T15:59:00+00:00") == "2026-11-01"
    assert at("2026-11-01T16:00:00+00:00") == "2026-11-02"


def test_get_spend_sums_today_and_the_7_day_window_per_family(monkeypatch, tmp_path):
    now = datetime.fromisoformat("2026-11-01T14:20:00+00:00").timestamp()
    day = ds.provider_day(now)
    _use_export(monkeypatch, tmp_path, [
        _export_row("substrate", day, 8.56, requests=12000),
        _export_row("substrate", day, 0.44, requests=500),        # same day, same key -> summed
        _export_row("substrate", "2026-10-30", 3.00, requests=700),
        _export_row("cosem", day, 2.00, requests=900),
    ])
    sp = ds.get_spend(now=now)
    assert sp["available"] is True
    assert sp["day"] == day and sp["day_label"] == "provider day (UTC+8)"
    assert sp["by_family"]["substrate"]["today_usd"] == pytest.approx(9.00)
    assert sp["by_family"]["substrate"]["last7_usd"] == pytest.approx(12.00)
    assert sp["by_family"]["substrate"]["today_requests"] == 12500
    assert sp["total_today_usd"] == pytest.approx(11.00)
    assert sp["total_7d_usd"] == pytest.approx(14.00)
    assert sp["budget_line_usd"] == 7.0 and sp["budget_warn_usd"] == 5.0
    assert sp["stale"] is False


def test_get_spend_ignores_rows_outside_the_7_day_window(monkeypatch, tmp_path):
    now = datetime.fromisoformat("2026-11-01T14:20:00+00:00").timestamp()
    day = ds.provider_day(now)
    _use_export(monkeypatch, tmp_path, [
        _export_row("substrate", day, 1.00),
        _export_row("substrate", "2026-10-01", 99.00),
    ])
    sp = ds.get_spend(now=now)
    assert sp["total_today_usd"] == pytest.approx(1.00)
    assert sp["total_7d_usd"] == pytest.approx(1.00)


def test_get_spend_skips_malformed_lines_never_zeroes_them(monkeypatch, tmp_path):
    now = datetime.fromisoformat("2026-11-01T14:20:00+00:00").timestamp()
    day = ds.provider_day(now)
    raw = ("\n".join([
        json.dumps(_export_row("substrate", day, 2.50)),
        '{"api_key_name": "second", "date": "' + day + '"',   # truncated
        '{"no_cost": true}',                                   # missing keys
        "",
    ]) + "\n")
    _use_export(monkeypatch, tmp_path, None, raw=raw)
    sp = ds.get_spend(now=now)
    assert sp["available"] is True
    assert sp["total_today_usd"] == pytest.approx(2.50)
    assert "second" not in sp["by_family"]


def test_get_spend_missing_export_is_unavailable_never_zero(monkeypatch, tmp_path):
    """An absent export must read as 'we cannot see', never as '$0 spent'."""
    monkeypatch.setattr(ds, "_USAGE_EXPORT_PATH", tmp_path / "nope.jsonl")
    sp = ds.get_spend(now=0.0)
    assert sp["available"] is False and sp["reason"]
    assert "total_today_usd" not in sp
    assert sp["by_family"] == {}


def test_get_spend_flags_a_stale_export_without_hiding_the_figure(monkeypatch, tmp_path):
    now = datetime.fromisoformat("2026-11-01T14:20:00+00:00").timestamp()
    day = ds.provider_day(now)
    _use_export(monkeypatch, tmp_path, [
        _export_row("substrate", day, 2.00, updated_at="2026-11-01T06:00:00+00:00"),
    ])
    sp = ds.get_spend(now=now)
    assert sp["available"] is True and sp["stale"] is True
    assert sp["total_today_usd"] == pytest.approx(2.00)   # real, just old -- labelled, not dropped
    assert sp["age_s"] > ds._USAGE_STALE_AFTER_S


def test_family_to_key_is_the_shipped_map_verbatim():
    assert ds.family_to_key() == ds._key_map()
    assert ds.family_to_key() is not ds._key_map()   # a copy, not a handle on module state


def test_api_fleet_joins_per_family_spend_onto_each_key_row(fleet_server, monkeypatch):
    """The export bills by api_key_name == family slug, so the card's per-KEY
    rows must SUM every family that shares a key (and surface an export name the
    map does not know rather than dropping it)."""
    base, _ = fleet_server
    day = "2026-11-01"
    fam_today = {"substrate": 5.00, "hifz": 3.00, "ghostkey": 3.99}
    monkeypatch.setattr(ds, "get_spend", lambda now=None: {
        "available": True, "day": day, "day_label": "provider day (UTC+8)",
        "total_today_usd": 11.99, "total_7d_usd": 41.30, "budget_line_usd": 7.0,
        "budget_warn_usd": 5.0, "updated_at": None, "age_s": 60, "stale": False,
        "by_family": {
            "substrate": {"today_usd": 5.00, "last7_usd": 20.0, "today_requests": 100},
            "hifz": {"today_usd": 3.00, "last7_usd": 10.0, "today_requests": 50},
            "ghostkey": {"today_usd": 3.99, "last7_usd": 11.3, "today_requests": 7},
        },
    })
    r = httpx.get(base + "/api/fleet", headers=AUTH, timeout=10)
    du = r.json()["deepseek_usage"]
    # every family the map sends to the shared key sums into the shared row
    shared_fams = sorted(f for f, k in ds._key_map().items() if k == ds.SHARED_KEY_NAME)
    expected = sum(v for f, v in fam_today.items() if f in shared_fams)
    assert du["shared"]["spend"]["today_usd"] == pytest.approx(expected)
    assert du["shared"]["families"] == shared_fams
    # an export name with no map row becomes its own flagged row, never dropped
    ghost = [k for k in du["keys"] if k["key_name"] == "ghostkey"]
    assert len(ghost) == 1 and ghost[0]["unmapped"] is True
    assert ghost[0]["spend"]["today_usd"] == pytest.approx(3.99)
    # keys are ordered by today's cost, dearest first
    costs = [(k.get("spend") or {}).get("today_usd") or 0.0 for k in du["keys"]]
    assert costs == sorted(costs, reverse=True), costs
    assert du["spend"]["total_today_usd"] == pytest.approx(11.99)


def test_card_payload_puts_every_live_lane_under_exactly_one_key(monkeypatch, fake_key):
    """console #90081.2: the render showed cosem-adcda / cosem-exams* under the
    shared key AND under cosem. The payload builder must be disjoint: one lane,
    one key -- including the shared row, which used to list every live lane."""
    lanes = ["fleet-health", "cosem-adcda", "cosem-exams", "wingmen-sites", "mystery-lane"]
    monkeypatch.setattr(console_app, "_deepseek_lanes",
                        lambda: [{"session": s, "model": "deepseek-flash",
                                  "model_label": "DeepSeek-V4.1-Flash-1017"} for s in lanes])
    monkeypatch.setattr(console_app, "_family_of",
                        lambda sess: {"fleet-health": "fleet", "cosem-adcda": "cosem",
                                      "cosem-exams": "cosem", "wingmen-sites": "wingmen",
                                      # unknown family -> falls back to the shared key at boot
                                      }.get(sess))
    monkeypatch.setattr(ds, "http_get_json", _fake_http([]))
    recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    _write_history(ds._SHARED_HISTORY_PATH, {"ts": recent, "balance_usd": 46.68})
    monkeypatch.setattr(ds, "get_spend", lambda now=None: {
        "available": True, "day": "2026-11-01", "day_label": "provider day (UTC+8)",
        "total_today_usd": 1.0, "total_7d_usd": 2.0, "budget_line_usd": 7.0,
        "budget_warn_usd": 5.0, "updated_at": None, "age_s": 1, "stale": False,
        "by_family": {}})

    payload = console_app._deepseek_card_payload()
    seen = {}
    rows = [payload["shared"]] + payload["keys"]
    for row in rows:
        for lane in row.get("lanes") or []:
            seen.setdefault(lane, []).append(row.get("key_name"))
    dupes = {lane: keys for lane, keys in seen.items() if len(keys) > 1}
    assert not dupes, f"lane(s) under more than one key: {dupes}"
    # and no lane silently vanishes: every live lane is on exactly one row
    assert sorted(seen) == sorted(lanes), (sorted(seen), sorted(lanes))


def test_a_lane_whose_family_maps_to_no_key_falls_to_shared_never_vanishes(monkeypatch, fake_key):
    """cc-quality-2 advisory on the 31f4887b review, folded in here.

    The first fix filtered the shared row by `family is None or family in
    <shared families>`. That caught a lane with NO family, but a lane whose
    family EXISTS and maps to no key at all appeared under neither the shared
    row nor a key row -- it vanished. On a cost card a vanished lane is
    untracked spend attribution, and it holds by construction only if the
    shared row is the true complement of the per-key rows.

    `ghostfam` is deliberately a real string that is absent from the shipped
    deepseek_key_map.json, which is exactly the case the None-family test does
    not exercise."""
    lanes = ["fleet-health", "ghost-lane"]
    monkeypatch.setattr(console_app, "_deepseek_lanes",
                        lambda: [{"session": s, "model": "deepseek-flash",
                                  "model_label": "DeepSeek-V4.1-Flash-1017"} for s in lanes])
    monkeypatch.setattr(console_app, "_family_of",
                        lambda sess: {"fleet-health": "fleet",
                                      "ghost-lane": "ghostfam"}.get(sess))
    monkeypatch.setattr(ds, "http_get_json", _fake_http([]))
    recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    _write_history(ds._SHARED_HISTORY_PATH, {"ts": recent, "balance_usd": 46.68})
    monkeypatch.setattr(ds, "get_spend", lambda now=None: {
        "available": True, "day": "2026-11-01", "day_label": "provider day (UTC+8)",
        "total_today_usd": 1.0, "total_7d_usd": 2.0, "budget_line_usd": 7.0,
        "budget_warn_usd": 5.0, "updated_at": None, "age_s": 1, "stale": False,
        "by_family": {}})

    assert "ghostfam" not in ds._key_map(), "fixture broke: ghostfam must not be a mapped family"
    payload = console_app._deepseek_card_payload()
    assert "ghost-lane" in payload["shared"]["lanes"], payload["shared"]["lanes"]

    shown = [s for k in payload["keys"] for s in k["lanes"]] + payload["shared"]["lanes"]
    assert sorted(shown) == sorted(lanes)      # exactly once each ...
    assert len(shown) == len(set(shown))       # ... never twice


# ---- fleet.js card render (node vm sandbox, same idiom as fleet_glm.test.js) --

@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="node not installed")
def test_fleet_js_deepseek_card_renders():
    import subprocess
    r = subprocess.run(["node", str(ROOT / "tests" / "console" / "fleet_deepseek.test.js")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
