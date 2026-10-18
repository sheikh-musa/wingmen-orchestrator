#!/usr/bin/env python3
"""gate_validation.py — hc#95 exec-broker re-validation (contract §4).

Re-checks, independently of exec_prod's own local screen, whenever the inner
SQL is DDL-shaped:
  1. project_ref is one of the 3 allowlisted irsyad refs (exact string).
  2. gate_id is present, well-formed, and the fleet-bus row it names is a real
     console/cai GO (message_type decision/agreed), in the past, and mentions
     the target silo.

Rules vendored from ihsanos-irsyad main's scripts/db/exec_prod.py (sha256
9bc04456e03430f500ce6748524208c49c3eccd394f14cc7239e70f2af348a03, commit
5053826e — same commit the socket contract itself is frozen at): _GATE_AGENTS,
_GATE_TYPES, _SILO_ALIASES, and the whole-word silo-mention check are ported
verbatim in logic from that file's _require_gate/_text_mentions_silo. Re-sync
by re-reading that file's current state and updating this pin if it changes.

KNOWN GAP (flagged to orch-console, bus #70870, unresolved as of writing): the
frozen §2 wire request has no migration/pr field, so exec_prod._require_gate's
"digit-boundary PR/migration match" sub-check cannot be re-performed here from
the wire data alone. require_gate() accepts optional migration/pr kwargs and
will apply that check if either is ever passed a non-None value by a caller —
today's server.py always calls it with both left at their None default, so
only the sub-check that needs them is skipped; every other sub-check below
still runs.
"""
from __future__ import annotations

import re

from .ddl_detect import is_ddl_shaped
from .protocol import ErrorCode


class BrokerValidationError(Exception):
    """A §4 re-validation failure. Always carries the exact §5 error code the
    wire response must report."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


_GATE_AGENTS = {"orch-console", "cai"}
_GATE_TYPES = {"decision", "agreed"}

# alias -> project ref (vendored from exec_prod.py's _SILO_ALIASES).
SILO_ALIASES = {
    "goumlyne": "goumlynecruxrlmzlntp",
    "ceayj": "ceayjeamtmcyzzvqflus",
    "qa": "oovskwogvefnznfowjky",
    "irsyad-qa": "oovskwogvefnznfowjky",
}
ALLOWED_PROJECT_REFS = set(SILO_ALIASES.values())


def check_project_ref(project_ref: str) -> None:
    """§4.1 — exact-string allowlist of the 3 irsyad refs."""
    if project_ref not in ALLOWED_PROJECT_REFS:
        raise BrokerValidationError(
            ErrorCode.BAD_PROJECT_REF,
            f"project_ref {project_ref!r} is not one of the allowlisted irsyad refs",
        )


def _needles_for_silo(project_ref: str) -> set[str]:
    """The alias(es) + raw ref that correspond to the GIVEN project_ref, and
    ONLY those — never every alias in the table (cc-quality #56681 finding 2:
    checking every alias let an unrelated "QA signed off" phrase authorize a
    goumlyne apply)."""
    needles = {project_ref}
    for alias, ref in SILO_ALIASES.items():
        if ref == project_ref:
            needles.add(alias)
    return needles


def _text_mentions_silo(text: str, project_ref: str) -> bool:
    """Whole-word match only (cc-quality #56681 finding 2) — a short alias
    like "qa" must not match merely because it appears inside an unrelated
    word or phrase."""
    t = text.lower()
    for needle in _needles_for_silo(project_ref):
        if re.search(rf"\b{re.escape(needle.lower())}\b", t):
            return True
    return False


def require_gate(
    guarded_sql: str,
    gate_id: str | None,
    project_ref: str,
    cur,
    migration: str | None = None,
    pr: str | None = None,
) -> None:
    """§4 — no-op for DML-only SQL. For DDL-shaped SQL, re-fetches gate_id
    from the fleet bus via `cur` (a psycopg cursor opened with
    row_factory=psycopg.rows.dict_row) and re-validates from_agent,
    message_type, is_past, and silo-mention. Raises BrokerValidationError on
    any failure, BEFORE any mgmt-API call."""
    if not is_ddl_shaped(guarded_sql):
        return

    if not gate_id:
        raise BrokerValidationError(
            ErrorCode.GATE_REQUIRED,
            "inner SQL is DDL-shaped — a gate_id is required",
        )
    if not gate_id.isdigit():
        raise BrokerValidationError(
            ErrorCode.GATE_INVALID,
            f"gate_id must be a positive integer agent_messages id, got {gate_id!r}",
        )

    cur.execute(
        "SELECT from_agent, message_type, subject, body, (created_at < now()) AS is_past "
        "FROM agent_messages WHERE id = %s",
        (int(gate_id),),
    )
    row = cur.fetchone()

    if row is None:
        raise BrokerValidationError(ErrorCode.GATE_INVALID, f"gate {gate_id} not found on the fleet bus")
    if row["from_agent"] not in _GATE_AGENTS:
        raise BrokerValidationError(
            ErrorCode.GATE_INVALID,
            f"gate {gate_id} is from {row['from_agent']!r}, not one of {sorted(_GATE_AGENTS)}",
        )
    if row["message_type"] not in _GATE_TYPES:
        raise BrokerValidationError(
            ErrorCode.GATE_INVALID,
            f"gate {gate_id} has message_type {row['message_type']!r}, not one of {sorted(_GATE_TYPES)}",
        )
    if not row["is_past"]:
        raise BrokerValidationError(ErrorCode.GATE_INVALID, f"gate {gate_id} has created_at not in the past")

    text = f"{row.get('subject') or ''} {row.get('body') or ''}"
    if not _text_mentions_silo(text, project_ref):
        raise BrokerValidationError(
            ErrorCode.GATE_INVALID,
            f"gate {gate_id} does not mention the target silo {project_ref!r}",
        )

    # migration/pr: no wire field carries this today (see module docstring's
    # KNOWN GAP) -- only applied if a future caller passes one explicitly.
    if migration or pr:
        if not _text_mentions_migration_or_pr(text, migration, pr):
            raise BrokerValidationError(
                ErrorCode.GATE_INVALID,
                f"gate {gate_id} does not mention migration {migration!r} or PR {pr!r}",
            )


def _text_mentions_migration_or_pr(text: str, migration: str | None, pr: str | None) -> bool:
    """Digit-boundary match (cc-quality #56681 finding 1) — ported for
    forward-compat with the migration/pr kwargs; unused by any current caller."""
    t = text.lower()
    if migration:
        if re.search(rf"(?<!\d){re.escape(migration.lower())}(?!\d)", t):
            return True
    if pr:
        p = re.escape(str(pr).lower())
        for pat in (rf"#{p}(?!\d)", rf"pr#{p}(?!\d)", rf"pr\s+{p}(?!\d)"):
            if re.search(pat, t):
                return True
    return False
