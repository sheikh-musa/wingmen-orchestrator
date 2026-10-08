#!/usr/bin/env python3
"""stage_client_file.py — deterministic, no-LLM structural + PII-shape inspection
of an inbound client file (xlsx/csv/docx/pdf), for orch-console to run BEFORE
handing a file to a lane (orch-console bus #51060, Musa op#25437-25440: a lane
must never tell a client "I can't open your file" — the file is staged here
first, by a human, not by a lane reading raw cell values).

STDOUT always stays values-free: structure (sheets/dims/image count) + PII-
shape COUNTS (never a matched value) + the CLEAN/HOLD verdict + reason(s) —
this is what any agent running the script sees. The exported FILE is
different: on CLEAN with --export, it writes the ACTUAL readable content
(sheets as markdown tables, docx/pdf prose as text) — that is the whole point
of staging a CLEAN file, so a lane can read it without opening the raw file
itself — preceded by the same values-free structural header. On HOLD, nothing
is written, ever; only counts + reason print to stdout (orch-console bus
#51060/#51085).

Fail-closed (orch-console #51085): any PII-shape hit OR a person-record
signal (header-keyword match OR >20 rows with a name-like column) forces
HOLD, never guessed CLEAN — content export is gated entirely on CLEAN, so a
person-record / PII-shaped file's content is never written anywhere. A parse
error carries no file content.

KNOWN GAP (cc-quality bus #51094, non-blocking): the ORIGINAL FILENAME is
never itself scanned for a PII shape, and is echoed verbatim both in the
stdout report and as the exported report's own filename. A file literally
named with a client's NRIC (etc.) would carry that into the export untouched.
Narrow (filenames rarely carry PII) — not fixed here, flag if it matters.

Image support + person-name hardening (bus #52461/#52465, real incident
2026-10-05): a lane Read a genuine UAE-gov trainee gradebook SCREENSHOT
directly (no stager coverage for images at all), and separately this stager
returned CLEAN on a single-person progress report whose only PII-shaped
signal was a name + grades — no NRIC/phone/email shape, no 20+-row table, so
neither existing heuristic fired. Images are staged via local OCR only (no
network, no LLM — pytesseract/tesseract, same fail-closed posture as every
other format here); a >3-"row" OCR text block is treated as a data table
and forces HOLD the same way a >20-row sheet does for tabular formats, just
at a much lower bar (an image table is usually a small gradebook/roster, not
a 1000-row export). Person-name detection now also scans free TEXT (not just
sheet headers) for name-shaped patterns: a label (Name/Student/Trainee/
Candidate/Learner) followed by a value, an honorific + capitalised name, or
an Arabic-script / name-particle (bin/binti/ibn) run — any hit forces HOLD
regardless of row count, closing the single-person-record gap above.

Per-channel sensitive override (bus #52465): --channel reads
bot_channels.sensitive_data (migration 091; DEFAULT true -- fails CLOSED
for every channel except an explicit internal-console allowlist
(nazim-console, operator-orch, cai-channel, finance-console, war-room);
an unknown channel or an
unreachable DB is also treated as sensitive). A sensitive channel NEVER
exports full content on --export, even on a CLEAN verdict — only the
values-free structural header, so a gov/client-data channel (cosem-exams,
gazzabyte-irsyad, cosem-tdu, ...) can't have content heuristics alone
decide what leaves the raw file.

Structure-only on a HELD file (--structure-only, bus #52465 follow-up): a
HOLD verdict normally exports NOTHING, which leaves a lane unable to see even
a blank template's column/field LAYOUT to build a feature from. Passing
--structure-only with --export forces the values-free structural export
(export_structure_only) REGARDLESS of verdict — the column headers / sheet
names / row+col counts / kind only, NEVER a single row data value — so a lane
can get a HELD file's shape safely. Plain --export is unchanged: full content
on CLEAN, nothing on HOLD.

Operator-attested content export on a HELD file (--operator-attested <op_msg_id>,
orch-console): a sanctioned, auditable way for a lane to get the ACTUAL readable
content of a file EVEN WHEN the verdict is HOLD, used when the operator (Musa)
has explicitly authorized working from that file's content (e.g. "update the
existing skill sheets with the changes in these files"). With --export, passing
--operator-attested <op_msg_id> (an operator_messages row id) exports the full
content (the SAME writer as a CLEAN --export, export_clean_file) on ANY verdict
including HOLD, and logs every such export to an append-only audit file.

  WHY THIS IS SAFE (a deliberate, narrow loosening of the "agents don't open
  client files" floor). It is NOT a blanket bypass; it is a sanctioned
  alternative to an agent reaching for an un-audited `cat`/Bash bypass when the
  operator genuinely wants the content used. The loosening is made safe by four
  things an agent cannot forge on its own:
    1. a REAL operator-authorization ROW in operator_messages whose
       from_user_id == MUSA_TELEGRAM_ID — an agent, a relay, or a mis-targeted
       send-keys cannot fabricate an inbound message from Musa's own Telegram id
       (same trust anchor as require_verified_authorization.py);
    2. RECENCY — the cited row must be within the last 48h
       (_OPERATOR_ATTESTED_MAX_AGE_HOURS), so an old authorization can't be
       replayed indefinitely;
    3. FAIL-CLOSED verification — no row, wrong from_user_id, too old, no
       MUSA_TELEGRAM_ID, or an UNREACHABLE DB all REFUSE (exit 2, write
       nothing); there is no "assume yes" path (matches is_sensitive_channel
       and require_verified_authorization — fail-closed on every uncertainty);
    4. an APPEND-ONLY AUDIT LOG — every operator-attested export (CLEAN or HOLD)
       appends one line to reports/client-file-staging/_operator_attested_exports.log
       (ISO timestamp, op_id, source basename, cited op_msg_id, verified
       from_user_id, verdict), so every use is reviewable after the fact.

  KNOWN LIMITATION (stated plainly, by design): this gate verifies that the
  cited row is a RECENT message from Musa — it does NOT verify that the row
  SEMANTICALLY authorizes THIS SPECIFIC file. The CALLER (console) is
  accountable for citing a row that genuinely authorizes the file in question;
  the audit log is what makes a wrong/over-broad citation reviewable. Plain
  --export (full content on CLEAN, nothing on HOLD), --structure-only, and the
  sensitive-channel structure-only path are all UNCHANGED by this flag.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── PII-shape patterns (COUNT-ONLY — never used to extract/print a value) ────
# Singapore NRIC/FIN/birth-cert shape: letter + 7 digits + letter.
_NRIC_FIN_BC_RE = re.compile(r"\b[STFGM]\d{7}[A-Z]\b", re.IGNORECASE)
# UAE Emirates ID: 784-YYYY-NNNNNNN-N.
_EMIRATES_ID_RE = re.compile(r"\b784-\d{4}-\d{7}-\d\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# A phone-shaped number: has a leading + or visible separators/parens, distinct
# from a bare long digit run (caught separately by _LONG_NUMBER_RE below).
_PHONE_RE = re.compile(
    r"(?<![\w.-])(?:\+\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]\d{3,4}[-.\s]?\d{3,4}(?![\w.-])"
)
# A BARE unformatted local mobile number (cc-quality bus #51094 BLOCKING #2):
# SG mobiles are 8 digits, UAE mobiles 10 -- "91234567" or "0501234567" have
# no separator/country-code/parens at all, so _PHONE_RE above (which requires
# a mandatory separator between its first two digit groups) never matches
# them, and _LONG_NUMBER_RE's 12+ floor is too high. Either a CONTIGUOUS
# 8-10-digit run, or exactly one space-separated 3-4+3-4(+2-4) split (the
# common "9123 4567" display convention) -- deliberately NOT dash-separated,
# since an ISO date ("2026-01-01") or an Emirates ID fragment ("784-1990-...")
# would otherwise false-positive (each is digit-DASH-digit, which the earlier,
# looser `(?:\d[ -]?){8,10}` form matched by mistake). The negative look-
# around on '.'/digit on both sides keeps it off a decimal amount or a
# substring of a longer (12+) digit run.
_BARE_LOCAL_PHONE_RE = re.compile(
    r"(?<![\d.])(?:\d{8,10}|\d{3,4} \d{3,4}(?: \d{2,4})?)(?![\d.])"
)
# Any other long digit run (12+) — card/account/id-shaped numbers.
_LONG_NUMBER_RE = re.compile(r"\b(?:\d[ -]?){12,}\b")

_PERSON_HEADER_KEYWORDS = (
    "name", "military", "id", "phone", "mobile", "dob", "birth",
    "emirates", "nric", "email", "address", "nationality",
    "student", "trainee", "candidate", "learner",
)

# ── person-name shape patterns, scanned over free TEXT (not just headers) ───
# bus #52465: a single-person progress report ("Student: Ahmad bin Yusof")
# has no 20+-row table and no header row at all — these catch the name
# itself, wherever it appears in the extracted text.
_PERSON_LABEL_VALUE_RE = re.compile(
    r"\b(?:name|student|trainee|candidate|learner)\s*[:\-]\s*[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+)*",
    re.IGNORECASE,
)
_HONORIFIC_NAME_RE = re.compile(
    r"\b(?:Mr|Mrs|Ms|Mx|Dr|Ustaz|Ustazah|Hajjah|Haji|Sheikh)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*"
)
# "<Capitalised word> bin/binti/ibn/bint <...>" — common Malay/Arabic
# patronymic name construction (e.g. "Ahmad bin Yusof").
_ARABIC_NAME_PARTICLE_RE = re.compile(
    r"\b[A-Z][a-z]+\s+(?:bin|binti|ibn|bint)\s+[A-Z][a-z]+\b"
)
# Arabic-script run (2+ letters) — a name rendered in Arabic script rather
# than transliterated is still a name.
_ARABIC_SCRIPT_RE = re.compile(r"[؀-ۿ]{2,}")

# "progress report"/"transcript"/etc. ALONE is not sensitive (it's just a
# document type) -- only flagged as an extra reason when it co-occurs with
# an actual name-shaped value (scan_person_name_hits > 0), which already
# forces HOLD on its own. This just makes the HOLD reason legible.
_RECORD_TYPE_KEYWORDS_RE = re.compile(
    r"\b(?:progress report|transcript|result slip|grade|score|student id)\b", re.IGNORECASE
)


@dataclass
class PiiCounts:
    nric_fin_bc: int = 0
    emirates_id: int = 0
    phone: int = 0
    email: int = 0
    long_number: int = 0

    def total(self) -> int:
        return self.nric_fin_bc + self.emirates_id + self.phone + self.email + self.long_number


def _spans(pattern: re.Pattern, text: str) -> list[tuple[int, int]]:
    return [m.span() for m in pattern.finditer(text)]


def _overlaps(span: tuple[int, int], others: list[tuple[int, int]]) -> bool:
    s0, e0 = span
    return any(s0 < e1 and s1 < e0 for s1, e1 in others)


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Overlap-collapse a list of (start, end) spans so a value matched by
    TWO sub-patterns (e.g. a formatted and a bare-digit phone pattern both
    matching the same number) counts once, not twice."""
    if not spans:
        return []
    ordered = sorted(spans)
    merged = [ordered[0]]
    for s, e in ordered[1:]:
        ls, le = merged[-1]
        if s < le:
            merged[-1] = (ls, max(le, e))
        else:
            merged.append((s, e))
    return merged


