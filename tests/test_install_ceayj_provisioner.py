"""install_ceayj_provisioner.py — the ONE sanctioned install entrypoint for the
ceayj tenant-provisioner (op#35106 / bus #79277, runbook Install steps 1-4).

Run by orch-console under the console_irsyad_guard's second allow-exception:
`.venv/bin/python3 scripts/install_ceayj_provisioner.py [--dry-run|--apply]
[--step N]` and NOTHING else — no gate ids, DSNs or targets on the command
line (they are discovered in-process from gated bus rows and the console's
private credentials file, which this lane and its tests never read).

Output contract: key names, shas, counts, PIDs only — never a DSN or
password. DSN fixtures below are BUILT from parts (no literal DSN-shaped
string ever appears in this file — secrets_transcript_guard discipline).
The PG17 harness half (tests/provisioner/test_install_store_role_pg17.py)
proves the grant set by running REAL bundles as fleet_provisioner.
"""
import datetime as dt
import importlib.util
import os
import pathlib
import stat
import sys

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "lib"))
from provisioner import registry as inst_registry  # noqa: E402

_MOD_PATH = _REPO_ROOT / "scripts" / "install_ceayj_provisioner.py"
_spec = importlib.util.spec_from_file_location("install_ceayj_provisioner", _MOD_PATH)
inst = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(inst)

NOW = dt.datetime(2026, 10, 28, 5, 0, tzinfo=dt.timezone.utc)


def _dsn(user, pw, host="h.example", db="db", port="5432"):
    return "postgresql://" + user + ":" + pw + "@" + host + ":" + port + "/" + db


# --- CLI surface -------------------------------------------------------------
def test_parser_defaults_to_dry_run_and_validates_step():
    p = inst.build_parser()
    a = p.parse_args([])
    assert a.apply is False and a.step is None
    a = p.parse_args(["--apply", "--step", "3"])
    assert a.apply is True and a.step == 3
    a = p.parse_args(["--dry-run", "--step=2"])
    assert a.dry_run is True and a.step == 2


def test_parser_rejects_step_out_of_range_and_extra_args():
    p = inst.build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["--step", "0"])
    with pytest.raises(SystemExit):
        p.parse_args(["--step", "5"])
    with pytest.raises(SystemExit):
        p.parse_args(["--gate", "79277"])  # gates are discovered, never passed
    with pytest.raises(SystemExit):
        p.parse_args(["--apply", "extra"])


# --- step 1: migration target + gate discovery -------------------------------
def _draft(tmp_path, text="-- ledger: silo=tscuymavysscrvoberrr\nSELECT 1;\n"):
    d = tmp_path / "drafts"
    d.mkdir(exist_ok=True)
    f = d / "ceayj_provisioner_access_log.sql"
    f.write_text(text)
    return f


def _migrations_dir(tmp_path, top=127):
    m = tmp_path / "migrations"
    m.mkdir(exist_ok=True)
    for n in range(103, top + 1):
        (m / f"{n:03d}_x.sql").write_text(f"-- {n}\n")
    return m


def test_resolve_target_numbers_draft_after_current_top(tmp_path):
    draft = _draft(tmp_path)
    target, action = inst.resolve_target_migration(draft, _migrations_dir(tmp_path))
    assert target.name == "128_ceayj_provisioner_access_log.sql"
    assert action == "number"


def test_resolve_target_reuses_numbered_copy_with_same_sha(tmp_path):
    draft = _draft(tmp_path)
    m = _migrations_dir(tmp_path)
    (m / "128_ceayj_provisioner_access_log.sql").write_text(draft.read_text())
    target, action = inst.resolve_target_migration(draft, m)
    assert target.name == "128_ceayj_provisioner_access_log.sql"
    assert action == "reuse"


def test_resolve_target_refuses_drifted_numbered_copy(tmp_path):
    draft = _draft(tmp_path)
    m = _migrations_dir(tmp_path)
    (m / "128_ceayj_provisioner_access_log.sql").write_text("-- drifted\n")
    with pytest.raises(inst.InstallError, match="drift"):
        inst.resolve_target_migration(draft, m)


