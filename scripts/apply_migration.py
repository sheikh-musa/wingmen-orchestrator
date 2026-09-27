#!/usr/bin/env python3
"""apply_migration.py — the ONE generic migration applier (Phase 3 item 3, op#19103).

Replaces the 44 one-off `apply_*.py` scripts (6 of which already write
migration_ledger by hand, 38 of which don't) with a single tool that ALWAYS
ledgers, in the same transaction as the DDL, and refuses rather than guesses
on every ambiguous case. Direct psycopg apply only — NEVER `supabase db push`
(decision 962 / CC-SUBSTRATE-VIEW-INTEGRITY-001-FINDINGS: the CLI's shadow-diff
path silently strips later view arms).

Contract (per the work order, msg 37652 item 3):
  (a) refuses if (repo, sha256) is already ledgered under a DIFFERENT name —
      a migration cannot be renamed and reapplied as if new.
  (b) requires a `-- ledger: silo=<ref>` header line in the .sql — the file
      declares which store it targets; this is checked against --silo before
      anything touches the database (LAYER-VOCAB-001: name the store).
  (c) writes migration_ledger in the SAME transaction as the apply — an
      applied-but-unledgered migration and a ledgered-but-unapplied row are
      both treated as equally wrong.
  (d) never uses `supabase db push` — this module only ever opens a direct
      psycopg connection and runs the file's own SQL.

Privilege-revoke assertions (CAI-RESP-1397 #5, msg 37751): cc-quality
wet-proved that `REVOKE ... FROM x` run as `postgres` against a function
owned by a DIFFERENT role (e.g. `supabase_admin`) is a silent no-op —
`WARNING: no privileges could be revoked`, migration "succeeds", privilege
unchanged. A migration cannot be trusted to have done what it claims
without checking. So:

  -- assert: no_execute <role> <schema.function(arg_types)>
  -- assert: search_path <schema.function(arg_types)>
  -- assert: dropped <schema.function(arg_types)>
  -- assert: no_table_privilege <role> <schema.table> <privilege>

One per line, anywhere in the header. Checked AFTER the SQL body runs,
INSIDE the same transaction, before the ledger insert:
  no_execute         -> has_function_privilege(role, fn, 'EXECUTE') must be FALSE
  search_path        -> pg_proc.proconfig for fn must contain an entry starting
                        'search_path='
  dropped            -> to_regprocedure(fn) IS NULL
  no_table_privilege -> has_table_privilege(role, table, privilege) must be
                        FALSE (table-privilege analogue of no_execute — closes
                        the gap flagged in migration 065's header: a REVOKE on
                        a table is exactly as unverifiable as a REVOKE on a
                        function without this, and this project's
                        pg_default_acl grants anon/authenticated privileges on
                        every NEW table implicitly, so "this migration touches
                        no privilege" is never a valid reason to skip it for a
                        migration that creates a table)
Any assertion failing -> ROLLBACK, refuse, name the exact (kind, args) pair
that failed. --dry-run runs the assertions too (still rolls back either
way) so a preview genuinely previews whether a real apply would pass.
An unrecognized assert kind refuses before the SQL body ever runs.

REQUIRED-ness: if the SQL body contains `REVOKE` or `DROP FUNCTION`
(case-insensitive) and the file has ZERO `-- assert:` lines, this tool
refuses to apply at all — a revoke/drop with no way to check it happened
is exactly the defect CAI-RESP-1397 #5 closes. `REVOKE` covers both table
and function privileges, so a table-privilege REVOKE with zero asserts is
refused identically to a function one.

Usage:
  python scripts/apply_migration.py <NNN|path> --silo <ref> [--dsn <url>] [--dry-run]
  python scripts/apply_migration.py <NNN|path> --silo <ref> --gate <agent_messages id> [--gate-dsn <url>]
  python scripts/apply_migration.py <NNN|path> --silo <ref> --status

<NNN|path>: either a numeric prefix (looked up as migrations/<NNN>_*.sql) or
an explicit path to a .sql file (tests pass explicit paths).

--dsn defaults to $DATABASE_URL. --silo is a residency assertion, checked
against BOTH the file's `-- ledger:` header and the resolved DSN (the ref
string must appear in the DSN) before anything runs — a mismatch on either
side refuses.

--dry-run runs the migration body AND every `-- assert:` check inside a
transaction, verifies both apply/pass cleanly, then ROLLS BACK (no ledger
row written, nothing committed) — EXCEPT for sequence object state.
`setval()`/`ALTER SEQUENCE ... RESTART|SET` are never transactional in
Postgres — ROLLBACK cannot undo them, no matter how correctly the
surrounding transaction is wrapped. Found live (bus #44213/#44214): a
--dry-run of migration 075 permanently resynced the substrate's
operator_backlog_id_seq, with zero migration_ledger row for it — a
"dry_run_ok" that was actually a real, unledgered change. A migration whose
body (comments stripped) contains one of these is refused on --dry-run
outright (same shape as the REVOKE/DROP-without-assert refusal below) —
use a real --gate'd apply or a scratch/shadow DB instead. `nextval()` and
ordinary inserts into an identity/serial column are NOT refused: those only
ever advance a sequence forward and leave a harmless gap, which is normal,
expected behavior for every serial/identity column in this fleet.
--status only reads migration_ledger; touches nothing else.

Gate enforcement (op#22669/op#22521 item 3, orch-console ruling bus #43869):
a fork once applied migration 072 straight to the live substrate with no
review at all — an agent's self-promise to "always gate a live apply"
does not survive a context reset, so this is enforced here in code instead.
A non-dry-run apply against a PRODUCTION_SILOS member (docs/data-store-
registry.md) now REQUIRES `--gate <agent_messages id>`. That row (looked
up on the fleet bus, NOT necessarily the target --dsn — see --gate-dsn)
must be:
  - message_type = 'decision'
  - from_agent an allowlisted gate owner for THIS silo (GATE_OWNERS below)
  - created_at within GATE_MAX_AGE_DAYS of now (staleness floor, NOT a
    file-mtime comparison — orch-console bus #43974: the normal flow is
    gate-on-the-bus, THEN `git pull` on the target host, THEN apply, and a
    pull/checkout resets the file's mtime to "now", which would make even a
    same-day gate look like it predates the file it authorizes and get
    refused. An absolute age floor on the gate row itself has no such
    false-positive.)
  - body contains the file's sha256 prefix (first 12 hex chars) — ties the
    authorization to this exact content, not just the filename
Any failure refuses loudly before the target DB is ever touched. The gate
row's id is recorded in migration_ledger.note. --dry-run and any silo NOT
in PRODUCTION_SILOS (e.g. a test harness's throwaway DSN) stay ungated.

--repo defaulting (bus #44358): a cosem-platform lane once applied a migration
to ywrpttpxwfcoodovxhsr (its own production silo) without passing --repo, and
this tool silently ledgered it under DEFAULT_REPO ('orchestrator') — a real
mislabel, "fixed" by hand-running an UPDATE on migration_ledger that bypassed
this tool entirely. Now: for any PRODUCTION_SILOS member other than the
orchestrator substrate itself, --repo must be given explicitly, or be
derivable from the migration file's own git checkout (its `origin` remote
matched against REPOS.json — NOT the checkout directory's own basename, which
is frequently an unrelated lane/worktree nickname; see derive_repo_from_path).
Failing both, the apply refuses before touching the DSN. The resolved
(repo, source) is always printed — on --status, --dry-run, and a real apply —
so it is visible before anything is written. See resolve_repo().

Known gaps (documented per orch-console bus #43974, filed as P2 follow-ups —
NOT blockers for this change):
  a. Forgeability: agent_messages.from_agent is not authenticated
     (posted_by_identity is 'postgres' for every row; from_agent_verified is
     NULL). Any body holding gate_dsn's credentials can write a row "from
     orch-console". This stops a MISREADING fork (the op#22669 failure mode),
     not a deliberate forger. Follow-up: gate rows carry an HMAC over
     (gate id, sha12, silo) with a vault key only the gate owners can read,
     and check_gate() verifies it.
  b. Coverage: this tool IS usable cross-repo today (any repo can invoke it
     with an explicit file path + --repo <name> + --silo <ref> + its own
     --dsn — cosem-platform lanes have done exactly this against
     ywrpttpxwfcoodovxhsr) — the gap is non-enforcement, not impossibility:
     nothing stops a lane from running `psql -f`/a raw DSN directly INSTEAD
     of this tool, bypassing --gate entirely (confirmed live, bus #44135: a
     cosem-platform apply landed on ywrpttpxwfcoodovxhsr with a gate row on
     record saying to use this tool, but not through it — no ledger row, no
     gate check). Orch-console correction, bus #44140. Mitigated (detect,
     not prevent) by scripts/ddl_coverage_watchdog.py, which pages on a
     silo's schema fingerprint drifting with no matching new
     migration_ledger row. Real fix (still open, tracked P2, bus #44140
     Phase 2): make this tool the only path a lane's credentials CAN use —
     revoke DDL-capable ownership from the lane's normal connection role per
     production silo, and have this tool fetch a separate migrator role's
     DSN from a vault only after check_gate() passes.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT / "migrations"
REPOS_JSON = ROOT / "REPOS.json"
DEFAULT_REPO = "orchestrator"
ORCHESTRATOR_SUBSTRATE_SILO = "tscuymavysscrvoberrr"  # the orchestrator substrate (the monolith)

_LEDGER_HEADER_RE = re.compile(r"^--\s*ledger:\s*silo=(\S+)\s*$", re.MULTILINE)
# BEGIN/COMMIT are stripped (we supply our own transaction). Any OTHER top-level
# transaction-control statement is refused outright rather than silently dropped —
# a migration that ROLLBACKs or nests transactions is not safe to auto-wrap.
_STRIPPABLE_TXN_CTL = {"BEGIN;", "COMMIT;"}
_FORBIDDEN_TXN_CTL = {"ROLLBACK;", "START TRANSACTION;", "END TRANSACTION;", "END;"}

# Dollar-quote delimiter: `$$` or `$tag$` (tag = identifier). Used to keep the
# top-level transaction-control scan out of function/procedure bodies — a
# bare `end;` closing a plpgsql BEGIN...END block is completely idiomatic
# and is NOT top-level transaction control (fleet-shared bug, cc-cosem-exams
# 2026-09-06: strip_txn_control false-rejected every plpgsql migration whose
# function body ends on its own "end;" line).
_DOLLAR_TAG_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")

# `-- assert: <kind> <args...>` header lines (CAI-RESP-1397 #5).
_ASSERT_RE = re.compile(r"^--\s*assert:\s*(\S+)\s+(.+?)\s*$", re.MULTILINE)
_ASSERT_KINDS = {"no_execute", "search_path", "dropped", "no_table_privilege"}
# Case-insensitive: a migration doing either of these MUST carry an assertion
# (CAI-RESP-1397 #5) — a silent no-op REVOKE/DROP is otherwise indistinguishable
# from a real one.
_REQUIRES_ASSERT_RE = re.compile(r"\bREVOKE\b|\bDROP\s+FUNCTION\b", re.IGNORECASE)

# setval()/ALTER SEQUENCE ... RESTART|SET reposition a sequence's current
# value directly -- the dangerous class (bus #44213/#44214): a --dry-run
# containing one of these is NOT actually rolled back (see module docstring)
# and can silently move a sequence BACKWARD, causing future duplicate-key
# failures. `[^;]*` bounds the ALTER SEQUENCE scan to one statement (a
# semicolon ends it) without needing a full statement splitter.
# nextval() is deliberately NOT matched here -- it only ever advances a
# sequence forward (a harmless gap), the same as any ordinary insert into an
# identity/serial column, and is documented above instead of refused.
_SEQUENCE_MUTATION_RE = re.compile(
    r"\bsetval\s*\(|\bALTER\s+SEQUENCE\b[^;]*\b(?:RESTART|SET)\b", re.IGNORECASE
)


# docs/data-store-registry.md — every currently-provisioned production store
# (LAYER-VOCAB-001 refs). A silo NOT in this set (e.g. a test harness's
# throwaway DSN) is not gated — there is nothing in the registry to protect,
# and the ephemeral-PG17 migration tests must stay gate-free by construction.
PRODUCTION_SILOS = frozenset({
    ORCHESTRATOR_SUBSTRATE_SILO,
    "ceayjeamtmcyzzvqflus",  # ihsanos multi-tenant DB
    "goumlynecruxrlmzlntp",  # irsyad silo (goumlyne)
    "brrgastulcffamlbggyu",  # wingmen-personal
    "ywrpttpxwfcoodovxhsr",  # cosem-platform PRODUCTION store — holds the REAL
                             # ADCDA org 1478c9b2 gov-PII (CAI-RESP-1340), NOT
                             # demo/dev data (a label calling it dev is exactly
                             # how someone relaxes this gate later — orch-console
                             # bus #43974)
})

# Which bus identities may author a --gate decision row, per silo. Every known
# store defaults to the fleet's two governance/gate-holding bodies (orch-console
# holds the standing gate pens; money/governance-class decisions route through
# cai per fleet doctrine) until a stricter, silo-specific owner is ratified.
# Narrowing one silo is a one-line change here, not a redesign — do it in a
# bus-ratified PR, same discipline as tests/migrations/test_migration_number_collisions.py's
# allowlist.
_DEFAULT_GATE_OWNERS = frozenset({"orch-console", "cai"})
GATE_OWNERS: dict[str, frozenset[str]] = {silo: _DEFAULT_GATE_OWNERS for silo in PRODUCTION_SILOS}

_GATE_SHA_PREFIX_LEN = 12
GATE_MAX_AGE_DAYS = 14  # staleness floor on the gate row itself (see module docstring)


class Refuse(Exception):
    """Raised for any condition that must stop the apply before it mutates anything."""


def resolve_migration_path(arg: str, migrations_dir: Path = MIGRATIONS_DIR) -> Path:
    """A literal existing path wins; otherwise treat arg as a numeric prefix."""
    literal = Path(arg)
    if literal.exists():
        return literal
    if not re.fullmatch(r"\d+", arg):
        raise Refuse(f"not a path and not a bare numeric prefix: {arg!r}")
    matches = sorted(migrations_dir.glob(f"{arg}_*.sql"))
    if not matches:
        raise Refuse(f"no migrations/{arg}_*.sql found under {migrations_dir}")
    if len(matches) > 1:
        raise Refuse(f"ambiguous prefix {arg!r} — matches {[m.name for m in matches]}")
    return matches[0]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_ledger_header(sql_text: str) -> str:
    """Return the declared silo ref from a `-- ledger: silo=<ref>` line, or refuse."""
    m = _LEDGER_HEADER_RE.search(sql_text)
    if not m:
        raise Refuse(
            "missing required '-- ledger: silo=<ref>' header line — every migration "
            "this tool applies must declare its target store."
        )
    return m.group(1)


def parse_assert_lines(sql_text: str) -> list[dict]:
    """Parse every `-- assert: <kind> <args>` header line. Refuses immediately
    (before anything runs) on an unrecognized kind or malformed no_execute line."""
    assertions = []
    for kind, rest in _ASSERT_RE.findall(sql_text):
        if kind not in _ASSERT_KINDS:
            raise Refuse(
                f"unknown assert kind {kind!r} (line: '-- assert: {kind} {rest}') — "
                f"expected one of {sorted(_ASSERT_KINDS)}."
            )
        if kind == "no_execute":
            parts = rest.split(None, 1)
            if len(parts) != 2:
                raise Refuse(f"malformed 'assert: no_execute' line — expected '<role> <function>': {rest!r}")
            role, fn = parts
            assertions.append({"kind": kind, "role": role, "fn": fn})
        elif kind == "no_table_privilege":
            parts = rest.split()
            if len(parts) != 3:
                raise Refuse(
                    f"malformed 'assert: no_table_privilege' line — expected "
                    f"'<role> <schema.table> <privilege>': {rest!r}"
                )
            role, table, priv = parts
            assertions.append({"kind": kind, "role": role, "table": table, "priv": priv})
        else:
            assertions.append({"kind": kind, "fn": rest})
    return assertions


def check_required_assertions(body: str, assertions: list[dict]) -> None:
    """CAI-RESP-1397 #5: a REVOKE or DROP FUNCTION with zero assertions refuses."""
    if _REQUIRES_ASSERT_RE.search(body) and not assertions:
        raise Refuse(
            "revoke/drop migration without post-apply assertions — CAI-RESP-1397 #5. "
            "Add a '-- assert: no_execute|search_path|dropped ...' header line for "
            "every (role, function) the REVOKE/DROP is supposed to affect."
        )


