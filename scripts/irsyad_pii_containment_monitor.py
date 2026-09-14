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

# ── DEPLOY-LOCKSTEP FIELD-GATE (mig337 @fb9eacd6; Nazim #40043/#40044, Musa op#20281) ──
# The stem-discovery above is LEAKY for the enrichment envelope: mig337's new in-envelope
# columns (house/fee_code/class_id/citizenship/…) match no stem, so they would populate
# UNMONITORED (false-green), and a stem-matched authorized field going 0->N would false-P0
# as a "new field". The runtime field-gate below replaces stem-discovery for the AUTHORIZED
# path with an EXPLICIT allowlist + FAIL-CLOSED-UNKNOWN:
#   AUTHORIZED_CEILING — the in-envelope deep-PII set Nazim authorized; accepted <= the cohort
#     ceiling (--accept --ceiling N pre-authorizes EVERY one, incl. currently-zero fields, so an
#     authorized 0->N<=ceiling rise is suppressed and only >ceiling / a NEW-UNKNOWN field trips).
#   IGNORED — structural / universal / bookkeeping / unstructured cols that are NOT deep-PII and
#     are present for the whole roster (id/org_id/fks/timestamps/flags/student_number + the
#     universal display_name & status + import_batch_* + custom_fields/tags handled separately).
#     NB: display_name (persons) + status (sch_students) ARE in Nazim's authorized list but are
#     populated for ALL 1790 students (not the <=1147 cohort) -> ignored to avoid a ceiling-
#     overflow false-P0; they are not the containment-sensitive deep fields. (SRE flagged; his call.)
#   FAIL-CLOSED-UNKNOWN — DERIVED BY EXCLUSION at runtime: any persons/sch_students column that is
#     not AUTHORIZED, not IGNORED, not FORBIDDEN(below), not unstructured -> counted as out-of-
#     envelope (P0-on-any-nonzero, NEVER --accept'd). A NEW column we never classified fails CLOSED.
AUTHORIZED_CEILING = {
    # nric_hash (v1): AUTHORIZED per Nazim #40056 — same data class as nric_hash_v2 (op#20281-signed),
    # a column-name variant, not an envelope expansion.
    _T_PERSONS: {"date_of_birth", "address", "gender",
                 "nric_encrypted", "nric_hash", "nric_hash_v2", "nric_source"},
    # emergency_contact (bare legacy): AUTHORIZED per Nazim #40056 — same class as the structured
    # emergency_contact_* fields already authorized.
    _T_STUDENTS: {"emergency_contact", "emergency_contact_name", "emergency_contact_number",
                  "emergency_contact_email", "emergency_contact_relationship", "emergency_contact_note",
                  "class_id", "enrollment_date", "house", "citizenship", "nationality",
                  "country_of_birth", "race", "fee_code", "admission_year", "sibling_count",
                  "withdrawal_date"},
}
IGNORED_FIELDS = {
    _T_PERSONS: {"id", "org_id", "user_id", "merged_into", "created_at", "updated_at",
                 "deleted_at", "is_active", "display_name", "import_batch_id",
                 "import_batch_role", "import_batch_enriched_fields", "custom_fields", "tags"},
    # previous_school: IGNORE per Nazim #40056 — education metadata, low-sensitivity, outside the
    # deep-PII guard scope (not medical/custody/parent/contact); ignore (not ceiling-gate) to avoid
    # a false-P0 stalling the cutover.
    _T_STUDENTS: {"id", "org_id", "person_id", "created_at", "updated_at", "deleted_at",
                  "student_number", "status", "previous_school", "import_batch_id",
                  "import_batch_role", "import_batch_enriched_fields"},
}
# sch_students cols already covered as FORBIDDEN by count_forbidden() — excluded from the
# fail-closed-unknown enumeration so they are not double-counted (allergy* matched by ILIKE).
_FORBIDDEN_SCH = {"medical_notes", "custody_court_order_ref", "custody_under_court_order"}


def _present_columns(cur, table: str) -> List[str]:
    """All column names on `table` (public schema), from the catalog. Used to enumerate the
    fail-closed-unknown surface and to assert an AUTHORIZED field has not vanished from schema."""
    cur.execute("""SELECT column_name FROM information_schema.columns
                    WHERE table_schema='public' AND table_name=%s ORDER BY column_name""", (table,))
    return [r[0] for r in cur.fetchall()]


def all_authorized_labels() -> List[str]:
    """Every AUTHORIZED_CEILING field as a `table.col` label — the full set --accept pre-authorizes
    at the ceiling (so a currently-zero authorized field populating <= ceiling never false-P0s)."""
    return sorted(f"{t}.{c}" for t, cs in AUTHORIZED_CEILING.items() for c in cs)


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


