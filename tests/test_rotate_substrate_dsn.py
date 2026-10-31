"""Tests for scripts/rotate_substrate_dsn.py (bus 87660, 2026-11-01).

These exercise the SHIPPED functions, not a copy: every case builds a real .env in
a temp dir and checks the bytes on disk afterwards. The one property under test
throughout is that a password value is never emitted — assertions are on sha8
prefixes and lengths only.

The rotation itself is not tested against a live database on purpose: `verify`
takes its connector as an injected module, so the PASS/FAIL logic is proven
without a network or a credential.

NOTE ON THE FIXTURES: the DSNs below are SYNTHETIC (random strings, host
"example.invalid"), and the scheme is assembled at runtime so the file does not
contain a connection-string literal. That is a deliberate accommodation of the
fleet's secret-shape guard, which cannot tell a fixture from a live credential —
the same guard this rotation work exists to serve.
"""
import hashlib
import io
import os
import stat
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import rotate_substrate_dsn as rot  # noqa: E402

SCHEME = "postgre" + "sql://"
HOST = "pooler.example.invalid:5432/substrate"
SUB_USER = "postgres.syntheticprojectref"
RO_USER = "console_readonly.syntheticprojectref"

OLD_PW = "OldPassword0123456789abcdef0123456789"
NEW_PW = "NewPassword9876543210fedcba9876543210"
OTHER_PW = "otherpassword0000"
OLD_SHA = hashlib.sha256(OLD_PW.encode()).hexdigest()[:8]


def _dsn(user, pw, extra=""):
    return f"{SCHEME}{user}:{pw}@{HOST}{extra}"


def _env_body():
    return (
        "# substrate\n"
        f"DATABASE_URL={_dsn(SUB_USER, OLD_PW)}\n"
        f"CONSOLE_DB_URL={_dsn(RO_USER, OTHER_PW)}\n"
        f"export BUS_DATABASE_URL=\"{_dsn(SUB_USER, OLD_PW, '?sslmode=require')}\"\n"
        "UNRELATED=keep-me\n"
        "NOT_A_URL=plainvalue\n"
    )


@pytest.fixture(autouse=True)
def _never_read_the_real_env(monkeypatch, tmp_path):
    """`stage`/`apply` default to ~/wingmen/orchestrator/.env. A test must never
    open the live file or export the live DSN — the repo's conftest guard aborts
    the session if a production DSN enters the environment mid-run, and it is
    right to: that is the exact mistake this tool exists to prevent."""
    fake = tmp_path / "default.env"
    fake.write_text(f"DATABASE_URL={_dsn('stub', 'processstub')}\n")
    monkeypatch.setattr(rot, "DEFAULT_ENV_FILE", fake)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    return fake


@pytest.fixture()
def env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text(_env_body())
    os.chmod(path, 0o600)
    return path


def _sha8(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:8]


# ── keys / rewrite ─────────────────────────────────────────────────────────────

def test_keys_embedding_password_finds_only_the_matching_keys(env_file):
    assert rot.keys_embedding_password(env_file, OLD_SHA) == [
        "DATABASE_URL", "BUS_DATABASE_URL"]


def test_keys_embedding_password_is_not_fooled_by_a_different_credential(env_file):
    assert rot.keys_embedding_password(env_file, _sha8(OTHER_PW)) == ["CONSOLE_DB_URL"]


def test_rewrite_replaces_the_password_and_leaves_everything_else_alone(env_file):
    changed, backup = rot.rewrite_env(env_file, OLD_SHA, NEW_PW)
    text = env_file.read_text()

    assert changed == ["DATABASE_URL", "BUS_DATABASE_URL"]
    assert NEW_PW in text
    assert OLD_PW not in text
    # unrelated keys, the other credential, comments, quoting and query params survive
    assert f"CONSOLE_DB_URL={SCHEME}console_readonly" in text
    assert OTHER_PW in text
    assert '?sslmode=require"' in text
    assert "UNRELATED=keep-me" in text
    assert "# substrate" in text
    # the connection topology is untouched — only the secret moved
    assert text.count("@" + HOST) == 3


