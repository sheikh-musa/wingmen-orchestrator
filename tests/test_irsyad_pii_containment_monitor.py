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
