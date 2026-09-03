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
import time
from typing import Dict, List, Optional, Tuple

ORG_ID = "73339164-7c1f-40ba-a093-33f1f292dd4c"  # Madrasah Irsyad Zuhri Al-Islamiah (goumlyne)
_GOUMLYNE_ENV = "GOUMLYNE_RO_DATABASE_URL"  # CAI-1225 RO-move: read-only auditor_ro (was write-DSN); reads goumlyne, writes only a bus page

# Table names, as module constants so the end-to-end "prove-fired" test can point the
# detector at a synthetic stand-in (never the live silo). Production defaults unchanged.
_T_STUDENTS = "sch_students"
_T_PERSONS = "persons"
_T_PARENTS = "sch_student_parents"

# PII-class name stems (cai/Nazim #24985). The monitored column set is DISCOVERED from
# the catalog at runtime by matching these stems, NOT hardcoded — so a new variant
# (nric_hash_v3, a new contact field) is auto-covered instead of silently re-opening
# the false-green. A populated _hash with no plaintext is still a populated identifier.
_PII_STEMS = ("date_of_birth", "dob", "address", "phone", "email", "nric",
              "emergency", "medical", "previous_school")
# PII CONTENT lives in these types; excludes bool/uuid/timestamp (e.g. an *_verified
# flag or *_id would over-match a stem but carries no identifier content).
_CONTENT_TYPES = ("text", "character varying", "character", "date")
# Pinned coverage floor per table (today's live counts). Discovery finding FEWER than
# this = a rename/removal that could hide a field -> could-not-measure, LOUD (the
# "assert the total" teeth: coverage shrinking must surface, never silently drift).
EXPECTED_MIN = {"persons": 14, "sch_students": 3}
_PARENT_LABEL = "sch_student_parents.rows"


# Transient-blip retry for the goumlyne connect (port of ad38b99/f326d3e; 2026-09-03 pooler-blip
# sweep, Nazim GATE-approved). A transient DNS failure resolving the Supabase pooler host used to
# trip this dead-man (P1 "containment UNVERIFIED") on a blip that self-heals in seconds. We retry
# the connect+read a few times with short linear backoff so a blip is absorbed — but a GENUINE,
# persistent failure STILL re-raises to the dead-man page (the CAI-1060 load-bearing zero must
# never be silently un-monitored; retries only buy a grace window). Env-tunable.
_DB_ATTEMPTS = int(os.environ.get("IRSYAD_PII_DB_ATTEMPTS", "3"))
_DB_RETRY_BASE_S = float(os.environ.get("IRSYAD_PII_DB_RETRY_BASE_S", "1.0"))


def _sleep(seconds: float) -> None:
    """Indirection over time.sleep so tests can suppress real backoff delay."""
    time.sleep(seconds)


def _retry(op, *, attempts: int, base_delay_s: float, retry_on, sleep=_sleep):
    """Call op(); on a `retry_on` exception retry up to `attempts` TOTAL tries with linear
    backoff, then re-raise (a persistent failure still reaches the dead-man page). Non-`retry_on`
    exceptions propagate immediately."""
    for attempt in range(1, attempts + 1):
        try:
            return op()
        except retry_on:
            if attempt == attempts:
                raise
            sleep(base_delay_s * attempt)


def discover_pii_columns(cur, table: str) -> List[str]:
    """Deep-PII-classed columns on `table`, discovered from the catalog by stem match +
    content type. Auto-covers new variants; excludes id/org_id/display_name/flags."""
    stems_sql = " OR ".join(["column_name ILIKE %s"] * len(_PII_STEMS))
    cur.execute(
        f"""SELECT column_name FROM information_schema.columns
             WHERE table_schema='public' AND table_name=%s
               AND data_type = ANY(%s)
               AND ({stems_sql})
             ORDER BY column_name""",
        [table, list(_CONTENT_TYPES)] + ["%%%s%%" % s for s in _PII_STEMS],
    )
    return [r[0] for r in cur.fetchall()]


def coverage_shortfall(table: str, discovered_count: int) -> bool:
    """True if discovery covered FEWER columns than the pinned floor for `table`."""
    return discovered_count < EXPECTED_MIN.get(table, 0)


def classify_breaches(counts: Dict[str, Optional[int]]) -> List[Tuple[str, int]]:
    """Every field whose non-null count is > 0 — a containment breach. A None count is
    NOT a breach here (it is could-not-measure; see classify_could_not_measure)."""
    return [(label, n) for label, n in sorted(counts.items()) if isinstance(n, int) and n > 0]


def classify_could_not_measure(counts: Dict[str, Optional[int]]) -> List[str]:
    """Fields whose count came back None = the query failed. A measurement failure is
    LOUD, never silently green (dead-man's-switch)."""
    return [label for label, n in sorted(counts.items()) if n is None]


def count_unclassifiable(cur, org_id: str = ORG_ID) -> Optional[int]:
    """FAIL-CLOSED check for the jsonb/array backdoor (Nazim #25011): DOB/address
    stuffed into custom_fields as JSON would sail past the scalar stem filter. Count
    the org's persons with a NON-EMPTY custom_fields OR tags — an unstructured field we
    cannot count-classify. Counts only, never parses a value. None if the query fails."""
    subq = f"SELECT person_id FROM {_T_STUDENTS} WHERE org_id = %(o)s AND person_id IS NOT NULL"
    return _count(
        cur,
        f"""SELECT count(*) FROM {_T_PERSONS} WHERE id IN ({subq})
             AND ( (custom_fields IS NOT NULL AND custom_fields::text NOT IN ('{{}}','null'))
                   OR (tags IS NOT NULL AND cardinality(tags) > 0) )""",
        org_id,
    )


