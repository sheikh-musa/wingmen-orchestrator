import pytest

from reel_triage import config


def test_effort_weight_mapping():
    assert config.effort_weight("5min") == 1
    assert config.effort_weight("habit") == 2
    assert config.effort_weight("project") == 4


def test_feature_flag_off_by_default(monkeypatch):
    monkeypatch.delenv("WINGMEN_REEL_TRIAGE_ENABLED", raising=False)
    assert config.reel_triage_enabled() is False


def test_feature_flag_on(monkeypatch):
    monkeypatch.setenv("WINGMEN_REEL_TRIAGE_ENABLED", "1")
    assert config.reel_triage_enabled() is True


def test_constants():
    assert config.WIP_CAP == 3
    assert config.MAX_KEYFRAMES == 6
    assert config.FETCH_SLEEP_RANGE == (30, 60)
    assert config.AUTO_DISCARD_AFTER_DIGESTS == 2
    assert config.DIGEST_TOP_N == 5


def test_dsn_raises_when_unset(monkeypatch):
    monkeypatch.delenv("REEL_INBOX_DB_URL", raising=False)
    with pytest.raises(RuntimeError):
        config.reel_inbox_dsn()