def test_resolve_target_refuses_missing_draft(tmp_path):
    with pytest.raises(inst.InstallError):
        inst.resolve_target_migration(tmp_path / "drafts" / "nope.sql", _migrations_dir(tmp_path))


def _gate_row(id, frm="orch-console", typ="decision", body="", when=None, subject="install"):
    return {"id": id, "from_agent": frm, "message_type": typ, "subject": subject,
            "body": body, "created_at": when or NOW}


def test_discover_gate_finds_newest_matching_decision():
    sha12 = "abcdef123456"
    rows = [
        _gate_row(100, body=f"older GO sha {sha12}", when=NOW - dt.timedelta(days=1)),
        _gate_row(200, body=f"GO: apply migration sha256={sha12}7890", when=NOW),
    ]
    row = inst.discover_gate(rows, sha12, now=NOW)
    assert row["id"] == 200


def test_discover_gate_ignores_wrong_agent_type_and_stale():
    sha12 = "abcdef123456"
    rows = [
        _gate_row(300, frm="cc-bariq-1", body=f"sha {sha12}"),          # not a gate owner
        _gate_row(301, typ="update", body=f"sha {sha12}"),              # not a decision
        _gate_row(302, body=f"sha {sha12}",
                  when=NOW - dt.timedelta(days=15)),                    # stale (>14d)
        _gate_row(303, body="sha 000000000000"),                        # wrong sha
    ]
    with pytest.raises(inst.InstallError, match="no gate"):
        inst.discover_gate(rows, sha12, now=NOW)


def test_discover_gate_takes_newest_within_age_floor():
    sha12 = "abcdef123456"
    rows = [
        _gate_row(400, body=f"sha {sha12}", when=NOW - dt.timedelta(days=13)),
        _gate_row(401, body=f"sha {sha12}", when=NOW - dt.timedelta(days=2)),
    ]
    assert inst.discover_gate(rows, sha12, now=NOW)["id"] == 401


def test_step1_argv_rehearses_against_the_draft_when_unnumbered_dry_run():
    # orch-console bus #79788: dry-run on an unnumbered draft said "would
    # number draft -> 128_...sql" but never created that file, then still
    # passed the (nonexistent) numbered path to apply_migration.py, which
    # refused rc=3 ("not a path and not a bare numeric prefix") -- the
    # rehearsal never ran. The numbered TARGET only needs to exist on disk
    # for a REAL --apply; a --dry-run never writes the ledger (it always
    # ROLLBACKs), so rehearsing against the draft's own existing path is
    # exactly as safe and exercises identical bytes (resolve_target_migration
    # already proved same-sha before this is ever called).
    target = pathlib.Path("/repo/migrations/128_ceayj_provisioner_access_log.sql")
    argv = inst._step1_argv(target=target, action="number", apply=False, gate_id=999)
    assert str(inst.DRAFT) in argv
    assert str(target) not in argv
    assert "--dry-run" in argv


def test_step1_argv_uses_numbered_target_on_apply():
    target = pathlib.Path("/repo/migrations/128_ceayj_provisioner_access_log.sql")
    argv = inst._step1_argv(target=target, action="number", apply=True, gate_id=999)
    assert str(target) in argv
    assert str(inst.DRAFT) not in argv
    assert "--gate" in argv and "999" in argv


def test_step1_argv_reuses_numbered_target_on_dry_run_when_already_numbered():
    # action="reuse" means the numbered copy already exists on disk (prior
    # run numbered it) -- the existing bug was specific to action="number"
    # (nothing numbered yet); "reuse" rehearses against the real target.
    target = pathlib.Path("/repo/migrations/128_ceayj_provisioner_access_log.sql")
    argv = inst._step1_argv(target=target, action="reuse", apply=False, gate_id=999)
    assert str(target) in argv
    assert "--dry-run" in argv


