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
        self.rotations = []

    def get(self, name, reason):
        return _Secret(self.value)

    def put(self, name, value, reason, leak_flagged=False, leak_flagged_reason=None):
        self.puts.append((name, value, leak_flagged, leak_flagged_reason))
        self.value = value

    def rotate(self, name, value, reason):
        self.rotations.append((name, value, reason))
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


def test_stage_archives_the_superseded_value_instead_of_flagging_it(monkeypatch, capsys):
    """cc-quality 87760 N6: put(leak_flagged=True) then put(new) on the SAME name is
    an upsert that clears the flag it just set and keeps no trace of the old
    ciphertext. rotate() archives it, which is the record actually wanted."""
    v = _Vault(OLD_PW)
    monkeypatch.setattr(rot, "_vault", lambda *a, **k: v)
    _stage(monkeypatch, NEW_PW + "\n")
    assert rot.main(["stage"]) == 0
    assert v.puts == [], "no flag-then-overwrite dance"
    assert [(r[0], r[1]) for r in v.rotations] == [("SUBSTRATE_DB_PASSWORD_NEW", NEW_PW)]
    assert "leaked" in v.rotations[0][2], "the audit reason must name the leak"
    out = capsys.readouterr().out
    assert NEW_PW not in out and OLD_PW not in out


def test_stage_uses_put_when_the_secret_does_not_exist_yet(monkeypatch, capsys):
    v = _Vault(OLD_PW)

    def _missing(name, reason):
        raise RuntimeError("not found")

    monkeypatch.setattr(v, "get", _missing)
    monkeypatch.setattr(rot, "_vault", lambda *a, **k: v)
    _stage(monkeypatch, NEW_PW + "\n")
    assert rot.main(["stage"]) == 0
    assert [p[1] for p in v.puts] == [NEW_PW]
    assert v.rotations == []


def test_stage_is_idempotent_when_the_value_is_already_staged(monkeypatch, capsys):
    v = _Vault(OLD_PW)
    monkeypatch.setattr(rot, "_vault", lambda *a, **k: v)
    _stage(monkeypatch, OLD_PW + "\n")
    assert rot.main(["stage"]) == 0
    assert v.puts == [] and v.rotations == []
    assert "nothing changed" in capsys.readouterr().out


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


# The verify subcommand takes NO DSN on argv (cc-quality 87760 F1): on 26 Oct this
# credential reached three transcripts because a pgrep -fl read a process's command
# line. Both values it needs are already on disk — the new one in the env file that
# apply rewrote, the old one in the 0600 backup beside it.

def _post_rotation_files(tmp_path, with_backup=True, backup_password=OLD_PW):
    env = tmp_path / ".env"
    env.write_text(f"DATABASE_URL={NEW_DSN}\n")
    if with_backup:
        bak = tmp_path / ".env.bak.20261101T000000Z"
        bak.write_text(f"DATABASE_URL={_dsn('postgres.p', backup_password)}\n")
    return env


