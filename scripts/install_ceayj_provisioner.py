#!/usr/bin/env python3
"""install_ceayj_provisioner.py — the ONE sanctioned install entrypoint for the
ceayj tenant-provisioner (op#35106, bus #79277; runbook
docs/ceayj-tenant-provisioner-runbook.md Install steps 1-4).

Invoked by orch-console under console_irsyad_guard's second allow-exception:

    .venv/bin/python3 scripts/install_ceayj_provisioner.py [--dry-run|--apply] [--step N]

and NOTHING else — no gate ids, DSNs or targets on the command line. They are
discovered IN PROCESS: the substrate migration's gate row is found on the
fleet bus by its sha12 (orch-console posts it), and the store admin DSN is
read from the console's private client-silo credentials file, which exists in
memory only inside this process (fail-loud 0600 mode check, never printed).

Steps (in order, each idempotent):
  1. substrate access-log migration — numbers migrations/drafts/
     ceayj_provisioner_access_log.sql after the current top, discovers the
     gate row, applies via scripts/apply_migration.py (env DATABASE_URL only,
     never a DSN on the command line). Re-runs report already_applied.
  2. provisioner_log_role — mints its LOGIN password, grants SELECT on
     agent_messages, writes .secrets/provisioner_log_role.dsn (0600), and
     proves the credential with a rolled-back INSERT/UPDATE/SELECT roundtrip
     AS the role (a grant-level check cannot see RLS/acl reality, #77536).
  3. fleet_provisioner on the ceayj store — the DEDICATED role (never
     postgres) with EXACTLY the privileges render.py's bundles need
     (CREATEROLE, CREATE on public, USAGE WITH GRANT OPTION for the editor
     re-grants, INSERT/DELETE/REFERENCES + SELECT(id) on organizations).
     PG16+ CREATEROLE semantics: the negative escalation grant is re-proved
     live at install time. DSN written to .secrets/ceayj_admin_write.dsn
     (the plist's PROVISIONER_STORE_DSN_FILE), 0600. The full privilege
     surface is proven by a single transactional canary (create role, create
     FK table, org-row INSERT/DELETE, drop role, drop table) that always
     ROLLS BACK — the store is untouched.
  4. launchd — loads launchd/dev.wingmen.ceayj-provisioner.plist (skip if
     loaded), waits for the socket, sends an UNGATED request and requires
     the daemon to refuse it (GATE_INVALID) — the daemon being up AND gated.

Output discipline (op#35106): key names, shas, counts and PIDs only. Every
line passes through say(), which redacts anything DSN-shaped; passwords exist
only in process locals and the 0600 files they are written to.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus, urlsplit, urlunsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

import psycopg  # noqa: E402
from psycopg import sql as psql_sql  # noqa: E402
from substrate_dsn import dsn_from_env_file  # noqa: E402
from provisioner import registry as provisioner_registry  # noqa: E402

DRAFT = REPO_ROOT / "migrations" / "drafts" / "ceayj_provisioner_access_log.sql"
MIGRATIONS = REPO_ROOT / "migrations"
SECRETS = REPO_ROOT / ".secrets"
LOG_ROLE_DSN_FILE = SECRETS / "provisioner_log_role.dsn"
STORE_DSN_FILE = SECRETS / "ceayj_admin_write.dsn"  # the plist's PROVISIONER_STORE_DSN_FILE
ADMIN_CRED_FILE = Path("~/.wingmen/private/write_dsn.env").expanduser()
ADMIN_CRED_KEY = "IHSANOS_PROD_DATABASE_URL"
PLIST = REPO_ROOT / "launchd" / "dev.wingmen.ceayj-provisioner.plist"
PLIST_LABEL = "dev.wingmen.ceayj-provisioner"
SOCKET = SECRETS / "provisioner.sock"
SUBSTRATE_SILO = "tscuymavysscrvoberrr"

# apply_migration's own gate rules (GATE_OWNERS / GATE_MAX_AGE_DAYS), mirrored
# for DISCOVERY only — apply_migration remains the enforcer.
_GATE_AGENTS = frozenset({"orch-console", "cai"})
_GATE_MAX_AGE_DAYS = 14

_DSN_RE = re.compile(r"[a-z0-9_+]+://\S+")
_NUMBERED_RE = re.compile(r"^(\d{3})_ceayj_provisioner_access_log\.sql$")


class InstallError(Exception):
    """Fail-loud install failure. Never carries a credential in its text."""


def say(msg: str) -> None:
    print(_DSN_RE.sub("[redacted]", msg), flush=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="dry-run is the default; --apply executes. Output: key names, shas, counts, PIDs only.",
    )
    p.add_argument("--apply", action="store_true",
                   help="execute (default is a dry-run: report + apply_migration --dry-run rehearsal)")
    p.add_argument("--dry-run", action="store_true",
                   help="explicit dry-run (the default); mutually exclusive with --apply")
    p.add_argument("--step", type=int, choices=(1, 2, 3, 4), default=None,
                   help="run only this step (default: all four in order)")
    return p


# --- step 1: the substrate migration -----------------------------------------
def resolve_target_migration(draft: Path, migrations_dir: Path) -> tuple[Path, str]:
    """Return (target_path, action) where action is number|reuse. The draft is
    numbered after the current top once; the numbered copy is the artifact
    apply_migration ledgers, so re-runs reuse it. A numbered copy whose content
    hash differs from the draft refuses (drift — apply_migration would refuse
    it too, with a worse message)."""
    if not draft.is_file():
        raise InstallError(f"draft {draft.name} not found — run from a checkout that has it")
    sha = hashlib.sha256(draft.read_bytes()).hexdigest()
    existing = sorted(migrations_dir.glob("*_ceayj_provisioner_access_log.sql"))
    if existing:
        match = _NUMBERED_RE.match(existing[-1].name)
        if not match:
            raise InstallError(f"unexpected existing artifact name {existing[-1].name!r}")
        if hashlib.sha256(existing[-1].read_bytes()).hexdigest() != sha:
            raise InstallError(
                f"{existing[-1].name} has drifted from the draft (different sha256) — "
                "refusing rather than guessing which is truth")
        return existing[-1], "reuse"
    tops = [
        int(re.match(r"^(\d{3})_.*\.sql$", f.name).group(1))
        for f in migrations_dir.glob("*.sql")
        if re.match(r"^(\d{3})_.*\.sql$", f.name)
    ]
    top = max(tops) if tops else 0
    return migrations_dir / f"{top + 1:03d}_ceayj_provisioner_access_log.sql", "number"


def discover_gate(rows: list[dict], sha12: str, *, now: dt.datetime) -> dict:
    """Newest bus row that can gate this exact artifact: from a gate owner,
    message_type decision, within the age floor, mentioning the artifact's
    sha12 in subject or body. apply_migration re-validates everything.

    created_at is converted to UTC (astimezone), never relabeled
    (.replace(tzinfo=...)) -- psycopg returns an AWARE timestamptz that may
    carry a non-UTC offset, and relabeling its wall-clock numbers as UTC
    would silently shift the age computation by that offset, in the
    dangerous direction for a positive offset: a genuinely stale row can
    look fresher than it is (cc-quality Nit2, bus #79345)."""
    matches = [
        r for r in rows
        if r["from_agent"] in _GATE_AGENTS
        and r["message_type"] == "decision"
        and (now - r["created_at"].astimezone(dt.timezone.utc)).days <= _GATE_MAX_AGE_DAYS
        and sha12 in (str(r["subject"]) + str(r["body"]))
    ]
    if not matches:
        raise InstallError(
            f"no gate row found for sha12 {sha12} — orch-console posts a decision row "
            f"naming it (bus #79277); scanned {len(rows)} recent rows")
    return max(matches, key=lambda r: r["id"])


def fetch_gate_rows(dsn: str, sha12: str, limit: int = 500) -> list[dict]:
    """Bus rows that can gate THIS artifact. The sha12 filters IN SQL
    (subject/body LIKE) inside the age window — never a newest-N scan: a
    live gate (#79787) once scrolled out of the newest-500 window and had
    to be re-issued as #80290 purely because of scan volume. The LIMIT is
    a belt (an artifact named by >limit rows), not the filter."""
    pattern = f"%{sha12}%"
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, from_agent, message_type, subject, body, created_at
                 FROM agent_messages
                WHERE created_at > now() - interval '21 days'
                  AND (subject LIKE %s OR body LIKE %s)
                ORDER BY id DESC LIMIT %s""", (pattern, pattern, limit))
        cols = ["id", "from_agent", "message_type", "subject", "body", "created_at"]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _step1_argv(*, target: Path, action: str, apply: bool, gate_id: int) -> list[str]:
    """The numbered TARGET file only needs to exist on disk for a real
    --apply. A --dry-run never writes the ledger (apply_migration always
    ROLLBACKs it), so when action=="number" the target hasn't been created
    yet (dry-run never creates it — see step1_substrate_migration) and
    rehearsing against it would pass apply_migration a nonexistent path,
    which refuses rc=3 ("not a path and not a bare numeric prefix") before
    the rehearsal ever runs (orch-console bus #79788). Rehearse against the
    DRAFT's own existing path instead — resolve_target_migration has already
    proved it is byte-identical to what the numbered copy would hold."""
    rehearsal_path = DRAFT if (action == "number" and not apply) else target
    argv = [sys.executable, str(REPO_ROOT / "scripts" / "apply_migration.py"),
            str(rehearsal_path), "--silo", SUBSTRATE_SILO,
            "--gate-dsn-env", "DATABASE_URL"]
    if not apply:
        argv.append("--dry-run")
    else:
        argv += ["--gate", str(gate_id)]
    return argv


def step1_substrate_migration(*, apply: bool) -> None:
    substrate = dsn_from_env_file()
    target, action = resolve_target_migration(DRAFT, MIGRATIONS)
    sha12 = hashlib.sha256(DRAFT.read_bytes()).hexdigest()[:12]
    gate = discover_gate(fetch_gate_rows(substrate, sha12), sha12, now=dt.datetime.now(dt.timezone.utc))
    say(f"step1: gate row {gate['id']} from {gate['from_agent']} ({gate['created_at']}Z) "
        f"authorizes sha12 {sha12} — subject: {str(gate['subject'])[:80]!r}")
    if action == "number":
        if apply:
            shutil.copyfile(DRAFT, target)
            say(f"step1: numbered draft -> {target.name}")
        else:
            say(f"step1: [dry-run] would number draft -> {target.name}")
    argv = _step1_argv(target=target, action=action, apply=apply, gate_id=gate["id"])
    env = {**os.environ, "DATABASE_URL": substrate}
    proc = subprocess.run(argv, cwd=REPO_ROOT, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        say(f"step1: apply_migration FAILED rc={proc.returncode}: "
            f"{(proc.stdout + proc.stderr).strip()[:600]}")
        raise InstallError("apply_migration refused the access-log migration")
    status = next((s for s in ("already_applied", "dry_run_ok", "applied")
                   if s in proc.stdout), "unknown")
    say(f"step1: {target.name} status={status} (gate={gate['id']}, sha12={sha12})")


# --- step 2: provisioner_log_role credential ---------------------------------
def build_role_dsn(admin_dsn: str, role: str, password: str) -> str:
    """Swap the admin DSN's user for <role>.<tenant> (the pooler derives the
    tenant from the username's suffix, #77507) and the password. Host, port,
    db and scheme stay exactly the admin connection's."""
    parts = urlsplit(admin_dsn)
    tenant = _pooler_tenant_ref(admin_dsn, what="admin DSN")
    userinfo = f"{role}.{tenant}:{quote_plus(password)}"
    return urlunsplit((parts.scheme, f"{userinfo}@{parts.hostname}"
                       + (f":{parts.port}" if parts.port else ""),
                       parts.path, parts.query, parts.fragment))


IHSANOS_STORE_ALIAS = "ihsanos multi-tenant DB"


def _pooler_tenant_ref(dsn: str, *, what: str = "DSN") -> str:
    """The project ref a pooler DSN authenticates against: the username's
    <user>.<tenant> suffix (the pooler derives the tenant from it, #77507)."""
    parts = urlsplit(dsn)
    if "@" not in (parts.netloc or "") or not parts.username or "." not in parts.username:
        raise InstallError(f"{what} username is not pooler-tenant-suffixed (<user>.<tenant>) — refusing")
    return parts.username.split(".", 1)[1]


_ASSIGN_LINE_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_VARREF_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")
_LEADING_THEN_RE = re.compile(r"^then\b\s*(.*)$")
_TRAILING_FI_RE = re.compile(r"^(.*?)\bfi\b\s*$")


def _scan_credential_lines(text: str) -> list[tuple[str, str, frozenset]]:
    """Statically scan shell-script-shaped credential text for [export]
    KEY=value assignment lines, tracking which ones sit inside an
    `if [ ... $FLAG ... ]; then ... fi` conditional block -- the file's own
    allow-flag tripwire (orch-console bus #79852). NEVER evaluates the file
    as shell (no subprocess, no sourcing): this is a static line/clause
    scan recognizing the conventional if/then/fi idiom (single- or
    multi-line). Returns (key, value, required_flags) for every assignment
    found; required_flags is the union of every $VAR referenced in each
    still-open if-condition at that point. An unmodeled CONDITION (e.g. an
    equality test, not just `-n`, or `&&`) still contributes its referenced
    var names to required_flags -- more gates, never fewer, so an exotic
    comparison fails closed. This does NOT extend to unmodeled BRANCH
    STRUCTURE: only a bare `if ... ; then ... fi` is tracked, so an
    assignment inside an `elif`/`else`/`case` branch is scored against the
    outer if's condition (or as ungated, for a bare `case`), not its own --
    cc-quality PR#581 review, finding F1. Fine for the real file (a flat
    if/then/fi, confirmed), but this scanner must not be trusted against a
    credentials file with that richer shape without first modeling it."""
    gate_stack: list[frozenset] = []
    out: list[tuple[str, str, frozenset]] = []
    for raw_line in text.splitlines():
        for clause in raw_line.split(";"):
            clause = clause.strip()
            if not clause or clause.startswith("#"):
                continue
            m = _LEADING_THEN_RE.match(clause)
            if m:
                clause = m.group(1).strip()
                if not clause:
                    continue
            trailing_fi = False
            m = _TRAILING_FI_RE.match(clause)
            if m:
                trailing_fi = True
                clause = m.group(1).strip()
            if clause.startswith("if "):
                cond = re.sub(r"\bthen\b\s*$", "", clause[3:]).strip()
                gate_stack.append(frozenset(_VARREF_RE.findall(cond)))
            elif clause:
                m2 = _ASSIGN_LINE_RE.match(clause)
                if m2:
                    key = m2.group(1)
                    val = m2.group(2).strip().strip('"').strip("'")
                    required = frozenset().union(*gate_stack) if gate_stack else frozenset()
                    out.append((key, val, required))
            if trailing_fi and gate_stack:
                gate_stack.pop()
    return out


def read_admin_dsn(path: Path) -> str:
    """Read the admin/write DSN from the console's private credentials file.
    EXACT key name only — no heuristic pick (orch-console bus #79816: the
    file holds write credentials for MORE than one client store, so a
    "guess the one non-RO postgres DSN" fallback can land fleet_provisioner
    in another tenant's silo, exactly the TENANT-RESIDENCY-001 breach the
    guard exists to prevent). On a miss, fails loud and names the KEYS
    present (never a value) so console can supply the right one in one
    round. The file is a sourceable shell script (bus #79852): lines may
    carry an `export` prefix, and the real export may sit inside an
    `if [ ... $FLAG ... ]; then ... fi` tripwire block the file's author
    added against ACCIDENTAL exposure from an interactive `source`. This
    installer does not require that flag (bus #79852/#79884 decision,
    option D): it is already a stronger control than the flag — the
    guard's exception #2 admits only this exact entrypoint/argv, the key
    read here is exact-match only, and the DSN's residency is still
    asserted below regardless of whether the line was gated. When the
    matched assignment IS gated, this prints one line naming the required
    flag(s) and stating the condition is intentionally not enforced, so
    the bypass is visible, never silent. Independently of the key, the
    DSN's own project ref
    (pooler username suffix) must equal the ihsanos multi-tenant DB ref from
    docs/data-store-registry.md — loaded via registry.store_ref (the same
    never-hardcoded loader the provisioner package already uses), never a
    fresh literal here. This catches the key-name-right-value-wrong case a
    key match alone cannot."""
    if not path.is_file():
        raise InstallError(f"{path} not found — the console private credentials file is required for step 3")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & 0o077:
        raise InstallError(f"{path} mode is {oct(mode)} — must be 0600 (it is a credential)")
    assignments = _scan_credential_lines(path.read_text())
    keys_present = [key for key, _val, _req in assignments]
    match = next(((val, req) for key, val, req in assignments if key == ADMIN_CRED_KEY and val), None)
    if match is None:
        raise InstallError(
            f"{ADMIN_CRED_KEY} not found in {path.name} — keys present: "
            f"{', '.join(keys_present) or '(none)'}"
        )
    dsn, required_flags = match
    if required_flags:
        say(
            f"{ADMIN_CRED_KEY} in {path.name} is gated by an if-block requiring "
            f"{', '.join(sorted(required_flags))} — allow-flag condition intentionally "
            "not required here: sanctioned installer entrypoint (guard exception #2), "
            "exact key only, ihsanos-residency asserted next (bus #79852/#79884 decision)"
        )
    try:
        expected_ref = provisioner_registry.store_ref(IHSANOS_STORE_ALIAS)
    except provisioner_registry.Refuse as exc:
        raise InstallError(f"could not resolve the {IHSANOS_STORE_ALIAS!r} store ref: {exc}") from exc
    actual_ref = _pooler_tenant_ref(dsn, what=f"{ADMIN_CRED_KEY} DSN")
    if actual_ref != expected_ref:
        raise InstallError(
            f"{ADMIN_CRED_KEY} in {path.name} targets project ref {actual_ref!r}, "
            f"expected the {IHSANOS_STORE_ALIAS!r} ref {expected_ref!r} "
            f"(docs/data-store-registry.md) — refusing (TENANT-RESIDENCY-001)"
        )
    return dsn


def write_secret_file(path: Path, text: str) -> Path:
    d = path.parent
    if d.exists():
        mode = stat.S_IMODE(os.stat(d).st_mode)
        if mode & 0o077:
            raise InstallError(f"{d} mode is {oct(mode)} — must be 0700 (it holds credentials)")
    else:
        d.mkdir(mode=0o700)
    path.write_text(text)
    os.chmod(path, 0o600)
    return path


def _role_exists(cur, role: str) -> bool:
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
    return cur.fetchone() is not None


_LOG_CANARY = """
INSERT INTO provisioner_access_log
  (verb, client_key, silo_ref, bundle_sha256, gate_id, reason, status)
VALUES ('provision', 'install_canary', 'selfcheck', '%s', '0',
        'install credential roundtrip', 'started')
RETURNING id;
"""


def _set_role_password(cur, role: str, pw: str) -> None:
    """ALTER ROLE ... PASSWORD cannot take a server-side bind parameter:
    psycopg3 binds server-side and utility statements reject params
    outright (bus #80224 — the live step-2 apply died on exactly this).
    Compose the statement CLIENT-side via sql.Literal (safe quoting, never
    f-string/concat); the composed text carries the password, so it must
    never reach a log or an exception message — say() and InstallError
    texts elsewhere carry names/counts/shas only."""
    cur.execute(psql_sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
        psql_sql.Identifier(role), psql_sql.Literal(pw)))


def step2_log_role(*, apply: bool) -> None:
    substrate = dsn_from_env_file()
    with psycopg.connect(substrate) as conn, conn.cursor() as cur:
        if not _role_exists(cur, "provisioner_log_role"):
            raise InstallError("provisioner_log_role does not exist — run step 1 first")
    if not apply:
        say("step2: [dry-run] would mint provisioner_log_role LOGIN password, "
            "grant SELECT on agent_messages, write " + LOG_ROLE_DSN_FILE.name + " (0600)")
        return
    pw = secrets.token_urlsafe(24)
    with psycopg.connect(substrate) as conn, conn.cursor() as cur:
        _set_role_password(cur, "provisioner_log_role", pw)
        cur.execute("GRANT SELECT ON public.agent_messages TO provisioner_log_role")
        conn.commit()
    dsn = build_role_dsn(substrate, "provisioner_log_role", pw)
    write_secret_file(LOG_ROLE_DSN_FILE, dsn + "\n")
    # prove the credential AS the role (#77536: grant-level checks cannot see reality)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(_LOG_CANARY % ("0" * 64))
        row_id = cur.fetchone()[0]
        cur.execute("UPDATE provisioner_access_log SET status='error', ts_end=now() WHERE id=%s", (row_id,))
        cur.execute("SELECT id FROM agent_messages ORDER BY id DESC LIMIT 1")
        cur.fetchone()
        conn.rollback()
    say(f"step2: provisioner_log_role LOGIN minted; SELECT agent_messages + "
        f"INSERT/UPDATE provisioner_access_log verified as the role (txn rolled back); "
        f"DSN file {LOG_ROLE_DSN_FILE.name} (0600)")


# --- step 3: fleet_provisioner on the store ----------------------------------
def render_store_grants() -> str:
    """The EXACT privilege surface render.py's bundles need — derived from the
    bundle SQL, nothing more:
      CREATEROLE            editor-role create/drop (forward + revert)
      CREATE ON public      content/history/migration_ledger tables, audit fn
      USAGE WITH GRANT OPT  the bundle re-grants USAGE to each editor role
      INSERT/DELETE/REFER.  organizations: org row insert (forward), delete
                            (revert), FK REFERENCES on content/history
      SELECT (id)           the revert DELETE's WHERE — and nothing else:
                            fleet_provisioner must not read tenant org data
    NO ALTER ROLE attribute line: CREATE ROLE's defaults already carry
    NOSUPERUSER/NOBYPASSRLS/NOCREATEDB/NOREPLICATION, and only a superuser
    may issue ALTER ROLE NAMING those attributes even to set them false —
    Supabase's admin is not superuser (bus #80261). The attribute guarantee
    moved to assert_role_not_elevated, a fail-closed post-creation check.
    The negative probes (org-wide SELECT denied, superuser-grant denied) run
    live at install time; the PG17 harness re-proves the whole set with a
    real bundle apply/revert AS this role — admin side exercised AS a
    non-superuser CREATEROLE role mirroring Supabase."""
    return "\n".join([
        "DO $$ BEGIN",
        "  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fleet_provisioner') THEN",
        "    CREATE ROLE fleet_provisioner CREATEROLE NOLOGIN;",
        "  END IF;",
        "END $$;",
        "GRANT USAGE ON SCHEMA public TO fleet_provisioner WITH GRANT OPTION;",
        "GRANT CREATE ON SCHEMA public TO fleet_provisioner;",
        "GRANT INSERT, DELETE, REFERENCES ON TABLE public.organizations TO fleet_provisioner;",
        "GRANT SELECT (id) ON TABLE public.organizations TO fleet_provisioner;",
    ])


def assert_role_not_elevated(cur, role: str) -> None:
    """Fail-closed attribute guarantee replacing the dropped
    `ALTER ROLE ... NOSUPERUSER NOBYPASSRLS NOCREATEDB NOREPLICATION`:
    issuing that ALTER is superuser-only even to set the attributes FALSE
    (bus #80261 — Supabase's admin role is not a superuser), so the check is
    an assertion on pg_roles AFTER creation instead. Catches a drifted
    pre-existing role (the DO block skips creation when it exists) that
    silently carries an elevated attribute a fresh CREATE would never set."""
    row = cur.execute(
        "SELECT rolsuper, rolbypassrls, rolcreatedb, rolreplication "
        "FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    if row is None:
        raise InstallError(f"{role} does not exist after the grants render — refusing")
    attrs = dict(zip(("superuser", "bypassrls", "createdb", "replication"), row))
    if any(row):
        on = ", ".join(name for name, val in attrs.items() if val)
        raise InstallError(f"{role} carries elevated attribute(s): {on} — refusing (fail-closed)")


def render_install_canary() -> str:
    """One transactional sweep of the entire bundle surface, always ROLLED
    BACK — the store is untouched; only the privilege proof survives."""
    return "\n".join([
        "BEGIN;",
        "CREATE TABLE public.__fp_install_canary (org UUID REFERENCES organizations(id), k int);",
        "CREATE ROLE __fp_install_canary NOLOGIN;",
        "DROP ROLE __fp_install_canary;",
        "INSERT INTO organizations (id, name, uen, type, slug, settings)",
        "  VALUES ('00000000-0000-0000-0000-00000000c0de', 'install canary', '',",
        "          'business', 'install-canary', '{\"managed_by\":\"wingmen_provisioner\"}')",
        "  ON CONFLICT (id) DO NOTHING;",
        "DELETE FROM organizations WHERE id = '00000000-0000-0000-0000-00000000c0de';",
        "DROP TABLE public.__fp_install_canary;",
        "ROLLBACK;",
    ])


def step3_store_role(*, apply: bool) -> None:
    admin = read_admin_dsn(ADMIN_CRED_FILE)
    grants_sha12 = hashlib.sha256(render_store_grants().encode()).hexdigest()[:12]
    if not apply:
        with psycopg.connect(admin) as conn, conn.cursor() as cur:
            exists = _role_exists(cur, "fleet_provisioner")
        say(f"step3: [dry-run] grants sha12 {grants_sha12}; fleet_provisioner exists={exists}; "
            f"would apply grants + mint LOGIN + write {STORE_DSN_FILE.name} (0600) + canary txn")
        return
    pw = secrets.token_urlsafe(24)
    with psycopg.connect(admin) as conn, conn.cursor() as cur:
        cur.execute(render_store_grants())
        assert_role_not_elevated(cur, "fleet_provisioner")
        _set_role_password(cur, "fleet_provisioner", pw)
        conn.commit()
    dsn = build_role_dsn(admin, "fleet_provisioner", pw)
    write_secret_file(STORE_DSN_FILE, dsn + "\n")
    # positive: the whole bundle surface in one rolled-back txn, AS the role
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(render_install_canary())
        cur.execute("SELECT to_regclass('public.__fp_install_canary')")
        if cur.fetchone()[0] is not None:
            raise InstallError("canary table still visible after ROLLBACK")
        conn.rollback()
    # negatives, each a single autocommit statement (a denial aborts a txn)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM organizations LIMIT 1")
        cur.fetchall()
        for probe in ("SELECT name FROM organizations LIMIT 1",
                      "GRANT postgres TO fleet_provisioner"):
            cur.execute("BEGIN")
            try:
                cur.execute(probe)
            except psycopg.errors.InsufficientPrivilege:
                conn.rollback()
            else:
                conn.rollback()
                raise InstallError(f"negative probe was ALLOWED: {probe[:60]} — the role is over-privileged")
    say(f"step3: fleet_provisioner granted exact bundle surface (grants sha12 {grants_sha12}); "
        f"canary txn ok (rolled back); org-wide SELECT denied; superuser-grant denied; "
        f"DSN file {STORE_DSN_FILE.name} (0600)")


# --- step 4: launchd + ungated-refusal self-check -----------------------------
def check_ungated_refusal_response(resp) -> str:
    data = json.loads(resp.text)
    if data.get("status") == "error" and data.get("error", {}).get("code") in ("GATE_INVALID", "ACL_DENIED"):
        return data["error"]["code"]
    raise InstallError(f"daemon did not refuse the ungated request: {resp.text[:120]}")


def _plist_pid() -> int | None:
    proc = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[2] == PLIST_LABEL:
            return int(parts[0]) if parts[0].isdigit() else None
    return None


def step4_load_plist(*, apply: bool) -> None:
    if not PLIST.is_file():
        raise InstallError(f"{PLIST.name} not found in launchd/")
    if not apply:
        say(f"step4: [dry-run] would ensure {PLIST_LABEL} is loaded, wait for "
            f"{SOCKET.name}, and require refusal of an ungated request")
        return
    if _plist_pid() is None:
        proc = subprocess.run(["launchctl", "load", "-w", str(PLIST)],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise InstallError(f"launchctl load failed: {(proc.stdout + proc.stderr).strip()[:200]}")
    for _ in range(30):
        if SOCKET.exists():
            break
        time.sleep(0.5)
    else:
        raise InstallError(f"{SOCKET.name} never appeared — check logs/ceayj-provisioner.out.log")
    req = json.dumps({
        "verb": "provision", "client_key": "bariq", "gate_id": "0",
        "reason": "install self-check (ungated refusal)", "caller": "install_ceayj_provisioner"})
    import socket
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(str(SOCKET))
        s.sendall((req + "\n").encode())
        s.shutdown(socket.SHUT_WR)
        resp_text = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            resp_text += chunk
    code = check_ungated_refusal_response(type("R", (), {"text": resp_text.decode()})())
    pid = _plist_pid()
    with psycopg.connect(dsn_from_env_file()) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM provisioner_access_log WHERE gate_id = '0'")
        n = cur.fetchone()[0]
    say(f"step4: daemon pid {pid} ({PLIST_LABEL}) refused the ungated request with {code}; "
        f"self-check audit rows (gate_id='0'): {n}")


_STEPS = {1: step1_substrate_migration, 2: step2_log_role,
          3: step3_store_role, 4: step4_load_plist}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.apply and args.dry_run:
        print("--apply and --dry-run are mutually exclusive", file=sys.stderr)
        return 2
    for n in ([args.step] if args.step else (1, 2, 3, 4)):
        try:
            _STEPS[n](apply=args.apply)
        except InstallError as e:
            say(f"step{n}: FAILED — {e}")
            return 1
    say("install " + ("applied" if args.apply else "dry-run complete"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