def test_discover_gate_age_check_converts_non_utc_aware_timestamps():
    # cc-quality Nit2 (#79345): psycopg returns timestamptz as an AWARE
    # datetime that may carry a non-UTC offset (e.g. a DB/session set to
    # +14:00). The buggy form `.replace(tzinfo=utc)` RELABELS the local
    # wall-clock numbers as UTC instead of CONVERTING the instant -- for
    # a positive offset that makes a row look MORE RECENT than it truly
    # is, which can let a genuinely stale (>14d) gate row slip through
    # the freshness floor. Construct a row whose TRUE age is 15 days
    # (correctly stale) but whose +14:00 wall-clock numbers, misread as
    # UTC, land at 14 days (incorrectly fresh) -- the dangerous direction
    # for a security floor.
    sha12 = "abcdef123456"
    offset = dt.timezone(dt.timedelta(hours=14))
    true_creation_utc = NOW - dt.timedelta(days=15)  # genuinely stale
    created_at = true_creation_utc.astimezone(offset)  # same instant, +14:00 numbers
    # sanity: the naive relabel reads as a DIFFERENT (later) instant
    assert created_at.replace(tzinfo=dt.timezone.utc) != true_creation_utc
    rows = [_gate_row(500, body=f"sha {sha12}", when=created_at)]
    with pytest.raises(inst.InstallError, match="no gate"):
        inst.discover_gate(rows, sha12, now=NOW)


# --- DSN building (pooler tenant suffix, #77507) ------------------------------
def test_build_role_dsn_derives_tenant_suffix_from_admin_username():
    admin = _dsn("postgres.ceayjeamtmcyzzvqflus", "sekret",
                 host="aws-1-ap-southeast-1.pooler.example", db="postgres")
    dsn = inst.build_role_dsn(admin, "fleet_provisioner", "newpw")
    expected = _dsn("fleet_provisioner.ceayjeamtmcyzzvqflus", "newpw",
                    host="aws-1-ap-southeast-1.pooler.example", db="postgres")
    assert dsn == expected


def test_build_role_dsn_password_is_url_encoded():
    dsn = inst.build_role_dsn(_dsn("postgres.tnt", "x"), "provisioner_log_role", "a+b/c?d")
    expected_userinfo = "provisioner_log_role.tnt" + ":" + "a%2Bb%2Fc%3Fd" + "@"
    assert expected_userinfo in dsn


def test_build_role_dsn_refuses_unsuffixed_admin_username():
    with pytest.raises(inst.InstallError, match="tenant"):
        inst.build_role_dsn(_dsn("postgres", "x"), "fleet_provisioner", "pw")


# --- credential file handling --------------------------------------------------
# Every admin-DSN fixture below must be pooler-tenant-suffixed to the REAL
# ihsanos multi-tenant DB ref (loaded from the registry doc, never a fresh
# literal here either -- same no-hardcode discipline as registry.py itself)
# because read_admin_dsn now independently asserts it (orch-console bus
# #79816 Bug 2 challenge: TENANT-RESIDENCY-001 -- the file holds credentials
# for MORE than one client store, so a heuristic/wrong-tenant pick must
# refuse, not silently connect fleet_provisioner into another tenant's silo).
_IHSANOS_REF = inst_registry.store_ref("ihsanos multi-tenant DB")
_OTHER_REF = "x" * 20  # a different, equally-shaped, definitely-wrong ref


def test_read_admin_dsn_reads_env_file_key(tmp_path):
    f = tmp_path / "write_dsn.env"
    f.write_text("OTHER=1\nIHSANOS_PROD_DATABASE_URL="
                  + _dsn(f"postgres.{_IHSANOS_REF}", "x") + "\n")
    os.chmod(f, 0o600)
    assert inst.read_admin_dsn(f).endswith(_dsn(f"postgres.{_IHSANOS_REF}", "x"))


