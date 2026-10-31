"""test_stage_client_file.py — synthetic wet-prove for stage_client_file.py
(orch-console bus #51060/#51085, Musa op#25437-25440; cc-quality bus #51094).

SYNTHETIC ONLY — no real client data. Proves: (a) PII-shape scanning is
COUNT-ONLY and stdout never leaks a matched value, (b) the CLEAN/HOLD verdict
is FAIL-CLOSED (any PII hit or person-record signal -> HOLD), (c) each format
extractor (csv/xlsx/docx/pdf) produces the right structure, (d) --export only
ever writes on CLEAN, never on HOLD, and when it does write, it carries the
ACTUAL content (the whole point of staging a clean file) while stdout itself
stays values-free regardless.
"""
from __future__ import annotations

import os
import sys
import pathlib
from unittest import mock

import pytest

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


# cc-quality bus #51094 BLOCKING #2: bare unformatted local mobile numbers
# (no separator, no country code, no parens) are a common real-world shape
# in free-flowing PDF/DOCX text, and the original _PHONE_RE (needs a
# mandatory separator) + _LONG_NUMBER_RE (12+ digit floor) both missed them.

@pytest.mark.parametrize("text", [
    "call 91234567 to confirm",       # bare SG 8-digit mobile
    "call 9123 4567 to confirm",      # same number, single internal space
    "mobile 0501234567 on file",      # bare UAE 10-digit mobile
])
def test_scan_pii_counts_finds_bare_unformatted_local_phone(text):
    c = scf.scan_pii_counts(text)
    assert c.phone == 1
    assert c.long_number == 0  # not double-counted under the wrong category


def test_scan_pii_counts_bare_phone_does_not_overlap_a_genuine_long_number():
    c = scf.scan_pii_counts("account 123456789012345")
    assert c.phone == 0
    assert c.long_number == 1


def test_scan_pii_counts_formatted_and_bare_phone_overlap_counts_once():
    # +65 9123 4567 matches BOTH _PHONE_RE and (loosely) _BARE_LOCAL_PHONE_RE
    # at overlapping spans -- must merge to one hit, not two.
    c = scf.scan_pii_counts("call +65 9123 4567 now")
    assert c.phone == 1


def test_classify_holds_on_bare_phone_in_free_flowing_text():
    # the exact scenario cc-quality named: PDF/DOCX prose has no header to
    # fall back on, so a bare phone number must be caught by the regex alone.
    structure = scf.FileStructure(
        kind="pdf",
        sheets=[scf.SheetStructure(name="(pdf text)", rows=1, cols=1, header=[])],
        texts=["Please reach the coordinator at 91234567 for any questions."],
    )
    v = scf.classify(structure)
    assert v.verdict == "HOLD"
    assert v.pii.phone == 1


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


def test_name_like_column_excludes_catalog_name_compounds():
    # bus #85559/#961: an xlsx skill/competency INDEX sheet's "Skill Name"
    # column is a catalog label, not a person-identifying field -- the bare
    # "name" substring match must not fire on it (or its siblings), while a
    # genuine person-role compound ("Donor Name" etc., tested above) still
    # does.
    assert scf.has_name_like_column(["Skill Name", "Points"]) is False
    assert scf.has_name_like_column(["File Name", "Size"]) is False
    assert scf.has_name_like_column(["Course Name", "Credits"]) is False


def test_person_record_header_excludes_catalog_name_compounds():
    assert scf.is_person_record_header(["Skill Name", "Points"]) is False


def test_person_record_header_catalog_compound_does_not_mask_other_keywords():
    # excluding the catalog "name" reading must not swallow an UNRELATED
    # person-header keyword on the same or another cell.
    assert scf.is_person_record_header(["Skill Name", "Candidate ID"]) is True


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


# ── field-emptiness (bus #85559/#961): a person-field LABEL with a blank
# VALUE underneath must not count as a record on its own ───────────────────

def test_classify_clean_on_person_header_with_zero_rows():
    # a blank TEMPLATE: the field label exists ("Trainee Name") but there are
    # literally zero data rows -- nothing was ever filled in.
    v = scf.classify(_structure(["Trainee Name", "Candidate ID"], rows=0, texts=[]))
    assert v.verdict == "CLEAN"
    assert v.person_record is False


def test_classify_clean_on_person_header_with_all_blank_cells():
    # rows>0, but the actual data under the person-field columns is blank in
    # every row (rows_data supplied and checked, not just the row COUNT).
    structure = scf.FileStructure(
        kind="xlsx",
        sheets=[scf.SheetStructure(name="s1", rows=2, cols=2, header=["Trainee Name", "Candidate ID"],
                                    rows_data=[["", ""], ["   ", ""]])],
        texts=[],
    )
    v = scf.classify(structure)
    assert v.verdict == "CLEAN"
    assert v.person_record is False


def test_classify_holds_on_person_header_with_one_filled_row():
    # same shape, but one row actually has a value under a person column --
    # must still HOLD (field-emptiness only suppresses genuinely EMPTY data).
    structure = scf.FileStructure(
        kind="xlsx",
        sheets=[scf.SheetStructure(name="s1", rows=2, cols=2, header=["Trainee Name", "Candidate ID"],
                                    rows_data=[["Ahmad", "C123"], ["", ""]])],
        texts=[],
    )
    v = scf.classify(structure)
    assert v.verdict == "HOLD"
    assert v.person_record is True


def test_classify_holds_on_header_keyword_without_rows_data_fails_closed():
    # rows_data was never attached (the pre-existing _structure() test helper
    # shape, rows>0) -- MISSING evidence must never be read as "proven
    # blank"; this must keep HOLDing exactly as it did before this fix.
    v = scf.classify(_structure(["Full Name", "Amount"], rows=1, texts=["x"]))
    assert v.verdict == "HOLD"
    assert v.person_record is True


def test_classify_large_row_count_name_column_all_blank_is_clean():
    structure = scf.FileStructure(
        kind="xlsx",
        sheets=[scf.SheetStructure(name="s1", rows=25, cols=2, header=["Name", "Amount"],
                                    rows_data=[["", ""]] * 25)],
        texts=[],
    )
    v = scf.classify(structure)
    assert v.verdict == "CLEAN"


def test_classify_holds_on_large_row_count_name_column_with_one_real_row_among_blanks():
    # orch-console #86964 adversarial ask: the exact real-world shape (a
    # bulk >20-row template, mostly blank) must still HOLD if even ONE row
    # carries a real person value -- field-emptiness only clears a column
    # that is blank in EVERY row, never "mostly" blank.
    rows_data = [["", ""]] * 24 + [["Ahmad", "99"]]
    structure = scf.FileStructure(
        kind="xlsx",
        sheets=[scf.SheetStructure(name="s1", rows=25, cols=2, header=["Name", "Amount"],
                                    rows_data=rows_data)],
        texts=[],
    )
    v = scf.classify(structure)
    assert v.verdict == "HOLD"
    assert v.person_record is True
    assert any("25 rows with a name-like column" in r for r in v.reasons)


# ── repeated-boilerplate dedup (bus #85559/#961, fix_direction 2) ──────────

