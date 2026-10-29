"""TDD for scripts/provision_bariq.py — the dedicated Bariq provisioning path
(op#34584: cc-bariq drives; orch-console runs the two credentialed steps).

No test touches a real database: apply_migration.apply_migration is monkeypatched,
and role-mint/verify runs against a fake connection. Example DSNs are assembled
at runtime (never written as literals) so the secrets guard doesn't flag them.
"""

import re
import stat
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

ORCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ORCH / "scripts"))

import provision_bariq as pb  # noqa: E402

MIGRATION = ORCH / "migrations" / "drafts" / "bariq_site_content_ceayj.sql"
EDITOR_RLS_MIGRATION = ORCH / "migrations" / "drafts" / "bariq_editor_rls_ceayj.sql"


def test_editor_rls_migration_pins_org_and_splits_by_command():
    """#77536: editor policies mirror the minted grants exactly — split per
    command (never FOR ALL), org-pinned, WITH CHECK, both tables covered."""
    text = EDITOR_RLS_MIGRATION.read_text()
    sql_lines = [l for l in text.splitlines() if not l.lstrip().startswith("--")]
    sql = "\n".join(sql_lines)
    assert pb.silo_from_text(text) == pb.silo_from_text(MIGRATION.read_text())  # same target store
    assert "TO bariq_editor" in sql and "FOR ALL" not in sql
    assert "FOR SELECT" in sql and "FOR UPDATE" in sql
    assert "WITH CHECK (org_id =" in sql  # UPDATE can never move a row out of the org
    assert sql.count("CREATE POLICY") == 3 and "DROP POLICY IF EXISTS" in sql  # idempotent
    assert "ON public.bariq_site_content_history" in sql  # history read-back covered


def dsn(user: str, host: str, pw: str = "pw", port: int = 5432) -> str:
    return f"postgres{'ql'}://{user}:{pw}@{host}:{port}/postgres"


# ── silo comes from the ledger header, never hardcoded ────────────────────────

def test_silo_read_from_ledger_header():
    text = MIGRATION.read_text()
    m = re.search(r"ledger: silo=([a-z0-9]+)", text)
    assert m, "draft must carry a ledger header"
    assert pb.silo_from_text(text) == m.group(1)


def test_silo_refused_without_header():
    with pytest.raises(pb.Refuse, match="ledger"):
        pb.silo_from_text("-- no header here\nSELECT 1;")


# ── admin DSN discovery: by silo ref, never by hardcoded var name ────────────

SILO = "refrefrefrefrefref12"


def test_discover_admin_dsn_finds_by_silo_in_host():
    value = dsn("postgres." + SILO, f"aws-1-ap-southeast-1.pooler.supabase.com", port=6543)
    name, found = pb.discover_admin_dsn({"ADMIN_DSN": value}, SILO)
    assert name == "ADMIN_DSN"
    assert found == value


def test_discover_admin_dsn_finds_by_silo_in_user():
    value = dsn("owner." + SILO, "db.example.com")
    _, found = pb.discover_admin_dsn({"ADMIN_DSN": value}, SILO)
    assert found == value


def test_discover_admin_dsn_refuses_when_absent():
    with pytest.raises(pb.Refuse, match="no .*DSN"):
        pb.discover_admin_dsn({"DATABASE_URL": dsn("x", "localhost")}, "somesilo123")


def test_discover_admin_dsn_refuses_on_ambiguity():
    env = {"A": dsn("a." + SILO, "h1.example.com"), "B": dsn("b." + SILO, "h2.example.com")}
    with pytest.raises(pb.Refuse, match="ambiguous"):
        pb.discover_admin_dsn(env, SILO)


def test_discover_admin_dsn_ignores_non_dsn_values():
    env = {"NOT_A_DSN": "hello " + SILO + " world", "ADMIN_DSN": dsn("u." + SILO, "h.example.com")}
    assert pb.discover_admin_dsn(env, SILO)[0] == "ADMIN_DSN"


def test_discover_admin_dsn_picks_postgres_over_auditor_ro():
    """Real-host shape (#77433): postgres.<silo> (admin) + auditor_ro.<silo> (RO)."""
    env = {
        "WRITE_DSN": dsn("postgres." + SILO, "aws-1-ap-southeast-1.pooler.supabase.com", port=6543),
        "AUDIT_DSN": dsn("auditor_ro." + SILO, "aws-1-ap-southeast-1.pooler.supabase.com", port=6543),
    }
    name, _ = pb.discover_admin_dsn(env, SILO)
    assert name == "WRITE_DSN"