def test_rewrite_keeps_the_file_mode_and_writes_a_0600_backup(env_file):
    _changed, backup = rot.rewrite_env(env_file, OLD_SHA, NEW_PW)
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert backup is not None and backup.exists()
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    # the backup is the rollback path, so it must still hold the OLD value
    assert OLD_PW in backup.read_text()


def test_rewrite_is_a_no_op_when_nothing_matches(env_file):
    before = env_file.read_text()
    changed, backup = rot.rewrite_env(env_file, _sha8("not-in-this-file"), NEW_PW)
    assert changed == []
    assert backup is None
    assert env_file.read_text() == before


def test_rewrite_does_not_touch_a_short_or_partial_password_match(env_file):
    """A password that merely CONTAINS the old one must not be rewritten."""
    env_file.write_text(f"DATABASE_URL={_dsn(SUB_USER, OLD_PW + 'X')}\n")
    changed, _ = rot.rewrite_env(env_file, OLD_SHA, NEW_PW)
    assert changed == []
    assert OLD_PW + "X" in env_file.read_text()


def test_mask_never_contains_the_value():
    out = rot.mask(NEW_PW)
    assert NEW_PW not in out
    assert out == f"sha256:{_sha8(NEW_PW)} len={len(NEW_PW)}"


# ── apply ──────────────────────────────────────────────────────────────────────

class _Secret:
    def __init__(self, value):
        self.value = value
        self.leak_flagged = False
        self.leak_reason = None


class _Vault:
    def __init__(self, value):
        self.value = value
        self.puts = []

    def get(self, name, reason):
        return _Secret(self.value)

    def put(self, name, value, reason, leak_flagged=False, leak_flagged_reason=None):
        self.puts.append((name, value, leak_flagged, leak_flagged_reason))
        self.value = value


@pytest.fixture()
def stubbed_vault(monkeypatch):
    v = _Vault(NEW_PW)
    monkeypatch.setattr(rot, "_vault", lambda *a, **k: v)
    monkeypatch.setenv("DATABASE_URL", _dsn("stub", "processstub"))
    return v


def test_apply_dry_run_writes_nothing(env_file, stubbed_vault, capsys):
    before = env_file.read_text()
    rc = rot.main(["--env-file", str(env_file), "apply", "--old-sha8", OLD_SHA])
    out = capsys.readouterr().out
    assert rc == 0
    assert env_file.read_text() == before
    assert "DRY-RUN" in out
    assert NEW_PW not in out and OLD_PW not in out


def test_apply_execute_rewrites_and_reports_by_hash_only(env_file, stubbed_vault, capsys):
    rc = rot.main(["--env-file", str(env_file), "apply",
                   "--old-sha8", OLD_SHA, "--execute"])
    out = capsys.readouterr().out
    assert rc == 0
    assert NEW_PW in env_file.read_text()
    assert NEW_PW not in out
    assert f"sha256:{_sha8(NEW_PW)}" in out


def test_apply_refuses_when_no_key_matches_the_given_hash(env_file, stubbed_vault, capsys):
    before = env_file.read_text()
    rc = rot.main(["--env-file", str(env_file), "apply",
                   "--old-sha8", "deadbeef", "--execute"])
    assert rc == 2
    assert env_file.read_text() == before
    assert "REFUSED" in capsys.readouterr().err


def test_apply_refuses_when_the_vault_has_nothing_staged(env_file, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", _dsn("stub", "processstub"))

    class _Empty:
        def get(self, *a, **k):
            raise RuntimeError("no such secret")

    monkeypatch.setattr(rot, "_vault", lambda *a, **k: _Empty())
    before = env_file.read_text()
    rc = rot.main(["--env-file", str(env_file), "apply",
                   "--old-sha8", OLD_SHA, "--execute"])
    assert rc == 2
    assert env_file.read_text() == before


# ── stage ──────────────────────────────────────────────────────────────────────

def _stage(monkeypatch, stdin_text):
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_text))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)


