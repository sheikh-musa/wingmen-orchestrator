"""Tests for scripts/substrate_rotation_executor.py (bus 87803).

Every test replaces the side effects and nothing else: the real rewrite, the real
preflight checks and the real ordering logic run. The properties that matter most are
(a) NOTHING is written before the patch unless every precondition holds, (b) a failure
before the patch puts the files back exactly as they were, and (c) the password never
appears in the journal or on stdout.
"""
import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import substrate_rotation_executor as ex  # noqa: E402
import rotate_substrate_dsn as rot  # noqa: E402

SCHEME = "postgre" + "sql://"
HOST = "pooler.example.invalid:5432/substrate"
OLD_PW = "OldPassword0123456789abcdef0123456789"
OLD_SHA = hashlib.sha256(OLD_PW.encode()).hexdigest()[:8]


def _dsn(pw):
    return f"{SCHEME}postgres.synthetic:{pw}@{HOST}"


def _write(tmp_path, name=".env", body=None):
    p = tmp_path / name
    p.write_text(body or f"DATABASE_URL={_dsn(OLD_PW)}\nOTHER=keep\n")
    os.chmod(p, 0o600)
    return p


class Deps:
    def __init__(self, probe_ok=True, patch_ok=True, daemons_ok=True,
                 old_still_works=False, remote_text=None):
        self.calls = []
        self.probe_ok = probe_ok
        self.old_still_works = old_still_works
        self.patch_ok = patch_ok
        self.daemons_ok = daemons_ok
        self.remote_text = remote_text
        self.kickstarted = []
        self.patched = False

    def probe(self, dsn):
        """True for a DSN carrying the NEW password; the OLD one follows the flag."""
        self.calls.append(("probe", dsn))
        if not self.probe_ok:
            return False
        if OLD_PW in dsn:
            # before the patch the old credential is the live one; after it, this
            # is the question the verify step exists to answer
            return True if not self.patched else self.old_still_works
        return True

    def patch_password(self, ref, token, pw):
        self.calls.append(("patch", ref, pw))
        if self.patch_ok:
            self.patched = True
            return True, "HTTP 200"
        return False, "HTTP 403"

    def launchd_present(self, label):
        return self.daemons_ok

    def kickstart(self, label):
        self.kickstarted.append(label)
        return True, ""

    def tmux_set_global(self, name, value):
        self.calls.append(("tmux", name, value))
        return True, ""

    def vault_has_current(self, name, reason):
        return None

    def vault_rotate(self, name, value, reason):
        self.calls.append(("vault_rotate", name, value))

    def read_remote(self, host, path):
        return self.remote_text

    def write_remote(self, host, path, content):
        self.calls.append(("write_remote", host, path))
        self.remote_text = content
        return f"{path}.bak.20261101T000000Z"

    def as_dict(self):
        return {k: getattr(self, k) for k in
                ("probe", "patch_password", "launchd_present", "kickstart",
                 "tmux_set_global", "vault_has_current", "vault_rotate",
                 "read_remote", "write_remote")}


def _run(tmp_path, deps, *, files=None, mode="execute", confirm=ex.CONFIRM_PHRASE,
         remote=None, remote_path=None, watch_seconds=0):
    files = files or [str(_write(tmp_path))]
    args = argparse.Namespace(
        mode=mode, env_file=list(files), old_sha8=OLD_SHA,
        ref="tscuymavysscrvoberrr", token_env="SUPABASE_ACCESS_TOKEN",
        remote=remote, remote_path=remote_path, job=None, bytes=36, retries=2,
        retry_delay=0.0, watch_seconds=watch_seconds, reason="test",
        execute=(mode == "execute"), confirm=confirm,
        journal=str(tmp_path / "journal.jsonl"))
    return ex.Rotation(args, deps=deps.as_dict()).run()


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("SUPABASE_ACCESS_TOKEN", "test-token-not-a-real-pat")


# ── preflight ─────────────────────────────────────────────────────────────────

def test_plan_writes_nothing_and_says_so(tmp_path, capsys):
    p = _write(tmp_path)
    before = p.read_text()
    deps = Deps()
    assert _run(tmp_path, deps, mode="plan", files=[str(p)]) == 0
    assert p.read_text() == before
    assert not any(c[0] in ("patch", "vault_rotate") for c in deps.calls)
    assert "nothing written" in capsys.readouterr().out


def test_execute_without_the_confirm_phrase_refuses_before_anything(tmp_path, capsys):
    p = _write(tmp_path)
    before = p.read_text()
    deps = Deps()
    rc = _run(tmp_path, deps, files=[str(p)], confirm="yes")
    assert rc == 2
    assert p.read_text() == before
    assert deps.calls == []


def test_preflight_refuses_a_target_that_does_not_carry_the_old_password(tmp_path):
    good = _write(tmp_path, ".env")
    other = tmp_path / "other.env"
    other.write_text(f"DATABASE_URL={_dsn('a-different-password-000000')}\n")
    before = good.read_text()
    deps = Deps()
    rc = _run(tmp_path, deps, files=[str(good), str(other)])
    assert rc == 3
    assert good.read_text() == before, "a refused preflight touches no file"
    assert "REFUSED" in (tmp_path / "journal.jsonl").read_text()


