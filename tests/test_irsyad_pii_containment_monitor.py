"""CAI-RESP-1060: the standing deep-field-zero containment monitor for Madrasah
Irsyad student PII. The 890 minors are contained BECAUSE the deep fields are all
zero (a load-bearing fact); any non-zero => F2 harm => P0.

These tests pin the parts that must not silently rot:
  * the breach decision (any non-zero count is a breach),
  * fail-LOUD on a could-not-measure (a monitor that can't read must not read green),
  * and — the one that matters most on a minors'-PII monitor — the FIELD SET is
    complete: it covers persons (where DOB/address/phone/email/nric actually live,
    not sch_students) AND every encrypted/hash variant, so a future edit cannot
    quietly drop `nric_hash` and re-introduce the false-green the schema check caught.

COUNTS ONLY — the monitor never reads a row; nothing here asserts on PII values.
"""
from scripts import irsyad_pii_containment_monitor as M


def test_all_zero_is_no_breach():
    counts = {"persons.date_of_birth": 0, "sch_students.medical_notes": 0,
              "sch_student_parents.rows": 0}
    assert M.classify_breaches(counts) == []


def test_any_nonzero_is_a_breach_naming_the_field():
    counts = {"persons.date_of_birth": 0, "persons.nric_hash": 4, "sch_students.medical_notes": 0}
    breaches = M.classify_breaches(counts)
    assert breaches == [("persons.nric_hash", 4)]


def test_could_not_measure_is_loud_not_green():
    """A None count (query failed) is NOT zero — it is a measurement failure and must
    surface (dead-man's-switch), never be treated as containment holding."""
    counts = {"persons.address": None, "persons.date_of_birth": 0}
    assert M.classify_could_not_measure(counts) == ["persons.address"]


def test_discovery_is_schema_derived_and_finds_every_live_pii_variant():
    """CAI-1060 / Nazim #24985: the PII column set is DISCOVERED from the catalog at
    runtime, not hardcoded — so a NEW variant (nric_hash_v3, a new contact field) is
    auto-covered instead of silently re-opening the false-green. This runs against the
    LIVE goumlyne schema and asserts discovery finds every known variant AND excludes
    the shallow/non-PII columns (id, org_id, display_name, gender)."""
    import os
    import psycopg2
    conn = psycopg2.connect(os.environ["GOUMLYNE_DATABASE_URL"]); conn.autocommit = True
    cur = conn.cursor()
    persons = set(M.discover_pii_columns(cur, "persons"))
    for c in ("date_of_birth", "address",
              "phone", "phone_encrypted", "phone_hash", "phone_hash_v2",
              "email", "email_encrypted", "email_hash", "email_hash_v2",
              "nric_encrypted", "nric_hash", "nric_source", "nric_hash_v2"):
        assert c in persons, f"discovery missed persons.{c} (false-green risk)"
    for c in ("id", "org_id", "display_name", "gender", "created_at", "person_id"):
        assert c not in persons, f"discovery over-matched non-PII persons.{c}"
    students = set(M.discover_pii_columns(cur, "sch_students"))
    for c in ("emergency_contact", "medical_notes", "previous_school"):
        assert c in students
    for c in ("id", "org_id", "student_number", "status"):
        assert c not in students


def test_coverage_floor_guard_fires_when_discovery_shrinks():
    """assert-the-total teeth: if discovery finds FEWER columns than the pinned floor
    (a rename/removal that could hide a field), that is a could-not-measure, not green."""
    # 2 discovered but floor is 14 -> shortfall surfaces
    assert M.coverage_shortfall("persons", 2) is True
    assert M.coverage_shortfall("persons", M.EXPECTED_MIN["persons"]) is False


def test_org_id_is_the_verified_madrasah_irsyad_org():
    assert M.ORG_ID == "73339164-7c1f-40ba-a093-33f1f292dd4c"


# ── outcome tiering (Nazim #25011: red breach vs amber can't-measure/can't-classify) ──

def test_decide_ok_when_all_zero_and_nothing_unclassifiable():
    assert M.decide_page({"persons.address": 0}, 0) == (0, None, "ok", [])


def test_decide_scalar_breach_is_red_p0():
    code, pri, kind, _ = M.decide_page({"persons.nric_hash": 3}, 0)
    assert (code, pri, kind) == (1, "P0", "breach")


