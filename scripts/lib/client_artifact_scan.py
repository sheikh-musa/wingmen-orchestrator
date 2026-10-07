"""client_artifact_scan.py — output-tree scanner for files bound for a client.

Refuses a client-bound artifact whose RENDERED content (not the generator
source) carries a JS-serialization placeholder, fleet-internal vocabulary, or
a stale PROPOSED label on a doc that claims to be applied.

WHY (fable audit 2026-10-06 fork E, reports/fable-audit-substrate-20261006/
E-client-lanes-quality.md §1):
  D1 — a client-facing workbook shipped with "undefinedp" x4 in its
       Period-Accounting line, present in BOTH the C2 and C3 Overview
       workbooks, and it passed the console's own C2 verify-render too
       (#54400, #54497, #54403). The lane's own gate was a text-layer
       assert, but it ran on the fact/source layer, not the rendered file.
  D2 — a separate C3 render wrote the fleet bus id `op#26543` into all 4
       client docs, because the mechanical vocab scan covered `src/` only,
       not the rendered reports tree (#54497 self-catch).

Both root causes are the same: the check ran on the thing that MADE the
artifact, not on the artifact itself. This module scans the artifact that
is actually about to leave (xlsx sharedStrings via cell values, docx
document.xml via paragraph/table text, PDF page text, or raw text for
plain-text formats) and refuses to let it ship.

Wire `assert_clean()` into every path that sends/uploads a file to a
client — see scripts/lib/client_artifact_scan.sh for the shell wrapper
used by tg_send_file.sh, reviewer_send_file.sh and
irsyad_support_send_file.sh.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

# D1: JS-serialization placeholders that leak when a template substitution
# silently fails. Plain substrings on purpose (report's own wording) — a
# token like "undefinedp" (D1's real case) has no trailing word boundary.
PLACEHOLDER_RE = re.compile(r"undefined|null|NaN|\[object")

# D2: fleet-internal vocabulary that has no business in a client-bound file.
FLEET_VOCAB_PATTERNS: dict[str, re.Pattern] = {
    "op-ref": re.compile(r"op#\d+"),
    # a bare 5-digit bus/message id, e.g. "#54659" — NOT preceded by "op"
    # (that's op-ref's job) and not itself part of a longer digit run.
    "bare-bus-id": re.compile(r"(?<!op)#\d{5}\b"),
    "cc-agent": re.compile(r"\bcc-[a-z]+"),
    "cai-ref": re.compile(r"CAI-"),
    "orch-console-or-nazim": re.compile(r"orch-console|Nazim"),
    "internal-term": re.compile(r"wet-proof|\bsilo\b|\blane\b|bus row"),
}

# Synthetic (no D-case in E §1 — see SYNTHESIS.md item 4 / E §106): a doc
# that labels itself "applied" but still carries a literal stale "PROPOSED"
# marker somewhere in its own text.
STALE_PROPOSED_RE = re.compile(r"\bPROPOSED\b")
APPLIED_LABEL_RE = re.compile(r"(?i)\bapplied\b")

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".html", ".htm", ".yaml", ".yml"}


@dataclass(frozen=True)
class Finding:
    category: str  # "placeholder" | "fleet-vocab:<name>" | "stale-proposed"
    excerpt: str
    source: str  # where in the artifact, e.g. "Sheet1!B4", "page 2", "paragraph 3"


class ClientArtifactViolation(Exception):
    """Raised by assert_clean() when a client-bound artifact fails the scan."""

    def __init__(self, path: Path, findings: list[Finding]):
        self.path = path
        self.findings = findings
        lines = "\n".join(f"  - [{f.category}] {f.source}: {f.excerpt!r}" for f in findings)
        super().__init__(f"{path} -- {len(findings)} violation(s):\n{lines}")


def _excerpt(text: str, m: "re.Match[str]", pad: int = 24) -> str:
    start = max(0, m.start() - pad)
    end = min(len(text), m.end() + pad)
    return text[start:end].replace("\n", " ")


def scan_text(text: str, source: str = "") -> list[Finding]:
    """Scan one chunk of RENDERED text; returns every finding (not just the first)."""
    findings: list[Finding] = []
    if not text:
        return findings
    m = PLACEHOLDER_RE.search(text)
    if m:
        findings.append(Finding("placeholder", _excerpt(text, m), source))
    for name, pattern in FLEET_VOCAB_PATTERNS.items():
        m = pattern.search(text)
        if m:
            findings.append(Finding(f"fleet-vocab:{name}", _excerpt(text, m), source))
    m = STALE_PROPOSED_RE.search(text)
    if m and APPLIED_LABEL_RE.search(text):
        findings.append(Finding("stale-proposed", _excerpt(text, m), source))
    return findings


def _scan_xlsx(path: Path) -> list[Finding]:
    import openpyxl

    findings: list[Finding] = []
    wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    try:
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str) and cell.value:
                        findings.extend(scan_text(cell.value, source=f"{ws.title}!{cell.coordinate}"))
    finally:
        wb.close()
    return findings


def _scan_docx(path: Path) -> list[Finding]:
    import docx

    findings: list[Finding] = []
    d = docx.Document(str(path))
    for i, p in enumerate(d.paragraphs):
        if p.text:
            findings.extend(scan_text(p.text, source=f"paragraph {i + 1}"))
    for ti, t in enumerate(d.tables):
        for ri, row in enumerate(t.rows):
            for ci, cell in enumerate(row.cells):
                if cell.text:
                    findings.extend(scan_text(cell.text, source=f"table{ti + 1} row{ri + 1} col{ci + 1}"))
    return findings


def _scan_pdf(path: Path) -> list[Finding]:
    import pdfplumber

    findings: list[Finding] = []
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages):
            page_text = page.extract_text() or ""
            if page_text:
                findings.extend(scan_text(page_text, source=f"page {i + 1}"))
    return findings


def _scan_plain_text(path: Path) -> list[Finding]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return scan_text(text, source=path.name)


def scan_file(path: Path) -> list[Finding]:
    """Scan a single rendered artifact. Dispatches by extension to the
    RENDERED content, not the generator source. Unknown/binary extensions
    (png, jpg, zip, ...) carry no extractable text layer and are not scanned.
    """
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        return _scan_xlsx(path)
    if suffix == ".docx":
        return _scan_docx(path)
    if suffix == ".pdf":
        return _scan_pdf(path)
    if suffix in TEXT_EXTENSIONS:
        return _scan_plain_text(path)
    return []


def assert_clean(path: Path) -> None:
    """Refuse (raise ClientArtifactViolation) if `path` fails the scan.

    Call this immediately before any client-bound send/upload.
    """
    findings = scan_file(path)
    if findings:
        raise ClientArtifactViolation(path, findings)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)

    if not args.path.exists():
        print(f"ERROR: {args.path} not found", file=sys.stderr)
        return 2
    try:
        assert_clean(args.path)
    except ClientArtifactViolation as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