def _assertion_label(a: dict) -> str:
    if a["kind"] == "no_execute":
        return f"no_execute {a['role']} {a['fn']}"
    if a["kind"] == "no_table_privilege":
        return f"no_table_privilege {a['role']} {a['table']} {a['priv']}"
    return f"{a['kind']} {a['fn']}"


def _check_assertion(cur, a: dict) -> tuple[bool, str]:
    """Runs one assertion against the DB (inside the caller's transaction).
    Returns (passed, detail-if-failed)."""
    label = _assertion_label(a)
    if a["kind"] == "dropped":
        cur.execute("SELECT to_regprocedure(%s) IS NULL", (a["fn"],))
        ok = cur.fetchone()[0]
        return ok, "" if ok else f"{label} — function still exists (to_regprocedure resolved it)"

    if a["kind"] == "no_execute":
        cur.execute("SELECT to_regprocedure(%s)", (a["fn"],))
        oid = cur.fetchone()[0]
        if oid is None:
            return False, f"{label} — function does not exist; cannot assert a privilege on it"
        cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (a["role"], a["fn"]))
        has_exec = cur.fetchone()[0]
        ok = not has_exec
        return ok, "" if ok else f"{label} — {a['role']} STILL has EXECUTE (the REVOKE was a silent no-op)"

    if a["kind"] == "search_path":
        cur.execute("SELECT proconfig FROM pg_proc WHERE oid = to_regprocedure(%s)", (a["fn"],))
        row = cur.fetchone()
        if row is None or row[0] is None:
            return False, f"{label} — function missing, or has no proconfig (no search_path pinned)"
        proconfig = row[0]
        ok = any(str(c).startswith("search_path=") for c in proconfig)
        return ok, "" if ok else f"{label} — proconfig has no search_path= entry: {proconfig}"

    if a["kind"] == "no_table_privilege":
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (a["table"],))
        exists = cur.fetchone()[0]
        if not exists:
            return False, f"{label} — table does not exist; cannot assert a privilege on it"
        cur.execute("SELECT has_table_privilege(%s, %s, %s)", (a["role"], a["table"], a["priv"]))
        has_priv = cur.fetchone()[0]
        ok = not has_priv
        return ok, "" if ok else (
            f"{label} — {a['role']} STILL has {a['priv']} (the REVOKE was a silent no-op, "
            f"or this project's pg_default_acl re-grants it on every new table)"
        )

    raise Refuse(f"unknown assert kind at check time: {a['kind']!r}")  # pragma: no cover — parse_assert_lines already refused