def decide_page(counts: Dict[str, Optional[int]], unclassifiable: Optional[int]):
    """The outcome tier (Nazim #25011: red vs amber distinguishable at a glance).
    Returns (exit_code, priority, kind, items). A confirmed scalar-field breach is P0
    (red); a could-not-measure OR a could-not-classify is P1 (amber); else OK."""
    breaches = classify_breaches(counts)
    if breaches:
        return (1, "P0", "breach", breaches)
    unmeasured = classify_could_not_measure(counts)
    if unmeasured or unclassifiable is None:
        items = list(unmeasured) + ([ "persons.custom_fields|tags" ] if unclassifiable is None else [])
        return (2, "P1", "could-not-measure", items)
    if unclassifiable > 0:
        return (2, "P1", "could-not-classify", [("persons.custom_fields|tags", unclassifiable)])
    return (0, None, "ok", [])


def run_counts(cur, org_id: str = ORG_ID) -> Dict[str, Optional[int]]:
    """COUNT-ONLY assertion for the org's students across all three tables, over the
    SCHEMA-DISCOVERED PII column set. Never selects a row value. A per-field query error
    -> None (loud); a coverage shortfall -> a None `_coverage.<table>` label (loud)."""
    counts: Dict[str, Optional[int]] = {}
    student_cols = discover_pii_columns(cur, _T_STUDENTS)
    person_cols = discover_pii_columns(cur, _T_PERSONS)
    # coverage floor guard (assert-the-total teeth): a shrunk set is could-not-measure
    if coverage_shortfall("sch_students", len(student_cols)):
        counts["_coverage.sch_students"] = None
    if coverage_shortfall("persons", len(person_cols)):
        counts["_coverage.persons"] = None
    person_subq = (
        f"SELECT person_id FROM {_T_STUDENTS} WHERE org_id = %(o)s AND person_id IS NOT NULL"
    )
    for c in student_cols:
        counts[f"sch_students.{c}"] = _count(
            cur, f"SELECT count(*) FROM {_T_STUDENTS} WHERE org_id = %(o)s AND {c} IS NOT NULL", org_id)
    for c in person_cols:
        counts[f"persons.{c}"] = _count(
            cur, f"SELECT count(*) FROM {_T_PERSONS} WHERE id IN ({person_subq}) AND {c} IS NOT NULL", org_id)
    counts[_PARENT_LABEL] = _count(
        cur, f"SELECT count(*) FROM {_T_PARENTS} WHERE org_id = %(o)s", org_id)
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
        _page("🟠 Irsyad PII monitor CANNOT RUN — missing goumlyne credential",
              f"{_GOUMLYNE_ENV} not set; cannot verify the load-bearing containment zero for org {ORG_ID}. "
              "Could-not-measure is not green (CAI-1060).", priority="P1")
        return 2
    def _read_goumlyne():
        # Retried as a UNIT on a transient pooler blip (SELECT-only, no PII rows read — counts
        # only). A persistent OperationalError re-raises to the dead-man page in the except below.
        conn = psycopg2.connect(dsn); conn.autocommit = True
        try:
            cur = conn.cursor()
            return run_counts(cur), count_unclassifiable(cur)
        finally:
            conn.close()

    try:
        counts, unclassifiable = _retry(
            _read_goumlyne, attempts=_DB_ATTEMPTS,
            base_delay_s=_DB_RETRY_BASE_S, retry_on=psycopg2.OperationalError)
    except Exception as e:  # dead-man's-switch — a persistent failure STILL pages loud
        _page("🟠 Irsyad PII monitor CONNECT FAILED — containment UNVERIFIED",
              f"Could not read goumlyne to verify org {ORG_ID} deep-field zero: {str(e).splitlines()[0]}. "
              "Loud-fail, not green (dead-man's-switch).", priority="P1")
        return 2

    code, priority, kind, items = decide_page(counts, unclassifiable)
    if kind == "breach":
        lines = "\n".join(f"  {label}: {n} non-null" for label, n in items)
        _page("🔴🔴 CONTAINMENT BREACH — Irsyad student deep PII POPULATED (CAI-1060 F2)",
              f"The load-bearing zero broke for org {ORG_ID}. Deep fields now non-zero:\n{lines}\n"
              "Counts only (no rows read). F2 harm has materialised — controller authorisation + gate.",
              priority="P0")
    elif kind == "could-not-classify":
        n = items[0][1]
        _page("🟠 Irsyad PII monitor — UNSTRUCTURED field populated (needs human eyes)",
              f"org {ORG_ID}: {n} persons now have a non-empty custom_fields/tags — an unstructured field "
              "the monitor cannot count-classify, so it may carry PII. NOT an auto-breach; a human must "
              "classify it (baseline was 0). Counts only, no values read.", priority="P1")
    elif kind == "could-not-measure":
        _page("🟠 Irsyad PII monitor PARTIAL — fields could not be measured",
              f"org {ORG_ID}: could-not-measure on {items}. Not asserting green on absence.", priority="P1")
    return code  # 0 = all zero, containment holds — page nothing (page-only default)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