def scan_pii_counts(text: str) -> PiiCounts:
    """Count-only PII-shape scan of *text*. NEVER returns or prints a matched
    value — only how many of each shape were found. Checked most-specific
    first (nric/emirates/phone/email); a long_number match that overlaps one
    of those is NOT double-counted — it is the same underlying value, already
    counted once under its more specific category. "phone" merges a formatted
    match (_PHONE_RE) with a bare unformatted local-mobile match
    (_BARE_LOCAL_PHONE_RE, cc-quality bus #51094 BLOCKING #2) so an overlap
    between the two sub-patterns isn't counted twice either."""
    nric_spans = _spans(_NRIC_FIN_BC_RE, text)
    emirates_spans = _spans(_EMIRATES_ID_RE, text)
    phone_spans = _merge_spans(_spans(_PHONE_RE, text) + _spans(_BARE_LOCAL_PHONE_RE, text))
    email_spans = _spans(_EMAIL_RE, text)
    specific_spans = nric_spans + emirates_spans + phone_spans + email_spans
    long_number = sum(
        1 for span in _spans(_LONG_NUMBER_RE, text) if not _overlaps(span, specific_spans)
    )
    return PiiCounts(
        nric_fin_bc=len(nric_spans),
        emirates_id=len(emirates_spans),
        phone=len(phone_spans),
        email=len(email_spans),
        long_number=long_number,
    )