def count_forbidden(cur, org_id: str = ORG_ID) -> Dict[str, Optional[int]]:
    """COUNT-ONLY out-of-envelope (F3/F4/F5) FORBIDDEN gate for the org's students+parent links.
    Never reads a value. Checked EXPLICITLY by exact column name — bypassing the stem/content-type
    discovery filter that misses bool/uuid/custody/parent (the invisibility gap, #39944). Bool flags
    counted as = TRUE (a SET flag is the harm; false/null benign); text as IS NOT NULL; parent links
    & parent-PII as row counts. A per-check error -> None (could-not-measure, LOUD dead-man). ANY
    nonzero here is P0 regardless of the authorized-cohort ceiling (--accept never touches this)."""
    f: Dict[str, Optional[int]] = {}
    S, P, PA = _T_STUDENTS, _T_PERSONS, _T_PARENTS
    # sch_students: medical + custody are ruled OUT of the enrollment envelope
    f[f"{S}.medical_notes"] = _count(cur, f"SELECT count(*) FROM {S} WHERE org_id=%(o)s AND medical_notes IS NOT NULL", org_id)
    f[f"{S}.custody_court_order_ref"] = _count(cur, f"SELECT count(*) FROM {S} WHERE org_id=%(o)s AND custody_court_order_ref IS NOT NULL", org_id)
    f[f"{S}.custody_under_court_order"] = _count(cur, f"SELECT count(*) FROM {S} WHERE org_id=%(o)s AND custody_under_court_order IS TRUE", org_id)
    # schema-forward: any allergy-like column (none today) -> non-null; discovery failure is loud
    try:
        cur.execute("""SELECT column_name FROM information_schema.columns WHERE table_schema='public'
                       AND table_name=%s AND column_name ILIKE '%%allergy%%'""", (S,))
        for (cn,) in cur.fetchall():
            f[f"{S}.{cn}"] = _count(cur, f"SELECT count(*) FROM {S} WHERE org_id=%(o)s AND {cn} IS NOT NULL", org_id)
    except Exception:
        f[f"{S}._allergy_discovery"] = None
    # sch_student_parents: ANY row is a parent-guardian LINK — out-of-envelope in the enrollment phase
    f[f"{PA}.rows"] = _count(cur, f"SELECT count(*) FROM {PA} WHERE org_id=%(o)s", org_id)
    f[f"{PA}.has_legal_custody"] = _count(cur, f"SELECT count(*) FROM {PA} WHERE org_id=%(o)s AND has_legal_custody IS TRUE", org_id)
    f[f"{PA}.marital_status"] = _count(cur, f"SELECT count(*) FROM {PA} WHERE org_id=%(o)s AND marital_status IS NOT NULL", org_id)
    # parent-guardian PII on the LINKED parent persons rows (discover persons PII cols; no hardcoded names)
    parent_ids = f"SELECT parent_person_id FROM {PA} WHERE org_id=%(o)s AND parent_person_id IS NOT NULL"
    ppcols = discover_pii_columns(cur, P)
    if ppcols:
        conds = " OR ".join(f"{c} IS NOT NULL" for c in ppcols)
        f["persons(parent).pii"] = _count(cur, f"SELECT count(*) FROM {P} WHERE id IN ({parent_ids}) AND ({conds})", org_id)
    else:
        f["persons(parent).pii"] = None  # could-not-discover -> loud
    # ── FAIL-CLOSED-UNKNOWN (deploy-lockstep, #40044): DERIVED BY EXCLUSION. Any column on the
    # student persons / sch_students rows that is NOT authorized, NOT ignored, NOT already a
    # forbidden check above, NOT unstructured (custom_fields/tags -> count_unclassifiable) is
    # OUT-OF-ENVELOPE by default -> counted here (P0-on-any-nonzero, never --accept'd). A NEW
    # column mig-added later that neither Nazim nor I classified fails CLOSED, never silent-green.
    student_persons = f"SELECT person_id FROM {S} WHERE org_id=%(o)s AND person_id IS NOT NULL"
    for c in sorted(_present_columns(cur, P)):
        if c in AUTHORIZED_CEILING[P] or c in IGNORED_FIELDS[P]:
            continue
        f[f"{P}(student).{c}"] = _count(cur, f'SELECT count(*) FROM {P} WHERE id IN ({student_persons}) AND "{c}" IS NOT NULL', org_id)
    for c in sorted(_present_columns(cur, S)):
        if (c in AUTHORIZED_CEILING[S] or c in IGNORED_FIELDS[S]
                or c in _FORBIDDEN_SCH or "allergy" in c.lower()):
            continue
        f[f"{S}.{c}"] = _count(cur, f'SELECT count(*) FROM {S} WHERE org_id=%(o)s AND "{c}" IS NOT NULL', org_id)
    return f