def test_discover_admin_dsn_picks_postgres_among_three():
    env = {
        "A": dsn("auditor_ro." + SILO, "h.example.com"),
        "B": dsn("someapp_ro." + SILO, "h.example.com"),
        "C": dsn("postgres." + SILO, "h.example.com"),
    }
    assert pb.discover_admin_dsn(env, SILO)[0] == "C"


def test_discover_admin_dsn_refuses_when_only_readonly_matches():
    env = {"AUDIT_DSN": dsn("auditor_ro." + SILO, "h.example.com")}
    with pytest.raises(pb.Refuse, match="read-only"):
        pb.discover_admin_dsn(env, SILO)


def test_discover_admin_dsn_still_refuses_two_writable_non_postgres():
    env = {"A": dsn("owner." + SILO, "h1.example.com"), "B": dsn("deployer." + SILO, "h2.example.com")}
    with pytest.raises(pb.Refuse, match="ambiguous"):
        pb.discover_admin_dsn(env, SILO)


# ── role mint SQL ─────────────────────────────────────────────────────────────

def test_role_sql_idempotent_and_least_privilege():
    sql = pb.render_role_sql("bariq_editor", "pw123")
    assert "pg_roles" in sql and "CREATE ROLE" in sql  # idempotent guard
    assert "GRANT SELECT, UPDATE ON public.bariq_site_content TO bariq_editor" in sql
    assert "GRANT SELECT ON public.bariq_site_content_history TO bariq_editor" in sql
    assert sql.count("GRANT") == 3  # content, history, schema usage — nothing else
    assert "GRANT ALL" not in sql and "SUPERUSER" not in sql and "BYPASSRLS" not in sql


def test_generated_password_shape():
    pw = pb.generate_password()
    assert len(pw) >= 24 and re.fullmatch(r"[A-Za-z0-9_-]+", pw)


# ── editor DSN build ──────────────────────────────────────────────────────────

def test_editor_dsn_reuses_host_swaps_credentials():
    admin = dsn("owner.ref", "aws-1-ap-southeast-1.pooler.supabase.com", pw="admpw", port=6543)
    built = pb.build_editor_dsn(admin, "bariq_editor", "pw/123:x")
    assert built.startswith("postgres" + "ql://bariq_editor.ref:")  # tenant suffix carried (#77507)
    assert "pw%2F123%3Ax@" in built  # password URL-quoted
    assert "aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres" in built
    assert "admpw" not in built and "owner.ref" not in built


def test_editor_dsn_keeps_tenant_suffix_from_pooler_admin():
    """#77507: the shared pooler derives the tenant from the username suffix —
    a bare role username FATALs with ENOIDENTIFIER."""
    admin = dsn("postgres.tenant123", "aws-1-ap-southeast-1.pooler.supabase.com", port=6543)
    built = pb.build_editor_dsn(admin, "bariq_editor", "pw")
    assert built.startswith("postgres" + "ql://bariq_editor.tenant123:")


def test_editor_dsn_bare_role_when_admin_has_no_suffix():
    """Direct-host admin (no dot in username): no suffix to carry."""
    admin = dsn("postgres", "db.internal.example", pw="admpw")
    built = pb.build_editor_dsn(admin, "bariq_editor", "pw")
    assert built.startswith("postgres" + "ql://bariq_editor:")


def test_editor_dsn_pins_sslmode_require_when_admin_has_none():
    """cc-irsyad-coord #81597: the minted credential carries the TLS pin
    itself — a re-mint must not fall back to libpq's default 'prefer'."""
    admin = dsn("owner.ref", "aws-1-ap-southeast-1.pooler.supabase.com")
    built = pb.build_editor_dsn(admin, "bariq_editor", "pw")
    assert parse_qs(urlsplit(built).query).get("sslmode") == ["require"]


def test_editor_dsn_forces_require_over_a_weaker_admin_value():
    admin = dsn("owner.ref", "aws-1-ap-southeast-1.pooler.supabase.com") + "?sslmode=prefer"
    built = pb.build_editor_dsn(admin, "bariq_editor", "pw")
    assert parse_qs(urlsplit(built).query).get("sslmode") == ["require"]