def merge_counts(counts: list[PiiCounts]) -> PiiCounts:
    out = PiiCounts()
    for c in counts:
        out.nric_fin_bc += c.nric_fin_bc
        out.emirates_id += c.emirates_id
        out.phone += c.phone
        out.email += c.email
        out.long_number += c.long_number
    return out


def is_person_record_header(headers: list[str]) -> bool:
    """True if any header cell matches a person-record keyword (substring,
    case-insensitive) — the first of the two person-record heuristics."""
    for h in headers:
        hl = (h or "").strip().lower()
        if any(kw in hl for kw in _PERSON_HEADER_KEYWORDS):
            return True
    return False


def has_name_like_column(headers: list[str]) -> bool:
    return any("name" in (h or "").strip().lower() for h in headers)


def scan_person_name_hits(text: str) -> int:
    """Count-only scan for a name-SHAPED value in free text (never returns
    the matched text itself) — label+value, honorific+name, Malay/Arabic
    patronymic particle, or an Arabic-script run. Any hit (>0) is a
    person-record signal on its own, independent of row count or header
    keywords (bus #52465 single-person-record gap)."""
    return (
        len(_PERSON_LABEL_VALUE_RE.findall(text))
        + len(_HONORIFIC_NAME_RE.findall(text))
        + len(_ARABIC_NAME_PARTICLE_RE.findall(text))
        + len(_ARABIC_SCRIPT_RE.findall(text))
    )


_HEADER_REDACTION = "⟨redacted⟩"  # ⟨redacted⟩