def classify_forbidden(forbidden: Dict[str, Optional[int]]):
    """(breaches, unmeasured): breaches = [(label,count)] with count>0 (out-of-envelope = P0);
    unmeasured = labels whose count is None (could-not-measure the gate = LOUD dead-man)."""
    breaches = [(l, n) for l, n in sorted(forbidden.items()) if isinstance(n, int) and n > 0]
    unmeasured = [l for l, n in sorted(forbidden.items()) if n is None]
    return breaches, unmeasured


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
    """COUNT-ONLY assertion for the org's students over the EXPLICIT AUTHORIZED_CEILING
    allowlist (deploy-lockstep field-gate; replaces the leaky stem-discovery for the
    authorized path). Never selects a row value. A per-field query error -> None (loud).
    COVERAGE TEETH: an AUTHORIZED field that has VANISHED from the schema -> None
    (could-not-measure, LOUD) — a rename/drop must surface, never silently narrow the gate.
    Out-of-envelope (forbidden + fail-closed-unknown) is NOT here — see count_forbidden()."""
    counts: Dict[str, Optional[int]] = {}
    present_p = set(_present_columns(cur, _T_PERSONS))
    present_s = set(_present_columns(cur, _T_STUDENTS))
    person_subq = (
        f"SELECT person_id FROM {_T_STUDENTS} WHERE org_id = %(o)s AND person_id IS NOT NULL"
    )
    for c in sorted(AUTHORIZED_CEILING[_T_PERSONS]):
        counts[f"{_T_PERSONS}.{c}"] = None if c not in present_p else _count(
            cur, f'SELECT count(*) FROM {_T_PERSONS} WHERE id IN ({person_subq}) AND "{c}" IS NOT NULL', org_id)
    for c in sorted(AUTHORIZED_CEILING[_T_STUDENTS]):
        counts[f"{_T_STUDENTS}.{c}"] = None if c not in present_s else _count(
            cur, f'SELECT count(*) FROM {_T_STUDENTS} WHERE org_id = %(o)s AND "{c}" IS NOT NULL', org_id)
    return counts


def _count(cur, sql: str, org_id: str) -> Optional[int]:
    try:
        cur.execute(sql, {"o": org_id})
        return int(cur.fetchone()[0])
    except Exception:
        return None  # could-not-measure -> surfaces via classify_could_not_measure


def _page(subject: str, body: str, priority: str = "P0", requires_response: bool = True) -> None:
    """Write a bus page from cc-fleet-health to orch-console. Substrate (DATABASE_URL).
    Carries only field labels + counts — never a PII value. `requires_response` is True
    for loud P0/P1 pages (breach / dead-man) and False for the low-freq non-P0 daily
    still-open reminder + the one-shot clear notice (they inform, they don't nag)."""
    import psycopg2
    conn = psycopg2.connect(os.environ["DATABASE_URL"]); conn.autocommit = True
    conn.cursor().execute(
        """INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, priority, requires_response, posted_by_identity)
           VALUES ('cc-fleet-health','orch-console','blocker',%s,%s,%s,%s,'cc-fleet-health')""",
        (subject, body, priority, requires_response))


# ─────────────────────────────────────────────────────────────────────────────
# SETTLE / DEDUP for the acknowledged breach state (orch-console #39889, APPROVED
# design; SRE #39886 proposal). GOAL: one loud P0 per DISTINCT breach state — never
# an hourly re-page of a known/frozen/reported breach — but LOUD P0 on ANY worsening
# (count climb past the acknowledged baseline = write-path FREEZE FAILED; a NEW field;
# a NEW org/vector). Detection itself is NEVER gated (guardrail #3): run_counts always
# runs, and the could-not-measure / connect-failed / missing-cred dead-man pages stay
# UNGATED-loud. This gates ONLY the re-page of an acknowledged BREACH state.
#   State keyed on {label: count} (counts INCLUDED so any increase re-fires). Cleared
# when the breach is gone (re-arm) OR when formally accepted via --accept (Musa sign-off
# per #39889 guardrail 4). A DAILY (not hourly) non-P0 reminder keeps a frozen-unremediated
# state from being forgotten. Reversible: delete the state file to reset; revert this file
# to restore the original always-page behavior.
# ─────────────────────────────────────────────────────────────────────────────
STATE_PATH = os.environ.get(
    "IRSYAD_PII_STATE_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "reports", "irsyad_pii_containment_state.json"))
# Separate state for the FORBIDDEN (out-of-envelope) gate — it is NEVER --accept'd, so it
# lives apart from the authorized-cohort state that --accept mutates.
STATE_PATH_FORBIDDEN = os.environ.get(
    "IRSYAD_PII_FORBIDDEN_STATE_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "reports", "irsyad_pii_forbidden_state.json"))
DAILY_REMINDER_S = int(os.environ.get("IRSYAD_PII_DAILY_S", str(24 * 3600)))


def _now_ts() -> int:
    return int(time.time())


def _load_state(path: str = STATE_PATH) -> dict:
    try:
        import json
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict, path: str = STATE_PATH) -> None:
    import json
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, path)  # atomic swap


