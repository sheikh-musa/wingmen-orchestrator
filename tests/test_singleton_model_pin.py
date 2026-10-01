"""Singleton boot model pin (orch-console #48930).

boot_fleet_health.sh used MODEL="${MODEL:-claude-opus-4-8}" and never read
~/wingmen/orchestrator/.fleet-health_model, so the opus-5-5 pin was inert and a
relaunch came up on opus-4-8. A pin nothing reads is not a pin.

These tests drive the SHIPPED bash function (scripts/lib/singleton_model_pin.sh)
and assert the shipped boot script actually calls it.
Precedence: MODEL env > pin file > default.
"""
import os
import subprocess

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LIB = os.path.join(_ROOT, "scripts", "lib", "singleton_model_pin.sh")
_BOOT = os.path.join(_ROOT, "scripts", "boot_fleet_health.sh")
_DEFAULT = "claude-opus-4-8"


def _resolve(pin_file, *, model_env=None, bash="/bin/bash"):
    env = dict(os.environ)
    env.pop("MODEL", None)
    if model_env is not None:
        env["MODEL"] = model_env
    out = subprocess.run(
        [bash, "-c", 'set -uo pipefail; source "$1"; resolve_singleton_model "$2" "$3"',
         "_", _LIB, pin_file, _DEFAULT],
        capture_output=True, text=True, env=env,
    )
    assert out.returncode == 0, out.stderr
    model, _, source = out.stdout.rstrip("\n").partition("\t")
    return model, source


def test_pin_file_is_read(tmp_path):
    pin = tmp_path / ".fleet-health_model"
    pin.write_text("claude-opus-5-5\n")
    assert _resolve(str(pin)) == ("claude-opus-5-5", f"pin {pin}")


def test_model_env_beats_pin(tmp_path):
    pin = tmp_path / ".fleet-health_model"
    pin.write_text("claude-opus-5-5\n")
    assert _resolve(str(pin), model_env="claude-sonnet-5-5") == ("claude-sonnet-5-5", "MODEL env")


def test_absent_pin_falls_back_to_default(tmp_path):
    assert _resolve(str(tmp_path / "missing")) == (_DEFAULT, "default")


@pytest.mark.parametrize("content", ["", "   \n\t\n"])
def test_blank_pin_falls_back_to_default(tmp_path, content):
    pin = tmp_path / ".fleet-health_model"
    pin.write_text(content)
    assert _resolve(str(pin)) == (_DEFAULT, "default")


def test_whitespace_is_stripped(tmp_path):
    pin = tmp_path / ".fleet-health_model"
    pin.write_text("  claude-opus-5-5  \r\n")
    assert _resolve(str(pin))[0] == "claude-opus-5-5"


def test_empty_model_env_does_not_win(tmp_path):
    pin = tmp_path / ".fleet-health_model"
    pin.write_text("claude-opus-5-5\n")
    assert _resolve(str(pin), model_env="") == ("claude-opus-5-5", f"pin {pin}")


def test_boot_script_uses_the_pin():
    src = open(_BOOT).read()
    assert 'MODEL="${MODEL:-claude-opus-4-8}"' not in src, "inert env-only default is back"
    assert "scripts/lib/singleton_model_pin.sh" in src
    assert 'resolve_singleton_model "$ORCH_DIR/.fleet-health_model"' in src