def structural_label_or_redacted(cell: str) -> str:
    """Make ONE column-header cell safe to emit in the values-free structural
    render. A header cell is normally a field LABEL ("Name", "Score") =
    structure, safe. BUT extract_csv/extract_xlsx set header = row 0
    UNCONDITIONALLY — there is no real-header detection — so for a HEADERLESS
    file the "header" is actually a DATA row. Scrub each cell through the SAME
    value-shape detectors used for row content: a cell carrying any PII-shape
    (phone/nric/emirates_id/email/long_number) or a name-SHAPE (label+value,
    honorific, Malay/Arabic particle, Arabic script) is a data VALUE, not a
    label, and is redacted — never emitted. Genuine labels carry none of those
    shapes and pass through verbatim. Keeps the structural render's contract
    ("never a row data value") true even for a headerless file."""
    c = (cell or "").strip()
    if not c:
        return ""
    if scan_pii_counts(c).total() > 0 or scan_person_name_hits(c) > 0:
        return _HEADER_REDACTION
    return c


# A data-table "row" detected in OCR'd image text, above which the image is
# treated as a roster/gradebook rather than a UI screenshot (bus #52461) --
# deliberately much lower than the 20-row tabular-format threshold, since an
# image table worth holding is usually a small roster, not a bulk export.
_IMAGE_DATA_ROW_HOLD_THRESHOLD = 3


@dataclass
class SheetStructure:
    name: str
    rows: int
    cols: int
    header: list[str] = field(default_factory=list)
    # Actual cell values, stringified — NEVER rendered except by render_content()
    # on a CLEAN --export, which only runs after classify() has already decided
    # there is nothing sensitive here.
    rows_data: list[list[str]] = field(default_factory=list)


@dataclass
class FileStructure:
    """Format-independent intermediate representation an extractor returns —
    the classifier below is format-agnostic and operates only on this."""
    kind: str  # "xlsx" | "csv" | "docx" | "pdf"
    sheets: list[SheetStructure] = field(default_factory=list)
    image_count: int = 0
    texts: list[str] = field(default_factory=list)  # all extracted text, for PII scanning
    # Non-tabular prose (docx body paragraphs, pdf page text) as (label, full_text)
    # pairs — same export-only-on-CLEAN rule as SheetStructure.rows_data.
    text_blocks: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class StageVerdict:
    verdict: str  # "CLEAN" | "HOLD"
    reasons: list[str] = field(default_factory=list)
    pii: PiiCounts = field(default_factory=PiiCounts)
    person_record: bool = False

    def render(self, structure: FileStructure) -> str:
        lines = [f"verdict: {self.verdict}"]
        if self.reasons:
            lines.append("reasons: " + "; ".join(self.reasons))
        lines.append(f"kind: {structure.kind}")
        lines.append(f"image_count: {structure.image_count}")
        lines.append("sheets:")
        for s in structure.sheets:
            lines.append(f"  - {s.name}: {s.rows} rows x {s.cols} cols")
            # Column HEADER LABELS are structure (field names like "Name",
            # "Score"), not PII values — a lane needs them to build from a
            # file's layout. Only the header row, never rows_data. BUT header
            # = row 0 unconditionally (no real-header detection), so a
            # HEADERLESS file's "header" is a DATA row — scrub each cell
            # through the value-shape detectors so a data value can never leak
            # here (structural_label_or_redacted); genuine labels pass through.
            if s.header:
                safe_cols = [structural_label_or_redacted(h) for h in s.header]
                lines.append("    columns: " + " | ".join(safe_cols))
        lines.append(
            "pii_shape_counts (counts only, no values): "
            f"nric_fin_bc={self.pii.nric_fin_bc} emirates_id={self.pii.emirates_id} "
            f"phone={self.pii.phone} email={self.pii.email} long_number={self.pii.long_number}"
        )
        lines.append(f"person_record_heuristic: {self.person_record}")
        return "\n".join(lines)


def _render_markdown_table(header: list[str], rows: list[list[str]]) -> str:
    if not header and not rows:
        return "(empty)"
    cols = len(header) if header else (len(rows[0]) if rows else 0)
    head = header if header else [f"col{i + 1}" for i in range(cols)]
    lines = [
        "| " + " | ".join(h or "" for h in head) + " |",
        "| " + " | ".join("---" for _ in head) + " |",
    ]
    for r in rows:
        cells = list(r) + [""] * (cols - len(r))
        lines.append("| " + " | ".join(str(c) if c is not None else "" for c in cells[:cols]) + " |")
    return "\n".join(lines)


def render_content(structure: FileStructure) -> str:
    """Render the file's ACTUAL content as readable markdown/text. Sheets
    render as markdown tables (SheetStructure.rows_data); free prose
    (FileStructure.text_blocks) renders as plain text blocks.

    MUST ONLY ever be called on the --export path after classify() returned
    CLEAN — never for stdout, never for a HOLD verdict. This function itself
    has no safety gate; the gate is "only call it when CLEAN" at the caller."""
    parts = []
    for s in structure.sheets:
        if s.rows_data or s.header:
            parts.append(f"## {s.name}\n\n" + _render_markdown_table(s.header, s.rows_data))
    for label, text in structure.text_blocks:
        if text.strip():
            parts.append(f"## {label}\n\n{text}")
    return "\n\n".join(parts) if parts else "(no content)"