def _fingerprint(items) -> dict:
    """items: list of (label,count) [breach/could-not-classify] or bare label [could-not-measure]
    -> {label: count-or-None}. Counts are part of the key so any change re-fires."""
    fp = {}
    for it in items:
        if isinstance(it, (list, tuple)):
            fp[str(it[0])] = it[1]
        else:
            fp[str(it)] = None
    return fp


def _is_worsening(cur_fp: dict, baseline_fp: dict) -> bool:
    """True if cur adds a NEW label vs baseline, any numeric count strictly INCREASES,
    or a previously-unmeasured(None) label now carries a real count. A decrease/removal
    (improvement) is NOT worsening. Never returns False when something got worse."""
    for label, cnt in cur_fp.items():
        if label not in baseline_fp:
            return True
        b = baseline_fp[label]
        if isinstance(cnt, int) and isinstance(b, int) and cnt > b:
            return True
        if b is None and isinstance(cnt, int):
            return True
    return False


def decide_notify(kind: str, items, prev: dict, now_ts: int, daily_s: int = DAILY_REMINDER_S):
    """PURE paging decision for a BREACH state. Returns (action, new_state, reason).
    action ∈ {'page','daily','suppress','clear','noop'}:
      page     — loud P0 (rr): first sighting of a state, or ANY worsening.
      daily    — low-freq non-P0 still-open reminder (>= daily_s since last notify).
      suppress — identical/improved acknowledged (or formally-accepted) state in window.
      clear    — breach gone -> re-arm (caller may emit a one-shot non-P0 note).
      noop     — nothing was open.
    Baseline for 'worsening' is the element-wise MAX of the last-known and the formally
    -accepted fingerprints, so a climb past EITHER re-fires (freeze-failed trips even
    after accept). NEVER suppresses a worsening."""
    prev = prev or {}
    prev_fp = prev.get("fingerprint") or {}
    accepted = bool(prev.get("accepted"))
    accepted_fp = prev.get("accepted_fingerprint") or {}
    first = prev.get("first_paged_at") or now_ts
    last_notified = prev.get("last_notified_at") or 0

    if kind == "ok":
        if prev_fp:
            return ("clear", {}, "breach cleared -> re-arm")
        return ("noop", {}, "ok; nothing open")

    cur_fp = _fingerprint(items)
    baseline = dict(prev_fp)
    for k, v in accepted_fp.items():
        if isinstance(v, int) and isinstance(baseline.get(k), int):
            baseline[k] = max(baseline[k], v)
        elif k not in baseline:
            baseline[k] = v

    worse = (not prev_fp and not accepted_fp) or _is_worsening(cur_fp, baseline)
    if kind == "breach" and prev.get("kind") not in (None, "breach"):
        worse = True  # amber -> red escalation is worsening

    if worse:
        return ("page",
                {"fingerprint": cur_fp, "kind": kind, "first_paged_at": now_ts,
                 "last_notified_at": now_ts, "accepted": False, "accepted_fingerprint": {}},
                "first sighting or worsening")

    new_state = {"fingerprint": cur_fp, "kind": kind, "first_paged_at": first,
                 "last_notified_at": last_notified, "accepted": accepted,
                 "accepted_fingerprint": accepted_fp}
    if accepted:
        # CEILING semantics (#39904, Musa sign-off op#20281): --accept authorizes the known
        # field-set up to the accepted counts = the cohort ceiling. We only reach here when NOT
        # worse (every count <= its ceiling, no new field/org), so an authorized within-cohort
        # rise (e.g. 900 -> ~1147 as enrollment completes) is FULLY suppressed (no daily). A
        # climb PAST the ceiling or a NEW field is 'worse' above and pages P0 (freeze-failed).
        return ("suppress", new_state, "authorized state within accepted cohort ceiling")
    if now_ts - last_notified >= daily_s:
        new_state["last_notified_at"] = now_ts
        return ("daily", new_state, "daily still-open reminder")
    return ("suppress", new_state, "identical acknowledged state within window")


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
            return run_counts(cur), count_unclassifiable(cur), count_forbidden(cur)
        finally:
            conn.close()

    try:
        counts, unclassifiable, forbidden = _retry(
            _read_goumlyne, attempts=_DB_ATTEMPTS,
            base_delay_s=_DB_RETRY_BASE_S, retry_on=psycopg2.OperationalError)
    except Exception as e:  # dead-man's-switch — a persistent failure STILL pages loud
        _page("🟠 Irsyad PII monitor CONNECT FAILED — containment UNVERIFIED",
              f"Could not read goumlyne to verify org {ORG_ID} deep-field zero: {str(e).splitlines()[0]}. "
              "Loud-fail, not green (dead-man's-switch).", priority="P1")
        return 2

    # ── FORBIDDEN gate (out-of-envelope F3/F4/F5) — HIGHEST priority, NEVER --accept'd, ──
    # ── bypasses the authorized-cohort ceiling. ANY nonzero -> P0. (#39946, runtime field-gate) ──
    f_breaches, f_unmeasured = classify_forbidden(forbidden)
    if f_unmeasured:  # can't verify the gate = LOUD dead-man, UNGATED (never suppressed)
        _page("🟠 Irsyad FORBIDDEN-gate could-not-measure — out-of-envelope UNVERIFIED",
              f"org {ORG_ID}: could-not-measure forbidden fields {f_unmeasured}. Not asserting the "
              "field-gate holds on absence (dead-man's-switch).", priority="P1")
    if f_breaches:
        fprev = _load_state(STATE_PATH_FORBIDDEN)  # forbidden state is never accepted
        f_act, f_new, _r = decide_notify("forbidden", f_breaches, fprev, _now_ts())
        flines = "\n".join(f"  {label}: {n}" for label, n in f_breaches)
        if f_act == "page":
            _page("🔴🔴 FORBIDDEN out-of-envelope PII populated — F3/F4/F5 field-gate TRIPPED (CAI-1030)",
                  f"org {ORG_ID}: OUT-OF-ENVELOPE fields/links now non-zero — medical/custody/parent are "
                  f"ruled OUT of the enrollment envelope; ANY nonzero is a gate breach:\n{flines}\n"
                  "Counts only (no rows read). NOT authorized by the ceiling; needs controller review NOW.",
                  priority="P0")
        elif f_act == "daily":
            _page("🔁 Irsyad FORBIDDEN-gate STILL TRIPPED — daily reminder (non-P0)",
                  f"org {ORG_ID}: out-of-envelope fields still populated (unchanged, already P0-reported):\n{flines}",
                  priority="P3", requires_response=False)
        _save_state(f_new, STATE_PATH_FORBIDDEN)
    else:
        fst = _load_state(STATE_PATH_FORBIDDEN)
        if fst.get("fingerprint"):  # forbidden was tripped, now cleared -> re-arm
            _page("✅ Irsyad FORBIDDEN-gate CLEARED — out-of-envelope fields back to zero",
                  f"org {ORG_ID}: forbidden fields/links all zero again; gate RE-ARMED.",
                  priority="P3", requires_response=False)
            _save_state({}, STATE_PATH_FORBIDDEN)

    prev_state = _load_state()
    code, priority, kind, items = decide_page(counts, unclassifiable)
    if kind == "breach":
        # SETTLE/DEDUP (#39889): one loud P0 per distinct state; LOUD on worsening;
        # daily non-P0 reminder; suppress an identical acknowledged/accepted state.
        had_prior = bool(prev_state.get("fingerprint"))
        action, new_state, reason = decide_notify(kind, items, prev_state, _now_ts())
        lines = "\n".join(f"  {label}: {n} non-null" for label, n in items)
        if action == "page":
            worsen = "  ⚠ WORSENING vs the acknowledged baseline — write-path freeze may have FAILED; verify who wrote.\n" \
                if had_prior else ""
            _page("🔴🔴 CONTAINMENT BREACH — Irsyad student deep PII POPULATED (CAI-1060 F2)",
                  f"The load-bearing zero broke for org {ORG_ID}. Deep fields now non-zero:\n{lines}\n{worsen}"
                  "Counts only (no rows read). F2 harm has materialised — controller authorisation + gate.",
                  priority="P0")
        elif action == "daily":
            _page("🔁 Irsyad PII containment STILL OPEN — daily reminder (non-P0)",
                  f"org {ORG_ID}: the acknowledged deep-PII breach is STILL unremediated (frozen, already "
                  f"reported via the P0 chain). Unchanged since first page. Current state:\n{lines}\n"
                  "Low-freq reminder so a frozen state isn't forgotten; NOT a new/worsening event. "
                  "Counts only. Clears on remediation or on formal sign-off (--accept).",
                  priority="P3", requires_response=False)
        # action == 'suppress' -> no page (identical acknowledged state within window)
        _save_state(new_state)
    elif kind == "could-not-classify":  # detector uncertainty — UNGATED (stays loud, guardrail #3)
        n = items[0][1]
        _page("🟠 Irsyad PII monitor — UNSTRUCTURED field populated (needs human eyes)",
              f"org {ORG_ID}: {n} persons now have a non-empty custom_fields/tags — an unstructured field "
              "the monitor cannot count-classify, so it may carry PII. NOT an auto-breach; a human must "
              "classify it (baseline was 0). Counts only, no values read.", priority="P1")
    elif kind == "could-not-measure":  # detector can't read — UNGATED dead-man (stays loud, guardrail #3)
        _page("🟠 Irsyad PII monitor PARTIAL — fields could not be measured",
              f"org {ORG_ID}: could-not-measure on {items}. Not asserting green on absence.", priority="P1")
    else:  # kind == 'ok': containment holds. If a breach was being tracked, it just cleared -> re-arm.
        if prev_state.get("fingerprint"):
            _page("✅ Irsyad PII containment RE-ESTABLISHED — deep-field zero restored",
                  f"org {ORG_ID}: all monitored deep-PII fields are back to zero; the load-bearing "
                  "containment zero is restored. Monitor state cleared + RE-ARMED (a future break pages fresh). "
                  "Counts only.", priority="P3", requires_response=False)
            _save_state({})
    return code  # 0 = all zero, containment holds — page nothing (page-only default)


