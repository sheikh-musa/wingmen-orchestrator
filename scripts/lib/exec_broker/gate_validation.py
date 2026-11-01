#!/usr/bin/env python3
"""gate_validation.py — hc#95 exec-broker re-validation (contract §4).

Re-checks, independently of exec_prod's own local screen, whenever the inner
SQL is DDL-shaped:
  1. project_ref is on the current milestone's allowlist (exact string) —
     QA-ONLY for milestone (a), see ALLOWED_PROJECT_REFS below.
  2. gate_id is present, well-formed, and the fleet-bus row it names is a real
     console/cai GO (message_type decision/agreed), in the past, and mentions
     the target silo.

Rules vendored from ihsanos-irsyad main's scripts/db/exec_prod.py (sha256
9bc04456e03430f500ce6748524208c49c3eccd394f14cc7239e70f2af348a03, commit
5053826e — same commit the socket contract itself is frozen at): _GATE_AGENTS,
_GATE_TYPES, _SILO_ALIASES, and the whole-word silo-mention check are ported
verbatim in logic from that file's _require_gate/_text_mentions_silo. Re-sync
by re-reading that file's current state and updating this pin if it changes.

KNOWN GAP, RESOLVED FAIL-CLOSED (bus #70870 -> orch-console's CORRECTED
ruling, bus #70929, after cc-fleet-health caught the hole in the original
#70876 ruling): the frozen §2 wire request has no migration/pr field, so
exec_prod._require_gate's "digit-boundary PR/migration match" sub-check
cannot be re-performed here from the wire data alone. The FIRST fix attempt
(skip that one sub-check, re-check everything else) was FAIL-OPEN in
practice: server.py always calls require_gate() with migration=pr=None, so
the skip was unconditional, and a gate that validly approved ONE migration to
a silo would then authorize ANY DDL to that same silo — reopening the exact
cross-authorization class exec_prod's own _require_gate was fixed to close
(cc-quality #56681). The correct fix, per the corrected ruling: milestone (a)
REFUSES ALL DDL-shaped SQL unconditionally (GATE_INVALID), regardless of
gate_id, after every other sub-check already passes — "we cannot
independently verify which migration/PR this gate authorizes" is a REFUSAL,
never an allow-by-default. require_gate() still accepts optional migration/pr
kwargs so that once the wire contract is amended at rollout step (c) to carry
them, re-enabling DDL is a matter of making the final refusal conditional on
their absence, not a redesign.

GAP CLOSED for DDL-shaped, NON-hazardous SQL (rollout step (c), bus
#79024/#79465, 2026-10-28): the §2 wire request now carries OPTIONAL
migration/pr fields (protocol.py), and server.py threads them into
require_gate(). The unconditional refusal above now applies ONLY when
migration and pr are both absent (an old, pre-amendment caller) — when
either is supplied, the full independent digit-boundary match
(_text_mentions_migration_or_pr, ported verbatim from exec_prod.py's own
function of the same name) is used instead, exactly as this module's kwargs
always anticipated.

GAP CLOSED (Part A 2/2, orch-console ruling bus #79477 after the feasibility
question in #79471): LEXICALLY HAZARDOUS SQL is now allowed, but ONLY behind a
byte binding. The gater cites sha256(wrap(file))[:12] -- the WRAPPED bytes,
computed by exec_prod.py --show-gate-sha at the cited commit, NOT the raw
file's sha12 that apply_migration.py cites for its own, unchanged convention --
and require_gate refuses unless sha256(guarded_sql)[:12] equals it. The broker
never calls wrap() and never reads the migration file: it proves the executed
bytes equal what the gater hashed; the file identity is attested by the gater,
not verified by the broker. Refusals on that axis are the additive
GATE_MISMATCH code; every failure the ruling lists as "unchanged" (expired /
out-of-scope / unauthorized gate, ungated hazardous SQL) still refuses, and a
byte match does NOT replace the migration/PR specificity check.

B1 + B2 + --ref ANCHOR (cc-fleet-health cutover spec, bus #89814, 2026-11-01):
  * B1 -- the call-allowlist is now a STANDING set loaded at daemon boot
    (EXEC_BROKER_LIVE_GATED_REFS, silo aliases; see exec_broker_daemon.py),
    defaulting to ALLOWED_PROJECT_REFS (QA only). Every ref on it is GATED:
    the old "no-op for DML" early return is gone, so ANY statement that is
    not a plain read (read_only.is_plain_read -- the SAME classifier the RO
    override uses) needs a valid gate. This mirrors exec_prod._require_gate
    (console #81213): DML is a data change and is gated on every silo.
  * B2 -- the wire request carries an optional `ref` key (protocol.py),
    threaded into require_gate(ref=...).
  * --ref -- an independent gate ANCHOR (op#N/hc#N/q#N/cp#N), ported
    verbatim in behaviour from ihsanos-irsyad scripts/db/exec_prod.py
    (_validate_ref/_text_mentions_ref, @ a1dc75f9): shape validated first
    and unconditionally; refused together with `migration`; never anchors
    DDL-shaped or lexically hazardous SQL on its own; must appear as an
    exact token in the gate text; a ref-anchored statement may never write
    schema_migrations (no stamp under a ref-only gate).
  * A NON-plain-read, non-DDL, non-hazardous statement (DML) still needs a
    SPECIFIC anchor -- ref, migration or pr matched against the gate text --
    exactly as exec_prod requires; a bare silo-mentioning gate never
    authorizes arbitrary DML (same cross-authorization class as #70929).
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from .ddl_detect import is_ddl_shaped
from .lexical_hazard import is_lexically_hazardous
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

# alias -> project ref (vendored from exec_prod.py's _SILO_ALIASES) — the full
# reference table of all 3 real irsyad refs, independently confirmed current by
# cc-irsyad-coord-1 (bus #70875). Used for silo-MENTION matching in a gate's
# text (_text_mentions_silo below); NOT the broker's own call-allowlist — see
# ALLOWED_PROJECT_REFS.
SILO_ALIASES = {
    "goumlyne": "goumlynecruxrlmzlntp",
    "ceayj": "ceayjeamtmcyzzvqflus",
    "qa": "oovskwogvefnznfowjky",
    "irsyad-qa": "oovskwogvefnznfowjky",
}

# Milestone (a) scope, enforced IN CODE (orch-console gate ruling, bus #70919
# answering PR#434's review): QA-ref ONLY, even though SILO_ALIASES names all
# 3 real refs above. A non-QA ref must return BAD_PROJECT_REF until option (2)
# — the full independent migration/PR gate re-check — plus a frozen-contract
# amendment land at rollout step (c) (see require_gate's migration/pr kwargs
# and the module docstring's KNOWN GAP). This is the never-trust-the-caller
# control itself, not a stand-in for the daemon simply being un-provisioned
# for goumlyne/ceayj — do not widen this set without that step-(c) work landing
# first, and do not rely on provisioning alone to keep real refs out of reach.
ALLOWED_PROJECT_REFS = frozenset({SILO_ALIASES["qa"]})

# B1 (bus #89814): the standing LIVE_GATED_REFS set is CONFIG, not a code
# constant -- exec_broker_daemon.load_live_gated_refs() resolves
# EXEC_BROKER_LIVE_GATED_REFS (silo aliases) at boot and hands the result to
# BrokerServer(live_gated_refs=...). Unset/blank -> this default, which is
# ALLOWED_PROJECT_REFS itself (QA only), so an un-configured daemon behaves
# exactly as before. Widening is an env edit + restart; narrowing takes
# effect on restart. Every ref in the set is fully gated (require_gate).
DEFAULT_LIVE_GATED_REFS = ALLOWED_PROJECT_REFS

# BOUNDED OVERRIDE (op#34322/#76985/#76989, 2026-10-26, orch-console gate) —
# THIS is the step-(c)-shaped amendment the comment above anticipated: a
# reviewed, wall-clock-gated, auto-expiring exception, never a live hand-edit
# of ALLOWED_PROJECT_REFS itself. check_project_ref optionally takes a single
# extra ref + its own expiry (loaded once at daemon boot from env, see
# exec_broker_daemon.py) and re-checks the current time on EVERY call, so the
# exception reverts BY CONSTRUCTION the moment it expires — no second root
# action needed to disarm, only to arm. Missing/None/expired means inert:
# falls through to ALLOWED_PROJECT_REFS-only behavior, identical to before
# this override existed.


def ref_override_is_live(
    override_ref: "str | None",
    override_expires_at: "datetime | None",
    now: "datetime | None" = None,
) -> bool:
    """The ONE definition of "the bounded ref override is currently in force":
    armed (both halves present) AND still unexpired. Shared by
    check_project_ref (the ref widening) and read_only.check_read_only_override
    (the read-only mode the override now implies, GO #78974) so the two can
    never drift — the same wall-clock instant flips both, and expiry reverts
    both together, by construction."""
    if override_ref is None or override_expires_at is None:
        return False
    return (now if now is not None else datetime.now(timezone.utc)) < override_expires_at


def check_project_ref(
    project_ref: str,
    *,
    override_ref: "str | None" = None,
    override_expires_at: "datetime | None" = None,
    now: "datetime | None" = None,
    allowed_refs: "frozenset[str] | None" = None,
) -> None:
    """§4.1 — exact-string allowlist. QA-only for milestone (a); see
    ALLOWED_PROJECT_REFS's comment before widening this. A still-live bounded
    override (override_ref + override_expires_at, both required and
    unexpired) additionally allows that ONE exact ref — see the module-level
    comment above ALLOWED_PROJECT_REFS.

    allowed_refs (B1, bus #89814) is the daemon's boot-loaded standing
    LIVE_GATED_REFS set; None means DEFAULT_LIVE_GATED_REFS (QA only) --
    never "allow everything"."""
    allowlist = DEFAULT_LIVE_GATED_REFS if allowed_refs is None else allowed_refs
    if project_ref in allowlist:
        return
    # Liveness first: a live override_ref is non-None by construction, so the
    # equality below can never pass for a None project_ref.
    if ref_override_is_live(override_ref, override_expires_at, now=now) and project_ref == override_ref:
        return
    raise BrokerValidationError(
        ErrorCode.BAD_PROJECT_REF,
        f"project_ref {project_ref!r} is not on the broker's current allowlist",
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


# --ref anchor (bus #89814 §3), ported verbatim in behaviour from ihsanos-irsyad
# scripts/db/exec_prod.py @ a1dc75f9 (_REF_RE / _validate_ref /
# _text_mentions_ref, console #83951). Strict shape; anything else refused.
# fullmatch (a bare `$` would accept a trailing \n); ASCII [0-9] (\d also
# matches non-ASCII digits). Re-sync by re-reading that file if it changes.
_REF_RE = re.compile(r"(?:op|hc|q|cp)#[0-9]+")

# A ref-anchored statement must never touch schema_migrations (bus #89814 §3.5:
# no stamp under a ref-only gate). Raw-text, case-insensitive, whole-word --
# deliberately over-refuses (a comment mentioning it is refused too).
_SCHEMA_MIGRATIONS_RE = re.compile(r"(?i)\bschema_migrations\b")


def _validate_ref(ref: str) -> str:
    """Return the ref unchanged if it is exactly <op|hc|q|cp>#<digits>
    (lower-case prefix), else raise GATE_INVALID. No normalisation at all --
    ' op#1', 'OP#1', 'op#12a', 'op#12 ', '36027' all refuse."""
    if not isinstance(ref, str) or not _REF_RE.fullmatch(ref):
        raise BrokerValidationError(
            ErrorCode.GATE_INVALID,
            f"ref must look like op#N, hc#N, q#N or cp#N (digits only after #), got {ref!r}",
        )
    return ref


def _text_mentions_ref(text: str, ref: str) -> bool:
    """The gate must contain the EXACT ref token (exec_prod, console #83951).
    A TRAILING digit guard so `op#51` does not match a gate saying `op#510`,
    and a LEADING guard so the prefix is a whole word (`q#210` must not match
    inside `faq#210`, nor `cp#5` inside `hcp#5`)."""
    r = _validate_ref(ref)
    return re.search(rf"(?<![a-z0-9_]){re.escape(r)}(?!\d)", text.lower()) is not None


def require_gate(
    guarded_sql: str,
    gate_id: str | None,
    project_ref: str,
    cur,
    migration: str | None = None,
    pr: str | None = None,
    ref: str | None = None,
) -> bool:
    """§4 re-validation, run for every call on a (gated) allowlisted ref.

    Returns False -- without touching `cur` -- ONLY for a plain read
    (read_only.is_plain_read: not DDL-shaped, not lexically hazardous, every
    statement a SELECT/WITH/TABLE/VALUES with no write tokens). Every other
    statement (DML, DO blocks, DDL, anything hazardous) needs a gate (B1, bus
    #89814): re-fetches gate_id from the fleet bus via `cur` (a psycopg
    cursor opened with row_factory=psycopg.rows.dict_row), re-validates
    from_agent, message_type, is_past and silo-mention, then requires a
    SPECIFIC anchor (ref / migration / pr) matched in the gate text. Returns
    True when a gate was required and fully validated. Raises
    BrokerValidationError on any failure, BEFORE any mgmt-API call."""
    # Lazy import: read_only imports BrokerValidationError/ref_override_is_live
    # from this module, so a module-level import would be circular.
    from .read_only import is_plain_read

    if ref is not None:
        # Shape refusal before anything else, even for a plain read, even when
        # migration/pr is also present (exec_prod parity).
        ref = _validate_ref(ref)
        # ref + migration is an ambiguous anchor (cc-quality #83972): a
        # migration apply never carries a ref, a ref data-fix has no migration.
        if migration:
            raise BrokerValidationError(
                ErrorCode.GATE_INVALID,
                f"ref {ref} and migration {migration} together -- a migration apply "
                "must not carry a ref, and a ref data correction has no migration; "
                "drop one",
            )

    hazardous = is_lexically_hazardous(guarded_sql)
    ddl = is_ddl_shaped(guarded_sql)
    if not ddl and not hazardous and is_plain_read(guarded_sql):
        return False

    if (ddl or hazardous) and ref and not (migration or pr):
        raise BrokerValidationError(
            ErrorCode.GATE_INVALID,
            f"ref {ref} is only a gate anchor for NON-DDL, non-hazardous data "
            "corrections -- this statement is DDL-shaped or lexically hazardous "
            "and needs a migration or PR matching the gate",
        )
    if ref and _SCHEMA_MIGRATIONS_RE.search(guarded_sql):
        raise BrokerValidationError(
            ErrorCode.GATE_INVALID,
            f"ref {ref} anchors a data correction, which must never write "
            "schema_migrations (no stamp under a ref gate) -- a migration apply "
            "carries migration, not ref",
        )

    if not gate_id:
        raise BrokerValidationError(
            ErrorCode.GATE_REQUIRED,
            "inner SQL is not a plain read (DML, DDL, DO block, or lexically "
            "hazardous) -- a gate_id is required",
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
    if ref and not _text_mentions_ref(text, ref):
        raise BrokerValidationError(
            ErrorCode.GATE_INVALID,
            f"gate {gate_id} does not mention ref {ref!r} (exact token, digit-boundary matched)",
        )

    if hazardous:
        # Part A 2/2 (orch-console ruling bus #79477, resolving the byte-shape
        # problem raised in #79471): lexically hazardous SQL may be authorized
        # ONLY by binding the gate to the EXACT executed bytes. Two things are
        # required, in this order:
        #   1. the same migration/PR specificity every other DDL statement
        #      needs -- a byte match says nothing on its own about WHICH
        #      migration the gate authorizes, so it is not a substitute for
        #      that check (deliberately fail-closed: byte binding is
        #      ADDITIONAL, never an alternative);
        #   2. the gate must cite sha256(guarded_sql)[:12] -- which the gater
        #      computes as sha256(wrap(file))[:12] via exec_prod.py
        #      --show-gate-sha at the cited commit -- and it must match.
        # The broker never calls wrap() and never reads the migration file, per
        # the ruling: "the broker proves the executed bytes equal what the
        # gater hashed; the file identity is attested by the gater (via
        # --show-gate-sha at the cited commit), not verified by the broker".
        # Do not widen this to a text-only match, and do not narrow it to a
        # byte match alone.
        if not (migration or pr):
            raise BrokerValidationError(
                ErrorCode.GATE_INVALID,
                f"gate {gate_id} authorizes lexically hazardous SQL, which "
                "additionally requires the migration or PR it is for on the "
                "wire (bus #79477)",
            )
        if not _text_mentions_migration_or_pr(text, migration, pr):
            raise BrokerValidationError(
                ErrorCode.GATE_INVALID,
                f"gate {gate_id} does not mention migration {migration!r} or PR {pr!r}",
            )
        cited = _cited_wrapped_sha12s(text)
        executed = _executed_sha12(guarded_sql)
        if not cited:
            raise BrokerValidationError(
                ErrorCode.GATE_MISMATCH,
                f"gate {gate_id} cites no wrapped-sha12, but this statement is "
                f"lexically hazardous and hashes to {executed}; the gater must "
                "cite the wrapped sha12 printed by exec_prod.py --show-gate-sha "
                "at the cited commit (bus #79477)",
            )
        if cited != {executed}:
            raise BrokerValidationError(
                ErrorCode.GATE_MISMATCH,
                f"executed bytes hash to {executed}, but gate {gate_id} cites "
                f"{sorted(cited)} -- the statement being executed is not the one "
                "the gate authorized (bus #79477)",
            )
        return True

    if not ddl:
        # B1 (bus #89814): a non-plain-read, non-DDL, non-hazardous statement
        # -- DML or a pure-DML DO block. It still needs a SPECIFIC anchor, as
        # exec_prod._require_gate requires: a gate that merely names the silo
        # must never authorize arbitrary data changes to it (the same
        # cross-authorization class #70929 closed for DDL). ref was already
        # exact-token matched above; migration/pr get the digit-boundary match.
        if ref:
            return True
        if migration or pr:
            if not _text_mentions_migration_or_pr(text, migration, pr):
                raise BrokerValidationError(
                    ErrorCode.GATE_INVALID,
                    f"gate {gate_id} does not mention migration {migration!r} or PR {pr!r}",
                )
            return True
        raise BrokerValidationError(
            ErrorCode.GATE_INVALID,
            f"gate {gate_id} is shape-valid but this data change carries no ref, "
            "migration or pr anchor on the wire, so the gate's specificity cannot "
            "be independently verified -- refused, not allowed by default (bus #89814)",
        )

    if migration or pr:
        # Forward-compat path for rollout step (c), once the wire contract
        # carries migration/pr: do the full independent match instead of the
        # unconditional refusal below. (A ref, if also present, was already
        # matched above -- it never anchors DDL on its own.)
        if not _text_mentions_migration_or_pr(text, migration, pr):
            raise BrokerValidationError(
                ErrorCode.GATE_INVALID,
                f"gate {gate_id} does not mention migration {migration!r} or PR {pr!r}",
            )
        return True

    # Milestone (a), FAIL CLOSED (bus #70929): migration/pr were not supplied
    # (today, NEVER are -- see module docstring), so this gate's SPECIFICITY
    # cannot be independently verified. Every other sub-check above already
    # passed (shape/agent/type/is_past/silo-mention) -- refuse anyway, rather
    # than let a gate approving one migration authorize any other DDL to the
    # same silo. This is the never-trust-the-caller principle itself: do not
    # remove this refusal without rollout step (c)'s contract amendment
    # landing first.
    raise BrokerValidationError(
        ErrorCode.GATE_INVALID,
        f"gate {gate_id} is shape-valid but DDL-shaped and cannot be "
        "independently migration/PR-matched in milestone (a) -- refused, "
        "not allowed by default (bus #70929)",
    )


def _text_mentions_migration_or_pr(text: str, migration: str | None, pr: str | None) -> bool:
    """Digit-boundary match (cc-quality #56681 finding 1) — the same check
    exec_prod._require_gate performs, ported verbatim in logic."""
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


# A gate that authorizes LEXICALLY HAZARDOUS SQL must cite
# sha256(guarded_sql)[:12] -- the WRAPPED bytes, not the raw migration file --
# under this label. Exactly 12 hex chars with a word boundary after them, so a
# full 64-char digest, or the row's separate raw-file sha12 sitting unlabelled
# elsewhere in the text, can never be mistaken for the citation (bus #79477:
# the raw-file convention stays reserved for apply_migration.py, unchanged; the
# wrap()ed-byte convention is what binds a broker call).
_WRAPPED_SHA12_RE = re.compile(
    r"wrapped[\s_-]*sha12\s*[:=]\s*([0-9a-fA-F]{12})\b", re.IGNORECASE
)


def _cited_wrapped_sha12s(text: str) -> set[str]:
    """Every DISTINCT wrapped-sha12 the gate text cites, lowercased. More than
    one distinct value is treated as ambiguous by require_gate (refuse, never
    pick one) -- see
    test_hazardous_refused_when_gate_cites_two_conflicting_wrapped_sha12s."""
    return {m.group(1).lower() for m in _WRAPPED_SHA12_RE.finditer(text)}


def _executed_sha12(guarded_sql: str) -> str:
    """sha256 of the EXACT bytes the broker is about to hand the management
    API, first 12 hex chars -- the same truncation apply_migration.py already
    uses for its (separate, unchanged) raw-file gate citations, applied here to
    the wrap()ed payload. The digest is over the request's `guarded_sql` str
    encoded as UTF-8: that is the wire encoding, it is the same str value the
    JSON body carries to the management API, and it is therefore the same byte
    string the gater hashed with --show-gate-sha."""
    return hashlib.sha256(guarded_sql.encode("utf-8")).hexdigest()[:12]