def classify(structure: FileStructure) -> StageVerdict:
    """PURE core (no I/O): decide CLEAN vs HOLD from an already-extracted
    FileStructure. FAIL-CLOSED: any PII-shape hit OR a person-record signal
    -> HOLD, never guessed CLEAN. Never inspects or returns a raw value —
    only the counts/booleans already computed by the scanners above."""
    pii = merge_counts([scan_pii_counts(t) for t in structure.texts])
    person_record = False
    reasons: list[str] = []
    for s in structure.sheets:
        if is_person_record_header(s.header):
            person_record = True
            reasons.append(f"sheet '{s.name}' header matches a person-record keyword")
        if s.rows > 20 and has_name_like_column(s.header):
            person_record = True
            reasons.append(f"sheet '{s.name}' has {s.rows} rows with a name-like column")
        if structure.kind == "image" and s.rows > _IMAGE_DATA_ROW_HOLD_THRESHOLD:
            person_record = True
            reasons.append(
                f"image text looks like a data table ({s.rows} rows, "
                f"threshold {_IMAGE_DATA_ROW_HOLD_THRESHOLD})"
            )
    name_hits = sum(scan_person_name_hits(t) for t in structure.texts)
    if name_hits > 0:
        person_record = True
        if any(_RECORD_TYPE_KEYWORDS_RE.search(t) for t in structure.texts):
            reasons.append(
                f"{name_hits} name-shaped value(s) found alongside a record-type keyword "
                "(progress report/transcript/grade/score/student ID)"
            )
        else:
            reasons.append(f"{name_hits} name-shaped value(s) detected")
    if pii.total() > 0:
        reasons.append(f"{pii.total()} PII-shaped value(s) detected")
    verdict = "HOLD" if (pii.total() > 0 or person_record) else "CLEAN"
    return StageVerdict(verdict=verdict, reasons=reasons, pii=pii, person_record=person_record)


# ── format-specific extractors — each returns the common FileStructure IR ────

def extract_csv(path: Path) -> FileStructure:
    import csv

    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        rows = list(csv.reader(fh))
    header = rows[0] if rows else []
    data_rows = rows[1:]
    texts = [",".join(r) for r in rows]
    return FileStructure(
        kind="csv",
        sheets=[SheetStructure(name=path.name, rows=len(data_rows), cols=len(header),
                                header=header, rows_data=data_rows)],
        image_count=0,
        texts=texts,
    )


def extract_xlsx(path: Path) -> FileStructure:
    import openpyxl

    wb = openpyxl.load_workbook(str(path), data_only=True, read_only=False)
    sheets: list[SheetStructure] = []
    texts: list[str] = []
    image_count = 0
    for ws in wb.worksheets:
        values = list(ws.iter_rows(values_only=True))
        header = [str(c) if c is not None else "" for c in values[0]] if values else []
        data_rows = values[1:] if values else []
        rows_data = [[str(c) if c is not None else "" for c in row] for row in data_rows]
        sheets.append(SheetStructure(name=ws.title, rows=len(data_rows), cols=(ws.max_column or 0),
                                      header=header, rows_data=rows_data))
        for row in values:
            texts.append(" ".join(str(c) for c in row if c is not None))
        image_count += len(getattr(ws, "_images", []))
    return FileStructure(kind="xlsx", sheets=sheets, image_count=image_count, texts=texts)


def extract_docx(path: Path) -> FileStructure:
    import docx

    d = docx.Document(str(path))
    texts = [p.text for p in d.paragraphs]
    sheets: list[SheetStructure] = []
    for i, t in enumerate(d.tables):
        rows = [[cell.text for cell in row.cells] for row in t.rows]
        header = rows[0] if rows else []
        data_rows = rows[1:] if rows else []
        sheets.append(SheetStructure(name=f"table{i + 1}", rows=len(data_rows), cols=len(header),
                                      header=header, rows_data=data_rows))
        for r in rows:
            texts.append(" ".join(r))
    image_count = len(d.inline_shapes)
    if not sheets:
        # No tables -- still report a pseudo-sheet so rows/cols aren't silently
        # absent from the structure summary.
        sheets.append(SheetStructure(name="(document body)", rows=len(d.paragraphs), cols=1, header=[]))
    # Body prose is content too (orch-console #51085) -- a CLEAN export must
    # carry it alongside any tables, not just the table data.
    body_text = "\n".join(t for t in texts if t.strip())
    text_blocks = [("document body", body_text)] if body_text else []
    return FileStructure(kind="docx", sheets=sheets, image_count=image_count, texts=texts, text_blocks=text_blocks)