def _read_state_once() -> Tuple[str, list]:
    """RO detector read -> (kind, items), for --seed. Counts only, no PII rows."""
    import psycopg2
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    except Exception:
        pass
    conn = psycopg2.connect(os.environ[_GOUMLYNE_ENV]); conn.autocommit = True
    try:
        cur = conn.cursor()
        counts = run_counts(cur); unclassifiable = count_unclassifiable(cur)
    finally:
        conn.close()
    _code, _pri, kind, items = decide_page(counts, unclassifiable)
    return kind, items


def _seed() -> int:
    """Stamp the CURRENT breach as the already-paged baseline so the acknowledged state
    (org 73339164, count=900 per #39864) is SUPPRESSED without a fresh page. Idempotent."""
    kind, items = _read_state_once()
    if kind != "breach":
        print(f"[seed] current kind={kind} (not a breach) — nothing to seed; state left empty.")
        return 0
    now = _now_ts()
    state = {"fingerprint": _fingerprint(items), "kind": kind, "first_paged_at": now,
             "last_notified_at": now, "accepted": False, "accepted_fingerprint": {}}
    _save_state(state)
    print(f"[seed] baseline written to {STATE_PATH}: {state['fingerprint']}")
    print("[seed] next run of the acknowledged state -> SUPPRESS (no fresh P0). Worsening still pages.")
    return 0