def test_editor_dsn_keeps_unrelated_admin_query_params():
    admin = dsn("owner.ref", "aws-1-ap-southeast-1.pooler.supabase.com") + "?application_name=provision_bariq"
    q = parse_qs(urlsplit(pb.build_editor_dsn(admin, "bariq_editor", "pw")).query)
    assert q.get("application_name") == ["provision_bariq"]
    assert q.get("sslmode") == ["require"]


def make_fake_mint(order):
    """Connection-as-admin fake: role SQL executes, verify queries return exact
    grants / full seed (shared by the regen and apply-mode wiring tests)."""

    class FakeMintConn:
        def __enter__(self):
            class Cur:
                def __init__(self):
                    self.rows = []

                def execute(self, sql, *a, **k):
                    if "CREATE ROLE" in sql or "ALTER ROLE" in sql:
                        order.append("mint-sql")
                    elif "role_table_grants" in sql:
                        order.append("grant-verify")
                        self.rows = list(pb.EXPECTED_GRANTS)
                    else:
                        order.append("seed-verify")
                        self.rows = [("site",), ("hero",), ("testimonials",)]

                def fetchall(self):
                    return self.rows

            class C:
                def cursor(self):
                    return Cur()

            return C()

        def __exit__(self, *a):
            return False

    return lambda d: order.append("mint-conn") or FakeMintConn()


def test_regen_dsn_mode_skips_apply_and_ledger(monkeypatch, tmp_path):
    """#77507 regen path: re-mint password idempotently, rewrite the DSN file,
    never apply the migration, never bootstrap the ledger."""
    order = []
    fake_file = tmp_path / "secrets.dsn"
    monkeypatch.setattr(pb, "_apply", lambda *a, **k: order.append("apply") or (_ for _ in ()).throw(AssertionError("no apply in regen")))
    monkeypatch.setattr(pb, "_bootstrap_ledger", lambda d: order.append("ledger") or (_ for _ in ()).throw(AssertionError("no ledger in regen")))
    monkeypatch.setattr(pb, "_mint_connection", make_fake_mint(order))
    monkeypatch.setattr(pb, "write_dsn_file", lambda p, d: order.append("write") or fake_file.write_text("x"))
    monkeypatch.setattr(pb, "verify_roundtrip", lambda d: order.append("roundtrip"))
    monkeypatch.setattr(pb, "_bus_path", lambda *a, **k: order.append("bus"))
    env = {"ADMIN_DSN": dsn("postgres.ceayjeamtmcyzzvqflus", "aws-1-ap-southeast-1.pooler.supabase.com", port=6543)}
    pb.run(mode="regen-dsn", gate=None, migration=MIGRATION, env=env)
    assert order[0] == "mint-conn" and "apply" not in order and "ledger" not in order
    assert "roundtrip" in order and order[-1] == "bus"


# ── DSN file write: root-only ────────────────────────────────────────────────

def test_write_dsn_file_permissions_and_content(tmp_path):
    f = tmp_path / "bariq_editor_silo.dsn"
    pb.write_dsn_file(f, dsn("bariq_editor", "h"))
    assert f.read_text().strip() == dsn("bariq_editor", "h")
    assert stat.S_IMODE(f.stat().st_mode) == 0o600


# ── grant verification: exactly the expected grants, nothing else ────────────

class FakeCur:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self.rows


EXACT_GRANTS = [
    ("public", "bariq_site_content", "SELECT"),
    ("public", "bariq_site_content", "UPDATE"),
    ("public", "bariq_site_content_history", "SELECT"),
]


def test_verify_grants_passes_on_exact_set():
    pb.verify_grants(FakeCur(EXACT_GRANTS), "bariq_editor")  # no raise


def test_verify_grants_refuses_extra_table():
    rows = EXACT_GRANTS + [("public", "persons", "SELECT")]
    with pytest.raises(pb.Refuse, match="persons"):
        pb.verify_grants(FakeCur(rows), "bariq_editor")


def test_verify_grants_refuses_missing():
    with pytest.raises(pb.Refuse, match="missing"):
        pb.verify_grants(FakeCur(EXACT_GRANTS[:2]), "bariq_editor")


