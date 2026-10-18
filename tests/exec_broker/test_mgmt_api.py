"""hc#95 exec-broker mgmt-API transport."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "lib"))

from exec_broker.mgmt_api import UpstreamError, post_to_mgmt_api  # noqa: E402


class FakeProc:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_post_success_returns_rows(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc(0, '[{"a":1}]', ""))
    assert post_to_mgmt_api("select 1", "ref", "tok") == [{"a": 1}]


def test_post_curl_nonzero_exit_raises_upstream_error(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc(7, "", "boom"))
    with pytest.raises(UpstreamError):
        post_to_mgmt_api("select 1", "ref", "tok")


def test_post_malformed_json_raises_upstream_error(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc(0, "not json", ""))
    with pytest.raises(UpstreamError):
        post_to_mgmt_api("select 1", "ref", "tok")


def test_post_error_shaped_body_raises_upstream_error(monkeypatch):
    # curl exits 0 (HTTP 4xx/5xx isn't a curl failure) but the body is an error
    # dict, not a rows list -- must still be classified UPSTREAM_ERROR.
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc(0, '{"message":"not found"}', ""))
    with pytest.raises(UpstreamError):
        post_to_mgmt_api("select 1", "ref", "tok")


def test_post_timeout_raises_upstream_error(monkeypatch):
    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd="curl", timeout=28)
    monkeypatch.setattr(subprocess, "run", _raise)
    with pytest.raises(UpstreamError):
        post_to_mgmt_api("select 1", "ref", "tok")


def test_post_oserror_raises_upstream_error(monkeypatch):
    def _raise(*a, **k):
        raise OSError(2, "no such file")
    monkeypatch.setattr(subprocess, "run", _raise)
    with pytest.raises(UpstreamError):
        post_to_mgmt_api("select 1", "ref", "tok")


def test_post_never_includes_token_in_a_raised_message(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc(7, "", "boom"))
    with pytest.raises(UpstreamError) as exc:
        post_to_mgmt_api("select 1", "ref", "secret-token-value")
    assert "secret-token-value" not in str(exc.value)


def test_post_never_includes_stderr_in_a_raised_message(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc(7, "", "connection to server at host X port Y"))
    with pytest.raises(UpstreamError) as exc:
        post_to_mgmt_api("select 1", "ref", "tok")
    assert "host X port Y" not in str(exc.value)


def test_post_passes_max_time_to_curl(monkeypatch):
    captured = {}

    def _fake_run(argv, **kwargs):
        captured["argv"] = argv
        return FakeProc(0, "[]", "")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    post_to_mgmt_api("select 1", "ref", "tok")
    assert "--max-time" in captured["argv"]