def extract_pdf(path: Path) -> FileStructure:
    import pdfplumber

    sheets: list[SheetStructure] = []
    texts: list[str] = []
    text_blocks: list[tuple[str, str]] = []
    image_count = 0
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages):
            page_text = page.extract_text() or ""
            texts.append(page_text)
            if page_text.strip():
                # Page prose is content too (orch-console #51085), alongside
                # any tables found on the same page.
                text_blocks.append((f"page {i + 1} text", page_text))
            image_count += len(page.images)
            for ti, table in enumerate(page.extract_tables() or []):
                header = [str(h or "") for h in (table[0] if table else [])]
                data_rows = [[str(c or "") for c in row] for row in (table[1:] if table else [])]
                sheets.append(SheetStructure(
                    name=f"page{i + 1}-table{ti + 1}", rows=len(data_rows), cols=len(header),
                    header=header, rows_data=data_rows,
                ))
        if not sheets:
            sheets.append(SheetStructure(name="(pdf text)", rows=len(pdf.pages), cols=1, header=[]))
    return FileStructure(kind="pdf", sheets=sheets, image_count=image_count, texts=texts, text_blocks=text_blocks)


def extract_image(path: Path) -> FileStructure:
    """Local OCR only (bus #52461) -- no network, no LLM. pytesseract shells
    out to the local `tesseract` binary; the image bytes never leave this
    process. A non-empty OCR line with >=2 whitespace-separated tokens AND
    at least one digit is treated as a data "row" (a UI label/button reads
    as 1-3 plain words; a gradebook/roster row reads as several
    digit-bearing tokens across OCR's whitespace-collapsed columns) -- see
    _IMAGE_DATA_ROW_HOLD_THRESHOLD and classify()."""
    from PIL import Image
    import pytesseract

    with Image.open(path) as img:
        text = pytesseract.image_to_string(img)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    def _is_row_like(line: str) -> bool:
        tokens = line.split()
        return len(tokens) >= 2 and any(re.search(r"\d", tok) for tok in tokens)

    row_lines = [ln for ln in lines if _is_row_like(ln)]
    rows_data = [ln.split() for ln in row_lines]
    cols = max((len(r) for r in rows_data), default=0)
    sheet = SheetStructure(name="(image text)", rows=len(rows_data), cols=cols, rows_data=rows_data)
    text_blocks = [("image OCR text", text)] if text.strip() else []
    return FileStructure(kind="image", sheets=[sheet], image_count=1, texts=[text], text_blocks=text_blocks)


_EXTRACTORS = {
    ".csv": extract_csv,
    ".xlsx": extract_xlsx,
    ".docx": extract_docx,
    ".pdf": extract_pdf,
    ".png": extract_image,
    ".jpg": extract_image,
    ".jpeg": extract_image,
}


def extract(path: Path) -> FileStructure:
    ext = path.suffix.lower()
    fn = _EXTRACTORS.get(ext)
    if fn is None:
        raise SystemExit(f"stage_client_file: unsupported extension {ext!r} (supported: {sorted(_EXTRACTORS)})")
    try:
        return fn(path)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 — never let a value ride out on a traceback
        raise SystemExit(
            f"stage_client_file: failed to parse {path.name}: {type(e).__name__} "
            "(no further detail, to avoid leaking content in an error)"
        ) from e


_OPID_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]")


def sanitize_op_id(op_id: str) -> str:
    return _OPID_SAFE_RE.sub("_", op_id) or "unknown"


