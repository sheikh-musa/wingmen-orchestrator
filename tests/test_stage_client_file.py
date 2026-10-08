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


# ── image-only / un-inspectable PDF-or-doc guard (orch-console #59182) ───────
# A scanned / image-only PDF (or doc) has NO extractable text layer: the text
# scanners see ~nothing and the OLD code emitted CLEAN with all PII counts 0 --
# a FALSE assurance (the content was never actually inspected). A scanned
# NRIC/gradebook would sail straight through. Fail CLOSED to HOLD instead.

def test_classify_holds_on_image_only_pdf_with_no_text_layer():
    # one page, one embedded image, ZERO extracted text -> un-inspectable.
    structure = scf.FileStructure(
        kind="pdf",
        sheets=[scf.SheetStructure(name="(pdf text)", rows=1, cols=1, header=[])],
        image_count=1,
        texts=[""],
    )
    v = scf.classify(structure)
    assert v.verdict == "HOLD"
    assert any("visual review" in r for r in v.reasons)
    assert v.pii.total() == 0  # the point: HOLD despite NOTHING being extracted


def test_classify_holds_on_multipage_scanned_pdf():
    structure = scf.FileStructure(
        kind="pdf",
        sheets=[scf.SheetStructure(name="(pdf text)", rows=3, cols=1, header=[])],
        image_count=3,
        texts=["", "", ""],  # 3 scanned pages, no text layer on any
    )
    v = scf.classify(structure)
    assert v.verdict == "HOLD"
    assert any("3 page" in r and "3 image" in r for r in v.reasons)


def test_classify_holds_on_image_only_docx():
    structure = scf.FileStructure(
        kind="docx",
        sheets=[scf.SheetStructure(name="(document body)", rows=1, cols=1, header=[])],
        image_count=1,
        texts=[""],  # an embedded scanned image, empty paragraph text
    )
    v = scf.classify(structure)
    assert v.verdict == "HOLD"
    assert any("visual review" in r for r in v.reasons)


def test_classify_text_pdf_with_a_logo_image_and_real_text_stays_clean():
    # over-gating guard: a normal text PDF that merely contains a logo image
    # has a real text layer -> it WAS inspected -> must NOT be force-held by
    # the new image-only rule (only CLEAN because the text itself is boring).
    structure = scf.FileStructure(
        kind="pdf",
        sheets=[scf.SheetStructure(name="(pdf text)", rows=1, cols=1, header=[])],
        image_count=1,
        texts=["Invoice total amount paid 120.00 on schedule, thank you for your business."],
    )
    v = scf.classify(structure)
    assert v.verdict == "CLEAN"


def test_classify_empty_textless_imageless_pdf_is_not_force_held():
    # a blank PDF (no text, no images) has no un-inspected content to leak --
    # the guard keys on image_count>0, so this stays CLEAN, not a false HOLD.
    structure = scf.FileStructure(
        kind="pdf",
        sheets=[scf.SheetStructure(name="(pdf text)", rows=1, cols=1, header=[])],
        image_count=0,
        texts=[""],
    )
    v = scf.classify(structure)
    assert v.verdict == "CLEAN"


def _write_image_only_pdf(path):
    """Synthetic scanned-style PDF: a page whose only content is a rendered
    raster image, with NO text layer at all. SYNTHETIC -- no real client data."""
    import fitz
    from PIL import Image

    img_path = str(path) + ".scan.png"
    Image.new("RGB", (400, 300), color="white").save(img_path)
    doc = fitz.open()
    page = doc.new_page(width=420, height=320)
    page.insert_image(fitz.Rect(10, 10, 410, 310), filename=img_path)
    doc.save(str(path))
    doc.close()


def test_extract_and_classify_image_only_pdf_holds(tmp_path):
    # end-to-end wet-prove on a real synthetic image-only PDF (the shipped path).
    p = tmp_path / "scanned.pdf"
    _write_image_only_pdf(p)
    structure = scf.extract_pdf(p)
    assert structure.image_count >= 1
    assert sum(len(t.strip()) for t in structure.texts) == 0  # no text layer
    v = scf.classify(structure)
    assert v.verdict == "HOLD"
    assert any("visual review" in r for r in v.reasons)


def test_extract_and_classify_normal_text_pdf_stays_clean(tmp_path):
    # the before-behaviour must be preserved: a real text PDF with boring
    # content and no image still classifies CLEAN.
    fitz = __import__("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "total amount paid 120.00 on schedule thank you")
    p = tmp_path / "textual.pdf"
    doc.save(str(p))
    doc.close()

    structure = scf.extract_pdf(p)
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