def _accept(ceiling: Optional[int] = None) -> int:
    """Formally ACCEPT the current tracked state (Musa sign-off op#20281, #39904). Suppresses
    the daily reminder while the state stays within the accepted ceiling — but a climb PAST the
    ceiling / a NEW field still pages LOUD (freeze-failed). Run when Nazim relays Musa's rule.
      --ceiling N : accept the known field-set up to N per field (the AUTHORIZED COHORT ceiling),
                    so an authorized rise up to N is suppressed and only >N (or a new field) trips.
      (no ceiling): pin acceptance at the CURRENT counts (a fixed, non-rising authorized state)."""
    st = _load_state()
    if not st.get("fingerprint"):
        print("[accept] no tracked breach state — nothing to accept.")
        return 1
    st["accepted"] = True
    if ceiling is not None:
        # Pre-authorize EVERY AUTHORIZED_CEILING field at N — INCLUDING currently-zero ones — so a
        # later authorized 0->N<=ceiling population (Shuq's enrich of address/fee_code/…) is
        # suppressed, not false-P0'd as a "new field". Out-of-envelope stays a fresh P0 (own gate).
        st["accepted_fingerprint"] = {k: ceiling for k in all_authorized_labels()}
        print(f"[accept] cohort CEILING = {ceiling} across ALL {len(all_authorized_labels())} authorized fields "
              f"(incl. currently-zero — an authorized 0->{ceiling} rise is suppressed; out-of-envelope still P0).")
    else:
        st["accepted_fingerprint"] = dict(st["fingerprint"])
        print(f"[accept] pinned at current counts (no ceiling): {st['accepted_fingerprint']}")
    _save_state(st)
    print("[accept] daily reminder suppressed within ceiling; a climb PAST it or a NEW field STILL pages P0.")
    return 0


