"""Tests for scripts/env_set.sh (Musa op#24408 delta, bus #48639/#48642) -- the
sanctioned escape hatch for changing one .env key without echoing its value."""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "env_set.sh"


def _run(args, stdin_text=None):
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        input=stdin_text, text=True, capture_output=True,
    )


def _sha1_prefix(value: str) -> str:
    return hashlib.sha1(value.encode()).hexdigest()[:10]


def test_updates_existing_key_in_place(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("A=1\nB=old\nC=3\n")

    r = _run([str(env_file), "B"], stdin_text="newvalue")
    assert r.returncode == 0, r.stderr
    assert "newvalue" not in r.stdout
    assert f"B updated (sha1 {_sha1_prefix('newvalue')})" in r.stdout

    content = env_file.read_text()
    assert content == "A=1\nB=newvalue\nC=3\n"


def test_appends_key_when_absent(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("A=1\n")

    r = _run([str(env_file), "NEW_KEY"], stdin_text="secretvalue")
    assert r.returncode == 0, r.stderr
    assert "secretvalue" not in r.stdout

    content = env_file.read_text()
    assert content == "A=1\nNEW_KEY=secretvalue\n"


def test_reads_value_from_file_flag(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("A=1\n")
    value_file = tmp_path / "value.txt"
    value_file.write_text("fromfile")

    r = _run([str(env_file), "A", "-f", str(value_file)])
    assert r.returncode == 0, r.stderr
    assert "fromfile" not in r.stdout
    assert env_file.read_text() == "A=fromfile\n"


def test_rejects_invalid_key_name(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("A=1\n")
    r = _run([str(env_file), "not a key"], stdin_text="x")
    assert r.returncode != 0
    assert "invalid key name" in r.stderr


def test_rejects_missing_file():
    r = _run(["/no/such/file", "KEY"], stdin_text="x")
    assert r.returncode != 0
    assert "no such file" in r.stderr


def test_never_prints_the_raw_value(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("TOKEN=old\n")
    secret = "sk-ant-ReallySensitiveValueForTestingOnly123"
    r = _run([str(env_file), "TOKEN"], stdin_text=secret)
    assert secret not in r.stdout
    assert secret not in r.stderr
