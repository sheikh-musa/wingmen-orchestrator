#!/usr/bin/env python3
"""Standing deep-field-zero containment monitor for Madrasah Irsyad student PII
(cai CAI-RESP-1060, assigned via orch-console #24967).

WHY: cai ruled the 890 minors are shallow + contained BECAUSE their deep fields are
all zero — that zero is now a LOAD-BEARING containment fact. F2 harm materialises the
instant any of them populate. This asserts the zero on every scheduled run and pages
P0 the moment it breaks.

⚠ SCHEMA REALITY (verified at source; a monitor on sch_students alone is FALSE-GREEN):
the deep PII spans THREE tables. sch_students carries only emergency_contact /
medical_notes / previous_school. date_of_birth, address, and phone/email/nric live on
`persons` (via sch_students.person_id), EACH as plaintext + _encrypted + _hash +
_hash_v2. Parent links live in sch_student_parents. The monitored set below covers all
three and every variant — dropping any one silently would let that field populate
undetected.

COUNTS ONLY — this NEVER reads a row (Satr; real minors' PII; TENANT-RESIDENCY /
LAYER-VOCAB apply). It reads goumlyne (GOUMLYNE_DATABASE_URL, project
goumlynecruxrlmzlntp) and writes only a bus page carrying a field LABEL + COUNT, never
a value. A could-not-measure (query/connection failure) pages LOUD — a monitor that
cannot read must never read green (dead-man's-switch).
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

ORG_ID = "73339164-7c1f-40ba-a093-33f1f292dd4c"  # Madrasah Irsyad Zuhri Al-Islamiah (goumlyne)
_GOUMLYNE_ENV = "GOUMLYNE_DATABASE_URL"

# Deep-field set, verified against the live schema. Any of these going non-null for an
# org student = a containment breach.
_STUDENT_DEEP_COLS = ["emergency_contact", "medical_notes", "previous_school"]
_PERSON_DEEP_COLS = [
    "date_of_birth", "address",
    "phone", "phone_encrypted", "phone_hash", "phone_hash_v2",
    "email", "email_encrypted", "email_hash", "email_hash_v2",
    "nric_encrypted", "nric_hash", "nric_source", "nric_hash_v2",
]
_PARENT_LABEL = "sch_student_parents.rows"


def monitored_labels() -> List[str]:
    """Every field/table this monitor asserts count-zero on (the false-green guard's
    subject: this set must stay complete)."""
    return (
        [f"sch_students.{c}" for c in _STUDENT_DEEP_COLS]
        + [f"persons.{c}" for c in _PERSON_DEEP_COLS]
        + [_PARENT_LABEL]
    )


def classify_breaches(counts: Dict[str, Optional[int]]) -> List[Tuple[str, int]]:
    """Every field whose non-null count is > 0 — a containment breach. A None count is
    NOT a breach here (it is could-not-measure; see classify_could_not_measure)."""
    return [(label, n) for label, n in sorted(counts.items()) if isinstance(n, int) and n > 0]


def classify_could_not_measure(counts: Dict[str, Optional[int]]) -> List[str]:
    """Fields whose count came back None = the query failed. A measurement failure is
    LOUD, never silently green (dead-man's-switch)."""
    return [label for label, n in sorted(counts.items()) if n is None]


def run_counts(cur, org_id: str = ORG_ID) -> Dict[str, Optional[int]]:
    """Run the COUNT-ONLY assertion for the org's students across all three tables.
    Never selects a row value. A per-field query error -> None for that field (loud)."""
    counts: Dict[str, Optional[int]] = {}
    person_subq = (
        "SELECT person_id FROM sch_students WHERE org_id = %(o)s AND person_id IS NOT NULL"
    )
    for c in _STUDENT_DEEP_COLS:
        counts[f"sch_students.{c}"] = _count(
            cur, f"SELECT count(*) FROM sch_students WHERE org_id = %(o)s AND {c} IS NOT NULL", org_id)
    for c in _PERSON_DEEP_COLS:
        counts[f"persons.{c}"] = _count(
            cur, f"SELECT count(*) FROM persons WHERE id IN ({person_subq}) AND {c} IS NOT NULL", org_id)
    counts[_PARENT_LABEL] = _count(
        cur, "SELECT count(*) FROM sch_student_parents WHERE org_id = %(o)s", org_id)
    return counts


def _count(cur, sql: str, org_id: str) -> Optional[int]:
    try:
        cur.execute(sql, {"o": org_id})
        return int(cur.fetchone()[0])
    except Exception:
        return None  # could-not-measure -> surfaces via classify_could_not_measure


def _page(subject: str, body: str, priority: str = "P0") -> None:
    """Write a bus page from cc-fleet-health to orch-console. Substrate (DATABASE_URL).
    Carries only field labels + counts — never a PII value."""
    import psycopg2
    conn = psycopg2.connect(os.environ["DATABASE_URL"]); conn.autocommit = True
    conn.cursor().execute(
        """INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, priority, requires_response, posted_by_identity)
           VALUES ('cc-fleet-health','orch-console','blocker',%s,%s,%s,true,'cc-fleet-health')""",
        (subject, body, priority))


def main() -> int:
    import psycopg2
    # Under launchd the process env has no .env (works-by-hand, fails-under-launchd —
    # the A3 runner class, fix 3f29019). Load the orch .env; load_dotenv does NOT
    # override already-set vars, so a hand run with .env sourced is unaffected.
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    except Exception:
        pass
    dsn = os.environ.get(_GOUMLYNE_ENV)
    if not dsn:
        _page("🔴 Irsyad PII monitor CANNOT RUN — missing goumlyne credential",
              f"{_GOUMLYNE_ENV} not set; cannot verify the load-bearing containment zero for org {ORG_ID}. "
              "Could-not-measure is not green (CAI-1060).")
        return 2
    try:
        conn = psycopg2.connect(dsn); conn.autocommit = True
        counts = run_counts(conn.cursor())
    except Exception as e:
        _page("🔴 Irsyad PII monitor CONNECT FAILED — containment UNVERIFIED",
              f"Could not read goumlyne to verify org {ORG_ID} deep-field zero: {str(e).splitlines()[0]}. "
              "Loud-fail, not green (dead-man's-switch).")
        return 2

    unmeasured = classify_could_not_measure(counts)
    breaches = classify_breaches(counts)
    if breaches:
        lines = "\n".join(f"  {label}: {n} non-null" for label, n in breaches)
        _page("🔴🔴 CONTAINMENT BREACH — Irsyad student deep PII POPULATED (CAI-1060 F2)",
              f"The load-bearing zero broke for org {ORG_ID}. Deep fields now non-zero:\n{lines}\n"
              "Counts only (no rows read). F2 harm has materialised — controller authorisation + gate.")
        return 1
    if unmeasured:
        _page("🟠 Irsyad PII monitor PARTIAL — some fields could not be measured",
              f"org {ORG_ID}: could-not-measure on {unmeasured}. Not asserting green on absence.")
        return 2
    return 0  # all zero, containment holds — page nothing (page-only-on-breach default)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