def _selftest() -> int:
    """Wet-proof the pure decide_notify against the #39889 guardrails. No DB, no bus."""
    DAY = 24 * 3600
    B900 = [("persons.date_of_birth", 900), ("sch_students.emergency_contact_name", 900)]
    B950 = [("persons.date_of_birth", 950), ("sch_students.emergency_contact_name", 900)]
    B900_newfield = B900 + [("persons.address", 5)]
    seeded = {"fingerprint": _fingerprint(B900), "kind": "breach", "first_paged_at": 1000,
              "last_notified_at": 1000, "accepted": False, "accepted_fingerprint": {}}
    accepted = {**seeded, "accepted": True, "accepted_fingerprint": _fingerprint(B900)}
    # CEILING accept (#39904): authorize the field-set up to N=1147 (the cohort ceiling)
    B1147 = [("persons.date_of_birth", 1147), ("sch_students.emergency_contact_name", 1147)]
    B1200 = [("persons.date_of_birth", 1200), ("sch_students.emergency_contact_name", 1147)]
    B1147_newfield = B1147 + [("persons.address", 3)]
    accepted_ceil = {**seeded, "accepted": True,
                     "accepted_fingerprint": {"persons.date_of_birth": 1147,
                                              "sch_students.emergency_contact_name": 1147}}
    # DEPLOY-LOCKSTEP (#40044): --accept --ceiling pre-authorizes EVERY authorized field at N,
    # so a currently-ZERO authorized field populating <= N is suppressed (no false-P0), while
    # > N still pages. accepted_all mirrors the real _accept output.
    accepted_all = {**seeded, "accepted": True,
                    "accepted_fingerprint": {k: 1147 for k in all_authorized_labels()}}
    B_newauth = [("persons.date_of_birth", 1147), ("persons.address", 3)]      # address was 0, now 3
    B_newauth_over = [("persons.date_of_birth", 1147), ("persons.address", 1200)]  # > ceiling
    cases = [
        ("first sighting (no prev) -> page",        ("breach", B900, {}, 5000), "page"),
        ("identical acknowledged, in-window -> suppress", ("breach", B900, seeded, 1000 + 10), "suppress"),
        ("identical acknowledged, >24h -> daily",   ("breach", B900, seeded, 1000 + DAY + 1), "daily"),
        ("count climb 900->950 (freeze failed) -> page", ("breach", B950, seeded, 1000 + 10), "page"),
        ("new field populated -> page",             ("breach", B900_newfield, seeded, 1000 + 10), "page"),
        ("breach cleared (ok, had prev) -> clear",  ("ok", [], seeded, 9000), "clear"),
        ("ok, nothing open -> noop",                ("ok", [], {}, 9000), "noop"),
        ("accepted (fixed) identical, >24h -> suppress (no daily)", ("breach", B900, accepted, 1000 + 5 * DAY), "suppress"),
        ("accepted (fixed) but count climbs past -> page",  ("breach", B950, accepted, 1000 + 5 * DAY), "page"),
        # CEILING accept: authorized within-cohort rise suppressed; beyond-cohort / new field pages
        ("accepted-ceiling: within-cohort 900, >24h -> suppress (no daily)", ("breach", B900, accepted_ceil, 1000 + 5 * DAY), "suppress"),
        ("accepted-ceiling: authorized rise to 1147 -> suppress", ("breach", B1147, accepted_ceil, 1000 + 5 * DAY), "suppress"),
        ("accepted-ceiling: climb PAST 1147 (1200) -> page (freeze-failed)", ("breach", B1200, accepted_ceil, 1000 + 10), "page"),
        ("accepted-ceiling: NEW field beyond cohort -> page", ("breach", B1147_newfield, accepted_ceil, 1000 + 10), "page"),
        # DEPLOY-LOCKSTEP field-gate: full-authorized accept (all fields pre-accepted at ceiling)
        ("accepted-ALL: currently-zero authorized field 0->3 -> suppress (NO false-P0)", ("breach", B_newauth, accepted_all, 1000 + 5 * DAY), "suppress"),
        ("accepted-ALL: authorized field climbs 0->1200 (>ceiling) -> page (freeze-failed)", ("breach", B_newauth_over, accepted_all, 1000 + 10), "page"),
    ]
    # improvement then re-climb (stateful two-step)
    improved = decide_notify("breach", [("persons.date_of_birth", 500), ("sch_students.emergency_contact_name", 900)], seeded, 1000 + 10)
    ok = True
    for name, args, want in cases:
        action, _st, reason = decide_notify(*args)
        good = action == want
        ok = ok and good
        print(f"  [{'PASS' if good else 'FAIL'}] {name}: got '{action}' ({reason})")
    # verify improvement did NOT page, then a re-climb above the improved baseline DOES page
    imp_ok = improved[0] in ("suppress", "daily")
    reclimb = decide_notify("breach", [("persons.date_of_birth", 600), ("sch_students.emergency_contact_name", 900)], improved[1], 1000 + 20)
    reclimb_ok = reclimb[0] == "page"
    print(f"  [{'PASS' if imp_ok else 'FAIL'}] improvement 900->500 does not page: got '{improved[0]}'")
    print(f"  [{'PASS' if reclimb_ok else 'FAIL'}] re-climb 500->600 pages: got '{reclimb[0]}'")
    ok = ok and imp_ok and reclimb_ok
    # ── FORBIDDEN gate ──
    fb, fu = classify_forbidden({"sch_students.medical_notes": 0, "sch_student_parents.rows": 0, "persons(parent).pii": 0})
    fz = (fb == [] and fu == [])
    print(f"  [{'PASS' if fz else 'FAIL'}] forbidden all-zero -> no breach/unmeasured: {fb},{fu}")
    fb2, fu2 = classify_forbidden({"sch_students.medical_notes": 0, "sch_student_parents.rows": 3, "sch_students.custody_under_court_order": None})
    fnz = (fb2 == [("sch_student_parents.rows", 3)] and fu2 == ["sch_students.custody_under_court_order"])
    print(f"  [{'PASS' if fnz else 'FAIL'}] forbidden nonzero+None -> breach + unmeasured: {fb2},{fu2}")
    ffirst = decide_notify("forbidden", [("sch_student_parents.rows", 3)], {}, 5000)
    fsupp = decide_notify("forbidden", [("sch_student_parents.rows", 3)],
                          {"fingerprint": {"sch_student_parents.rows": 3}, "kind": "forbidden",
                           "first_paged_at": 1000, "last_notified_at": 1000, "accepted": False,
                           "accepted_fingerprint": {}}, 1000 + 10)
    fflow = (ffirst[0] == "page" and fsupp[0] == "suppress")
    print(f"  [{'PASS' if fflow else 'FAIL'}] forbidden first-sighting->page, identical->suppress: {ffirst[0]},{fsupp[0]}")
    ok = ok and fz and fnz and fflow
    # ── FAIL-CLOSED-UNKNOWN: an out-of-envelope UNKNOWN field (email/previous_school) populating
    # flows through the same forbidden gate as a breach (P0), and zero is benign. ──
    fb3, fu3 = classify_forbidden({"persons(student).email": 5, "sch_students.previous_school": 0, "persons(student).phone": 0})
    funk = (fb3 == [("persons(student).email", 5)] and fu3 == [])
    print(f"  [{'PASS' if funk else 'FAIL'}] fail-closed-unknown email=5 -> breach; zero-unknowns benign: {fb3},{fu3}")
    # classification sets are disjoint (a col is never both authorized AND ignored) — a silent
    # overlap would let an authorized field also count as fail-closed (double-page) or vice-versa.
    disjoint = all(not (AUTHORIZED_CEILING[t] & IGNORED_FIELDS[t]) for t in (_T_PERSONS, _T_STUDENTS))
    print(f"  [{'PASS' if disjoint else 'FAIL'}] AUTHORIZED_CEILING and IGNORED_FIELDS are disjoint per table")
    ok = ok and funk and disjoint
    print(f"\n{'ALL PASS' if ok else 'FAILURES PRESENT'}")
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    import sys
    _args = sys.argv[1:]
    if "--selftest" in _args:
        raise SystemExit(_selftest())
    if "--seed" in _args:
        raise SystemExit(_seed())
    if "--accept" in _args:
        _ceil = None
        if "--ceiling" in _args:
            _ci = _args.index("--ceiling")
            if _ci + 1 < len(_args):
                _ceil = int(_args[_ci + 1])
        raise SystemExit(_accept(ceiling=_ceil))
    raise SystemExit(main())
