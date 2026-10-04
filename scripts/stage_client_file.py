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
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
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


_EXTRACTORS = {
    ".csv": extract_csv,
    ".xlsx": extract_xlsx,
    ".docx": extract_docx,
    ".pdf": extract_pdf,
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("path", help="path to the client file (.xlsx/.csv/.docx/.pdf)")
    p.add_argument("op_id", help="op id this file is staged under (free-form string)")
    p.add_argument("--export", action="store_true",
                   help="on CLEAN, write the actual readable content to reports/client-file-staging/<op_id>/; "
                        "no-op on HOLD")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.path)
    if not path.is_file():
        print(f"stage_client_file: no such file: {path}", file=sys.stderr)
        return 2
    structure = extract(path)
    verdict = classify(structure)
    print(verdict.render(structure))  # stdout: values-free, always
    if verdict.verdict == "CLEAN" and args.export:
        out_path = export_clean_file(verdict, structure, args.op_id, path.name)
        print(f"exported: {out_path}")
    return 0 if verdict.verdict == "CLEAN" else 1


if __name__ == "__main__":
    sys.exit(main())
