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


def test_field_set_covers_all_three_tables_and_every_pii_variant():
    """The false-green guard: the monitored set must include the persons deep cols
    (DOB/address live there, not on sch_students) AND every phone/email/nric variant.
    Dropping any of these silently would let that field populate undetected."""
    labels = set(M.monitored_labels())
    # persons is where the deep PII actually lives
    for c in ("date_of_birth", "address",
              "phone", "phone_encrypted", "phone_hash", "phone_hash_v2",
              "email", "email_encrypted", "email_hash", "email_hash_v2",
              "nric_encrypted", "nric_hash", "nric_source", "nric_hash_v2"):
        assert f"persons.{c}" in labels, f"missing persons.{c} (false-green risk)"
    # sch_students student-specific deep cols
    for c in ("emergency_contact", "medical_notes", "previous_school"):
        assert f"sch_students.{c}" in labels
    # parent links
    assert "sch_student_parents.rows" in labels


def test_org_id_is_the_verified_madrasah_irsyad_org():
    assert M.ORG_ID == "73339164-7c1f-40ba-a093-33f1f292dd4c"