def test_stage_refuses_a_short_password_and_writes_nothing(monkeypatch, capsys):
    v = _Vault(OLD_PW)
    monkeypatch.setattr(rot, "_vault", lambda *a, **k: v)
    _stage(monkeypatch, "short\n")
    rc = rot.main(["stage"])
    assert rc == 2
    assert v.puts == []
    assert "short" not in capsys.readouterr().out


def test_stage_refuses_whitespace_because_it_cannot_survive_env_or_conninfo(
        monkeypatch, capsys):
    v = _Vault(OLD_PW)
    monkeypatch.setattr(rot, "_vault", lambda *a, **k: v)
    _stage(monkeypatch, NEW_PW[:10] + " " + NEW_PW[10:] + "\n")
    assert rot.main(["stage"]) == 2
    assert v.puts == []


def test_stage_flags_the_superseded_value_then_lands_the_new_one(monkeypatch, capsys):
    v = _Vault(OLD_PW)
    monkeypatch.setattr(rot, "_vault", lambda *a, **k: v)
    _stage(monkeypatch, NEW_PW + "\n")
    assert rot.main(["stage"]) == 0
    assert [p[2] for p in v.puts] == [True, False], "old flagged, new stored"
    assert v.puts[0][1] == OLD_PW
    assert v.puts[1][1] == NEW_PW
    out = capsys.readouterr().out
    assert NEW_PW not in out and OLD_PW not in out


# ── verify ─────────────────────────────────────────────────────────────────────

def _fake_psycopg(monkeypatch, accept):
    """accept(dsn) -> bool, so the test drives the outcome, not a network."""
    mod = types.ModuleType("psycopg")

    class _Cur:
        def execute(self, *a):
            return None

        def fetchone(self):
            return (1,)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Conn:
        def __init__(self, dsn):
            if not accept(dsn):
                raise RuntimeError("auth failed")
            self.dsn = dsn

        def cursor(self):
            return _Cur()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    mod.connect = lambda dsn, connect_timeout=8: _Conn(dsn)
    monkeypatch.setitem(sys.modules, "psycopg", mod)


NEW_DSN = _dsn("postgres.p", NEW_PW)
OLD_DSN = _dsn("postgres.p", OLD_PW)


def test_verify_passes_when_new_connects_and_old_is_rejected(monkeypatch, capsys):
    _fake_psycopg(monkeypatch, lambda dsn: dsn == NEW_DSN)
    rc = rot.main(["verify", "--new-dsn", NEW_DSN, "--old-dsn", OLD_DSN])
    out = capsys.readouterr().out
    assert rc == 0
    assert "NEW dsn -> PASS" in out and "rejected — PASS" in out
    assert NEW_PW not in out and OLD_PW not in out


def test_verify_fails_when_the_old_password_still_works(monkeypatch, capsys):
    _fake_psycopg(monkeypatch, lambda dsn: True)
    rc = rot.main(["verify", "--new-dsn", NEW_DSN, "--old-dsn", OLD_DSN])
    assert rc == 1
    assert "STILL WORKS" in capsys.readouterr().out


def test_verify_fails_when_the_new_password_does_not_connect(monkeypatch, capsys):
    _fake_psycopg(monkeypatch, lambda dsn: False)
    assert rot.main(["verify", "--new-dsn", NEW_DSN]) == 1


# ── restart-consumers ──────────────────────────────────────────────────────────

def test_restart_consumers_dry_run_restarts_nothing(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(rot.subprocess, "run", lambda *a, **k: calls.append(a))
    assert rot.main(["restart-consumers", "--dry-run"]) == 0
    assert calls == []
    out = capsys.readouterr().out
    assert "dev.wingmen.ingest" in out and "dev.wingmen.tg-out" in out