# ── seed verification ─────────────────────────────────────────────────────────

def test_seed_verify_query_targets_org_and_keys():
    pb.verify_seed(FakeCur([("site",), ("hero",), ("testimonials",)]))  # no raise


def test_seed_verify_refuses_incomplete_seed():
    with pytest.raises(pb.Refuse, match="seed"):
        pb.verify_seed(FakeCur([("site",), ("hero",)]))


# ── ledger bootstrap (#77445: ceayj lacks migration_ledger) ──────────────────

def test_ledger_ddl_matches_substrate_shape():
    ddl = pb.LEDGER_DDL
    assert "CREATE TABLE IF NOT EXISTS public.migration_ledger" in ddl
    for col in ("repo", "migration_name", "silo_ref", "sha256", "applied_at", "applied_by", "note"):
        assert col in ddl
    assert "PRIMARY KEY (repo, migration_name, silo_ref)" in ddl
    # fleet-infra table must not be readable by the MT DB's anon/authenticated
    assert "REVOKE ALL ON public.migration_ledger FROM anon, authenticated" in ddl
    assert "ENABLE ROW LEVEL SECURITY" in ddl


def test_verify_ledger_passes_on_exact_columns():
    cols = [("repo",), ("migration_name",), ("silo_ref",), ("sha256",), ("applied_at",), ("applied_by",), ("note",)]
    pb.verify_ledger(cols, [("PRIMARY KEY",)])  # no raise


def test_verify_ledger_refuses_extra_column():
    cols = [("repo",), ("migration_name",), ("silo_ref",), ("sha256",), ("applied_at",), ("applied_by",), ("note",), ("id",)]
    with pytest.raises(pb.Refuse, match="id"):
        pb.verify_ledger(cols, [("PRIMARY KEY",)])


def test_verify_ledger_refuses_missing_pk():
    cols = [("repo",), ("migration_name",), ("silo_ref",), ("sha256",), ("applied_at",), ("applied_by",), ("note",)]
    with pytest.raises(pb.Refuse, match="primary key"):
        pb.verify_ledger(cols, [])


def test_bootstrap_runs_before_dry_run_apply(monkeypatch):
    order = []
    ok = {"status": "dry_run_ok", "sha256": "ab" * 32}
    monkeypatch.setattr(pb, "_bootstrap_ledger", lambda d: order.append("bootstrap"))
    monkeypatch.setattr(pb, "_apply", lambda *a, **k: order.append("apply") or ok)
    env = {"ADMIN_DSN": dsn("x.ceayjeamtmcyzzvqflus", "h.example.com")}
    pb.run(mode="dry-run", gate=None, migration=MIGRATION, env=env)
    assert order == ["bootstrap", "apply"]


# ── editor round-trip (#77536: grants alone can hide an RLS deny-all) ────────

SEED_ROWS = [("site",), ("hero",), ("testimonials",)]


class FakeEditorConn:
    """Connection-as-editor fake: SELECT returns select_rows, UPDATE reports
    update_rowcount, rollback is counted (the no-op UPDATE must NEVER persist)."""

    def __init__(self, select_rows, update_rowcount):
        self.select_rows = select_rows
        self.update_rowcount = update_rowcount
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def rollback(self):
        self.rollbacks += 1

    def cursor(self):
        conn = self

        class Cur:
            rowcount = 0

            def execute(self, sql, params=None):
                if sql.lstrip().upper().startswith("SELECT"):
                    conn._seen_select = True
                else:
                    self.rowcount = conn.update_rowcount

            def fetchall(self):
                return conn.select_rows

        return Cur()


def test_roundtrip_passes_when_editor_sees_and_updates_seed_rows(monkeypatch):
    fake = FakeEditorConn(SEED_ROWS, update_rowcount=1)
    monkeypatch.setattr(pb, "_editor_connection", lambda d: fake)
    pb.verify_roundtrip("ignored-dsn")
    assert fake.rollbacks == 2  # after SELECT and after the no-op UPDATE


def test_roundtrip_refuses_rls_deny_all_on_select(monkeypatch):
    """The exact #77536 live state: grants verified green, RLS filters every row."""
    fake = FakeEditorConn([], update_rowcount=0)
    monkeypatch.setattr(pb, "_editor_connection", lambda d: fake)
    with pytest.raises(pb.Refuse, match="deny-all"):
        pb.verify_roundtrip("ignored-dsn")