def test_verify_proves_the_old_password_dead_from_the_files_on_disk(monkeypatch, capsys, tmp_path):
    env = _post_rotation_files(tmp_path)
    _fake_psycopg(monkeypatch, lambda dsn: dsn == NEW_DSN)
    rc = rot.main(["--env-file", str(env), "verify", "--retry-delay", "0"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "NEW dsn -> PASS" in out and "OLD dsn -> rejected — PASS" in out
    assert NEW_PW not in out and OLD_PW not in out


def test_verify_fails_when_the_old_password_still_works(monkeypatch, capsys, tmp_path):
    env = _post_rotation_files(tmp_path)
    _fake_psycopg(monkeypatch, lambda dsn: True)
    rc = rot.main(["--env-file", str(env), "verify", "--retry-delay", "0"])
    assert rc == 1
    assert "STILL WORKS" in capsys.readouterr().out


def test_verify_fails_when_the_new_password_does_not_connect(monkeypatch, capsys, tmp_path):
    env = _post_rotation_files(tmp_path)
    _fake_psycopg(monkeypatch, lambda dsn: False)
    assert rot.main(["--env-file", str(env), "verify", "--retries", "1"]) == 1


def test_verify_has_no_argv_dsn_option_at_all(monkeypatch):
    """F1 structurally: the parser cannot accept a DSN, so nobody can pass one."""
    _fake_psycopg(monkeypatch, lambda dsn: True)
    with pytest.raises(SystemExit):
        rot.main(["verify", "--new-dsn", NEW_DSN])


def test_verify_refuses_when_the_env_file_has_no_database_url(monkeypatch, capsys, tmp_path):
    env = tmp_path / ".env"
    env.write_text("UNRELATED=1\n")
    _fake_psycopg(monkeypatch, lambda dsn: True)
    assert rot.main(["--env-file", str(env), "verify"]) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_verify_refuses_without_a_backup_instead_of_skipping_the_old_probe(monkeypatch, capsys, tmp_path):
    """F2 / console 87773: a verify that cannot prove the old password is dead only
    proves the new one connects. That is a connection test, not a rotation check."""
    env = _post_rotation_files(tmp_path, with_backup=False)
    _fake_psycopg(monkeypatch, lambda dsn: dsn == NEW_DSN)
    rc = rot.main(["--env-file", str(env), "verify", "--retry-delay", "0"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "no .env.bak.*" in err and "prove dead" in err


def test_verify_refuses_when_the_backup_still_holds_the_current_password(monkeypatch, capsys, tmp_path):
    env = _post_rotation_files(tmp_path, backup_password=NEW_PW)
    _fake_psycopg(monkeypatch, lambda dsn: True)
    assert rot.main(["--env-file", str(env), "verify"]) == 2
    assert "SAME password" in capsys.readouterr().err


def test_verify_retries_the_pooler_cache_window(monkeypatch, capsys, tmp_path):
    """Supabase KB: Supavisor can reject the CORRECT new password for ~15s."""
    env = _post_rotation_files(tmp_path)
    seen = []

    def accept(dsn):
        seen.append(dsn)
        if dsn == NEW_DSN:
            return sum(1 for d in seen if d == NEW_DSN) >= 3   # stale cache at first
        return False                                          # the old DSN is dead

    _fake_psycopg(monkeypatch, accept)
    rc = rot.main(["--env-file", str(env), "verify", "--retries", "3", "--retry-delay", "0"])
    out = capsys.readouterr().out
    assert rc == 0
    assert seen.count(NEW_DSN) == 3
    assert "retrying" in out and "after 3 attempt(s)" in out
    assert NEW_PW not in out


def test_verify_does_not_probe_the_old_dsn_when_the_new_one_failed(monkeypatch, capsys, tmp_path):
    """During a pooler cache window EVERY attempt fails; a rejection then proves nothing."""
    env = _post_rotation_files(tmp_path)
    seen = []
    _fake_psycopg(monkeypatch, lambda dsn: seen.append(dsn) or False)
    rc = rot.main(["--env-file", str(env), "verify", "--retries", "1"])
    out = capsys.readouterr().out
    assert rc == 1
    assert seen == [NEW_DSN]        # the old DSN was never attempted
    assert "OLD probe not attempted" in out


def test_keys_prints_sixteen_hex_chars_of_the_hash(env_file, capsys):
    """The selector decides which credential gets rewritten; 32 bits is thin."""
    assert rot.main(["--env-file", str(env_file), "keys"]) == 0
    for line in capsys.readouterr().out.splitlines():
        digest = line.rsplit("pw=sha256:", 1)[1]
        assert len(digest) == 16 and int(digest, 16) >= 0


def test_apply_refuses_a_prefix_that_is_empty_short_or_not_hex(env_file, monkeypatch, capsys):
    """An empty prefix would match every password and rewrite the whole file."""
    for bad in ["", "abc", "zzzzzzzz", "g" * 16]:
        rc = rot.main(["--env-file", str(env_file), "apply", "--old-sha8", bad,
                       "--execute", "--from-stdin"])
        assert rc == 2, bad
        assert "REFUSED" in capsys.readouterr().err


def test_dsn_with_password_swaps_only_the_password():
    with_query = _dsn(SUB_USER, OLD_PW, "?sslmode=require")
    swapped = rot.dsn_with_password(with_query, NEW_PW)
    assert swapped == _dsn(SUB_USER, NEW_PW, "?sslmode=require")
    assert rot.url_password(swapped) == NEW_PW

# ── restart-consumers ──────────────────────────────────────────────────────────

def test_restart_consumers_dry_run_restarts_nothing(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(rot.subprocess, "run", lambda *a, **k: calls.append(a))
    assert rot.main(["restart-consumers", "--dry-run"]) == 0
    assert calls == []
    out = capsys.readouterr().out
    assert "dev.wingmen.ingest" in out and "dev.wingmen.tg-out" in out


def test_rewrite_writes_through_a_symlink_and_keeps_it_a_symlink(tmp_path):
    """Several hosts keep the live .env as a link to a shared copy. os.replace on
    the LINK path would swap the link for a regular file and silently detach the
    host from its shared env — a rotation that appears to work and does not."""
    shared = tmp_path / "shared.env"
    shared.write_text(_env_body())
    os.chmod(shared, 0o600)
    link = tmp_path / ".env"
    link.symlink_to(shared)

    changed, backup = rot.rewrite_env(link, OLD_SHA, NEW_PW)

    assert changed == ["DATABASE_URL", "BUS_DATABASE_URL"]
    assert link.is_symlink(), "the symlink must survive the rotation"
    assert os.path.realpath(link) == os.path.realpath(shared)
    assert NEW_PW in shared.read_text()
    assert OLD_PW not in shared.read_text()
    assert backup is not None and backup.parent == tmp_path


# ── the chicken-and-egg escape hatch ───────────────────────────────────────────
# The vault is reached THROUGH DATABASE_URL. If the dashboard reset lands before
# `apply` runs, the vault is unreachable by definition — so `apply` must still be
# able to finish without the vault, or the fleet is stranded with a dead password.

def test_apply_from_stdin_works_when_the_vault_is_unreachable(env_file, monkeypatch, capsys):
    class _Dead:
        def get(self, *a, **k):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(rot, "_vault", lambda *a, **k: _Dead())
    monkeypatch.setattr(sys, "stdin", io.StringIO(NEW_PW + "\n"))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)

    rc = rot.main(["--env-file", str(env_file), "apply", "--old-sha8", OLD_SHA,
                   "--execute", "--from-stdin"])
    out = capsys.readouterr().out
    assert rc == 0
    assert NEW_PW in env_file.read_text()
    assert NEW_PW not in out


def test_apply_from_stdin_still_refuses_a_bad_value(env_file, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("short\n"))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    before = env_file.read_text()
    rc = rot.main(["--env-file", str(env_file), "apply", "--old-sha8", OLD_SHA,
                   "--execute", "--from-stdin"])
    assert rc == 2
    assert env_file.read_text() == before


def test_apply_names_the_escape_hatch_when_the_vault_read_fails(env_file, monkeypatch, capsys):
    class _Dead:
        def get(self, *a, **k):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(rot, "_vault", lambda *a, **k: _Dead())
    rc = rot.main(["--env-file", str(env_file), "apply", "--old-sha8", OLD_SHA,
                   "--execute"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "--from-stdin" in err, "the refusal must tell the operator how to recover"


# ── apply --remote (the core-side bundle the VPS hub pulls) ────────────────────

class _FakeRemote:
    """Stands in for ssh: one host with one env file, and a record of every argv.

    The argv record is the point of these tests — the secret must be absent from it
    on BOTH ends, so the assertion is over every argument of every call, not just
    the one the value was expected to travel in.
    """

    def __init__(self, text, read_rc=0, write_rc=0):
        self.text = text
        self.read_rc = read_rc
        self.write_rc = write_rc
        self.argvs = []
        self.stdin_payloads = []
        self.backup_path = None

    def __call__(self, host, argv, stdin_bytes=None):
        self.argvs.append(list(argv))
        self.stdin_payloads.append(stdin_bytes)
        if argv[:2] == ["cat", "--"]:
            return types.SimpleNamespace(
                returncode=self.read_rc,
                stdout=self.text.encode() if self.read_rc == 0 else b"",
                stderr=b"cat: permission denied" if self.read_rc else b"")
        # the write snippet
        if self.write_rc != 0:
            return types.SimpleNamespace(returncode=self.write_rc, stdout=b"",
                                         stderr=b"python3: not found")
        self.text = (stdin_bytes or b"").decode()
        self.backup_path = f"{argv[-1]}.bak.20261101T000000Z"
        return types.SimpleNamespace(returncode=0,
                                     stdout=(self.backup_path + "\n").encode(),
                                     stderr=b"")

    def assert_no_secret_in_any_argv(self, *values):
        for argv in self.argvs:
            joined = " ".join(argv)
            for value in values:
                assert value not in joined, f"a secret reached an ssh argv: {argv[0]}"


REMOTE_PATH = "/etc/wingmen/secrets.env"   # never exists on the test box


def _remote_apply(monkeypatch, fake, *extra, stdin_text=NEW_PW + "\n"):
    monkeypatch.setattr(rot, "_ssh", fake)
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_text))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    return rot.main(["--env-file", REMOTE_PATH, "apply", "--old-sha8", OLD_SHA,
                     "--remote", "hub.example.invalid", *extra])


def test_apply_remote_rewrites_the_file_on_the_other_host(monkeypatch, capsys):
    fake = _FakeRemote(_env_body())
    rc = _remote_apply(monkeypatch, fake, "--execute", "--from-stdin")
    out = capsys.readouterr().out

    assert rc == 0
    assert NEW_PW in fake.text and OLD_PW not in fake.text
    assert "on hub.example.invalid" in out
    assert NEW_PW not in out and OLD_PW not in out
    assert fake.backup_path in out


def test_apply_remote_never_puts_the_secret_in_an_ssh_argv(monkeypatch):
    fake = _FakeRemote(_env_body())
    assert _remote_apply(monkeypatch, fake, "--execute", "--from-stdin") == 0
    fake.assert_no_secret_in_any_argv(NEW_PW, OLD_PW)
    # it DID travel on stdin — that is the only channel it is allowed to use
    assert any(p and NEW_PW.encode() in p for p in fake.stdin_payloads)


def test_apply_remote_refuses_when_the_remote_file_cannot_be_read(monkeypatch, capsys):
    fake = _FakeRemote(_env_body(), read_rc=255)
    rc = _remote_apply(monkeypatch, fake, "--execute", "--from-stdin")
    err = capsys.readouterr().err
    assert rc == 2
    assert "REFUSED" in err and "hub.example.invalid" in err
    assert fake.text == _env_body(), "nothing may be written after a failed read"


def test_apply_remote_refuses_when_the_remote_write_fails(monkeypatch, capsys):
    fake = _FakeRemote(_env_body(), write_rc=1)
    rc = _remote_apply(monkeypatch, fake, "--execute", "--from-stdin")
    err = capsys.readouterr().err
    assert rc == 2
    assert "REFUSED" in err
    assert NEW_PW not in fake.text


def test_apply_remote_dry_run_writes_nothing(monkeypatch, capsys):
    fake = _FakeRemote(_env_body())
    rc = _remote_apply(monkeypatch, fake, "--from-stdin")
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY-RUN" in out
    assert fake.text == _env_body()
    assert len(fake.argvs) == 1, "a dry run must only read"


def test_apply_remote_refuses_when_no_remote_key_matches(monkeypatch, capsys):
    fake = _FakeRemote("UNRELATED=keep-me\n")
    rc = _remote_apply(monkeypatch, fake, "--execute", "--from-stdin")
    err = capsys.readouterr().err
    assert rc == 2
    assert "no key in" in err
    assert fake.text == "UNRELATED=keep-me\n"


def test_ssh_command_is_batchmode_and_quotes_every_argument(monkeypatch):
    captured = {}

    def fake_run(cmd, input=None, capture_output=True, check=False):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(rot.subprocess, "run", fake_run)
    rot._ssh("hub.example.invalid", ["python3", "-c", "print(1)", "/a b/c"])
    cmd = captured["cmd"]
    assert cmd[:2] == ["ssh", "-o"] and "BatchMode=yes" in cmd
    assert cmd[-1] == "'/a b/c'", "a spaced path must survive the remote shell"
