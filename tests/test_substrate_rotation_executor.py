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
                 "tmux_set_global", "vault_rotate",
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


def test_plan_refuses_cleanly_instead_of_raising_a_traceback(tmp_path, capsys,
                                                             monkeypatch):
    """Plan is the dry run a human reads before executing; a refusal must look like one.

    A refusal that escapes as a traceback prints a stack and exits 1, and 1 is not the
    refusal code — so a wrapper checking rc == 3 reads it as a crash rather than a
    decision, at 04:00Z, about the tool that is about to change the database password.
    """
    monkeypatch.delenv("SUPABASE_ACCESS_TOKEN", raising=False)
    p = _write(tmp_path)
    before = p.read_text()
    rc = _run(tmp_path, Deps(), mode="plan", files=[str(p)])
    assert rc == 3, "a plan-mode refusal must exit 3, not 1 from an uncaught exception"
    captured = capsys.readouterr()
    assert "plan: REFUSED" in captured.err
    assert "SUPABASE_ACCESS_TOKEN is not set" in captured.err
    assert "Traceback" not in captured.err
    assert p.read_text() == before
    assert not (tmp_path / "journal.jsonl").exists(), \
        "plan wrote a journal — plan must write nothing at all"


def test_plan_scrubs_a_library_exception_too(tmp_path, capsys):
    """The probe is the first step that reaches the network, so it fails like a library.

    psycopg quotes the connection string it could not use, and at that moment the OLD
    value is the live credential — the plan-mode path must mask it exactly as the
    execute path does.
    """
    p = _write(tmp_path)
    deps = Deps()

    def boom(dsn):
        raise RuntimeError(f"could not connect: {dsn}")

    deps.probe = boom
    assert _run(tmp_path, deps, mode="plan", files=[str(p)]) == 3
    err = capsys.readouterr().err
    assert OLD_PW not in err, "the old password reached stderr through a library error"
    assert rot.sha8(OLD_PW) in err, "the value must still be reported by mask"


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


def test_propagate_sets_a_full_dsn_carrying_the_new_password(tmp_path):
    """cc-quality 87911 F1: the tmux global must be a DSN, not the bare password.

    DATABASE_URL is a postgresql:// URL everywhere in this fleet, and dsn_at_use falls
    back to the inherited variable when no file can be read. The bare password would
    hand every new pane an unparseable value — the step would report success and break
    the one surface it exists to fix.
    """
    p = _write(tmp_path)
    deps = Deps()
    assert _run(tmp_path, deps, files=[str(p)]) == 0
    tmux = [c for c in deps.calls if c[0] == "tmux"]
    assert len(tmux) == 1
    value = tmux[0][2]
    assert value.startswith(rot.SCHEME_PREFIX if hasattr(rot, "SCHEME_PREFIX")
                            else "postgre" + "sql://"), "must be a DSN, not a password"
    assert OLD_PW not in value
    new_pw = [c[2] for c in deps.calls if c[0] == "patch"][0]
    assert rot.url_password(value) == new_pw
    assert value == p.read_text().split("DATABASE_URL=", 1)[1].split("\n")[0]


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


def test_a_library_exception_before_the_patch_still_rolls_back(tmp_path, capsys):
    """A psycopg or OSError is not a Refused, and it must not skip the rollback.

    The vault stage is the realistic case: a missing vault table, a dropped connection
    or a failed import raises something ordinary, pre-patch, with the files already
    rewritten. Catching only Refused would let that escape as a traceback with the files
    half-rotated — the guarantee would then hold for the errors we anticipated and fail
    for the ones we did not.
    """
    p = _write(tmp_path)
    before = p.read_text()
    deps = Deps()

    def boom(name, value, reason):
        raise RuntimeError(f"vault table missing while staging {value}")

    deps.vault_rotate = boom
    rc = _run(tmp_path, deps, files=[str(p)])
    assert rc == 3, "a library exception must be a refusal, not an uncaught traceback"
    assert p.read_text() == before, "the pre-patch failure must still restore the files"
    err = capsys.readouterr().err
    assert "STOPPED at generate" in err and "Files restored" in err
    assert "RuntimeError" in err, "the message must still name what actually failed"