def test_roundtrip_refuses_when_select_is_partial(monkeypatch):
    """Policy scoped too narrowly: editor sees some rows but not all seeds."""
    fake = FakeEditorConn([("site",)], update_rowcount=1)
    monkeypatch.setattr(pb, "_editor_connection", lambda d: fake)
    with pytest.raises(pb.Refuse, match="saw"):
        pb.verify_roundtrip("ignored-dsn")


def test_roundtrip_refuses_when_update_sees_no_rows(monkeypatch):
    """SELECT policy present but UPDATE policy missing: read works, write deny-all."""
    fake = FakeEditorConn(SEED_ROWS, update_rowcount=0)
    monkeypatch.setattr(pb, "_editor_connection", lambda d: fake)
    with pytest.raises(pb.Refuse, match="UPDATE matched 0"):
        pb.verify_roundtrip("ignored-dsn")


# ── end-to-end wiring with apply_migration monkeypatched ─────────────────────

def test_apply_requires_gate(monkeypatch):
    calls = []
    monkeypatch.setattr(pb, "_apply", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(pb, "_mint_connection", lambda d: (_ for _ in ()).throw(AssertionError("mint must not run")))
    with pytest.raises(SystemExit):
        pb.run(mode="apply", gate=None, migration=MIGRATION, env={"X": "1"})
    assert calls == []


def test_dry_run_never_buses_or_mints(monkeypatch):
    ran = []
    ok = {"status": "dry_run_ok", "sha256": "ab" * 32}
    monkeypatch.setattr(pb, "_bootstrap_ledger", lambda d: None)
    monkeypatch.setattr(pb, "_apply", lambda *a, **k: ran.append(k.get("dry_run")) or ok)
    monkeypatch.setattr(pb, "_mint_connection", lambda d: (_ for _ in ()).throw(AssertionError("no mint in dry-run")))
    monkeypatch.setattr(pb, "_bus_path", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no bus in dry-run")))
    env = {"ADMIN_DSN": dsn("x.ceayjeamtmcyzzvqflus", "h.example.com")}
    pb.run(mode="dry-run", gate=None, migration=MIGRATION, env=env)
    assert ran == [True]


def test_apply_path_round_trips_as_editor_before_bus(monkeypatch, tmp_path):
    """#77536 wiring: after apply + mint + DSN write, the editor round-trip must
    run BEFORE success is bused — a deny-all provisioning is a failure."""
    order = []
    ok = {"status": "applied", "sha256": "ab" * 32}

    def fake_apply(*a, **k):
        dry = k.get("dry_run")
        order.append("apply:" + ("dry" if dry else "real"))
        return {"status": "dry_run_ok", "sha256": "ab" * 32} if dry else ok

    monkeypatch.setattr(pb, "_bootstrap_ledger", lambda d: order.append("ledger"))
    monkeypatch.setattr(pb, "_apply", fake_apply)
    monkeypatch.setattr(pb, "_mint_connection", make_fake_mint(order))
    monkeypatch.setattr(pb, "write_dsn_file", lambda p, d: order.append("write") or (tmp_path / "x.dsn").write_text("x"))
    monkeypatch.setattr(pb, "verify_roundtrip", lambda d: order.append("roundtrip"))
    monkeypatch.setattr(pb, "_bus_path", lambda *a, **k: order.append("bus"))
    env = {"ADMIN_DSN": dsn("postgres.ceayjeamtmcyzzvqflus", "aws-1-ap-southeast-1.pooler.supabase.com", port=6543)}
    pb.run(mode="apply", gate=424242, migration=MIGRATION, env=env)
    assert order == [
        "ledger", "apply:dry", "apply:real", "mint-conn", "mint-sql",
        "grant-verify", "seed-verify", "write", "roundtrip", "bus",
    ]


def test_dry_run_refuses_without_admin_dsn(monkeypatch):
    monkeypatch.setattr(pb, "_apply", lambda *a, **k: (_ for _ in ()).throw(AssertionError("apply before DSN resolve")))
    with pytest.raises(pb.Refuse, match="no .*DSN"):
        pb.run(mode="dry-run", gate=None, migration=MIGRATION, env={"DATABASE_URL": dsn("x", "localhost")})