def run_assertions(cur, assertions: list[dict]) -> list[dict]:
    """Runs every assertion; raises Refuse naming the FIRST failure. Returns a
    results list (all-pass) for the caller to report on --dry-run / success."""
    results = []
    for a in assertions:
        ok, detail = _check_assertion(cur, a)
        results.append({**a, "passed": ok})
        if not ok:
            raise Refuse(f"post-apply assertion FAILED: {detail}")
    return results


def _dollar_quote_spans(sql_text: str) -> list[tuple[int, int]]:
    """Character-offset (start, end) spans covering every dollar-quoted region
    (`$$...$$`, `$tag$...$tag$`), delimiters included. A tag only closes a
    region opened with the SAME tag — a different tag encountered while
    already inside one is just literal text (this is exactly what named
    tags are for in real SQL: letting one dollar-quoted string contain
    another tag's delimiter as plain content) and does not toggle state.
    Does NOT try to understand ordinary '...'-quoted string literals — a
    literal `$$` inside a single-quoted string is a known, accepted gap
    (vanishingly rare in migration SQL; a real SQL tokenizer is out of
    proportion here)."""
    spans = []
    open_tag = None
    open_start = None
    for m in _DOLLAR_TAG_RE.finditer(sql_text):
        tag = m.group(1) or ""
        if open_tag is None:
            open_tag = tag
            open_start = m.start()
        elif tag == open_tag:
            spans.append((open_start, m.end()))
            open_tag = None
            open_start = None
        # else: a different tag while already inside one — literal content, ignore.
    return spans