def test_read_admin_dsn_refuses_loose_mode_and_missing_key(tmp_path):
    f = tmp_path / "write_dsn.env"
    f.write_text("IHSANOS_PROD_DATABASE_URL=" + _dsn(f"x.{_IHSANOS_REF}", "y") + "\n")
    os.chmod(f, 0o644)
    with pytest.raises(inst.InstallError, match="0600"):
        inst.read_admin_dsn(f)
    os.chmod(f, 0o600)
    g = tmp_path / "no_key.env"
    g.write_text("OTHER=1\n")
    os.chmod(g, 0o600)
    with pytest.raises(inst.InstallError, match="IHSANOS_PROD_DATABASE_URL"):
        inst.read_admin_dsn(g)


def test_read_admin_dsn_is_exact_key_only_no_heuristic_pick(tmp_path):
    # orch-console bus #79816: a heuristic ("the one non-RO postgres DSN in
    # the file") is NOT acceptable -- the file holds write credentials for
    # more than one client store, so a heuristic pick can land on another
    # tenant's silo. The correct DSN sitting under an unexpected key must be
    # REFUSED, not guessed at -- and the refusal names the key NAMES present
    # (never a value) so console can supply the right one in one round.
    f = tmp_path / "write_dsn.env"
    f.write_text(
        "SOME_UNEXPECTED_KEY_NAME=" + _dsn(f"postgres.{_IHSANOS_REF}", "x") + "\n"
        "ANOTHER_KEY=" + _dsn(f"postgres.{_IHSANOS_REF}", "y") + "\n"
    )
    os.chmod(f, 0o600)
    with pytest.raises(inst.InstallError, match="IHSANOS_PROD_DATABASE_URL") as exc:
        inst.read_admin_dsn(f)
    assert "SOME_UNEXPECTED_KEY_NAME" in str(exc.value)
    assert "ANOTHER_KEY" in str(exc.value)
    # the error names the candidate KEYS only, never a value
    assert "postgresql://" not in str(exc.value)


def test_read_admin_dsn_refuses_a_dsn_targeting_a_different_store_ref(tmp_path):
    # the exact documented key is present, but its VALUE targets a DIFFERENT
    # project ref -- the key-name match alone cannot catch a credentials
    # file that was edited to point the right-named var at the wrong store.
    f = tmp_path / "write_dsn.env"
    f.write_text("IHSANOS_PROD_DATABASE_URL=" + _dsn(f"postgres.{_OTHER_REF}", "x") + "\n")
    os.chmod(f, 0o600)
    with pytest.raises(inst.InstallError, match=_OTHER_REF):
        inst.read_admin_dsn(f)


def test_read_admin_dsn_accepts_exact_key_with_the_correct_store_ref(tmp_path):
    f = tmp_path / "write_dsn.env"
    f.write_text("IHSANOS_PROD_DATABASE_URL=" + _dsn(f"postgres.{_IHSANOS_REF}", "x") + "\n")
    os.chmod(f, 0o600)
    assert inst.read_admin_dsn(f).endswith(_dsn(f"postgres.{_IHSANOS_REF}", "x"))


def test_read_admin_dsn_strips_an_export_prefix(tmp_path):
    # orch-console bus #79852: the real file is a sourceable shell script --
    # its lines read `export KEY=value`, not bare `KEY=value`.
    f = tmp_path / "write_dsn.env"
    f.write_text("export IHSANOS_PROD_DATABASE_URL="
                  + _dsn(f"postgres.{_IHSANOS_REF}", "x") + "\n")
    os.chmod(f, 0o600)
    assert inst.read_admin_dsn(f).endswith(_dsn(f"postgres.{_IHSANOS_REF}", "x"))


def test_read_admin_dsn_key_listing_ignores_non_assignment_fragments(tmp_path):
    # bus #79852 finding 2: a plain "contains an =" scan picked up an `if`
    # line's test expression as a bogus "key name" in the miss diagnostic
    # (harmless but sloppy) -- an equality-style test ("$VAR" = "1") has an
    # "=" too, so it isn't skipped by a naive "no = means not a key" check.
    # Only real [export] KEY=value assignment lines count as a "key present".
    f = tmp_path / "write_dsn.env"
    f.write_text(
        'if [ "$ALLOW_WRITE_DSN" = "1" ]; then\n'
        "  export SOME_OTHER_KEY=" + _dsn(f"postgres.{_IHSANOS_REF}", "x") + "\n"
        "fi\n"
    )
    os.chmod(f, 0o600)
    with pytest.raises(inst.InstallError) as exc:
        inst.read_admin_dsn(f)
    msg = str(exc.value)
    assert "SOME_OTHER_KEY" in msg
    # the if-condition fragment itself must never be listed as a key name
    assert '[ "$ALLOW_WRITE_DSN"' not in msg


