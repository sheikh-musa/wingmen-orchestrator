"""Synthetic wet-prove for pii_safe_file_inspector (CAI-1424 / cc-storefront #354/#356).

SYNTHETIC ONLY — no real client data (CAI-1400). Proves the inspector: (a) shows
structure + the date FORMAT a builder needs, (b) FAIL-CLOSED-masks every PII column
INCLUDING a name in a freeform notes column (the leak pattern-scan-alone misses),
(c) never emits a raw PII value anywhere in its output, (d) reduces errors to
(row, class) with no value.
"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.lib.pii_safe_file_inspector import inspect_rows, inspect_csv  # noqa: E402

HEADER = ["Value Date", "Amount", "Donor Name", "Customer Email", "Phone", "Notes", "created (metadata)"]
# Synthetic rows — fabricated, resemble a GIRO/Stripe export shape. NO real people.
ROWS = [
    ["03/01/2026 10:05:00", "120.00", "Fatimah Synthetic", "syn1@example.test", "+65 9123 4567",
     "met Siti re her son's programme", "1767225600"],
    ["04/01/2026 11:30:00", "80.50", "Ahmad Fixture", "syn2@example.test", "91234567",
     "follow up next week", "1767312000"],
    ["05/01/2026 09:00:00", "200.00", "Test Donor Three", "syn3@example.test", "+65 8000 0000",
     "pledge", "1767398400"],
]


def _col(report, name):
    return next(c for c in report.columns if c.name == name)


def test_date_column_is_unmasked_and_shows_the_format():
    rep = inspect_rows(HEADER, ROWS)
    vd = _col(rep, "Value Date")
    assert vd.masked is False
    assert vd.inferred_type == "date"
    assert "n/n/YYYY" in vd.detail and "time" in vd.detail  # the format a builder needs


def test_amount_is_unmasked_numeric():
    rep = inspect_rows(HEADER, ROWS)
    amt = _col(rep, "Amount")
    assert amt.masked is False
    assert amt.inferred_type == "number"


def test_name_email_phone_are_masked():
    rep = inspect_rows(HEADER, ROWS)
    for name in ("Donor Name", "Customer Email", "Phone"):
        assert _col(rep, name).masked is True, f"{name} must be masked"
        assert _col(rep, name).detail == "***"


def test_freeform_notes_with_a_name_is_masked_by_default():
    # The leak pattern-scan-alone misses: "met Siti..." has no regex signature.
    # Allowlist-baseline (text masked by default) is what catches it.
    rep = inspect_rows(HEADER, ROWS)
    assert _col(rep, "Notes").masked is True


def test_epoch_column_is_unmasked_and_recognised():
    rep = inspect_rows(HEADER, ROWS)
    ep = _col(rep, "created (metadata)")
    assert ep.masked is False
    assert "epoch" in ep.detail


def test_no_raw_pii_value_anywhere_in_the_rendered_output():
    rep = inspect_rows(HEADER, ROWS)
    out = rep.render()
    # None of the synthetic PII values may appear in the output.
    for leak in ["Fatimah", "Ahmad", "Test Donor", "syn1@example.test", "syn2@example.test",
                 "9123", "91234567", "8000", "Siti", "her son"]:
        assert leak not in out, f"LEAK: {leak!r} appeared in inspector output"


def test_headers_are_shown_they_are_schema_not_pii():
    rep = inspect_rows(HEADER, ROWS)
    out = rep.render()
    for h in HEADER:
        assert h in out, f"header {h!r} should be shown"


def test_allowlist_unmasks_a_named_column():
    rep = inspect_rows(HEADER, ROWS, allowlist={"Donor Name"})
    assert _col(rep, "Donor Name").masked is False  # caller's explicit override


def test_ragged_row_does_not_leak_or_crash():
    ragged = ROWS + [["06/01/2026 09:00:00", "10.00"]]  # short row
    rep = inspect_rows(HEADER, ragged)
    out = rep.render()
    assert "Fatimah" not in out
    # still classifies the columns present
    assert _col(rep, "Value Date").inferred_type == "date"


def test_malformed_csv_never_raises_to_caller(tmp_path):
    # [cai CAI-RESP-1424 gate canary] An oversized field raises csv.Error DURING
    # iteration. The tool must degrade it to a (row, read_error) note, never let it
    # propagate (leak-vector 3 / the file's own 'every read is wrapped' guarantee).
    import csv
    p = tmp_path / "malformed.csv"
    with open(p, "w", newline="") as fh:
        fh.write("Value Date,Amount,Notes\n")
        fh.write("03/01/2026,10.00,ok\n")
        fh.write("04/01/2026,20.00," + ("x" * (csv.field_size_limit() + 1000)) + "\n")
        fh.write("05/01/2026,30.00,ok\n")
    # Must NOT raise:
    rep = inspect_csv(str(p))
    out = rep.render()
    assert rep.row_count >= 1
    assert any("read_error" in n for n in rep.parse_notes), "malformed row should be noted, not raised"
    assert "x" * 100 not in out  # the oversized field content never appears


def test_csv_roundtrip(tmp_path):
    import csv
    p = tmp_path / "syn.csv"
    with open(p, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(ROWS)
    rep = inspect_csv(str(p))
    out = rep.render()
    assert _col(rep, "Customer Email").masked is True
    assert _col(rep, "Value Date").masked is False
    assert "syn1@example.test" not in out
    assert "Fatimah" not in out


# ── VALUE-SHAPE profile for MASKED columns (console, Musa op#20591) ────────────
# Aggregates ONLY — never a value. Catches the class of quirk that only showed on
# the client's screen this week (honorific abbreviations, "Hamba Allah" markers,
# apostrophes/mojibake) without opening the data.
SHAPE_HEADER = ["Amount", "Donor Name", "Customer Email", "Created date (UTC)"]
SHAPE_ROWS = [
    ["10", "Siti Aminah Bte Ahmad", "a@example.test", "2026-02-01 10:00:00"],
    ["20", "Hamba Allah", "b@example.test", "2026-02-02 10:00:00"],
    ["30", "HAMBA ALLAH", "c@example.test", "02/03/2026 10:00:00"],
    ["40", "Md Faizal", "d@example.test", "2026-02-04 10:00:00"],
    ["50", "O'Neil-Rahman", "e@example.test", "2026-02-05 10:00:00"],
    ["60", "Nurâ€™aini", "f@example.test", "2026-02-06 10:00:00"],
    ["70", "", "g@example.test", "2026-02-07 10:00:00"],
]


def test_masked_text_column_carries_a_value_shape_with_no_values():
    rep = inspect_rows(SHAPE_HEADER, SHAPE_ROWS)
    dn = _col(rep, "Donor Name")
    assert dn.masked is True and dn.detail == "***"
    assert dn.shape is not None
    assert dn.shape["words"] == {"1": 0, "2": 4, "3": 0, "4+": 1} or dn.shape["words"]["2"] == 4
    assert dn.shape["markers"]["anonymous"] == 2          # "Hamba Allah" + "HAMBA ALLAH"
    assert dn.shape["punct"]["apostrophe"] >= 1
    assert dn.shape["punct"]["hyphen"] >= 1
    assert dn.shape["punct"]["mojibake"] == 1             # "â€™"
    assert dn.shape["case"]["upper"] == 1
    assert dn.shape["distinct"] == 6
    rendered = rep.render()
    for raw in ("Siti", "Aminah", "Faizal", "O'Neil", "Nur", "example.test"):
        assert raw not in rendered, f"value leaked: {raw}"
    assert "markers" in rendered and "words" in rendered


def test_email_column_shape_counts_email_shaped_values_only():
    rep = inspect_rows(SHAPE_HEADER, SHAPE_ROWS)
    ce = _col(rep, "Customer Email")
    assert ce.masked is True
    assert ce.shape["email_shaped"] == 7
    assert "@example.test" not in rep.render()


def test_date_column_reports_a_format_mix_not_just_the_first_sample():
    rep = inspect_rows(SHAPE_HEADER, SHAPE_ROWS)
    cd = _col(rep, "Created date (UTC)")
    assert cd.masked is False
    assert "ISO YYYY-MM-DD + time: 6" in cd.detail
    assert "n/n/YYYY + time" in cd.detail and ": 1" in cd.detail


def test_unmasked_columns_have_no_shape_block():
    rep = inspect_rows(SHAPE_HEADER, SHAPE_ROWS)
    assert _col(rep, "Amount").shape is None
