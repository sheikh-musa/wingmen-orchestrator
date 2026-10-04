#!/usr/bin/env python3
"""stage_client_file.py — deterministic, no-LLM structural + PII-shape inspection
of an inbound client file (xlsx/csv/docx/pdf), for orch-console to run BEFORE
handing a file to a lane (orch-console bus #51060, Musa op#25437-25440: a lane
must never tell a client "I can't open your file" — the file is staged here
first, by a human, not by a lane reading raw cell values).

Prints ONLY structure (sheets/dims/image count) + PII-shape COUNTS (never a
matched value) + a CLEAN/HOLD verdict + the reason(s). On CLEAN with --export,
writes the RENDERED REPORT (never raw cell values — this is a safe copy of the
*inspection*, not of the file's content) as a markdown file under
reports/client-file-staging/<op_id>/ and prints that path. On HOLD, writes
nothing.

Same safety invariants as scripts/lib/pii_safe_file_inspector.py: fail-closed
(any PII-shape hit or person-record signal -> HOLD, never guessed clean),
never print a matched value, a parse error carries no file content.
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


def scan_pii_counts(text: str) -> PiiCounts:
    """Count-only PII-shape scan of *text*. NEVER returns or prints a matched
    value — only how many of each shape were found. Checked most-specific
    first (nric/emirates/phone/email); a long_number match that overlaps one
    of those is NOT double-counted — it is the same underlying value, already
    counted once under its more specific category."""
    nric_spans = _spans(_NRIC_FIN_BC_RE, text)
    emirates_spans = _spans(_EMIRATES_ID_RE, text)
    phone_spans = _spans(_PHONE_RE, text)
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


@dataclass
class FileStructure:
    """Format-independent intermediate representation an extractor returns —
    the classifier below is format-agnostic and operates only on this."""
    kind: str  # "xlsx" | "csv" | "docx" | "pdf"
    sheets: list[SheetStructure] = field(default_factory=list)
    image_count: int = 0
    texts: list[str] = field(default_factory=list)  # all extracted text, for PII scanning


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
        sheets=[SheetStructure(name=path.name, rows=len(data_rows), cols=len(header), header=header)],
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
        sheets.append(SheetStructure(name=ws.title, rows=len(data_rows), cols=(ws.max_column or 0), header=header))
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
        sheets.append(SheetStructure(name=f"table{i + 1}", rows=len(data_rows), cols=len(header), header=header))
        for r in rows:
            texts.append(" ".join(r))
    image_count = len(d.inline_shapes)
    if not sheets:
        # No tables -- still report a pseudo-sheet so rows/cols aren't silently
        # absent from the structure summary.
        sheets.append(SheetStructure(name="(document body)", rows=len(d.paragraphs), cols=1, header=[]))
    return FileStructure(kind="docx", sheets=sheets, image_count=image_count, texts=texts)


def extract_pdf(path: Path) -> FileStructure:
    import pdfplumber

    sheets: list[SheetStructure] = []
    texts: list[str] = []
    image_count = 0
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages):
            texts.append(page.extract_text() or "")
            image_count += len(page.images)
            for ti, table in enumerate(page.extract_tables() or []):
                header = [str(h or "") for h in (table[0] if table else [])]
                data_rows = table[1:] if table else []
                sheets.append(SheetStructure(
                    name=f"page{i + 1}-table{ti + 1}", rows=len(data_rows), cols=len(header), header=header,
                ))
        if not sheets:
            sheets.append(SheetStructure(name="(pdf text)", rows=len(pdf.pages), cols=1, header=[]))
    return FileStructure(kind="pdf", sheets=sheets, image_count=image_count, texts=texts)


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


def export_report(report_text: str, op_id: str, original_name: str) -> Path:
    out_dir = Path("reports/client-file-staging") / sanitize_op_id(op_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{Path(original_name).stem}.md"
    out_path.write_text(report_text + "\n", encoding="utf-8")
    return out_path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("path", help="path to the client file (.xlsx/.csv/.docx/.pdf)")
    p.add_argument("op_id", help="op id this file is staged under (free-form string)")
    p.add_argument("--export", action="store_true", help="on CLEAN, write a safe markdown report copy")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.path)
    if not path.is_file():
        print(f"stage_client_file: no such file: {path}", file=sys.stderr)
        return 2
    structure = extract(path)
    verdict = classify(structure)
    report = verdict.render(structure)
    print(report)
    if verdict.verdict == "CLEAN" and args.export:
        out_path = export_report(report, args.op_id, path.name)
        print(f"exported: {out_path}")
    return 0 if verdict.verdict == "CLEAN" else 1


if __name__ == "__main__":
    sys.exit(main())