def test_decide_could_not_measure_is_amber_p1():
    code, pri, kind, _ = M.decide_page({"persons.address": None}, 0)
    assert (code, pri, kind) == (2, "P1", "could-not-measure")


def test_decide_unclassifiable_customfields_is_amber_p1_not_p0():
    """custom_fields/tags non-empty = an unstructured field we can't count-classify =
    potential PII for human eyes (amber), NOT an auto-P0 breach (#25011)."""
    code, pri, kind, _ = M.decide_page({"persons.address": 0}, 5)
    assert (code, pri, kind) == (2, "P1", "could-not-classify")


def test_decide_breach_precedes_unclassifiable():
    code, pri, kind, _ = M.decide_page({"persons.nric_hash": 1}, 9)
    assert (code, pri, kind) == (1, "P0", "breach")


def test_decide_unclassifiable_none_is_could_not_measure():
    code, pri, kind, _ = M.decide_page({"persons.address": 0}, None)
    assert (code, pri, kind) == (2, "P1", "could-not-measure")


# ── PROVE-FIRED: end-to-end synthetic breach on a STAND-IN (never the live silo) ──────
# Nazim #25011: "a detector nobody has watched fail is untested." This builds a
# stand-in with the real column shape on the substrate (rolled back), points the
# detector's table constants at it, and proves run_counts -> decide_page fires P0 on a
# populated deep field, amber on the custom_fields backdoor, and OK when clean.

_PERSONS_PII = ("date_of_birth date", "address text", "phone text", "phone_encrypted text",
                "phone_hash text", "phone_hash_v2 text", "email text", "email_encrypted text",
                "email_hash text", "email_hash_v2 text", "nric_encrypted text", "nric_hash text",
                "nric_source text", "nric_hash_v2 text")


def _standin(cur):
    import os
    cols = ", ".join(_PERSONS_PII)
    cur.execute(f"""CREATE TABLE _sre_persons (id uuid PRIMARY KEY, org_id uuid,
        display_name text, custom_fields jsonb, tags text[], {cols})""")
    cur.execute("""CREATE TABLE _sre_students (id uuid PRIMARY KEY, org_id uuid, person_id uuid,
        student_number text, status text, emergency_contact text, medical_notes text, previous_school text)""")
    cur.execute("CREATE TABLE _sre_parents (id uuid PRIMARY KEY, org_id uuid)")


def _seed_one(cur, org):
    cur.execute("INSERT INTO _sre_persons (id, org_id, display_name) VALUES (gen_random_uuid(), %s, 'shell') RETURNING id", (org,))
    pid = cur.fetchone()[0]
    cur.execute("INSERT INTO _sre_students (id, org_id, person_id) VALUES (gen_random_uuid(), %s, %s)", (org, pid))
    return pid


def test_prove_fired_end_to_end(monkeypatch):
    import os, psycopg2
    conn = psycopg2.connect(os.environ["DATABASE_URL"]); conn.autocommit = False
    cur = conn.cursor()
    try:
        _standin(cur)
        monkeypatch.setattr(M, "_T_PERSONS", "_sre_persons")
        monkeypatch.setattr(M, "_T_STUDENTS", "_sre_students")
        monkeypatch.setattr(M, "_T_PARENTS", "_sre_parents")
        org = "73339164-7c1f-40ba-a093-33f1f292dd4c"
        pid = _seed_one(cur, org)

        # 1) clean shell record -> OK (containment holds)
        counts = M.run_counts(cur, org); uncl = M.count_unclassifiable(cur, org)
        assert M.decide_page(counts, uncl)[:3] == (0, None, "ok"), "clean state must be OK"

        # 2) a deep field populates -> P0 RED breach naming the field
        cur.execute("UPDATE _sre_persons SET nric_hash = 'SYNTH' WHERE id = %s", (pid,))
        counts = M.run_counts(cur, org); uncl = M.count_unclassifiable(cur, org)
        code, pri, kind, items = M.decide_page(counts, uncl)
        assert (code, pri, kind) == (1, "P0", "breach")
        assert any(lbl == "persons.nric_hash" for lbl, _ in items), "breach must name the field"

        # 3) revert the scalar, populate the jsonb backdoor -> P1 AMBER could-not-classify
        cur.execute("UPDATE _sre_persons SET nric_hash = NULL, custom_fields = '{\"dob\":\"x\"}'::jsonb WHERE id = %s", (pid,))
        counts = M.run_counts(cur, org); uncl = M.count_unclassifiable(cur, org)
        assert M.decide_page(counts, uncl)[:3] == (2, "P1", "could-not-classify")
    finally:
        conn.rollback(); conn.close()


