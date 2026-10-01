"""Tests for pooler_authfail_tripwire — the MULTI-HOST attribution + threshold gate.

The first cut was Mini-only, so a gzb-origin auth-fail burst was INVISIBLE and blinded
us during a real breaker trip whose source was gzb (op#24342). These tests lock in that
ONE Mgmt-API-backed tripwire now (a) attributes fails per host by peer_ip and (b) raises
a breach when EITHER host crosses the per-host threshold — most importantly on a gzb-ONLY
burst, the exact regression that went dark.
"""
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pooler_authfail_tripwire as t  # noqa: E402


class _FakeResp(io.BytesIO):
    """urlopen() is used as a context manager, then json.load()'d — BytesIO gives us
    both __enter__/__exit__ and .read()."""
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def _mgmt_response(rows):
    # Mgmt API returns {"result": [...]} (the code also tolerates a bare list).
    return _FakeResp(json.dumps({"result": rows}).encode())


def _row(peer_ip, attr_key="log_attributes", as_list=False):
    attrs = {"peer_ip": peer_ip} if peer_ip is not None else {}
    if as_list:
        attrs = [attrs]
    return {"timestamp": "2026-10-02T00:00:00Z",
            "event_message": "FATAL: password authentication failed for user \"x\"",
            attr_key: attrs}


# ── peer_ip extraction (shape-robust) ────────────────────────────────────────
def test_extract_peer_ip_dict_and_list_and_metadata():
    assert t._extract_peer_ip(_row(t.HOST_IPS["mini"])) == t.HOST_IPS["mini"]
    assert t._extract_peer_ip(_row(t.HOST_IPS["gzb"], as_list=True)) == t.HOST_IPS["gzb"]
    # peer_ip may arrive under `metadata` instead of `log_attributes`
    assert t._extract_peer_ip(_row(t.HOST_IPS["gzb"], attr_key="metadata")) == t.HOST_IPS["gzb"]
    assert t._extract_peer_ip(_row(None)) is None


# ── per-host attribution ──────────────────────────────────────────────────────
def test_attribution_buckets_per_host(monkeypatch):
    rows = (
        [_row(t.HOST_IPS["mini"])] * 3
        + [_row(t.HOST_IPS["gzb"], as_list=True)] * 7
        + [_row("10.9.9.9")] * 2          # some unknown source -> unattributed
        + [_row(None)]                    # no peer_ip -> unattributed
    )
    monkeypatch.setenv("SUPABASE_ACCESS_TOKEN", "dummy")
    monkeypatch.setattr(t.urllib.request, "urlopen",
                        lambda req, timeout=30: _mgmt_response(rows))
    counts = t._count_authfails_by_host()
    assert counts["mini"] == 3
    assert counts["gzb"] == 7
    assert counts["unattributed"] == 3


def test_missing_token_raises_not_silent_green(monkeypatch):
    monkeypatch.delenv("SUPABASE_ACCESS_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        t._fetch_authfail_rows()


# ── threshold / breach, per host ─────────────────────────────────────────────
def test_gzb_only_burst_breaches_the_regression_that_went_dark():
    # gzb over threshold, Mini quiet -> the Mini-only version would have missed this.
    over = int(t.RATE_THRESHOLD * t.WINDOW_MIN) + 1  # > 5/min across the window
    counts = {"mini": 1, "gzb": over, "unattributed": 0}
    rates = t._rates(counts)
    assert rates["gzb"] > t.RATE_THRESHOLD
    assert rates["mini"] <= t.RATE_THRESHOLD
    assert t._breached_hosts(rates) == ["gzb"]


def test_mini_only_burst_still_breaches():
    over = int(t.RATE_THRESHOLD * t.WINDOW_MIN) + 1
    counts = {"mini": over, "gzb": 0, "unattributed": 0}
    assert t._breached_hosts(t._rates(counts)) == ["mini"]


def test_both_hosts_can_breach_together():
    over = int(t.RATE_THRESHOLD * t.WINDOW_MIN) + 1
    counts = {"mini": over, "gzb": over, "unattributed": 0}
    assert t._breached_hosts(t._rates(counts)) == ["gzb", "mini"]


def test_under_threshold_no_breach():
    # Right at the window-sized count for exactly 5/min is NOT over (strict >).
    at_threshold = int(t.RATE_THRESHOLD * t.WINDOW_MIN)
    counts = {"mini": at_threshold, "gzb": at_threshold, "unattributed": 100}
    assert t._breached_hosts(t._rates(counts)) == []


def test_remediation_is_host_specific():
    assert "option (a)" in t._remediation_for(["mini"])
    assert "gzb" in t._remediation_for(["gzb"]).lower()
    both = t._remediation_for(["gzb", "mini"])
    assert "option (a)" in both and "gzb" in both.lower()