def _line_fully_inside_any_span(line_start: int, line_end: int, spans: list[tuple[int, int]]) -> bool:
    return any(s <= line_start and line_end <= e for s, e in spans)


def strip_txn_control(sql_text: str) -> str:
    """Strip top-level BEGIN/COMMIT so a self-committing migration can't escape our
    own transaction and ledger-atomicity guarantee (see the self-committing-migration
    finding: a `\\i`-ed inner BEGIN/COMMIT otherwise breaks caller ROLLBACK). Any other
    top-level transaction-control statement (ROLLBACK, nested BEGIN, ...) refuses.

    Dollar-quote-aware: a line that falls entirely inside a $$...$$/$tag$...$tag$
    region (e.g. a plpgsql function body's bare `end;` closing its BEGIN block)
    is left untouched regardless of its content — it is not top-level SQL at
    all, so it can never be the top-level BEGIN/COMMIT/ROLLBACK this guard
    exists to catch."""
    spans = _dollar_quote_spans(sql_text)
    kept = []
    pos = 0
    for ln in sql_text.splitlines(keepends=True):
        raw = ln[:-1] if ln.endswith("\n") else ln
        line_start, line_end = pos, pos + len(raw)
        pos += len(ln)

        if _line_fully_inside_any_span(line_start, line_end, spans):
            kept.append(raw)
            continue

        token = raw.strip().upper()
        if token in _STRIPPABLE_TXN_CTL:
            continue
        if token in _FORBIDDEN_TXN_CTL:
            raise Refuse(f"top-level transaction-control statement survived the strip: {raw!r}")
        kept.append(raw)
    return "\n".join(kept)


