from ihsanos_drain.kill_switch import drain_disabled


def test_enabled_when_flag_unset(monkeypatch):
    monkeypatch.delenv("WINGMEN_IHSANOS_DRAIN_DISABLED", raising=False)
    assert drain_disabled() is False


def test_disabled_for_truthy_values(monkeypatch):
    for v in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("WINGMEN_IHSANOS_DRAIN_DISABLED", v)
        assert drain_disabled() is True


def test_enabled_for_falsey_values(monkeypatch):
    for v in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("WINGMEN_IHSANOS_DRAIN_DISABLED", v)
        assert drain_disabled() is False