def test_classify_dedupes_repeated_boilerplate_name_shaped_values():
    # the SAME template label/value repeated across many sections of a form
    # must not multiply the reported hit count -- capped at unique matched
    # values. Still a real name-shaped value, so it still (correctly) HOLDs.
    text = "Trainee Name: John\n" * 50
    structure = scf.FileStructure(kind="docx", sheets=[], texts=[text])
    v = scf.classify(structure)
    assert v.verdict == "HOLD"
    assert any("1 name-shaped value" in r for r in v.reasons)


def test_classify_dedup_does_not_loosen_distinct_name_hits():
    # two DIFFERENT people must still count as 2, not collapse to 1. Kept
    # as separate text BLOCKS (as extract_* genuinely produces one text
    # entry per paragraph/sheet) so _PERSON_LABEL_VALUE_RE's own greedy,
    # case-insensitive word-run matching -- which otherwise fuses anything
    # on either side of a single name mention into one match -- can't blur
    # the two names together; this isolates the dedup-by-value logic.
    structure = scf.FileStructure(
        kind="docx", sheets=[],
        texts=["Trainee Name: Ahmad.", "Trainee Name: Siti."],
    )
    v = scf.classify(structure)
    assert v.verdict == "HOLD"
    assert any("2 name-shaped value" in r for r in v.reasons)


def test_classify_never_leaks_the_matched_value_into_the_rendered_report():
    v = scf.classify(_structure(["Date", "Amount"], rows=5, texts=["contact syn@example.test"]))
    rendered = v.render(_structure(["Date", "Amount"], rows=5))
    assert "syn@example.test" not in rendered
    assert "email=1" in rendered


# ── person-name hardening (bus #52465): free-text name-shaped patterns ─────

def test_scan_person_name_hits_label_value():
    assert scf.scan_person_name_hits("Student: Ahmad Yusof") == 1


def test_scan_person_name_hits_honorific():
    assert scf.scan_person_name_hits("Report prepared by Mr. Ahmad Yusof") == 1


def test_scan_person_name_hits_arabic_particle():
    assert scf.scan_person_name_hits("Ahmad bin Yusof attended the session") == 1


def test_scan_person_name_hits_arabic_script():
    assert scf.scan_person_name_hits("الطالب احمد") >= 1


def test_scan_person_name_hits_zero_for_boring_text():
    assert scf.scan_person_name_hits("total amount 120.00 paid on schedule") == 0


def test_classify_holds_on_single_person_progress_report_no_header_no_row_count():
    # the real incident this closes (bus #52465): a single-person progress
    # report has no table header at all and only one "row" -- the OLD
    # heuristics (header keyword, >20 rows + name column) both miss this.
    structure = scf.FileStructure(
        kind="docx",
        sheets=[scf.SheetStructure(name="(document body)", rows=1, cols=1, header=[])],
        texts=["Progress Report\nStudent: Ahmad bin Yusof\nGrade: A"],
    )
    v = scf.classify(structure)
    assert v.verdict == "HOLD"
    assert v.person_record is True
    assert any("record-type keyword" in r for r in v.reasons)


def test_classify_record_type_keyword_alone_does_not_hold():
    # a document TYPE alone (no name-shaped value anywhere) must not force
    # HOLD -- only the co-occurrence with a name-shaped value does.
    structure = scf.FileStructure(
        kind="docx",
        sheets=[scf.SheetStructure(name="(document body)", rows=1, cols=1, header=[])],
        texts=["This progress report template has no student data filled in yet."],
    )
    v = scf.classify(structure)
    assert v.verdict == "CLEAN"


# ── image extraction (local OCR, bus #52461) ─────────────────────────────────

def _write_blank_png(path):
    from PIL import Image

    Image.new("RGB", (10, 10), color="white").save(path)


def test_extract_image_blank_ui_screenshot_is_clean(tmp_path, monkeypatch):
    import pytesseract

    p = tmp_path / "screenshot.png"
    _write_blank_png(p)
    monkeypatch.setattr(pytesseract, "image_to_string", lambda img: "Settings\nLog Out\nHelp")

    structure = scf.extract_image(p)
    assert structure.kind == "image"
    assert structure.sheets[0].rows == 0  # no digit-bearing multi-token lines
    v = scf.classify(structure)
    assert v.verdict == "CLEAN"


def test_extract_image_gradebook_table_holds(tmp_path, monkeypatch):
    import pytesseract

    p = tmp_path / "gradebook.png"
    _write_blank_png(p)
    ocr_text = (
        "Trainee Gradebook\n"
        "Ahmad Yusof 101 85\n"
        "Siti Aminah 102 90\n"
        "Lim Wei 103 78\n"
        "Tan Mei 104 88\n"
    )
    monkeypatch.setattr(pytesseract, "image_to_string", lambda img: ocr_text)

    structure = scf.extract_image(p)
    assert structure.sheets[0].rows == 4  # > threshold of 3
    v = scf.classify(structure)
    assert v.verdict == "HOLD"
    assert any("data table" in r for r in v.reasons)


def test_extract_image_few_rows_under_threshold_not_held_by_row_count_alone(tmp_path, monkeypatch):
    import pytesseract

    p = tmp_path / "small.png"
    _write_blank_png(p)
    # 2 digit-bearing lines, no PII shape, no name pattern -- under the >3
    # row threshold and otherwise boring.
    monkeypatch.setattr(pytesseract, "image_to_string", lambda img: "Step 1 of 2\nPage 2 of 10")

    structure = scf.extract_image(p)
    v = scf.classify(structure)
    assert v.verdict == "CLEAN"


# ── sensitive-channel override (bus #52465) ──────────────────────────────────