def test_a_library_exception_never_prints_the_password_it_quoted(tmp_path, capsys):
    """Library errors quote what they were given. That must not become a leak."""
    p = _write(tmp_path)
    deps = Deps()
    seen = {}

    def boom(name, value, reason):
        seen["pw"] = value
        raise RuntimeError(f"could not connect with {value} to the vault")

    deps.vault_rotate = boom
    assert _run(tmp_path, deps, files=[str(p)]) == 3
    captured = capsys.readouterr()
    journal = (tmp_path / "journal.jsonl").read_text()
    new_pw = seen["pw"]
    assert new_pw not in captured.err and new_pw not in journal, \
        "the exception text was printed verbatim, so the new password leaked"
    assert new_pw not in captured.out
    assert rot.sha8(new_pw) in (captured.err + journal), \
        "the value must still be reported by mask so runs can be told apart"


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


def test_verify_uses_the_old_value_captured_at_preflight_not_a_stale_backup(
        tmp_path, capsys):
    """A decoy backup left by an EARLIER rotation must never answer the old half.

    That value is dead for reasons that have nothing to do with tonight's reset, so
    probing it would report 'rejected' — a false PASS on the only check that decides
    whether the rotation happened at all.
    """
    p = _write(tmp_path)
    ancient = "AnOldPasswordFromAPreviousRotation0000"
    decoy = tmp_path / (".env.bak.99991231T000000Z")  # sorts last: the tempting one
    decoy.write_text(f"DATABASE_URL={_dsn(ancient)}\n")
    deps = Deps()
    assert _run(tmp_path, deps, files=[str(p)]) == 0
    probed = [c[1] for c in deps.calls if c[0] == "probe"]
    assert not [d for d in probed if ancient in d], \
        "the verify probed a stale backup instead of this run's old value"
    assert [d for d in probed if OLD_PW in d], "this run's old value must be probed"


def test_preflight_refuses_when_the_pat_variable_is_missing(tmp_path, capsys,
                                                           monkeypatch):
    """The check that could never fire: it tested the default name, not --token-env."""
    monkeypatch.delenv("SUPABASE_ACCESS_TOKEN", raising=False)
    p = _write(tmp_path)
    before = p.read_text()
    deps = Deps()
    rc = _run(tmp_path, deps, files=[str(p)])
    assert rc == 3
    assert "SUPABASE_ACCESS_TOKEN is not set" in capsys.readouterr().err
    assert p.read_text() == before
    assert not any(c[0] == "patch" for c in deps.calls)


def test_a_patch_error_body_that_echoes_the_value_is_scrubbed(tmp_path, capsys):
    """cc-quality 88061: the API's own words reach the operator, so they get scrubbed too.

    patch_password builds its failure text from the Management API's response body. A
    refusal is OUR exception type, but its message is partly THEIRS — so passing a
    Refused through unscrubbed on the assumption that we wrote the string ourselves is
    how the value leaks on the one step that must never leak it.
    """
    p = _write(tmp_path)
    before = p.read_text()
    deps = Deps()

    def patch_echoing(ref, token, pw):
        deps.calls.append(("patch", ref, pw))
        return False, f"HTTP 400: password {pw} was rejected by policy"

    deps.patch_password = patch_echoing
    assert _run(tmp_path, deps, files=[str(p)]) == 3
    captured = capsys.readouterr()
    journal = (tmp_path / "journal.jsonl").read_text()
    new_pw = [c[2] for c in deps.calls if c[0] == "patch"][0]
    assert new_pw not in captured.err and new_pw not in captured.out
    assert new_pw not in journal, "the API's error body carried the value into the journal"
    assert rot.sha8(new_pw) in (captured.err + journal), \
        "the value must still be reportable by mask"
    assert "was rejected by policy" in captured.err, \
        "scrubbing the value must not discard the reason the API gave"
    assert p.read_text() == before, "a patch refusal is pre-patch: files must be restored"


def test_a_step_detail_that_carries_the_value_never_reaches_the_journal(tmp_path,
                                                                      capsys):
    """The boundary rule: no emitted line holds the value, whatever produced it.

    tmux and launchd return third-party text that goes straight into a journal field.
    Scrubbing only the failure message the driver builds leaves every other journal
    field as an open channel.
    """
    p = _write(tmp_path)
    deps = Deps()

    def tmux_complains(name, value):
        deps.calls.append(("tmux", name, value))
        return False, f"tmux: bad value {value}"

    deps.tmux_set_global = tmux_complains
    assert _run(tmp_path, deps, files=[str(p)]) == 0, "a WARN is not a failure"
    captured = capsys.readouterr()
    journal = (tmp_path / "journal.jsonl").read_text()
    new_pw = [c[2] for c in deps.calls if c[0] == "patch"][0]
    assert new_pw not in captured.out and new_pw not in journal
    assert "tmux: bad value" in journal, "the diagnostic itself must survive"


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
