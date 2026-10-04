"""test_stage_client_file.py — synthetic wet-prove for stage_client_file.py
(orch-console bus #51060, Musa op#25437-25440).

SYNTHETIC ONLY — no real client data. Proves: (a) PII-shape scanning is
COUNT-ONLY and never leaks a matched value into the rendered report, (b) the
CLEAN/HOLD verdict is FAIL-CLOSED (any PII hit or person-record signal -> HOLD),
(c) each format extractor (csv/xlsx/docx/pdf) produces the right structure, (d)
--export only ever writes on CLEAN, never on HOLD, and the exported file
carries no raw value either.
"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts import stage_client_file as scf  # noqa: E402

# ── scan_pii_counts: each shape, count-only ──────────────────────────────────

def test_scan_pii_counts_finds_nric():
    c = scf.scan_pii_counts("participant S1234567D attended")
    assert c.nric_fin_bc == 1 and c.total() == 1


def test_scan_pii_counts_finds_emirates_id():
    c = scf.scan_pii_counts("id 784-1990-1234567-1 on file")
    assert c.emirates_id == 1 and c.total() == 1


def test_scan_pii_counts_finds_email():
    c = scf.scan_pii_counts("contact syn@example.test for details")
    assert c.email == 1 and c.total() == 1


def test_scan_pii_counts_finds_phone_shaped_number():
    c = scf.scan_pii_counts("call +65 9123 4567 tomorrow")
    assert c.phone == 1


def test_scan_pii_counts_finds_long_number_not_already_a_phone():
    c = scf.scan_pii_counts("account 123456789012345")
    assert c.long_number == 1


def test_scan_pii_counts_zero_for_clean_text():
    c = scf.scan_pii_counts("total amount 120.00 paid on schedule")
    assert c.total() == 0


def test_merge_counts_sums_every_field():
    merged = scf.merge_counts([
        scf.PiiCounts(nric_fin_bc=1, email=2),
        scf.PiiCounts(email=1, phone=3),
    ])
    assert (merged.nric_fin_bc, merged.email, merged.phone) == (1, 3, 3)


# ── person-record heuristics ─────────────────────────────────────────────────

def test_person_record_header_matches_keyword():
    assert scf.is_person_record_header(["Full Name", "Amount"]) is True


def test_person_record_header_no_match_for_unrelated_headers():
    assert scf.is_person_record_header(["Date", "Amount", "Category"]) is False


def test_name_like_column_detected():
    assert scf.has_name_like_column(["Donor Name", "Amount"]) is True
    assert scf.has_name_like_column(["Amount", "Category"]) is False


# ── classify(): the fail-closed CLEAN/HOLD core ──────────────────────────────

def _structure(header, rows, texts=None, kind="csv"):
    return scf.FileStructure(
        kind=kind,
        sheets=[scf.SheetStructure(name="s1", rows=rows, cols=len(header), header=header)],
        image_count=0,
        texts=texts if texts is not None else [],
    )


def test_classify_clean_for_boring_numeric_sheet():
    v = scf.classify(_structure(["Date", "Amount"], rows=5, texts=["2026-01-01 120.00"]))
    assert v.verdict == "CLEAN"
    assert v.person_record is False
    assert v.pii.total() == 0


def test_classify_holds_on_pii_shape_hit():
    v = scf.classify(_structure(["Date", "Amount"], rows=5, texts=["contact syn@example.test"]))
    assert v.verdict == "HOLD"
    assert v.pii.email == 1
    assert any("PII-shaped" in r for r in v.reasons)


def test_classify_holds_on_header_keyword_regardless_of_row_count():
    # a single row is enough -- heuristic 1 is not gated on row count.
    v = scf.classify(_structure(["Full Name", "Amount"], rows=1, texts=["x"]))
    assert v.verdict == "HOLD"
    assert v.person_record is True
    assert any("header matches" in r for r in v.reasons)


def test_classify_holds_on_large_row_count_with_name_like_column():
    v = scf.classify(_structure(["Name", "Amount"], rows=25, texts=["x"]))
    assert v.verdict == "HOLD"
    assert v.person_record is True
    assert any("25 rows with a name-like column" in r for r in v.reasons)


def test_classify_never_leaks_the_matched_value_into_the_rendered_report():
    v = scf.classify(_structure(["Date", "Amount"], rows=5, texts=["contact syn@example.test"]))
    rendered = v.render(_structure(["Date", "Amount"], rows=5))
    assert "syn@example.test" not in rendered
    assert "email=1" in rendered


# ── format extractors (real tiny files, built on the fly, synthetic only) ───

def test_extract_csv_reports_structure(tmp_path):
    p = tmp_path / "sample.csv"
    p.write_text("Date,Amount\n2026-01-01,100.00\n2026-01-02,50.00\n", encoding="utf-8")
    structure = scf.extract_csv(p)
    assert structure.kind == "csv"
    assert structure.sheets[0].rows == 2
    assert structure.sheets[0].header == ["Date", "Amount"]
    assert structure.image_count == 0


def test_extract_xlsx_reports_sheet_structure(tmp_path):
    openpyxl = __import__("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Date", "Amount"])
    ws.append(["2026-01-01", 100.0])
    ws.append(["2026-01-02", 50.0])
    p = tmp_path / "sample.xlsx"
    wb.save(str(p))

    structure = scf.extract_xlsx(p)
    assert structure.kind == "xlsx"
    assert structure.sheets[0].name == "Sheet1"
    assert structure.sheets[0].rows == 2
    assert structure.sheets[0].header == ["Date", "Amount"]
    assert structure.image_count == 0


def test_extract_docx_reports_table_structure(tmp_path):
    docx = __import__("docx")
    d = docx.Document()
    table = d.add_table(rows=3, cols=2)
    table.rows[0].cells[0].text = "Date"
    table.rows[0].cells[1].text = "Amount"
    table.rows[1].cells[0].text = "2026-01-01"
    table.rows[1].cells[1].text = "100.00"
    table.rows[2].cells[0].text = "2026-01-02"
    table.rows[2].cells[1].text = "50.00"
    p = tmp_path / "sample.docx"
    d.save(str(p))

    structure = scf.extract_docx(p)
    assert structure.kind == "docx"
    assert structure.sheets[0].rows == 2
    assert structure.sheets[0].header == ["Date", "Amount"]
    assert structure.image_count == 0


def test_extract_docx_without_tables_still_reports_a_pseudo_sheet(tmp_path):
    docx = __import__("docx")
    d = docx.Document()
    d.add_paragraph("just prose, no table")
    p = tmp_path / "prose.docx"
    d.save(str(p))

    structure = scf.extract_docx(p)
    assert structure.sheets[0].name == "(document body)"


def test_extract_pdf_reports_text_and_page_structure(tmp_path):
    fitz = __import__("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "total amount paid 120.00")
    p = tmp_path / "sample.pdf"
    doc.save(str(p))
    doc.close()

    structure = scf.extract_pdf(p)
    assert structure.kind == "pdf"
    assert structure.image_count == 0
    assert any("120.00" in t for t in structure.texts)


def test_extract_unsupported_extension_refuses(tmp_path):
    p = tmp_path / "sample.txt"
    p.write_text("x", encoding="utf-8")
    import pytest

    with pytest.raises(SystemExit, match="unsupported extension"):
        scf.extract(p)


def test_extract_parse_error_never_leaks_content(tmp_path, monkeypatch):
    import pytest

    p = tmp_path / "sample.csv"
    p.write_text("Date,Amount\n", encoding="utf-8")

    def _boom(path):
        raise ValueError("synthetic-secret-value-should-never-appear")

    monkeypatch.setitem(scf._EXTRACTORS, ".csv", _boom)
    with pytest.raises(SystemExit) as exc:
        scf.extract(p)
    assert "synthetic-secret-value-should-never-appear" not in str(exc.value)


# ── sanitize_op_id + export_report ───────────────────────────────────────────

def test_sanitize_op_id_strips_unsafe_characters():
    assert scf.sanitize_op_id("op#25437") == "op_25437"
    assert scf.sanitize_op_id("") == "unknown"


def test_export_report_writes_under_sanitized_op_id_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out_path = scf.export_report("verdict: CLEAN\n...", "op#25437", "sample.csv")
    assert out_path.resolve() == tmp_path / "reports" / "client-file-staging" / "op_25437" / "sample.md"
    assert out_path.read_text(encoding="utf-8").startswith("verdict: CLEAN")


# ── CLI main(): CLEAN exports, HOLD never writes anything ───────────────────

def test_main_clean_csv_with_export_writes_report_and_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "clean.csv"
    p.write_text("Date,Amount\n2026-01-01,100.00\n", encoding="utf-8")

    rc = scf.main([str(p), "op25437", "--export"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "verdict: CLEAN" in out
    exported = tmp_path / "reports" / "client-file-staging" / "op25437" / "clean.md"
    assert exported.exists()
    assert "100.00" not in exported.read_text(encoding="utf-8")  # structure/report only, never a raw cell


def test_main_hold_csv_never_exports_anything(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "holdme.csv"
    p.write_text("Date,Contact\n2026-01-01,syn@example.test\n", encoding="utf-8")

    rc = scf.main([str(p), "op25438"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "verdict: HOLD" in out
    assert "syn@example.test" not in out
    report_dir = tmp_path / "reports" / "client-file-staging" / "op25438"
    assert not report_dir.exists()


def test_main_without_export_never_writes_even_on_clean(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "clean.csv"
    p.write_text("Date,Amount\n2026-01-01,100.00\n", encoding="utf-8")

    rc = scf.main([str(p), "op25439"])
    assert rc == 0
    assert not (tmp_path / "reports").exists()


def test_main_refuses_missing_file(tmp_path, capsys):
    rc = scf.main([str(tmp_path / "nope.csv"), "op1"])
    assert rc == 2
    assert "no such file" in capsys.readouterr().err