def strip_sql_comments(sql_text: str) -> str:
    """Drop full-line `--` comments before scanning a migration body for a
    dangerous statement pattern (_SEQUENCE_MUTATION_RE) -- a commented-out
    `-- setval(...)` must not trigger the dry-run refusal below. Reuses the
    same dollar-quote-span awareness as strip_txn_control so a `--` that is
    only literal content inside a function body is left alone rather than
    (wrongly) treated as a comment start. Not a general SQL comment
    stripper -- e.g. a trailing `-- comment` after real SQL on the same line
    is left in place, since this only needs to stop a WHOLE-LINE comment
    from matching."""
    spans = _dollar_quote_spans(sql_text)
    kept = []
    pos = 0
    for ln in sql_text.splitlines(keepends=True):
        raw = ln[:-1] if ln.endswith("\n") else ln
        line_start, line_end = pos, pos + len(raw)
        pos += len(ln)
        if not _line_fully_inside_any_span(line_start, line_end, spans) and raw.lstrip().startswith("--"):
            continue
        kept.append(raw)
    return "\n".join(kept)


def check_dry_run_sequence_safety(body: str, dry_run: bool) -> None:
    """Postgres sequence state is never transactional -- ROLLBACK cannot undo
    a setval()/ALTER SEQUENCE ... RESTART|SET no matter how correctly the
    surrounding transaction is wrapped (see module docstring; bus
    #44213/#44214). Refusing this on --dry-run is the same shape as
    check_required_assertions' REVOKE/DROP refusal: a "preview" that can
    silently move a sequence backward under a "dry_run_ok" banner is exactly
    the printed-safeguard-not-a-ran-safeguard defect class."""
    if dry_run and _SEQUENCE_MUTATION_RE.search(strip_sql_comments(body)):
        raise Refuse(
            "this migration mutates sequence state directly (setval(...) or "
            "ALTER SEQUENCE ... RESTART/SET) -- Postgres sequences are NOT "
            "transactional, so --dry-run's ROLLBACK cannot undo this the way "
            "it undoes ordinary DML/DDL (bus #44213/#44214). A 'dry_run_ok' "
            "here would be a real, permanent, unledgered live change. Use a "
            "real --gate'd apply instead, or test against a scratch/shadow "
            "DB. (nextval()/plain inserts into an identity or serial column "
            "are fine under --dry-run -- those only ever leave a harmless "
            "gap.)"
        )