def test_preflight_refuses_when_a_daemon_label_is_gone(tmp_path, capsys):
    p = _write(tmp_path)
    rc = _run(tmp_path, Deps(daemons_ok=False), files=[str(p)])
    assert rc == 3
    assert "not loaded" in capsys.readouterr().err


def test_preflight_refuses_when_the_old_password_already_fails(tmp_path):
    p = _write(tmp_path)
    before = p.read_text()
    rc = _run(tmp_path, Deps(probe_ok=False), files=[str(p)])
    assert rc == 3
    assert p.read_text() == before


# ── ordering and rollback ─────────────────────────────────────────────────────

def test_happy_path_rewrites_patches_and_restarts_every_holder(tmp_path, capsys):
    p = _write(tmp_path)
    deps = Deps()
    assert _run(tmp_path, deps, files=[str(p)]) == 0
    text = p.read_text()
    assert OLD_PW not in text
    assert [c[0] for c in deps.calls].count("patch") == 1
    assert len(deps.kickstarted) == len(ex.DEFAULT_DAEMONS)
    out = capsys.readouterr().out
    assert "rotation COMPLETE" in out
    assert OLD_PW not in out


def test_a_failed_patch_restores_the_files_byte_for_byte(tmp_path, capsys):
    p = _write(tmp_path)
    before = p.read_text()
    rc = _run(tmp_path, Deps(patch_ok=False), files=[str(p)])
    assert rc == 3
    assert p.read_text() == before, "a pre-patch failure must leave no half-rotation"
    assert "database still has the OLD password" in capsys.readouterr().err


def test_a_failure_after_the_patch_does_not_claim_a_rollback(tmp_path, capsys):
    p = _write(tmp_path)
    deps = Deps()
    deps.kickstart = lambda label: (False, "service not found")
    rc = _run(tmp_path, deps, files=[str(p)])
    assert rc == 3
    err = capsys.readouterr().err
    assert "THE DATABASE NOW HAS THE NEW PASSWORD" in err
    assert "rebuild" not in err.lower()


def test_verify_refuses_when_the_old_password_still_works(tmp_path, capsys):
    """The half of a rotation that matters: the check that the OLD value is dead."""
    p = _write(tmp_path)
    deps = Deps(old_still_works=True)
    rc = _run(tmp_path, deps, files=[str(p)])
    assert rc == 3
    entries = [json.loads(l) for l in (tmp_path / "journal.jsonl").read_text().splitlines()]
    verdicts = [e.get("verdict") for e in entries if e["step"] == "verify"]
    assert "STILL WORKS" in verdicts
    assert "reset did not land" in capsys.readouterr().err


def test_verify_refuses_rather_than_skipping_when_no_backup_holds_the_old_value(
        tmp_path, capsys, monkeypatch):
    """'New value connects' is not proof; only 'old value is dead' is.

    With no backup the old value cannot be recovered, so the check cannot run. The
    executor must refuse, not record a pass it did not earn — that fail-open is the
    exact shape cc-quality flagged on the tool (F2), and it must not reappear here.
    """
    p = _write(tmp_path)
    monkeypatch.setattr(rot, "latest_backup", lambda path: None)
    rc = _run(tmp_path, Deps(), files=[str(p)])
    assert rc == 3
    err = capsys.readouterr().err
    assert "cannot recover the OLD password" in err
    assert "rotation COMPLETE" not in err
    entries = [json.loads(l) for l in (tmp_path / "journal.jsonl").read_text().splitlines()]
    assert not [e for e in entries
                if e["step"] == "verify" and e.get("verdict") == "not attempted (no backup)"]


# ── the secret must never surface ─────────────────────────────────────────────

def test_the_journal_and_stdout_never_contain_the_password(tmp_path, capsys):
    p = _write(tmp_path)
    deps = Deps()
    assert _run(tmp_path, deps, files=[str(p)]) == 0
    out = capsys.readouterr().out
    journal = (tmp_path / "journal.jsonl").read_text()
    new_pw = [c[2] for c in deps.calls if c[0] == "patch"][0]
    assert new_pw not in out and new_pw not in journal
    assert OLD_PW not in out and OLD_PW not in journal
    assert rot.sha8(new_pw) in out or rot.sha8(new_pw) in journal


def test_the_journal_is_0600(tmp_path):
    p = _write(tmp_path)
    assert _run(tmp_path, Deps(), files=[str(p)]) == 0
    assert stat.S_IMODE((tmp_path / "journal.jsonl").stat().st_mode) == 0o600


def test_every_restart_is_recorded(tmp_path):
    p = _write(tmp_path)
    deps = Deps()
    assert _run(tmp_path, deps, files=[str(p)]) == 0
    entries = [json.loads(l) for l in (tmp_path / "journal.jsonl").read_text().splitlines()]
    restarts = [e for e in entries if e["step"] == "restart" and e["outcome"] == "OK"]
    assert len(restarts) == len(ex.DEFAULT_DAEMONS)
