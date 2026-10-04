"""_proc_ground_truth / _proc_models / _proc_accounts (nervous_system/console/app.py),
and the auth_mismatch / live-fp-preference logic the main /api/fleet view applies with
them (op#25671/orch-console #51875 "it says it's on syed").

cc-quality's PR review (console content hash c1d10103e7daf9d3) verified all of this by
hand with a standalone script and found it correct, but flagged zero permanent test
coverage for the new behavior -- this file is that coverage, replaying the same
scenarios cc-quality exercised: the field-shape match against the real
panes.token_ground_truth() row, _proc_models() back-compat, _proc_accounts() host
scoping, and the incident-replay (a stale agent_status snapshot disagreeing with the
live-pid read) that this PR exists to fix.
"""
import time

from nervous_system.console import app as console_app


def _reset_proc_cache():
    with console_app._PROC_MODEL_LOCK:
        console_app._PROC_MODEL_CACHE["at"] = None
        console_app._PROC_MODEL_CACHE["by_session"] = {}


def _fake_rows():
    """The exact 3-row shape cc-quality's verification script used: a normal Mini
    session, an unverified Mini session (no model -- mid-resume-menu or similar), and
    a cross-host gzb session (must never leak into the Mini-scoped maps)."""
    return {
        "rows": [
            {"session": "quality", "account": "musa2", "fp": "e1dfa48eec85",
             "model": "claude-sonnet-5", "host": "Mini", "verified": True,
             "expected": "musa2", "expected_fp": "e1dfa48eec85", "mismatch": False,
             "metered": False},
            {"session": "cosem-adcda", "account": None, "fp": None,
             "model": None, "host": "Mini", "verified": False,
             "expected": "syed", "expected_fp": "582043088eae", "mismatch": False,
             "metered": False},
            {"session": "irsyad-coord", "account": "musa2", "fp": "e1dfa48eec85",
             "model": "claude-opus-5-5", "host": "gzb", "verified": True,
             "expected": "musa2", "expected_fp": "e1dfa48eec85", "mismatch": False,
             "metered": False},
        ],
    }


def test_proc_ground_truth_matches_the_real_row_shape(monkeypatch):
    """Field-shape check: every key _proc_ground_truth() reads must exist on the
    real panes.token_ground_truth() row -- a silent KeyError/shape drift would
    otherwise just degrade to empty dicts (the try/except), masking the bug."""
    _reset_proc_cache()
    monkeypatch.setattr(console_app.panes, "token_ground_truth",
                         lambda include_remote=False: _fake_rows())
    gt = console_app._proc_ground_truth()
    assert gt["quality"] == {
        "model": "claude-sonnet-5", "account": "musa2", "fp": "e1dfa48eec85",
        "mismatch": False, "expected": "musa2",
    }


def test_proc_models_is_unchanged_and_host_scoped(monkeypatch):
    """Back-compat: _proc_models() must still be {session: model}, Mini-only,
    model-presence filtered -- byte-identical to its pre-refactor behavior."""
    _reset_proc_cache()
    monkeypatch.setattr(console_app.panes, "token_ground_truth",
                         lambda include_remote=False: _fake_rows())
    models = console_app._proc_models()
    assert models == {"quality": "claude-sonnet-5"}  # gzb excluded; no-model Mini session excluded


def test_proc_accounts_includes_every_mini_session_regardless_of_model(monkeypatch):
    """Unlike _proc_models, _proc_accounts() must include a Mini session even with
    no model yet (mid-resume-menu) -- the account/fp read doesn't depend on having
    resolved a model. gzb stays excluded (cross-host; a local proc read can't see it)."""
    _reset_proc_cache()
    monkeypatch.setattr(console_app.panes, "token_ground_truth",
                         lambda include_remote=False: _fake_rows())
    accounts = console_app._proc_accounts()
    assert set(accounts) == {"quality", "cosem-adcda"}
    assert accounts["cosem-adcda"] == {"account": None, "fp": None, "mismatch": False, "expected": "syed"}


def test_auth_mismatch_fires_and_live_fp_wins_over_stale_snapshot(monkeypatch):
    """The incident this PR fixes, replayed through the REAL _resolve_auth_fp():
    agent_status.auth_fp is stuck on an OLD account (musa) while the live process
    has long since moved to musa2 (same shape as cc-quality-1's own auth_account
    bug, PR#291/#292). The main view must show the LIVE account and flag the
    disagreement -- never silently trust the stale snapshot."""
    _reset_proc_cache()
    monkeypatch.setattr(console_app.panes, "token_ground_truth",
                         lambda include_remote=False: _fake_rows())
    proc_accounts = console_app._proc_accounts()

    stale_snapshot_auth_fp = "68142948c003"  # musa -- what the DB row still says
    resolved_fp, auth_mismatch = console_app._resolve_auth_fp("quality", stale_snapshot_auth_fp, proc_accounts)

    assert auth_mismatch is True
    assert resolved_fp == "e1dfa48eec85"  # musa2 -- the LIVE truth, not the stale snapshot


def test_no_mismatch_when_snapshot_already_agrees_with_live(monkeypatch):
    _reset_proc_cache()
    monkeypatch.setattr(console_app.panes, "token_ground_truth",
                         lambda include_remote=False: _fake_rows())
    proc_accounts = console_app._proc_accounts()
    _, auth_mismatch = console_app._resolve_auth_fp("quality", "e1dfa48eec85", proc_accounts)
    assert auth_mismatch is False


def test_no_live_data_falls_back_to_snapshot_untouched(monkeypatch):
    """A body _proc_accounts() has never observed (e.g. a cross-host coordinator,
    or the Mini proc read failed entirely) must fall through to the snapshot as-is,
    auth_mismatch False -- never invented, never flagged on absence of signal."""
    _reset_proc_cache()
    monkeypatch.setattr(console_app.panes, "token_ground_truth",
                         lambda include_remote=False: _fake_rows())
    proc_accounts = console_app._proc_accounts()
    resolved_fp, auth_mismatch = console_app._resolve_auth_fp(
        "irsyad-coord", "e1dfa48eec85", proc_accounts)  # gzb; not in the Mini map

    assert auth_mismatch is False
    assert resolved_fp == "e1dfa48eec85"  # the snapshot, unchanged


def test_proc_ground_truth_cache_survives_a_ps_failure(monkeypatch):
    """Any failure reading the live process truth must return the last good
    cache, never raise into /api/fleet (the existing _proc_models() guarantee,
    now shared by _proc_accounts() too since both read the same cache)."""
    _reset_proc_cache()
    monkeypatch.setattr(console_app.panes, "token_ground_truth",
                         lambda include_remote=False: _fake_rows())
    assert console_app._proc_accounts()["quality"]["fp"] == "e1dfa48eec85"  # warm the cache

    def _boom(include_remote=False):
        raise RuntimeError("ps failed")
    monkeypatch.setattr(console_app.panes, "token_ground_truth", _boom)
    with console_app._PROC_MODEL_LOCK:
        console_app._PROC_MODEL_CACHE["at"] = time.monotonic() - 999  # force past the TTL
    assert console_app._proc_accounts()["quality"]["fp"] == "e1dfa48eec85"  # last good, not an exception