def test_read_admin_dsn_gated_line_prints_bypass_notice_instead_of_refusing(tmp_path, monkeypatch, capsys):
    # orch-console bus #79852/#79884 decision (option D): the file's own
    # if/then/fi allow-flag tripwire guards against ACCIDENTAL shell
    # exposure. This installer is a stronger control (sanctioned
    # entrypoint, exact-key-only read, residency asserted), so it must NOT
    # require that flag in its own environment -- it must say, by name,
    # that it is intentionally not enforcing it, and still return the DSN.
    f = tmp_path / "write_dsn.env"
    f.write_text(
        'if [ "$ALLOW_WRITE_DSN" = "1" ]; then\n'
        "  export IHSANOS_PROD_DATABASE_URL=" + _dsn(f"postgres.{_IHSANOS_REF}", "x") + "\n"
        "fi\n"
    )
    os.chmod(f, 0o600)
    monkeypatch.delenv("ALLOW_WRITE_DSN", raising=False)
    dsn = inst.read_admin_dsn(f)
    assert dsn.endswith(_dsn(f"postgres.{_IHSANOS_REF}", "x"))
    out = capsys.readouterr().out
    assert "ALLOW_WRITE_DSN" in out
    assert "not required" in out
    assert "x" * 1 not in out.split("ALLOW_WRITE_DSN")[0]  # no password/value leaked


def test_read_admin_dsn_bypass_notice_handles_single_line_if_then_fi(tmp_path, monkeypatch, capsys):
    f = tmp_path / "write_dsn.env"
    f.write_text(
        'if [ "$ALLOW_WRITE_DSN" = "1" ]; then export IHSANOS_PROD_DATABASE_URL='
        + _dsn(f"postgres.{_IHSANOS_REF}", "x") + "; fi\n"
    )
    os.chmod(f, 0o600)
    monkeypatch.delenv("ALLOW_WRITE_DSN", raising=False)
    dsn = inst.read_admin_dsn(f)
    assert dsn.endswith(_dsn(f"postgres.{_IHSANOS_REF}", "x"))
    assert "ALLOW_WRITE_DSN" in capsys.readouterr().out


def test_read_admin_dsn_residency_assert_still_enforced_on_gated_bypass_path(tmp_path, monkeypatch):
    # the bypass must never short-circuit TENANT-RESIDENCY-001 -- a gated
    # line targeting the WRONG store still refuses, flag or no flag.
    f = tmp_path / "write_dsn.env"
    f.write_text(
        'if [ "$ALLOW_WRITE_DSN" = "1" ]; then\n'
        "  export IHSANOS_PROD_DATABASE_URL=" + _dsn(f"postgres.{_OTHER_REF}", "x") + "\n"
        "fi\n"
    )
    os.chmod(f, 0o600)
    monkeypatch.delenv("ALLOW_WRITE_DSN", raising=False)
    with pytest.raises(inst.InstallError, match="TENANT-RESIDENCY-001"):
        inst.read_admin_dsn(f)


def test_read_admin_dsn_ungated_line_needs_no_flag(tmp_path, monkeypatch):
    # most keys in the file are plain, unconditional lines -- the gate
    # requirement must only apply to assignments actually inside an if/fi.
    f = tmp_path / "write_dsn.env"
    f.write_text("export IHSANOS_PROD_DATABASE_URL="
                  + _dsn(f"postgres.{_IHSANOS_REF}", "x") + "\n")
    os.chmod(f, 0o600)
    monkeypatch.delenv("ALLOW_WRITE_DSN", raising=False)
    assert inst.read_admin_dsn(f).endswith(_dsn(f"postgres.{_IHSANOS_REF}", "x"))