def check_residency(dsn: str, silo: str, header_silo: str) -> None:
    if header_silo != silo:
        raise Refuse(
            f"--silo {silo!r} does not match the file's '-- ledger: silo={header_silo}' header "
            f"— refusing (residency guard)."
        )
    if silo not in dsn:
        raise Refuse(f"resolved DSN does not contain silo ref {silo!r} — refusing (residency guard).")


def check_ledger_collision(cur, repo: str, name: str, silo: str, sha: str) -> str:
    """Returns 'new' | 'already_applied'. Raises Refuse on any drift/rename case."""
    cur.execute(
        "SELECT migration_name, sha256 FROM migration_ledger WHERE repo=%s AND silo_ref=%s AND sha256=%s",
        (repo, silo, sha),
    )
    same_content = cur.fetchall()
    for other_name, _ in same_content:
        if other_name != name:
            raise Refuse(
                f"content sha256={sha[:12]}… is already ledgered as {other_name!r} in {silo} — "
                f"a migration cannot be renamed and reapplied as new."
            )

    cur.execute(
        "SELECT sha256 FROM migration_ledger WHERE repo=%s AND silo_ref=%s AND migration_name=%s",
        (repo, silo, name),
    )
    row = cur.fetchone()
    if row is None:
        return "new"
    existing_sha = row[0]
    if existing_sha != sha:
        raise Refuse(
            f"{name} is already ledgered in {silo} with sha256={existing_sha[:12]}… but the file on "
            f"disk hashes to {sha[:12]}… — the applied migration and the tracked file have drifted; "
            f"refusing rather than guessing which is truth."
        )
    return "already_applied"


def check_gate(gate_dsn: str, gate_id: int, *, silo: str, path: Path, sha: str) -> dict:
    """Looks up agent_messages id=gate_id on the FLEET BUS (gate_dsn — not
    necessarily the target silo's --dsn) and refuses unless it is a valid,
    silo-scoped authorization for applying THIS exact file content. Returns
    the row (as a dict) on success, for the caller to ledger."""
    owners = GATE_OWNERS.get(silo)
    if not owners:
        raise Refuse(f"no gate-owner allowlist configured for silo {silo!r} — refusing rather than guessing.")

    with psycopg.connect(gate_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, from_agent, message_type, body, created_at FROM agent_messages WHERE id = %s",
            (gate_id,),
        )
        row = cur.fetchone()

    if row is None:
        raise Refuse(f"--gate {gate_id} does not exist in agent_messages (checked via gate DSN).")

    row_id, from_agent, mtype, gate_body, created_at = row

    if mtype != "decision":
        raise Refuse(f"--gate {gate_id} is message_type={mtype!r}, not 'decision' — not an authorization.")

    if from_agent not in owners:
        raise Refuse(
            f"--gate {gate_id} is from {from_agent!r}, not an allowlisted gate owner for "
            f"silo {silo!r} ({sorted(owners)})."
        )

    age = datetime.now(timezone.utc) - created_at
    if age.days > GATE_MAX_AGE_DAYS:
        raise Refuse(
            f"--gate {gate_id} (created_at={created_at}) is {age.days}d old, over the "
            f"{GATE_MAX_AGE_DAYS}d staleness floor — get a fresh decision for {path.name}."
        )

    sha_prefix = sha[:_GATE_SHA_PREFIX_LEN]
    if sha_prefix not in (gate_body or ""):
        raise Refuse(
            f"--gate {gate_id}'s body does not contain {path.name}'s sha256 prefix ({sha_prefix}) "
            f"— it does not authorize THIS exact content."
        )

    return {"id": row_id, "from_agent": from_agent, "created_at": created_at}