def export_clean_file(verdict: StageVerdict, structure: FileStructure, op_id: str, original_name: str) -> Path:
    """Write a CLEAN file's ACTUAL content as a readable markdown copy —
    sheets as tables, docx/pdf prose as text — preceded by the same
    values-free structural header shown on stdout. Caller MUST have already
    confirmed verdict.verdict == 'CLEAN'; this function does not re-check."""
    header = verdict.render(structure)
    content = render_content(structure)
    full_text = f"{header}\n\n---\n\n{content}\n"
    out_dir = Path("reports/client-file-staging") / sanitize_op_id(op_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{Path(original_name).stem}.md"
    out_path.write_text(full_text, encoding="utf-8")
    return out_path


def is_sensitive_channel(channel: str) -> bool:
    """Look up bot_channels.sensitive_data for *channel* (bus #52465). Fails
    CLOSED -- an unknown channel, a missing column/table, or an unreachable
    DB is treated as sensitive, matching this script's existing
    fail-closed-on-ambiguity design throughout (orch-console #51085). DSN
    comes from the DATABASE_URL env var only — never a literal/CLI arg."""
    try:
        import psycopg

        dsn = os.environ["DATABASE_URL"]
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT sensitive_data FROM bot_channels WHERE channel_key = %s", (channel,))
                row = cur.fetchone()
                if row is None:
                    return True
                return bool(row[0])
    except Exception:
        return True


def export_structure_only(verdict: StageVerdict, structure: FileStructure, op_id: str, original_name: str) -> Path:
    """Values-free structural export: the structural header only (kind, sheet
    names, row+col counts, COLUMN HEADER LABELS), never any row data value.

    Two callers (both bus #52465):
      - a sensitive channel on a CLEAN verdict — content-heuristics alone must
        not decide what leaves the raw file (migration 091 allowlist);
      - the --structure-only flag on ANY verdict, including HOLD — so a lane
        can get a HELD file's column/field LAYOUT without any data escaping.
    The structural header this emits is the SAME values-free header shown on
    stdout, so it never carries a row value regardless of verdict."""
    header = verdict.render(structure)
    out_dir = Path("reports/client-file-staging") / sanitize_op_id(op_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{Path(original_name).stem}.md"
    if verdict.verdict == "HOLD":
        note = ("(structure only — HELD file, content withheld; column headers / "
                "sheet names / shape only, no data values)")
    else:
        note = "(structure only — sensitive channel, content withheld)"
    out_path.write_text(header + "\n\n" + note + "\n", encoding="utf-8")
    return out_path


# ── operator-attested content export (orch-console) ─────────────────────────
# A deliberate, narrow loosening of the "agents don't open client files" floor,
# made safe by a REAL operator-authorization row an agent cannot forge, recency,
# fail-closed verification, and an append-only audit log. See the module
# docstring ("Operator-attested content export") for the full rationale and the
# stated limitation.

# How recent the cited operator_messages row must be, in hours. Configurable
# here so the recency floor is a single, obvious constant.
_OPERATOR_ATTESTED_MAX_AGE_HOURS = 48

# Append-only audit log of every operator-attested export (CLEAN or HOLD).
_OPERATOR_ATTESTED_AUDIT_LOG = (
    Path("reports/client-file-staging") / "_operator_attested_exports.log"
)


@dataclass
class AttestationResult:
    """Outcome of verify_operator_attestation. ok=False is always fail-closed:
    the caller must write nothing and refuse. from_user_id is the id the row
    verified against (for the audit log) only when ok is True."""
    ok: bool
    from_user_id: str | None = None
    reason: str = ""


def _as_aware_utc(ts) -> datetime | None:
    """Normalize a timestamp (datetime or ISO-8601 string, as psycopg or a
    monkeypatched row may hand back) to an aware UTC datetime; None if it can't
    be parsed (caller treats as fail-closed)."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, str):
        s = ts.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def verify_operator_attestation(
    op_msg_id: int, *, max_age_hours: int = _OPERATOR_ATTESTED_MAX_AGE_HOURS
) -> AttestationResult:
    """Verify a cited operator_messages row authorizes using a HELD file's
    content, FAIL-CLOSED. Returns AttestationResult(ok=False, ...) — refuse,
    write nothing — unless ALL hold:
      - a row with id=op_msg_id exists in operator_messages,
      - its from_user_id == MUSA_TELEGRAM_ID (the operator's real Telegram id —
        an agent/relay cannot forge an inbound message from Musa's own id),
      - it was created within the last `max_age_hours` (default 48h).

    DB access uses psycopg with the DSN from DATABASE_URL (the SAME env-only
    pattern as is_sensitive_channel — never a literal/CLI DSN). Any uncertainty
    — MUSA_TELEGRAM_ID unset, DATABASE_URL unset, an UNREACHABLE DB, a missing
    row, a wrong from_user_id, an unparseable/too-old created_at — all REFUSE.
    There is no "assume yes" path.

    This verifies the row is a RECENT Musa message; it does NOT verify the row
    semantically authorizes THIS file — see the module docstring's KNOWN
    LIMITATION. The audit log makes every use reviewable."""
    musa_id = os.environ.get("MUSA_TELEGRAM_ID", "").strip()
    if not musa_id:
        return AttestationResult(False, reason="MUSA_TELEGRAM_ID unset — cannot verify — fail-closed")
    try:
        import psycopg

        dsn = os.environ["DATABASE_URL"]
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT from_user_id, created_at FROM operator_messages WHERE id = %s",
                    (op_msg_id,),
                )
                row = cur.fetchone()
    except Exception as e:  # unreachable DB / no DATABASE_URL / query error -> DENY
        return AttestationResult(
            False, reason=f"attestation DB check failed ({type(e).__name__}) — fail-closed"
        )
    if row is None:
        return AttestationResult(
            False, reason=f"no operator_messages row id={op_msg_id} — fail-closed"
        )
    from_user_id, created_at = row[0], row[1]
    if str(from_user_id or "") != musa_id:
        return AttestationResult(
            False,
            reason=f"operator_messages id={op_msg_id} from_user_id does not match "
                   "MUSA_TELEGRAM_ID — fail-closed",
        )
    created = _as_aware_utc(created_at)
    if created is None:
        return AttestationResult(
            False, reason=f"operator_messages id={op_msg_id} has no parseable created_at — fail-closed"
        )
    age = datetime.now(timezone.utc) - created
    if age > timedelta(hours=max_age_hours):
        return AttestationResult(
            False,
            reason=f"operator_messages id={op_msg_id} is older than {max_age_hours}h "
                   f"(age {age}) — fail-closed",
        )
    return AttestationResult(True, from_user_id=str(from_user_id),
                             reason=f"verified recent Musa authorization op_msg id={op_msg_id}")


def append_operator_attested_audit(
    *, op_id: str, source_basename: str, op_msg_id: int, from_user_id: str, verdict: str
) -> None:
    """Append ONE line to the append-only operator-attested export audit log —
    ISO timestamp, op_id, source basename, cited op_msg_id, the verified
    from_user_id, and the verdict. Called on EVERY operator-attested export,
    whether the file was CLEAN or HOLD, so every use of the loosened path is
    reviewable after the fact. Open mode 'a' only — never truncates."""
    _OPERATOR_ATTESTED_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = "\t".join([
        datetime.now(timezone.utc).isoformat(),
        op_id,
        source_basename,
        str(op_msg_id),
        str(from_user_id),
        verdict,
    ])
    with open(_OPERATOR_ATTESTED_AUDIT_LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("path", help="path to the client file (.xlsx/.csv/.docx/.pdf/.png/.jpg/.jpeg)")
    p.add_argument("op_id", help="op id this file is staged under (free-form string)")
    p.add_argument("--export", action="store_true",
                   help="on CLEAN, write the actual readable content to reports/client-file-staging/<op_id>/; "
                        "no-op on HOLD (unless --structure-only is also passed)")
    p.add_argument("--structure-only", action="store_true",
                   help="with --export, write only the values-free structural header (kind, sheet names, "
                        "row+col counts, column header LABELS — never a row data value) REGARDLESS of the "
                        "CLEAN/HOLD verdict, so a lane can get a HELD file's column/field layout safely")
    p.add_argument("--channel",
                   help="bot_channels.channel_key this file arrived on; if flagged sensitive_data, --export "
                        "writes structure only (never content), even on a CLEAN verdict (bus #52465)")
    p.add_argument("--operator-attested", type=int, metavar="OP_MSG_ID", default=None,
                   help="operator_messages row id in which Musa (from_user_id == MUSA_TELEGRAM_ID, within the "
                        "last 48h) authorized using this file's content. With --export, exports the ACTUAL "
                        "content on ANY verdict INCLUDING HOLD (fail-closed verification; every export is "
                        "appended to reports/client-file-staging/_operator_attested_exports.log). Requires "
                        "--export. The caller is accountable for citing a row that genuinely authorizes this file.")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # --operator-attested only means anything WITH --export; it is an error on
    # its own (parser.error exits 2), so an attestation can never be cited
    # without the export it gates.
    if args.operator_attested is not None and not args.export:
        parser.error("--operator-attested requires --export")
    path = Path(args.path)
    if not path.is_file():
        print(f"stage_client_file: no such file: {path}", file=sys.stderr)
        return 2
    structure = extract(path)
    verdict = classify(structure)
    print(verdict.render(structure))  # stdout: values-free, always
    if args.operator_attested is not None:
        # Operator-attested content export (orch-console): on ANY verdict,
        # INCLUDING HOLD, export the ACTUAL content IFF a fail-closed check of
        # the cited operator_messages row passes (recent, from Musa's TG id).
        # Any failure writes NOTHING and exits 2. Every export (CLEAN or HOLD)
        # is appended to the append-only audit log. See the module docstring.
        res = verify_operator_attestation(args.operator_attested)
        if not res.ok:
            print(f"stage_client_file: operator attestation REFUSED — {res.reason}", file=sys.stderr)
            return 2
        out_path = export_clean_file(verdict, structure, args.op_id, path.name)
        append_operator_attested_audit(
            op_id=args.op_id,
            source_basename=path.name,
            op_msg_id=args.operator_attested,
            from_user_id=res.from_user_id,
            verdict=verdict.verdict,
        )
        print(
            f"exported (OPERATOR-ATTESTED content, verdict={verdict.verdict}, "
            f"attested by op_msg {args.operator_attested}): {out_path}"
        )
        return 0 if verdict.verdict == "CLEAN" else 1
    if args.export and args.structure_only:
        # Values-free structural export on ANY verdict (CLEAN or HOLD): only
        # the structural header leaves — column header LABELS / shape, never a
        # row data value — so a lane can build from a HELD file's layout
        # without any data escaping.
        out_path = export_structure_only(verdict, structure, args.op_id, path.name)
        held = ", HELD file" if verdict.verdict == "HOLD" else ""
        print(f"exported (structure only{held} — no values): {out_path}")
    elif verdict.verdict == "CLEAN" and args.export:
        if args.channel and is_sensitive_channel(args.channel):
            out_path = export_structure_only(verdict, structure, args.op_id, path.name)
        else:
            out_path = export_clean_file(verdict, structure, args.op_id, path.name)
        print(f"exported: {out_path}")
    return 0 if verdict.verdict == "CLEAN" else 1


if __name__ == "__main__":
    sys.exit(main())