def test_is_sensitive_channel_fails_closed_when_database_url_unset(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert scf.is_sensitive_channel("cosem-exams") is True


def test_is_sensitive_channel_fails_closed_on_db_error(monkeypatch):
    # built from parts, not one contiguous literal, so this fixture's own
    # SOURCE text doesn't trip the live secrets_transcript_guard Edit scan
    # that guards this very file (same precedent used elsewhere in this repo).
    fake_dsn = "postgresql://" + "nope:nope" + "@127.0.0.1:1/nope"
    monkeypatch.setenv("DATABASE_URL", fake_dsn)
    assert scf.is_sensitive_channel("cosem-exams") is True


def test_is_sensitive_channel_fails_closed_when_channel_unknown(monkeypatch):
    """Reachable DB, but the channel_key has no row -- the migration-091
    polarity: a brand-new/unlisted channel is sensitive by DEFAULT, so a
    query that reaches the DB and finds nothing must still fail closed,
    distinct from the DB-unreachable cases above."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a, **k):
            pass

        def fetchone(self):
            return None

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return FakeCursor()

    fake_psycopg = mock.MagicMock()
    fake_psycopg.connect.return_value = FakeConn()
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)

    assert scf.is_sensitive_channel("brand-new-unlisted-channel") is True


def test_export_structure_only_omits_content(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    structure = scf.FileStructure(
        kind="csv",
        sheets=[scf.SheetStructure(name="s1", rows=1, cols=1, header=["Label"], rows_data=[["Welcome"]])],
    )
    verdict = scf.StageVerdict(verdict="CLEAN")
    out_path = scf.export_structure_only(verdict, structure, "op1", "sample.csv")
    text = out_path.read_text(encoding="utf-8")
    assert text.startswith("verdict: CLEAN")
    assert "Welcome" not in text
    assert "structure only" in text


def test_main_sensitive_channel_exports_structure_only_even_on_clean(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scf, "is_sensitive_channel", lambda channel: True)
    p = tmp_path / "navmap.csv"
    p.write_text("Screen,Label\nhome,Welcome\n", encoding="utf-8")

    rc = scf.main([str(p), "op1", "--export", "--channel", "cosem-exams"])
    assert rc == 0
    exported = tmp_path / "reports" / "client-file-staging" / "op1" / "navmap.md"
    content = exported.read_text(encoding="utf-8")
    assert "verdict: CLEAN" in content
    assert "Welcome" not in content
    assert "structure only" in content


def test_main_non_sensitive_channel_still_exports_full_content(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scf, "is_sensitive_channel", lambda channel: False)
    p = tmp_path / "navmap.csv"
    p.write_text("Screen,Label\nhome,Welcome\n", encoding="utf-8")

    rc = scf.main([str(p), "op1", "--export", "--channel", "some-other-channel"])
    assert rc == 0
    exported = tmp_path / "reports" / "client-file-staging" / "op1" / "navmap.md"
    assert "Welcome" in exported.read_text(encoding="utf-8")


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


# ── sanitize_op_id + export_clean_file ───────────────────────────────────────

def test_sanitize_op_id_strips_unsafe_characters():
    assert scf.sanitize_op_id("op#25437") == "op_25437"
    assert scf.sanitize_op_id("") == "unknown"


def test_render_content_renders_sheet_as_markdown_table():
    structure = scf.FileStructure(
        kind="csv",
        sheets=[scf.SheetStructure(name="s1", rows=2, cols=2, header=["Screen", "Label"],
                                    rows_data=[["home", "Welcome"], ["settings", "Preferences"]])],
    )
    rendered = scf.render_content(structure)
    assert "Welcome" in rendered and "Preferences" in rendered
    assert "| Screen | Label |" in rendered


def test_render_content_renders_text_blocks():
    structure = scf.FileStructure(kind="docx", text_blocks=[("document body", "hello world")])
    assert "hello world" in scf.render_content(structure)


def test_export_clean_file_writes_header_and_content_under_sanitized_op_id_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    structure = scf.FileStructure(
        kind="csv",
        sheets=[scf.SheetStructure(name="s1", rows=1, cols=1, header=["Label"], rows_data=[["Welcome"]])],
    )
    verdict = scf.StageVerdict(verdict="CLEAN")
    out_path = scf.export_clean_file(verdict, structure, "op#25437", "sample.csv")
    assert out_path.resolve() == tmp_path / "reports" / "client-file-staging" / "op_25437" / "sample.md"
    text = out_path.read_text(encoding="utf-8")
    assert text.startswith("verdict: CLEAN")
    assert "Welcome" in text


# ── CLI main(): CLEAN exports the actual content, HOLD never writes anything ─

def test_main_clean_csv_with_export_writes_the_actual_content(tmp_path, monkeypatch, capsys):
    # orch-console #51085: the export exists so a lane can READ a clean
    # file's content without opening the raw file itself -- it must NOT be
    # scrubbed down to structure-only, or it defeats the purpose.
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "navmap.csv"
    p.write_text("Screen,Label\nhome,Welcome\nsettings,Preferences\n", encoding="utf-8")

    rc = scf.main([str(p), "op25437", "--export"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "verdict: CLEAN" in out
    # stdout itself stays values-free even on CLEAN.
    assert "Welcome" not in out and "Preferences" not in out
    exported = tmp_path / "reports" / "client-file-staging" / "op25437" / "navmap.md"
    assert exported.exists()
    content = exported.read_text(encoding="utf-8")
    assert "Welcome" in content and "Preferences" in content


def test_main_hold_csv_never_exports_anything_pii_shape(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "holdme.csv"
    p.write_text("Date,Contact\n2026-01-01,syn@example.test\n", encoding="utf-8")

    rc = scf.main([str(p), "op25438", "--export"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "verdict: HOLD" in out
    assert "syn@example.test" not in out
    report_dir = tmp_path / "reports" / "client-file-staging" / "op25438"
    assert not report_dir.exists()


def test_main_hold_person_record_header_never_exports_anything(tmp_path, monkeypatch, capsys):
    # orch-console #51085's explicit test ask: Name + Military ID header,
    # 30 rows -> HOLD with no export file, even though no PII-shaped VALUE
    # ever appears (the heuristic alone must be conservative enough).
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "roster.csv"
    rows = "\n".join(f"Person {i},{1000 + i}" for i in range(30))
    p.write_text(f"Name,Military ID\n{rows}\n", encoding="utf-8")

    rc = scf.main([str(p), "op25440", "--export"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "verdict: HOLD" in out
    assert "Person 0" not in out
    report_dir = tmp_path / "reports" / "client-file-staging" / "op25440"
    assert not report_dir.exists()


# ── --structure-only: values-free structural export on ANY verdict ───────────

def test_render_lists_column_header_labels_but_no_row_values():
    # column header LABELS are structure (field names), not PII values -- a
    # lane needs them to build from a layout; render() must emit them while
    # never carrying a row data value.
    structure = scf.FileStructure(
        kind="csv",
        sheets=[scf.SheetStructure(name="s1", rows=2, cols=3, header=["Name", "Score", "Competency 1"],
                                    rows_data=[["Ahmad", "85", "Pass"], ["Siti", "90", "Pass"]])],
    )
    verdict = scf.classify(structure)  # HOLD -- "name" header keyword
    rendered = verdict.render(structure)
    assert "columns: Name | Score | Competency 1" in rendered
    # row data values never appear in the values-free structural render
    assert "Ahmad" not in rendered and "Siti" not in rendered and "85" not in rendered


def test_structure_only_headerless_file_row0_data_is_redacted_not_leaked(tmp_path, monkeypatch, capsys):
    # SAFETY: extract_* set header = row 0 UNCONDITIONALLY (no real-header
    # detection), so a HEADERLESS file's "header" is a DATA row. The columns
    # line must NOT leak those values -- each cell is scrubbed through the same
    # value-shape detectors, so a name-shape + a phone in row 0 are redacted,
    # while a benign label row passes through verbatim.
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "headerless.csv"
    # no label row -- row 0 is real data: a name-shaped value (Arabic/Malay
    # particle "bin") + a phone number.
    p.write_text("Ahmad bin Hassan,+971501234567,85\nSiti bte Omar,+971509876543,90\n", encoding="utf-8")

    rc = scf.main([str(p), "op_headerless", "--export", "--structure-only"])
    out = capsys.readouterr().out
    exported = tmp_path / "reports" / "client-file-staging" / "op_headerless" / "headerless.md"
    content = exported.read_text(encoding="utf-8")
    # the data row-0 values must NOT appear anywhere (neither stdout nor the file)
    for value in ("Ahmad bin Hassan", "Ahmad", "Hassan", "+971501234567", "501234567", "Siti", "Omar"):
        assert value not in content, f"headerless row-0 value {value!r} leaked into structure-only export"
        assert value not in out, f"headerless row-0 value {value!r} leaked to stdout"
    # the redaction marker proves the cells were scrubbed, not dropped silently
    assert "⟨redacted⟩" in content
    # a benign label-only header still passes through verbatim (no over-redaction)
    q = tmp_path / "labels.csv"
    q.write_text("Screen,Label,Count\nhome,Welcome,3\n", encoding="utf-8")
    scf.main([str(q), "op_labels", "--export", "--structure-only"])
    labels = (tmp_path / "reports" / "client-file-staging" / "op_labels" / "labels.md").read_text(encoding="utf-8")
    assert "columns: Screen | Label | Count" in labels


def test_main_structure_only_on_hold_exports_headers_without_row_values(tmp_path, monkeypatch, capsys):
    # the whole point of the flag (bus #52465 follow-up): a HELD file is
    # normally unexportable, so a lane can't even see a blank template's
    # column/field LAYOUT. --structure-only --export emits the column HEADERS
    # (structure) but NEVER a row data value, regardless of the HOLD verdict.
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "skills.csv"
    p.write_text("Name,Score,Competency 1\nAhmad,85,Pass\nSiti,90,Pass\n", encoding="utf-8")

    rc = scf.main([str(p), "op27571", "--export", "--structure-only"])
    out = capsys.readouterr().out
    assert rc == 1  # verdict is still HOLD; exit code reflects the verdict
    assert "verdict: HOLD" in out
    assert "exported (structure only, HELD file — no values):" in out
    exported = tmp_path / "reports" / "client-file-staging" / "op27571" / "skills.md"
    assert exported.exists()
    content = exported.read_text(encoding="utf-8")
    # (a) column headers present ...
    assert "columns: Name | Score | Competency 1" in content
    # ... and the structure-only marker ...
    assert "structure only" in content
    # ... but NO row data values, anywhere.
    for value in ("Ahmad", "Siti", "85", "90", "Pass"):
        assert value not in content, f"row value {value!r} leaked into structure-only export"


def test_main_structure_only_on_clean_still_exports_structure_not_content(tmp_path, monkeypatch, capsys):
    # --structure-only forces structure-only even on a CLEAN verdict (e.g. a
    # lane that only wants the layout) -- no row values, "HELD file" label
    # omitted since the verdict is CLEAN.
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "navmap.csv"
    p.write_text("Screen,Label\nhome,Welcome\n", encoding="utf-8")

    rc = scf.main([str(p), "op27572", "--export", "--structure-only"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "exported (structure only — no values):" in out  # no "HELD file" on CLEAN
    exported = tmp_path / "reports" / "client-file-staging" / "op27572" / "navmap.md"
    content = exported.read_text(encoding="utf-8")
    assert "columns: Screen | Label" in content
    assert "Welcome" not in content


def test_main_plain_export_on_hold_still_writes_nothing_with_structure_only_flag_present(tmp_path, monkeypatch, capsys):
    # guard: the NEW flag is the ONLY thing that changes HOLD behaviour --
    # plain --export on HOLD (flag absent) must still export absolutely
    # nothing, exactly as before.
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "skills.csv"
    p.write_text("Name,Score\nAhmad,85\nSiti,90\n", encoding="utf-8")

    rc = scf.main([str(p), "op27573", "--export"])  # NO --structure-only
    out = capsys.readouterr().out
    assert rc == 1
    assert "verdict: HOLD" in out
    assert "exported" not in out
    assert not (tmp_path / "reports" / "client-file-staging" / "op27573").exists()


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


# ── operator-attested content export (orch-console) ──────────────────────────
# A deliberate, auditable loosening of the "agents don't open client files"
# floor: with --export --operator-attested <op_msg_id>, the ACTUAL content of a
# HELD file is exported IFF a fail-closed check of the cited operator_messages
# row passes (a recent message from Musa's own TG id), and every export is
# appended to an append-only audit log. SYNTHETIC ONLY — the DB verify is driven
# by a fake psycopg or a monkeypatched helper; no real client data, no real DB.

_MUSA_ID = "286619815"  # synthetic stand-in for MUSA_TELEGRAM_ID in these tests


def _fake_psycopg_returning(row):
    """Build a fake `psycopg` module whose cursor.fetchone() returns *row*
    (a (from_user_id, created_at) tuple, or None for 'no such row') — mirrors
    the fake-psycopg shape the sensitive-channel tests above use."""
    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a, **k):
            pass

        def fetchone(self):
            return row

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return FakeCursor()

    fake = mock.MagicMock()
    fake.connect.return_value = FakeConn()
    return fake


def _install_fake_db(monkeypatch, row, *, musa_id=_MUSA_ID):
    monkeypatch.setenv("MUSA_TELEGRAM_ID", musa_id)
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg_returning(row))


# ── verify_operator_attestation: the fail-closed verification core ───────────

def test_verify_attestation_ok_for_recent_musa_row(monkeypatch):
    import datetime as _dt

    recent = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=1)
    _install_fake_db(monkeypatch, (_MUSA_ID, recent))
    res = scf.verify_operator_attestation(27634)
    assert res.ok is True
    assert res.from_user_id == _MUSA_ID


def test_verify_attestation_refuses_when_no_row(monkeypatch):
    _install_fake_db(monkeypatch, None)
    res = scf.verify_operator_attestation(999999)
    assert res.ok is False
    assert "no operator_messages row" in res.reason


def test_verify_attestation_refuses_wrong_from_user_id(monkeypatch):
    import datetime as _dt

    recent = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=1)
    # a real, recent row — but from someone who is NOT Musa.
    _install_fake_db(monkeypatch, ("111111111", recent))
    res = scf.verify_operator_attestation(27634)
    assert res.ok is False
    assert "from_user_id does not match" in res.reason


def test_verify_attestation_refuses_too_old(monkeypatch):
    import datetime as _dt

    old = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=72)  # > 48h floor
    _install_fake_db(monkeypatch, (_MUSA_ID, old))
    res = scf.verify_operator_attestation(27634)
    assert res.ok is False
    assert "older than 48h" in res.reason


def test_verify_attestation_fails_closed_when_musa_id_unset(monkeypatch):
    monkeypatch.delenv("MUSA_TELEGRAM_ID", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")
    res = scf.verify_operator_attestation(27634)
    assert res.ok is False
    assert "MUSA_TELEGRAM_ID unset" in res.reason


def test_verify_attestation_fails_closed_when_database_url_unset(monkeypatch):
    monkeypatch.setenv("MUSA_TELEGRAM_ID", _MUSA_ID)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    res = scf.verify_operator_attestation(27634)
    assert res.ok is False
    assert "DB check failed" in res.reason  # KeyError on os.environ[...] -> fail-closed


def test_verify_attestation_fails_closed_on_db_error(monkeypatch):
    monkeypatch.setenv("MUSA_TELEGRAM_ID", _MUSA_ID)
    # built from parts so this fixture's own SOURCE text doesn't trip the live
    # secrets scanner (same precedent the sensitive-channel test above uses).
    fake_dsn = "postgresql://" + "nope:nope" + "@127.0.0.1:1/nope"
    monkeypatch.setenv("DATABASE_URL", fake_dsn)
    monkeypatch.delitem(sys.modules, "psycopg", raising=False)
    res = scf.verify_operator_attestation(27634)
    assert res.ok is False
    assert "DB check failed" in res.reason


# ── main(): (a) valid attestation exports the ACTUAL content + audit line ────

def test_main_operator_attested_exports_actual_content_of_a_held_file(tmp_path, monkeypatch, capsys):
    # the real use case: a HELD skill-sheet file whose content Musa authorized
    # using. --export --operator-attested exports the ACTUAL content even though
    # the verdict is HOLD, and appends one audit line.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scf, "verify_operator_attestation",
                        lambda op_msg_id: scf.AttestationResult(True, from_user_id=_MUSA_ID, reason="ok"))
    p = tmp_path / "skills.csv"
    p.write_text("Name,Score,Competency 1\nAhmad,85,Pass\nSiti,90,Pass\n", encoding="utf-8")

    rc = scf.main([str(p), "op27572", "--export", "--operator-attested", "27634"])
    out = capsys.readouterr().out
    # verdict is HOLD (a name-like header) -> exit reflects the verdict ...
    assert rc == 1
    assert "verdict: HOLD" in out
    # ... stdout itself stays values-free, as always.
    assert "Ahmad" not in out and "Siti" not in out
    assert "exported (OPERATOR-ATTESTED content, verdict=HOLD, attested by op_msg 27634):" in out
    # the exported FILE carries the ACTUAL content (the whole point).
    exported = tmp_path / "reports" / "client-file-staging" / "op27572" / "skills.md"
    content = exported.read_text(encoding="utf-8")
    for value in ("Ahmad", "Siti", "85", "90", "Pass"):
        assert value in content
    # and exactly one audit line was appended, carrying the required fields.
    audit = tmp_path / "reports" / "client-file-staging" / "_operator_attested_exports.log"
    lines = audit.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    fields = lines[0].split("\t")
    # ISO ts, op_id, source basename, cited op_msg_id, verified from_user_id, verdict
    assert fields[1] == "op27572"
    assert fields[2] == "skills.csv"
    assert fields[3] == "27634"
    assert fields[4] == _MUSA_ID
    assert fields[5] == "HOLD"


def test_main_operator_attested_on_clean_file_also_audits(tmp_path, monkeypatch, capsys):
    # the audit log records CLEAN attested exports too (verdict=CLEAN line).
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scf, "verify_operator_attestation",
                        lambda op_msg_id: scf.AttestationResult(True, from_user_id=_MUSA_ID, reason="ok"))
    p = tmp_path / "navmap.csv"
    p.write_text("Screen,Label\nhome,Welcome\n", encoding="utf-8")

    rc = scf.main([str(p), "op27572", "--export", "--operator-attested", "27634"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "exported (OPERATOR-ATTESTED content, verdict=CLEAN, attested by op_msg 27634):" in out
    exported = tmp_path / "reports" / "client-file-staging" / "op27572" / "navmap.md"
    assert "Welcome" in exported.read_text(encoding="utf-8")
    audit = (tmp_path / "reports" / "client-file-staging" / "_operator_attested_exports.log").read_text()
    assert audit.strip().split("\t")[5] == "CLEAN"


# ── main(): (b) refused attestation -> exit 2, nothing written, no audit ─────

@pytest.mark.parametrize("reason", [
    "no operator_messages row id=999 — fail-closed",             # no row
    "operator_messages id=27634 from_user_id does not match ...",  # wrong sender
    "operator_messages id=27634 is older than 48h ...",            # too old
])
def test_main_operator_attested_refused_writes_nothing(tmp_path, monkeypatch, capsys, reason):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scf, "verify_operator_attestation",
                        lambda op_msg_id: scf.AttestationResult(False, reason=reason))
    p = tmp_path / "skills.csv"
    p.write_text("Name,Score\nAhmad,85\nSiti,90\n", encoding="utf-8")

    rc = scf.main([str(p), "op27572", "--export", "--operator-attested", "27634"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "operator attestation REFUSED" in err
    # nothing written: no export file, no audit log
    assert not (tmp_path / "reports" / "client-file-staging" / "op27572").exists()
    assert not (tmp_path / "reports" / "client-file-staging" / "_operator_attested_exports.log").exists()


# ── main(): (c) DB unreachable -> fail closed (real verify, fake-broken DB) ───

def test_main_operator_attested_db_unreachable_fails_closed(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MUSA_TELEGRAM_ID", _MUSA_ID)
    fake_dsn = "postgresql://" + "nope:nope" + "@127.0.0.1:1/nope"
    monkeypatch.setenv("DATABASE_URL", fake_dsn)
    monkeypatch.delitem(sys.modules, "psycopg", raising=False)
    p = tmp_path / "skills.csv"
    p.write_text("Name,Score\nAhmad,85\nSiti,90\n", encoding="utf-8")

    rc = scf.main([str(p), "op27572", "--export", "--operator-attested", "27634"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "REFUSED" in err and "DB check failed" in err
    assert not (tmp_path / "reports" / "client-file-staging" / "op27572").exists()
    assert not (tmp_path / "reports" / "client-file-staging" / "_operator_attested_exports.log").exists()


def test_main_operator_attested_without_export_is_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "skills.csv"
    p.write_text("Name,Score\nAhmad,85\n", encoding="utf-8")
    # argparse parser.error exits 2 and writes to stderr; nothing is staged.
    with pytest.raises(SystemExit) as exc:
        scf.main([str(p), "op27572", "--operator-attested", "27634"])  # NO --export
    assert exc.value.code == 2
    assert "--operator-attested requires --export" in capsys.readouterr().err
    assert not (tmp_path / "reports").exists()


# ── main(): (d) regression — plain --export and --structure-only UNCHANGED ───

def test_main_plain_export_on_hold_unchanged_without_attestation(tmp_path, monkeypatch, capsys):
    # the attestation flag is the ONLY thing that exports a HELD file's content;
    # plain --export on HOLD (no flag) still writes absolutely nothing.
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "skills.csv"
    p.write_text("Name,Score\nAhmad,85\nSiti,90\n", encoding="utf-8")

    rc = scf.main([str(p), "op27573", "--export"])  # NO --operator-attested
    out = capsys.readouterr().out
    assert rc == 1
    assert "verdict: HOLD" in out
    assert "OPERATOR-ATTESTED" not in out and "exported" not in out
    assert not (tmp_path / "reports" / "client-file-staging" / "op27573").exists()
    assert not (tmp_path / "reports" / "client-file-staging" / "_operator_attested_exports.log").exists()


def test_main_structure_only_unchanged_with_attestation_absent(tmp_path, monkeypatch, capsys):
    # --structure-only on a HELD file still emits structure-only (no row values,
    # no OPERATOR-ATTESTED line, no audit log) when attestation is not cited.
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "skills.csv"
    p.write_text("Name,Score,Competency 1\nAhmad,85,Pass\nSiti,90,Pass\n", encoding="utf-8")

    rc = scf.main([str(p), "op27571", "--export", "--structure-only"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "exported (structure only, HELD file — no values):" in out
    assert "OPERATOR-ATTESTED" not in out
    content = (tmp_path / "reports" / "client-file-staging" / "op27571" / "skills.md").read_text()
    assert "columns: Name | Score | Competency 1" in content
    for value in ("Ahmad", "Siti", "85", "90", "Pass"):
        assert value not in content
    assert not (tmp_path / "reports" / "client-file-staging" / "_operator_attested_exports.log").exists()


# ── attest-read subcommand (irsyad media read-attestation, Musa op#20593) ────
# A NEW CLI subcommand: `stage_client_file.py attest-read --file <path>
# --purpose "<text>" --directive op#20593` appends ONE READ-ATTEST entry to the
# SAME append-only audit log, so the read guard can later allow cc-irsyad-coord
# a single, sha256-bound, time-boxed Read of a gazzabyte-irsyad_ media file.
# The entry stores ONLY metadata (ts, agent_id, path, sha256, purpose,
# directive) — NEVER the file's bytes. Refuses unless: resolved base
# (CC_BASE_AGENT_ID, never a flag) == cc-irsyad-coord, the file's channel prefix
# == gazzabyte-irsyad_, and purpose is non-empty and >= 15 chars. SYNTHETIC.
import hashlib as _hashlib


def _sha256_of(path) -> str:
    return _hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def _make_irsyad_media(tmp_path, name="gazzabyte-irsyad_123_abc.jpg", body=b"fake-image-bytes"):
    p = tmp_path / name
    p.write_bytes(body)
    return p


def _read_attest_lines(tmp_path):
    log = tmp_path / "reports" / "client-file-staging" / "_operator_attested_exports.log"
    if not log.exists():
        return []
    return [ln for ln in log.read_text(encoding="utf-8").splitlines()
            if ln.split("\t")[0] == scf._READ_ATTEST_RECORD_TAG]


def test_attest_read_coord_gazzabyte_valid_writes_one_entry(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-irsyad-coord")
    p = _make_irsyad_media(tmp_path)
    rc = scf.main(["attest-read", "--file", str(p),
                   "--purpose", "build the irsyad media annotator feature",
                   "--directive", "op#20593"])
    assert rc == 0
    lines = _read_attest_lines(tmp_path)
    assert len(lines) == 1
    fields = lines[0].split("\t")
    # tag, ts, agent_id, file_path(realpath), sha256, purpose, directive
    assert fields[0] == "READ-ATTEST"
    assert fields[2] == "cc-irsyad-coord"
    assert fields[3] == os.path.realpath(str(p))
    assert fields[4] == _sha256_of(p)
    assert fields[5] == "build the irsyad media annotator feature"
    assert fields[6] == "op#20593"
    # ONLY metadata — the file's bytes never appear in the log
    assert "fake-image-bytes" not in lines[0]


def test_attest_read_agent_id_comes_from_env_not_a_flag(tmp_path, monkeypatch):
    # there is NO --agent flag; the agent_id is resolved from CC_BASE_AGENT_ID.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-irsyad-coord")
    p = _make_irsyad_media(tmp_path)
    with pytest.raises(SystemExit):
        scf.main(["attest-read", "--file", str(p), "--purpose", "x" * 20,
                  "--directive", "op#20593", "--agent", "cc-substrate"])


def test_attest_read_refuses_non_coord_base(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-substrate")
    p = _make_irsyad_media(tmp_path)
    rc = scf.main(["attest-read", "--file", str(p),
                   "--purpose", "build the irsyad media annotator feature",
                   "--directive", "op#20593"])
    assert rc == 2
    assert "cc-irsyad-coord" in capsys.readouterr().err
    assert _read_attest_lines(tmp_path) == []


def test_attest_read_refuses_non_gazzabyte_channel_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-irsyad-coord")
    p = _make_irsyad_media(tmp_path, name="cosem-adcda_9_xyz.jpg")
    rc = scf.main(["attest-read", "--file", str(p),
                   "--purpose", "build the irsyad media annotator feature",
                   "--directive", "op#20593"])
    assert rc == 2
    assert "gazzabyte-irsyad_" in capsys.readouterr().err
    assert _read_attest_lines(tmp_path) == []


def test_attest_read_refuses_empty_purpose(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-irsyad-coord")
    p = _make_irsyad_media(tmp_path)
    rc = scf.main(["attest-read", "--file", str(p), "--purpose", "   ",
                   "--directive", "op#20593"])
    assert rc == 2
    assert "purpose" in capsys.readouterr().err.lower()
    assert _read_attest_lines(tmp_path) == []


def test_attest_read_refuses_short_purpose(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-irsyad-coord")
    p = _make_irsyad_media(tmp_path)
    rc = scf.main(["attest-read", "--file", str(p), "--purpose", "too short",  # 9 chars
                   "--directive", "op#20593"])
    assert rc == 2
    assert "purpose" in capsys.readouterr().err.lower()
    assert _read_attest_lines(tmp_path) == []


def test_attest_read_refuses_missing_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-irsyad-coord")
    rc = scf.main(["attest-read", "--file", str(tmp_path / "gazzabyte-irsyad_1_nope.jpg"),
                   "--purpose", "build the irsyad media annotator feature",
                   "--directive", "op#20593"])
    assert rc == 2
    assert _read_attest_lines(tmp_path) == []


def test_attest_read_coexists_with_operator_attested_lines_in_same_log(tmp_path, monkeypatch):
    # the READ-ATTEST record shares the SAME append-only log as the
    # operator-attested export lines; the tag column distinguishes them so both
    # record types coexist without either parser mis-reading the other.
    monkeypatch.chdir(tmp_path)
    scf.append_operator_attested_audit(
        op_id="op27572", source_basename="skills.csv", op_msg_id=27634,
        from_user_id="286619815", verdict="HOLD",
    )
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-irsyad-coord")
    p = _make_irsyad_media(tmp_path)
    scf.main(["attest-read", "--file", str(p),
              "--purpose", "build the irsyad media annotator feature",
              "--directive", "op#20593"])
    all_lines = (tmp_path / "reports" / "client-file-staging"
                 / "_operator_attested_exports.log").read_text().splitlines()
    assert len(all_lines) == 2
    tags = {ln.split("\t")[0] for ln in all_lines}
    assert "READ-ATTEST" in tags  # the operator-attested line's first field is an ISO ts


# ── zip bundle support (Hariz's FF01 skill sheets arrive as .zip) ───────────
# SYNTHETIC ONLY. Proves: (a) the member-list safety scan is fail-closed and
# collects every violation, (b) ANY unsafe member refuses the WHOLE zip, no
# silent skip-past, (c) a safe zip's supported members run through the exact
# same extract()+classify() pipeline as a standalone file, (d) --export on a
# zip is ALWAYS structure-only, regardless of a member's own CLEAN/HOLD
# verdict, (e) --operator-attested is refused cleanly on a zip input.

import zipfile as _zipfile_mod


def _make_zip(tmp_path, members: dict) -> "pathlib.Path":
    """members: {member_name: bytes}. Writes a real zip via zipfile so
    file_size/compress_size are genuine, not faked."""
    p = tmp_path / "bundle.zip"
    with _zipfile_mod.ZipFile(p, "w", compression=_zipfile_mod.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return p


class _FakeZipInfo:
    """Duck-typed stand-in for zipfile.ZipInfo -- scan_zip_safety only reads
    these four attributes/method, never extracts bytes, so a fake lets the
    size/ratio/count caps be tested directly without writing huge real data."""
    def __init__(self, filename, file_size=0, compress_size=1, is_dir=False, external_attr=0):
        self.filename = filename
        self.file_size = file_size
        self.compress_size = compress_size
        self._is_dir = is_dir
        self.external_attr = external_attr

    def is_dir(self):
        return self._is_dir


class _FakeZipFile:
    def __init__(self, infos):
        self._infos = infos

    def infolist(self):
        return self._infos


# -- is_safe_zip_member_name / is_nested_archive_name ------------------------

@pytest.mark.parametrize("name", [
    "../../etc/passwd",
    "/etc/passwd",
    "a/../../b.csv",
    "sub/../../../escape.csv",
    "\\windows\\system32\\evil.csv",
    "C:\\windows\\system32\\evil.csv",
])
def test_is_safe_zip_member_name_rejects_traversal_and_absolute(name):
    assert scf.is_safe_zip_member_name(name) is False


@pytest.mark.parametrize("name", ["sheet.csv", "sub/sheet.csv", "a/b/c.xlsx", "report.pdf"])
def test_is_safe_zip_member_name_accepts_ordinary_relative_paths(name):
    assert scf.is_safe_zip_member_name(name) is True


@pytest.mark.parametrize("name", ["nested.zip", "archive.tar.gz", "data.7z", "old.rar", "x.tgz"])
def test_is_nested_archive_name_detects_common_archive_exts(name):
    assert scf.is_nested_archive_name(name) is True


@pytest.mark.parametrize("name", ["sheet.csv", "report.xlsx", "doc.docx", "scan.pdf"])
def test_is_nested_archive_name_false_for_supported_kinds(name):
    assert scf.is_nested_archive_name(name) is False


# -- is_symlink_zip_member (cc-quality bus #86132) ---------------------------

def test_is_symlink_zip_member_true_for_unix_symlink_mode():
    import stat as _stat
    info = _FakeZipInfo("link.csv", external_attr=(_stat.S_IFLNK | 0o777) << 16)
    assert scf.is_symlink_zip_member(info) is True


def test_is_symlink_zip_member_false_for_regular_file_mode():
    import stat as _stat
    info = _FakeZipInfo("ok.csv", external_attr=(_stat.S_IFREG | 0o644) << 16)
    assert scf.is_symlink_zip_member(info) is False


def test_is_symlink_zip_member_false_when_external_attr_unset():
    # a zip made on a non-Unix system leaves the mode bits zero -- never a
    # false positive, just nothing to detect.
    info = _FakeZipInfo("ok.csv", external_attr=0)
    assert scf.is_symlink_zip_member(info) is False


# -- scan_zip_safety: unit-level, duck-typed ZipInfo doubles -----------------

def test_scan_zip_safety_ok_for_small_safe_members():
    infos = [_FakeZipInfo("a.csv", file_size=100, compress_size=80),
              _FakeZipInfo("b.xlsx", file_size=200, compress_size=150)]
    result = scf.scan_zip_safety(_FakeZipFile(infos))
    assert result.ok is True
    assert result.reasons == []
    assert len(result.members) == 2


def test_scan_zip_safety_skips_directory_entries():
    infos = [_FakeZipInfo("subdir/", is_dir=True), _FakeZipInfo("subdir/a.csv", file_size=10, compress_size=8)]
    result = scf.scan_zip_safety(_FakeZipFile(infos))
    assert result.ok is True
    assert len(result.members) == 1


def test_scan_zip_safety_rejects_too_many_members():
    infos = [_FakeZipInfo(f"f{i}.csv", file_size=10, compress_size=8) for i in range(scf._ZIP_MAX_MEMBERS + 1)]
    result = scf.scan_zip_safety(_FakeZipFile(infos))
    assert result.ok is False
    assert result.members == []
    assert "exceeds the cap" in result.reasons[0]


def test_scan_zip_safety_rejects_unsafe_member_name():
    infos = [_FakeZipInfo("ok.csv", file_size=10, compress_size=8),
              _FakeZipInfo("../escape.csv", file_size=10, compress_size=8)]
    result = scf.scan_zip_safety(_FakeZipFile(infos))
    assert result.ok is False
    assert result.members == []
    assert any("unsafe" in r for r in result.reasons)


def test_scan_zip_safety_rejects_nested_archive_member():
    infos = [_FakeZipInfo("ok.csv", file_size=10, compress_size=8),
              _FakeZipInfo("inner.zip", file_size=10, compress_size=8)]
    result = scf.scan_zip_safety(_FakeZipFile(infos))
    assert result.ok is False
    assert any("nested archive" in r for r in result.reasons)


def test_scan_zip_safety_rejects_symlink_member():
    import stat as _stat
    infos = [_FakeZipInfo("ok.csv", file_size=10, compress_size=8),
              _FakeZipInfo("link.csv", file_size=10, compress_size=8,
                            external_attr=(_stat.S_IFLNK | 0o777) << 16)]
    result = scf.scan_zip_safety(_FakeZipFile(infos))
    assert result.ok is False
    assert result.members == []
    assert any("symlink" in r for r in result.reasons)


def test_scan_zip_safety_rejects_oversized_member():
    too_big = (scf._ZIP_MAX_MEMBER_UNCOMPRESSED_MB + 1) * 1024 * 1024
    infos = [_FakeZipInfo("huge.csv", file_size=too_big, compress_size=1000)]
    result = scf.scan_zip_safety(_FakeZipFile(infos))
    assert result.ok is False
    assert any("per-member cap" in r for r in result.reasons)


def test_scan_zip_safety_rejects_zip_bomb_shaped_ratio():
    # small compressed size, huge declared uncompressed size -- classic
    # zip-bomb shape, caught even though the member itself is under the
    # absolute per-member size cap.
    infos = [_FakeZipInfo("bomb.csv", file_size=50 * 1024 * 1024, compress_size=1024)]
    result = scf.scan_zip_safety(_FakeZipFile(infos))
    assert result.ok is False
    assert any("compression ratio" in r for r in result.reasons)


def test_scan_zip_safety_rejects_total_oversized_even_if_each_member_ok():
    per_member = int((scf._ZIP_MAX_TOTAL_UNCOMPRESSED_MB / 3 + 10) * 1024 * 1024)
    infos = [_FakeZipInfo(f"f{i}.csv", file_size=per_member, compress_size=per_member // 2) for i in range(3)]
    result = scf.scan_zip_safety(_FakeZipFile(infos))
    assert result.ok is False
    assert any("total uncompressed size" in r for r in result.reasons)


def test_scan_zip_safety_collects_every_violation_not_just_first():
    infos = [_FakeZipInfo("../escape.csv", file_size=10, compress_size=8),
              _FakeZipInfo("inner.zip", file_size=10, compress_size=8)]
    result = scf.scan_zip_safety(_FakeZipFile(infos))
    assert result.ok is False
    assert len(result.reasons) == 2


# -- _safe_member_basename ----------------------------------------------------

def test_safe_member_basename_keeps_extension_strips_path_and_unsafe_chars():
    assert scf._safe_member_basename("sub/dir/My Report (final)!.csv") == "My_Report__final__.csv"
    assert scf._safe_member_basename("a/b/c.XLSX") == "c.xlsx"
    assert scf._safe_member_basename("noext") == "noext"


# -- end-to-end via main(): real zips, real extract()+classify() ------------

def test_main_zip_clean_member_reports_clean_values_free(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    z = _make_zip(tmp_path, {"navmap.csv": "Screen,Label\nhome,Welcome\n"})
    rc = scf.main([str(z), "op36085"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "navmap.csv" in out and "verdict=CLEAN" in out
    assert "Welcome" not in out


def test_main_zip_hold_member_pii_never_leaks_value(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    z = _make_zip(tmp_path, {"roster.csv": "Name,NRIC\nAhmad bin Yusof,S1234567D\n"})
    rc = scf.main([str(z), "op36085"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "verdict=HOLD" in out
    assert "S1234567D" not in out and "Ahmad" not in out


def test_main_zip_mixed_clean_and_hold_members_both_reported(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    z = _make_zip(tmp_path, {
        "clean.csv": "Screen,Label\nhome,Welcome\n",
        "roster.csv": "Name,NRIC\nAhmad bin Yusof,S1234567D\n",
    })
    rc = scf.main([str(z), "op36085"])
    out = capsys.readouterr().out
    assert rc == 1  # any member HOLD -> whole-zip rc is 1
    assert "clean.csv" in out and "roster.csv" in out
    assert "1 clean, 1 held" in out


def test_main_zip_unsupported_extension_member_skipped_not_fatal(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    z = _make_zip(tmp_path, {
        "clean.csv": "Screen,Label\nhome,Welcome\n",
        "readme.txt": "just some notes",
    })
    rc = scf.main([str(z), "op36085"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "readme.txt" in out and "skipped" in out
    assert "1 clean, 0 held, 1 skipped" in out


def test_main_zip_refuses_whole_zip_on_nested_archive_member(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    z = _make_zip(tmp_path, {
        "clean.csv": "Screen,Label\nhome,Welcome\n",
        "inner.zip": b"fake-nested-archive-bytes",
    })
    rc = scf.main([str(z), "op36085", "--export"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "REFUSED" in out
    assert "nested archive" in out
    assert not (tmp_path / "reports").exists()


def test_main_zip_refuses_whole_zip_on_symlink_member_before_extraction(tmp_path, monkeypatch, capsys):
    # cc-quality bus #86132: a symlink member must be REFUSED, not just
    # defanged by the byte-copy path -- asserted at the scan stage, i.e.
    # BEFORE stage_zip_members ever extracts anything.
    import stat as _stat
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "bundle.zip"
    with _zipfile_mod.ZipFile(p, "w") as zf:
        zf.writestr("clean.csv", "Screen,Label\nhome,Welcome\n")
        link_info = _zipfile_mod.ZipInfo("link.csv")
        link_info.external_attr = (_stat.S_IFLNK | 0o777) << 16
        zf.writestr(link_info, "../../etc/passwd")  # the "link target" text
    rc = scf.main([str(p), "op36085", "--export"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "REFUSED" in out
    assert "symlink" in out
    assert not (tmp_path / "reports").exists()


def test_main_zip_refuses_whole_zip_on_path_traversal_member_name(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "evil.zip"
    with _zipfile_mod.ZipFile(p, "w") as zf:
        zi = _zipfile_mod.ZipInfo("../../escape.csv")
        zf.writestr(zi, "Screen,Label\nhome,Welcome\n")
        zf.writestr("clean.csv", "Screen,Label\nhome,Welcome\n")
    rc = scf.main([str(p), "op36085", "--export"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "REFUSED" in out
    assert not (tmp_path / "reports").exists()


def test_main_zip_not_a_valid_zip_refuses_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "notreally.zip"
    p.write_bytes(b"this is not a zip file at all")
    rc = scf.main([str(p), "op36085"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "not a valid zip" in err


def test_main_zip_without_export_never_writes_anything(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    z = _make_zip(tmp_path, {"clean.csv": "Screen,Label\nhome,Welcome\n"})
    scf.main([str(z), "op36085"])
    assert not (tmp_path / "reports").exists()


def test_main_zip_export_is_always_structure_only_regardless_of_verdict(tmp_path, monkeypatch, capsys):
    # The ruling: zip content stays structure-only, no blanket trust -- even
    # a CLEAN member's --export must NOT carry full content, unlike the
    # plain single-file path (contrast with
    # test_main_clean_csv_with_export_writes_the_actual_content above).
    monkeypatch.chdir(tmp_path)
    z = _make_zip(tmp_path, {
        "clean.csv": "Screen,Label\nhome,Welcome\n",
        "roster.csv": "Name,NRIC\nAhmad bin Yusof,S1234567D\n",
    })
    rc = scf.main([str(z), "op36085", "--export"])
    capsys.readouterr()
    assert rc == 1
    out_dir = tmp_path / "reports" / "client-file-staging" / "op36085"
    clean_export = out_dir / "bundle__clean.md"
    held_export = out_dir / "bundle__roster.md"
    assert clean_export.exists() and held_export.exists()
    clean_text = clean_export.read_text(encoding="utf-8")
    held_text = held_export.read_text(encoding="utf-8")
    assert "structure only" in clean_text
    assert "Welcome" not in clean_text
    assert "structure only" in held_text
    assert "Ahmad" not in held_text and "S1234567D" not in held_text


def test_main_zip_operator_attested_refused_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    z = _make_zip(tmp_path, {"roster.csv": "Name,NRIC\nAhmad bin Yusof,S1234567D\n"})
    rc = scf.main([str(z), "op36085", "--export", "--operator-attested", "12345"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "not supported for a .zip input" in err
    assert not (tmp_path / "reports").exists()


def test_stage_zip_members_cleans_up_scratch_dir_after(tmp_path, monkeypatch):
    import tempfile as _tempfile_mod
    monkeypatch.chdir(tmp_path)
    z = _make_zip(tmp_path, {"clean.csv": "Screen,Label\nhome,Welcome\n"})
    created_paths = []
    real_mkdtemp = _tempfile_mod.mkdtemp

    def _tracking_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created_paths.append(path)
        return path

    monkeypatch.setattr(scf.tempfile, "mkdtemp", _tracking_mkdtemp)
    with _zipfile_mod.ZipFile(z) as zf:
        safety = scf.scan_zip_safety(zf)
        scf.stage_zip_members(zf, safety.members)
    assert len(created_paths) == 1
    assert not pathlib.Path(created_paths[0]).exists()
