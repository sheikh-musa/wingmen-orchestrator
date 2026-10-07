#!/usr/bin/env python3
"""data_truth.py — the ONE sanctioned lookup for "is this data real or synthetic?"

Musa directive (bus #51657/#51670, 2026-10-05): a single source of truth any
agent consults BEFORE raising or dismissing a data-exposure alarm — "a quran
for agents". Backed by the `data_provenance` table (migration 089, orchestrator
substrate tscuymavysscrvoberrr).

classify() NEVER infers from a project/org/slug NAME. That is the specific
trap bus #51670 surfaced: cosem org 1478c9b2 (slug "demo-academy", no
"-synthetic" suffix) is the org that will hold REAL client data at go-live,
while its CURRENT rows are a synthetic reseed — the opposite of what the slug
suggests. Classification comes only from a `data_provenance` row whose
`evidence` field cites an actual script/commit/migration/bus-message.

FAIL-SAFE DIRECTION (orch-console gate condition #2, bus #51717): an
unregistered project/org returns classification="UNCLASSIFIED", and
treat_as_real() is True for it. Unregistered NEVER defaults to "safe to
treat as synthetic" — the cost of a false data-security alarm is a wasted
investigation; the cost of waving off a REAL data path as synthetic is a
residency violation. Register it (with evidence) or escalate; don't act on
an absence of a row as if it told you anything.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

import psycopg

sys.path.insert(0, os.path.dirname(__file__))
import bus_send  # noqa: E402 — reuse its dburl() so DSN resolution has one owner

UNCLASSIFIED = "UNCLASSIFIED"
REPOS_JSON = Path(__file__).resolve().parent.parent / "REPOS.json"


@dataclass
class Classification:
    project_ref: str
    org_id: str
    registered: bool
    classification: str
    evidence: str | None = None
    owner: str | None = None
    alias: str | None = None
    updated_at: str | None = None

    def treat_as_real(self) -> bool:
        """The one sanctioned safety check for consumers. True for REAL, MIXED,
        MIXED_PENDING_REAL, and UNCLASSIFIED (fail-safe default) — False only
        for a registered, evidenced SYNTHETIC row."""
        return self.classification != "SYNTHETIC"

    def needs_a_second_look(self) -> bool:
        """MIXED / MIXED_PENDING_REAL / UNCLASSIFIED — never auto-treat as settled."""
        return self.classification in ("MIXED", "MIXED_PENDING_REAL", UNCLASSIFIED)


def _from_row(project_ref: str, org_id: str, row: tuple | None) -> Classification:
    """Pure constructor, DB-free — the thing under test for the fail-safe
    contract (no row -> UNCLASSIFIED, never a bare guess)."""
    if row is None:
        return Classification(project_ref=project_ref, org_id=org_id, registered=False, classification=UNCLASSIFIED)
    classification, evidence, owner, alias, updated_at = row
    return Classification(
        project_ref=project_ref, org_id=org_id, registered=True,
        classification=classification, evidence=evidence, owner=owner,
        alias=alias, updated_at=str(updated_at) if updated_at else None,
    )


def _resolve_project_ref(project_ref: str) -> str:
    """Resolve a REPOS.json repo NAME (e.g. "cosem-platform") to its registered
    data-store project_ref (e.g. "ywrpttpxwfcoodovxhsr") -- data_provenance rows
    are keyed by the raw store ref, but a caller reading REPOS.json/AGENTS.md
    for context often only has the repo name in hand, so an exact-match lookup
    silently fell through to UNCLASSIFIED (bus #59033). This is NOT the
    name-inference classify() bars (that's about guessing a CLASSIFICATION from
    a slug) -- it's resolving an identifier via the one authoritative registry
    before the exact-match lookup, same direction as LAYER-VOCAB-001's "name
    the exact store + project ref". A project_ref that's already a store ref,
    or a repo with no registered supabase_project_ref, passes through
    unchanged -- this never invents a ref, only translates a known alias."""
    try:
        repos = json.loads(REPOS_JSON.read_text()).get("repos", [])
    except (OSError, json.JSONDecodeError):
        return project_ref
    for repo in repos:
        if repo.get("name") == project_ref and repo.get("supabase_project_ref"):
            return repo["supabase_project_ref"]
    return project_ref


def classify(project_ref: str, org_id: str | None = None, *, dsn: str | None = None) -> Classification:
    """The one sanctioned lookup. Queries data_provenance via classify_data_provenance().
    org_id=None/'' both mean "store-level default" — matches the table's own
    convention (org_id is NOT NULL DEFAULT '' there, for the same dedup reason)."""
    org_id = org_id or ""
    resolved_ref = _resolve_project_ref(project_ref)
    dsn = dsn or bus_send.dburl(os.environ)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "select classification, evidence, owner, alias, updated_at "
            "from classify_data_provenance(%s, %s)",
            (resolved_ref, org_id),
        )
        row = cur.fetchone()
    return _from_row(resolved_ref, org_id, row)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("classify", help="look up classification for a store/org")
    c.add_argument("project_ref")
    c.add_argument("org_id", nargs="?", default="")
    c.add_argument("--json", action="store_true", help="machine-readable output")

    args = p.parse_args(argv)

    if args.cmd == "classify":
        result = classify(args.project_ref, args.org_id)
        if args.json:
            print(json.dumps(asdict(result)))
            return 0
        org_label = f"/{args.org_id}" if args.org_id else ""
        if not result.registered:
            print(
                f"{UNCLASSIFIED}: {args.project_ref}{org_label} — not in data_provenance. "
                f"Fail-safe: treat_as_real()=True until registered. Register it with evidence "
                f"(script/commit/migration/bus-msg) or escalate — never infer from the name."
            )
            return 1
        flag = " ⚠ needs a second look" if result.needs_a_second_look() else ""
        print(
            f"{result.classification}: {args.project_ref}{org_label} ({result.alias}){flag}\n"
            f"  evidence: {result.evidence}\n"
            f"  owner: {result.owner}  updated_at: {result.updated_at}"
        )
        return 0
    return 2  # pragma: no cover — argparse enforces a valid subcommand


if __name__ == "__main__":
    sys.exit(main())