def test_write_secret_file_enforces_0700_dir_0600_file(tmp_path):
    out = inst.write_secret_file(tmp_path / ".secrets" / "role.dsn", _dsn("r", "pw"))
    mode_f = stat.S_IMODE(out.stat().st_mode)
    mode_d = stat.S_IMODE(out.parent.stat().st_mode)
    assert mode_f == 0o600 and mode_d == 0o700


def test_write_secret_file_refuses_loose_dir(tmp_path):
    d = tmp_path / ".secrets"
    d.mkdir()
    os.chmod(d, 0o755)
    with pytest.raises(inst.InstallError, match="mode"):
        inst.write_secret_file(d / "role.dsn", _dsn("r", "pw"))


# --- the store grant set: EXACTLY what the bundles need ------------------------
def test_render_store_grants_is_the_exact_bundle_surface():
    sql = inst.render_store_grants()
    # CREATEROLE (bundles create/drop editor roles) + LOGIN mint is separate
    assert "CREATE ROLE fleet_provisioner CREATEROLE NOLOGIN" in sql
    # safety pins, idempotent on re-run
    for pin in ("NOSUPERUSER", "NOBYPASSRLS", "NOCREATEDB", "NOREPLICATION"):
        assert pin in sql
    # schema: CREATE for tables/functions; USAGE WITH GRANT OPTION because the
    # forward bundle re-grants USAGE to each editor role it creates
    assert "GRANT USAGE ON SCHEMA public TO fleet_provisioner WITH GRANT OPTION" in sql
    assert "GRANT CREATE ON SCHEMA public TO fleet_provisioner" in sql
    # organizations: org-row INSERT (forward) + DELETE (revert) + REFERENCES
    # (content/history FKs) + SELECT(id) only (DELETE's WHERE; never the table)
    assert "GRANT INSERT, DELETE, REFERENCES ON TABLE public.organizations TO fleet_provisioner" in sql
    assert "GRANT SELECT (id) ON TABLE public.organizations TO fleet_provisioner" in sql
    # residency pin: NO bare table-wide SELECT on organizations
    assert "GRANT SELECT ON TABLE public.organizations" not in sql
    assert "GRANT SELECT ON public.organizations" not in sql


def test_render_install_canary_proves_full_surface_and_rolls_back():
    sql = inst.render_install_canary()
    for needle in (
        "CREATE TABLE public.__fp_install_canary",
        "REFERENCES organizations(id)",
        "CREATE ROLE __fp_install_canary NOLOGIN",
        "DROP ROLE __fp_install_canary",
        "INSERT INTO organizations",
        "ON CONFLICT (id) DO NOTHING",
        "DELETE FROM organizations",
        "DROP TABLE public.__fp_install_canary",
        "BEGIN",
        "ROLLBACK",
    ):
        assert needle in sql, needle


# --- output redaction ----------------------------------------------------------
def test_say_redacts_dsn_shaped_text(capsys):
    inst.say("wrote " + _dsn("fleet_provisioner.x", "pw") + " to file")
    out = capsys.readouterr().out
    assert "postgresql://" not in out and "pw" not in out
    assert "[redacted]" in out


# --- step-4 self-check against a canned response -------------------------------
def test_ungated_refusal_check_accepts_gate_invalid():
    class FakeResp:
        def __init__(self, text):
            self.text = text
    verdict = inst.check_ungated_refusal_response(
        FakeResp('{"status":"error","error":{"code":"GATE_INVALID","message":"gate 0 does not exist"}}'))
    assert verdict == "GATE_INVALID"


def test_ungated_refusal_check_refuses_an_ok_response():
    class FakeResp:
        def __init__(self, text):
            self.text = text
    with pytest.raises(inst.InstallError, match="refuse"):
        inst.check_ungated_refusal_response(
            FakeResp('{"status":"ok","client_key":"bariq","verb":"provision"}'))
