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
    rows = {r["key_name"]: sorted(r["families"]) for r in ds.distinct_key_rows()}
    assert rows["DEEPSEEK_KEY_COSEM"] == ["cosem", "cosem-tdu"]
    assert rows["DEEPSEEK_KEY_INTERNAL"] == ["fleet", "hc95"]
    assert rows["DEEPSEEK_KEY_BARIQ"] == ["bariq"]
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
    row = next(k for k in out["keys"] if k["key_name"] == "DEEPSEEK_KEY_BARIQ")
    assert row["delta_usd"] is None


def test_delta_reflects_change_since_the_last_good_read(fake_key, monkeypatch):
    monkeypatch.setattr(ds, "http_get_json", _fake_http([], body=BALANCE_OK))
    ds.get_deepseek_usage(now=1000.0)
    lower = json.loads(json.dumps(BALANCE_OK))
    lower["balance_infos"][1]["total_balance"] = "44.37"
    monkeypatch.setattr(ds, "http_get_json", _fake_http([], body=lower))
    out = ds.get_deepseek_usage(now=1000.0 + ds.CACHE_TTL_S + 1)
    row = next(k for k in out["keys"] if k["key_name"] == "DEEPSEEK_KEY_BARIQ")
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
    row = next(k for k in out["keys"] if k["key_name"] == "DEEPSEEK_KEY_BARIQ")
    assert round(row["delta_usd"], 2) == -1.87


def test_a_failed_refresh_never_serves_the_old_balance_as_current(fake_key, monkeypatch):
    monkeypatch.setattr(ds, "http_get_json", _fake_http([]))
    ds.get_deepseek_usage(now=1000.0)

    def _boom(url, key):
        raise ds.DeepseekUsageError("deepseek balance HTTP 500")
    monkeypatch.setattr(ds, "http_get_json", _boom)
    out = ds.get_deepseek_usage(now=1000.0 + ds.CACHE_TTL_S + 1)
    row = next(k for k in out["keys"] if k["key_name"] == "DEEPSEEK_KEY_BARIQ")
    assert row["available"] is False and row["balance_usd"] is None


# ---- shared key (op#35897/orch-console #83397): today's REAL spend location --

def test_shared_key_usage_reads_the_legacy_shared_key_by_name(fake_key, monkeypatch):
    calls = []
    monkeypatch.setattr(ds, "http_get_json", _fake_http(calls))
    out = ds.get_shared_key_usage(now=1000.0)
    assert calls == [(ds.BALANCE_URL, FAKE_KEY)]
    assert fake_key == [ds.SHARED_KEY_NAME]
    assert out["key_name"] == ds.SHARED_KEY_NAME
    assert out["available"] is True and out["balance_usd"] == 46.87


def test_shared_key_usage_is_cached_independently_of_the_per_client_keys(fake_key, monkeypatch):
    calls = []
    monkeypatch.setattr(ds, "http_get_json", _fake_http(calls))
    ds.get_deepseek_usage(now=1000.0)             # warms the 5 per-client keys
    ds.get_shared_key_usage(now=1000.0)            # must be its OWN HTTP call
    assert len(calls) == len(ds.distinct_key_rows()) + 1


def test_shared_key_failure_is_could_not_read(monkeypatch):
    def _missing(name):
        raise ds.DeepseekKeyNotProvisioned("should never happen for the shared key")
    monkeypatch.setattr(ds, "read_key", _missing)
    out = ds.get_shared_key_usage(now=1.0)
    assert out["available"] is False and out["reason"] == "key not created yet"


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
    with caplog.at_level(logging.DEBUG):
        r = httpx.get(base + "/api/fleet", headers=AUTH, timeout=10)
    assert r.status_code == 200
    body = r.json()
    du = body["deepseek_usage"]
    assert du["shared"]["available"] is True and du["shared"]["balance_usd"] == 46.87
    assert du["shared"]["lanes"] == ["bariq"]
    bariq = next(k for k in du["keys"] if k["key_name"] == "DEEPSEEK_KEY_BARIQ")
    assert bariq["available"] is True and bariq["balance_usd"] == 46.87
    assert bariq["lanes"] == ["bariq"]
    assert FAKE_KEY not in r.text
    assert all(FAKE_KEY not in rec.getMessage() for rec in caplog.records)
    access = tmp / "console_access.log"
    assert not access.exists() or FAKE_KEY not in access.read_text()


def test_api_fleet_deepseek_per_client_key_not_provisioned_is_distinct_from_shared_read(
        fleet_server, fake_key, monkeypatch):
    """orch-console #83397: tonight's REAL state -- the shared key reads fine
    while every per-client key says 'key not created yet', not 'could not read'."""
    base, _ = fleet_server
    real_read = ds.read_key

    def _per_client_missing(name):
        if name == ds.SHARED_KEY_NAME:
            return real_read(name)
        raise ds.DeepseekKeyNotProvisioned(f"vault has no secret named {name}")
    monkeypatch.setattr(ds, "read_key", _per_client_missing)
    monkeypatch.setattr(ds, "http_get_json", _fake_http([]))
    r = httpx.get(base + "/api/fleet", headers=AUTH, timeout=10)
    du = r.json()["deepseek_usage"]
    assert du["shared"]["available"] is True and du["shared"]["balance_usd"] == 46.87
    bariq = next(k for k in du["keys"] if k["key_name"] == "DEEPSEEK_KEY_BARIQ")
    assert bariq == {"available": False, "balance_usd": None, "delta_usd": None,
                      "reason": "key not created yet", "fetched_at": bariq["fetched_at"],
                      "key_name": "DEEPSEEK_KEY_BARIQ", "families": ["bariq"], "age_s": None,
                      "lanes": ["bariq"]}


# ---- fleet.js card render (node vm sandbox, same idiom as fleet_glm.test.js) --

@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="node not installed")
def test_fleet_js_deepseek_card_renders():
    import subprocess
    r = subprocess.run(["node", str(ROOT / "tests" / "console" / "fleet_deepseek.test.js")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