def _normalize_git_remote(url: str) -> str:
    url = url.strip()
    if url.endswith(".git"):
        url = url[:-4]
    return url.rstrip("/").lower()


def _repo_registry() -> list[dict]:
    try:
        return json.loads(REPOS_JSON.read_text()).get("repos", [])
    except (OSError, json.JSONDecodeError):
        return []


def derive_repo_from_path(path: Path) -> str | None:
    """Walk up from `path` to the enclosing git checkout, read its `origin`
    remote, and match it against REPOS.json's `github` field to recover the
    CANONICAL repo name — never the checkout directory's own basename, which
    is frequently an unrelated lane/worktree nickname. Confirmed live (bus
    #44358's root cause file): it sat under a `cosem-port-lane` WORKTREE
    whose `origin` is github.com/sheikh-musa/cosem-platform — a basename
    guess would have ledgered `repo='cosem-port-lane'`, a name that doesn't
    even exist in REPOS.json, which is exactly as wrong as the
    repo='orchestrator' default this function replaces. Returns None (never
    guesses) if there's no enclosing checkout, no origin remote, or no
    REPOS.json entry matches it — the caller refuses rather than falling
    back to a heuristic name.
    """
    try:
        here = path.resolve().parent
    except OSError:
        return None
    for candidate in (here, *here.parents):
        if not (candidate / ".git").exists():
            continue
        try:
            proc = subprocess.run(
                ["git", "-C", str(candidate), "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        origin = _normalize_git_remote(proc.stdout)
        for entry in _repo_registry():
            github = entry.get("github")
            if github and _normalize_git_remote(github) == origin:
                return entry.get("name")
        return None
    return None


def resolve_repo(*, silo: str, repo: str | None, path: Path) -> tuple[str, str]:
    """Returns (resolved_repo, source) where source is 'explicit', 'derived',
    or 'default'. bus #44358: `--repo` silently defaulted to DEFAULT_REPO
    ('orchestrator') even when --silo targeted a completely different repo's
    production store — a cosem-platform migration applied to
    ywrpttpxwfcoodovxhsr got ledgered under repo='orchestrator', and the fix
    was a hand-run UPDATE on migration_ledger that bypassed this tool
    entirely. Refuse rather than repeat that: for any PRODUCTION_SILOS
    member OTHER than the orchestrator substrate itself, repo must be given
    explicitly or derivable from the migration's own git checkout (see
    derive_repo_from_path) — DEFAULT_REPO is never used by fallthrough for
    those silos. Scoped to PRODUCTION_SILOS (mirrors the existing --gate
    scoping immediately below) rather than every non-substrate silo, so the
    ephemeral PG17 test harness's throwaway silo refs stay friction-free by
    construction, same as they already are for --gate.
    """
    if repo is not None:
        return repo, "explicit"
    if silo == ORCHESTRATOR_SUBSTRATE_SILO or silo not in PRODUCTION_SILOS:
        return DEFAULT_REPO, "default"
    derived = derive_repo_from_path(path)
    if derived:
        return derived, "derived"
    raise Refuse(
        f"--silo {silo!r} is a production store other than the orchestrator substrate "
        f"({ORCHESTRATOR_SUBSTRATE_SILO}) and no --repo was given — refusing to silently "
        f"default repo={DEFAULT_REPO!r} (bus #44358: this exact default once mislabeled a "
        f"cosem-platform migration's ledger row). Pass --repo <name> explicitly, or run "
        f"this from within {path.name}'s own repo checkout so it can be derived from "
        f"'git remote get-url origin' against REPOS.json."
    )


def apply_migration(
    dsn: str,
    path: Path,
    *,
    silo: str,
    repo: str | None = None,
    dry_run: bool = False,
    applied_by: str = "apply_migration.py",
    gate: int | None = None,
    gate_dsn: str | None = None,
) -> dict:
    repo, repo_source = resolve_repo(silo=silo, repo=repo, path=path)
    sql_text = path.read_text()
    header_silo = parse_ledger_header(sql_text)
    check_residency(dsn, silo, header_silo)
    assertions = parse_assert_lines(sql_text)
    sha = file_sha256(path)
    body = strip_txn_control(sql_text)
    check_required_assertions(body, assertions)
    check_dry_run_sequence_safety(body, dry_run)

    note = None
    if not dry_run and silo in PRODUCTION_SILOS:
        if gate is None:
            raise Refuse(
                f"silo {silo!r} is a production store (docs/data-store-registry.md) — a non-dry-run "
                f"apply requires --gate <agent_messages id> (op#22669/#22521 item 3, bus #43869)."
            )
        if not gate_dsn:
            raise Refuse("--gate given but no gate DSN resolved — pass --gate-dsn or set --gate-dsn-env's variable.")
        gate_row = check_gate(gate_dsn, gate, silo=silo, path=path, sha=sha)
        note = f"gate={gate_row['id']} from={gate_row['from_agent']}"

    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        state = check_ledger_collision(cur, repo, path.name, silo, sha)
        if state == "already_applied":
            conn.rollback()
            return {
                "status": "already_applied", "migration": path.name, "silo": silo,
                "sha256": sha, "repo": repo, "repo_source": repo_source,
            }

        cur.execute(body)

        try:
            results = run_assertions(cur, assertions)
        except Refuse:
            conn.rollback()
            raise

        cur.execute(
            """INSERT INTO migration_ledger (repo, migration_name, silo_ref, sha256, applied_by, note)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (repo, path.name, silo, sha, applied_by, note),
        )

        if dry_run:
            conn.rollback()
            return {
                "status": "dry_run_ok", "migration": path.name, "silo": silo,
                "sha256": sha, "assertions": results, "repo": repo, "repo_source": repo_source,
            }

        conn.commit()
        return {
            "status": "applied", "migration": path.name, "silo": silo,
            "sha256": sha, "assertions": results, "note": note,
            "repo": repo, "repo_source": repo_source,
        }


def status(dsn: str, path: Path, *, silo: str, repo: str = DEFAULT_REPO) -> dict | None:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT migration_name, silo_ref, sha256, applied_at, applied_by, note
               FROM migration_ledger WHERE repo=%s AND silo_ref=%s AND migration_name=%s""",
            (repo, silo, path.name),
        )
        row = cur.fetchone()
    if row is None:
        return None
    cols = ["migration_name", "silo_ref", "sha256", "applied_at", "applied_by", "note"]
    return dict(zip(cols, row))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("migration", help="numeric prefix (NNN) or explicit path to a .sql file")
    p.add_argument("--silo", required=True, help="project ref this migration must target (residency guard)")
    p.add_argument(
        "--repo", default=None,
        help=(
            f"repo name recorded in migration_ledger. Defaults to {DEFAULT_REPO!r} only when "
            f"--silo is the orchestrator substrate ({ORCHESTRATOR_SUBSTRATE_SILO}); for any "
            f"other PRODUCTION_SILOS member this must be given explicitly, or be derivable "
            f"from the migration file's own git checkout (bus #44358) — refuses rather than "
            f"silently defaulting to a different repo's name."
        ),
    )
    p.add_argument("--dsn", default=None, help="defaults to $DATABASE_URL")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--status", action="store_true")
    p.add_argument(
        "--gate", type=int, default=None,
        help="agent_messages id authorizing a non-dry-run apply to a production silo (op#22669 item 3)",
    )
    p.add_argument("--gate-dsn", default=None, help="DSN to look up --gate on (the fleet bus); overrides --gate-dsn-env")
    p.add_argument(
        "--gate-dsn-env", default="DATABASE_URL",
        help="env var to read the gate-lookup DSN from when --gate-dsn is not given (default: DATABASE_URL)",
    )
    args = p.parse_args(argv)

    dsn = args.dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        print("✗ no DSN: pass --dsn or set DATABASE_URL", file=sys.stderr)
        return 2
    gate_dsn = args.gate_dsn or os.environ.get(args.gate_dsn_env)

    try:
        path = resolve_migration_path(args.migration)
        if args.status:
            repo, repo_source = resolve_repo(silo=args.silo, repo=args.repo, path=path)
            print(f"  repo: {repo} ({repo_source})")
            row = status(dsn, path, silo=args.silo, repo=repo)
            if row is None:
                print(f"NOT LEDGERED: {path.name} in {args.silo}")
                return 1
            print(f"LEDGERED: {row}")
            return 0

        result = apply_migration(
            dsn, path, silo=args.silo, repo=args.repo, dry_run=args.dry_run,
            gate=args.gate, gate_dsn=gate_dsn,
        )
        print(
            f"✓ {result['status']}: {result['migration']} ({args.silo}, "
            f"repo={result['repo']} [{result['repo_source']}], sha256 {result['sha256'][:12]}…)"
        )
        if result.get("note"):
            print(f"  ledger note: {result['note']}")
        for a in result.get("assertions") or []:
            print(f"  ✓ assert {_assertion_label(a)}")
        return 0
    except Refuse as e:
        print(f"✗ REFUSE (did not apply): {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