# ── retry-before-page on the goumlyne connect (port of ad38b99/f326d3e; 2026-09-03 pooler-blip
#    sweep, Nazim GATE-approved). A transient Supabase pooler-DNS blip must NOT trip the P1
#    "containment UNVERIFIED" dead-man; a PERSISTENT failure STILL pages loud (CAI-1060 zero
#    must never be silently un-monitored).
import pytest as _pytest


class _Transient(Exception):
    """Stand-in for psycopg2.OperationalError."""


def test_retry_recovers_after_transient():
    n = {"c": 0}
    slept = []
    def op():
        n["c"] += 1
        if n["c"] < 3:
            raise _Transient("could not translate host name pooler.supabase.com")
        return "ok"
    assert M._retry(op, attempts=3, base_delay_s=0.01, retry_on=_Transient, sleep=slept.append) == "ok"
    assert n["c"] == 3 and slept == [0.01, 0.02]


def test_retry_reraises_on_exhaustion():
    def op():
        raise _Transient("down")
    with _pytest.raises(_Transient):
        M._retry(op, attempts=3, base_delay_s=0.01, retry_on=_Transient, sleep=lambda s: None)


def test_retry_skips_non_transient():
    n = {"c": 0}
    def op():
        n["c"] += 1
        raise ValueError("x")
    with _pytest.raises(ValueError):
        M._retry(op, attempts=3, base_delay_s=0.01, retry_on=_Transient, sleep=lambda s: None)
    assert n["c"] == 1


class _FakeConn:
    autocommit = False
    def cursor(self):
        return object()
    def close(self):
        pass


def _prep_main(monkeypatch):
    import psycopg2
    monkeypatch.setenv(M._GOUMLYNE_ENV, "postgres://goumlyne-ignored")
    monkeypatch.setattr(M, "_sleep", lambda s: None)
    monkeypatch.setattr(M, "run_counts", lambda cur, **k: {})
    monkeypatch.setattr(M, "count_unclassifiable", lambda cur, **k: 0)
    monkeypatch.setattr(M, "decide_page", lambda counts, unc: ("ok", None, "none", []))
    paged = []
    monkeypatch.setattr(M, "_page", lambda subject, body, priority="P0": paged.append((subject, priority)))
    return psycopg2, paged


def test_main_recovers_from_transient_goumlyne_blip(monkeypatch):
    """goumlyne connect raises OperationalError ONCE (pooler DNS blip) then succeeds -> main
    recovers and does NOT page 'containment UNVERIFIED'."""
    psycopg2, paged = _prep_main(monkeypatch)
    n = {"c": 0}
    def fake_connect(dsn):
        n["c"] += 1
        if n["c"] == 1:
            raise psycopg2.OperationalError(
                "could not translate host name aws-1-ap-southeast-1.pooler.supabase.com")
        return _FakeConn()
    monkeypatch.setattr(psycopg2, "connect", fake_connect)
    rc = M.main()
    assert n["c"] == 2, "must retry the transient blip"
    assert not any("CONNECT FAILED" in s for s, _ in paged), "a transient blip must NOT page containment-UNVERIFIED"


def test_main_persistent_goumlyne_failure_still_pages_dead_man(monkeypatch):
    """A GENUINE persistent goumlyne outage MUST still P1-page 'containment UNVERIFIED' after
    the retry budget — the CAI-1060 zero is never silently un-monitored."""
    psycopg2, paged = _prep_main(monkeypatch)
    def fake_connect(dsn):
        raise psycopg2.OperationalError("persistent outage")
    monkeypatch.setattr(psycopg2, "connect", fake_connect)
    rc = M.main()
    assert rc == 2
    assert any("CONNECT FAILED" in s and p == "P1" for s, p in paged), \
        "persistent failure MUST P1-page containment-UNVERIFIED (dead-man preserved)"
