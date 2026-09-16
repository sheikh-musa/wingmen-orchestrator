#!/usr/bin/env python3
"""pii_safe_file_inspector — read a CLIENT data file's STRUCTURE + a masked preview
WITHOUT ever emitting raw PII (donor names, emails, phones, IDs).

Why this exists (console #39543/#39556; cai CAI-RESP-1422/1424; cc-storefront
decision_audits #354/#356): agents must be able to inspect a client file to build
and debug (columns, types, a date FORMAT) — but opening the raw file mints an
irreversible PII transcript into agent context/logs (CAI-1034). This tool reads
the file and returns ONLY structure + a FAIL-CLOSED-masked preview, so the read
never becomes a PII incident. It is the "look at the file without the data" path.

DESIGN (must hold — the whole point is airtightness):
  1. FAIL-CLOSED ALLOWLIST masking (CAI-1424 / cc-storefront #354): every column
     is MASKED BY DEFAULT. A column is unmasked ONLY if (a) it is explicitly
     allowlisted, OR (b) its values are a structurally-non-PII type (a real number,
     a date, a boolean, or empty) AND a value-pattern scan finds no PII signature.
     A free-text/"notes" column is masked even if it *usually* looks harmless — a
     name in prose has no regex signature, so text is never unmasked by scan alone.
  2. NO RAW PII IN OUTPUT (cc-storefront #356 leak-vector 1+2): a masked column
     emits only its header + inferred type + "***" — never a value. Unmasked
     columns emit a date FORMAT / numeric range / a small non-PII sample.
  3. ERRORS CARRY NO VALUES (leak-vector 1+3): a parse problem is reported as
     (row_index, error_class) — never the offending cell; every read is wrapped so
     a library traceback embedding a value can never reach the caller/logs.
  4. Column HEADERS are schema, not PII, and are always shown (that is what a
     builder needs — e.g. to see a 'created (metadata)' or 'Value Date' column).

Gate: the DESIGN goes to cai before this is run on a REAL client file (cai's
condition). Synthetic wet-prove first (tests/test_pii_safe_file_inspector.py).

Pure stdlib (csv, re) — deliberately NOT pandas: pandas exceptions routinely embed
the offending cell value in the traceback (leak-vector 3), and its dtype inference
would pull values into memory/String reprs we then have to scrub. csv keeps every
value a plain str we control.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from typing import Optional

# ── PII value-signatures (the ADDITIONAL scan layer, never the sole gate) ──────
# An email: local@domain.tld.
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
# A phone / long identifier: 7+ digits once separators are stripped (catches
# phone numbers, NRIC/FIN-like ids, account numbers hiding in a "numeric" column).
_LONG_DIGITS_RE = re.compile(r"\d[\d\s\-()+]{6,}")
# A real number (money/amount/count): optional sign, thousands, decimals. A SHORT
# integer (<=6 digits, e.g. a year or a small count) is a number; a long digit run
# is caught by _LONG_DIGITS_RE above and treated as a possible id.
_NUMBER_RE = re.compile(r"^[-+]?(\d{1,3}(,\d{3})*|\d{1,6})(\.\d+)?$")
_BOOL_VALS = {"true", "false", "yes", "no", "y", "n", "0", "1"}

# Date shapes → a human-readable format label (dates are not PII; the format is the
# thing a builder needs). Order matters: most specific first.
_DATE_SHAPES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?"), "ISO YYYY-MM-DD + time"),
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "ISO YYYY-MM-DD"),
    (re.compile(r"^\d{8}$"), "YYYYMMDD"),
    (re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{4}\s+\d{1,2}:\d{2}(:\d{2})?$"), "n/n/YYYY + time (locale, ambiguous order)"),
    (re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{4}$"), "n/n/YYYY (locale, ambiguous order)"),
    (re.compile(r"^\d{10,13}$"), "unix-epoch (seconds/millis)"),
]


# ── VALUE-SHAPE profile (console, Musa op#20591) ──────────────────────────────
# For a MASKED text column we still need to know what KIND of values it holds —
# that is where every client-file quirk of 2026-09-15 lived (honorific
# abbreviations, "Hamba Allah" anonymous markers, apostrophes, mojibake). The
# shape is AGGREGATES ONLY: histograms and counts computed from values, never a
# value, never a substring, never a sample. It cannot reconstruct a name.
_ANON_MARKERS = {"hamba allah", "hambaallah", "anonymous", "anon", "tanpa nama", "no name"}
_MOJIBAKE_RE = re.compile(r"â€|Ã.|Â")
_TITLE_TOKEN_RE = re.compile(r"^[A-Z][a-z'’\-]*$")


def _shape_key(v: str) -> str:
    return re.sub(r"[\s_]+", " ", re.sub(r"[-_.,/]+", " ", v.lower())).strip()


def value_shape(values: list[str]) -> dict:
    """Aggregate-only profile of a list of cell values. Emits counts/labels only."""
    words = {"1": 0, "2": 0, "3": 0, "4+": 0}
    case = {"upper": 0, "lower": 0, "title": 0, "mixed": 0}
    punct = {"apostrophe": 0, "hyphen": 0, "period": 0, "comma": 0, "slash": 0, "parens": 0,
             "digits": 0, "non_ascii": 0, "mojibake": 0}
    markers = {"anonymous": 0}
    email_shaped = 0
    digits_only = 0
    distinct: set[int] = set()
    max_len = 0
    n = 0
    for raw in values:
        v = (raw or "").strip()
        if v == "":
            continue
        n += 1
        distinct.add(hash(v))          # a hash of the value is not the value; only its COUNT is emitted
        max_len = max(max_len, len(v))
        toks = v.split()
        words["1" if len(toks) == 1 else "2" if len(toks) == 2 else "3" if len(toks) == 3 else "4+"] += 1
        if v.isupper():
            case["upper"] += 1
        elif v.islower():
            case["lower"] += 1
        elif all(_TITLE_TOKEN_RE.match(t) or not t[:1].isalpha() for t in toks):
            case["title"] += 1
        else:
            case["mixed"] += 1
        if "'" in v or "’" in v or "‘" in v:
            punct["apostrophe"] += 1
        if "-" in v:
            punct["hyphen"] += 1
        if "." in v:
            punct["period"] += 1
        if "," in v:
            punct["comma"] += 1
        if "/" in v:
            punct["slash"] += 1
        if "(" in v or ")" in v:
            punct["parens"] += 1
        if any(ch.isdigit() for ch in v):
            punct["digits"] += 1
        if any(ord(ch) > 127 for ch in v):
            punct["non_ascii"] += 1
        if _MOJIBAKE_RE.search(v):
            punct["mojibake"] += 1
        if _shape_key(v) in _ANON_MARKERS:
            markers["anonymous"] += 1
        if _EMAIL_RE.search(v):
            email_shaped += 1
        if v.replace(" ", "").isdigit():
            digits_only += 1
    return {
        "n": n, "distinct": len(distinct), "max_len": max_len, "words": words, "case": case,
        "punct": {k: c for k, c in punct.items()}, "markers": markers,
        "email_shaped": email_shaped, "digits_only": digits_only,
    }


def _render_shape(sh: dict) -> str:
    w = ",".join(f"{k}:{c}" for k, c in sh["words"].items() if c)
    cs = ",".join(f"{k}:{c}" for k, c in sh["case"].items() if c)
    pc = ",".join(f"{k}:{c}" for k, c in sh["punct"].items() if c)
    return (f"shape: distinct={sh['distinct']} max_len={sh['max_len']} words{{{w}}} case{{{cs}}} "
            f"punct{{{pc}}} markers{{anonymous:{sh['markers']['anonymous']}}} "
            f"email_shaped={sh['email_shaped']} digits_only={sh['digits_only']}")


@dataclass
class ColumnReport:
    name: str
    inferred_type: str          # "date" | "number" | "boolean" | "empty" | "text"
    masked: bool
    detail: str                 # date FORMAT / numeric range / "***" (masked) — never a raw PII value
    non_empty: int
    shape: Optional[dict] = None   # MASKED text columns only: aggregate value-shape, never a value


@dataclass
class InspectReport:
    row_count: int
    columns: list[ColumnReport] = field(default_factory=list)
    parse_notes: list[str] = field(default_factory=list)   # (row_index, error_class) only — never a value

    def render(self) -> str:
        lines = [f"rows: {self.row_count}", "columns:"]
        for c in self.columns:
            tag = "MASKED" if c.masked else c.detail
            lines.append(f"  - {c.name} [{c.inferred_type}] ({c.non_empty} non-empty): {tag}")
            if c.masked and c.shape:
                lines.append(f"      {_render_shape(c.shape)}")
        if self.parse_notes:
            lines.append("parse_notes (row, class — no values):")
            lines.extend(f"  - {n}" for n in self.parse_notes)
        return "\n".join(lines)


def _classify_value(v: str) -> str:
    s = v.strip()
    if s == "":
        return "empty"
    if _EMAIL_RE.search(s):
        return "pii_email"
    for rx, _label in _DATE_SHAPES:
        if rx.match(s):
            return "date"
    if _LONG_DIGITS_RE.fullmatch(s) or _LONG_DIGITS_RE.fullmatch(s.replace(" ", "")):
        # a long digit run that is NOT a plain small number → possible phone/id
        if not _NUMBER_RE.match(s):
            return "pii_longnum"
    if _NUMBER_RE.match(s):
        return "number"
    if s.lower() in _BOOL_VALS:
        return "boolean"
    return "text"


def _date_format_label(sample: str) -> str:
    s = sample.strip()
    for rx, label in _DATE_SHAPES:
        if rx.match(s):
            return label
    return "date (unrecognised shape)"


def inspect_rows(header: list[str], rows: list[list[str]],
                 allowlist: Optional[set[str]] = None) -> InspectReport:
    """PURE core (no I/O): classify + fail-closed-mask each column from parsed rows.
    `allowlist` = column names the caller has PROVEN PII-free (they get unmasked
    detail). Everything else is masked unless it is structurally non-PII AND scans
    clean. NEVER returns a raw value for a masked column."""
    allow = {a.strip().lower() for a in (allowlist or set())}
    ncols = len(header)
    report = InspectReport(row_count=len(rows))
    for ci in range(ncols):
        name = header[ci] if ci < len(header) else f"col{ci}"
        classes: dict[str, int] = {}
        date_formats: dict[str, int] = {}
        col_values: list[str] = []
        non_empty = 0
        for ri, r in enumerate(rows):
            try:
                v = r[ci] if ci < len(r) else ""
                cls = _classify_value(v)
            except Exception:  # noqa: BLE001 — never let a value ride out on a traceback
                report.parse_notes.append(f"(row {ri}, classify_error)")
                continue
            if cls != "empty":
                non_empty += 1
                col_values.append(v)
            classes[cls] = classes.get(cls, 0) + 1
            if cls == "date":
                lbl = _date_format_label(v)
                date_formats[lbl] = date_formats.get(lbl, 0) + 1
        # Decide the column's dominant type + masking.
        has_pii = classes.get("pii_email", 0) or classes.get("pii_longnum", 0)
        non_empty_classes = {k: v for k, v in classes.items() if k != "empty"}
        allowlisted = name.strip().lower() in allow
        if not non_empty_classes:
            report.columns.append(ColumnReport(name, "empty", masked=False, detail="(all empty)", non_empty=0))
            continue
        dominant = max(non_empty_classes, key=non_empty_classes.get)
        # FAIL-CLOSED: any PII signature anywhere in the column → masked (unless the
        # caller explicitly allowlisted it, an override they own).
        if has_pii and not allowlisted:
            report.columns.append(ColumnReport(name, "text", masked=True, detail="***", non_empty=non_empty,
                                               shape=value_shape(col_values)))
            continue
        if dominant == "date":
            mix = ", ".join(f"{k}: {c}" for k, c in sorted(date_formats.items(), key=lambda kv: -kv[1]))
            report.columns.append(ColumnReport(name, "date", masked=False, detail=f"format: {mix}", non_empty=non_empty))
        elif dominant == "number" and not has_pii:
            report.columns.append(ColumnReport(name, "number", masked=False, detail="numeric", non_empty=non_empty))
        elif dominant == "boolean":
            report.columns.append(ColumnReport(name, "boolean", masked=False, detail="boolean", non_empty=non_empty))
        elif allowlisted:
            report.columns.append(ColumnReport(name, dominant, masked=False, detail="(allowlisted)", non_empty=non_empty))
        else:
            # text / anything else / PII-adjacent → MASK by default (with an aggregate shape).
            report.columns.append(ColumnReport(name, "text", masked=True, detail="***", non_empty=non_empty,
                                               shape=value_shape(col_values)))
    return report


def inspect_csv(path: str, allowlist: Optional[set[str]] = None,
                max_rows: int = 5000) -> InspectReport:
    """Read a CSV file and return a fail-closed-masked structure report. Reads at
    most `max_rows` data rows (enough to classify + find the date format). Any read
    error is reduced to (row, class) — the raw content never rides out."""
    header: list[str] = []
    rows: list[list[str]] = []
    notes: list[str] = []
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.reader(fh)
        it = iter(reader)
        i = -1
        # [cai CAI-RESP-1424 gate fix] The ITERATION itself (not just the append)
        # can raise csv.Error (bad quoting, field-size-limit). Wrap each next() so
        # a malformed row degrades to a (row, read_error) note and iteration
        # continues — a library error can never propagate a value out (the tool's
        # whole guarantee). csv.Error messages are structural (line/field), not cell
        # content, but "probably fine" is exactly what this tool exists to not rely on.
        while True:
            i += 1
            try:
                row = next(it)
            except StopIteration:
                break
            except Exception:  # noqa: BLE001 — structural csv error; never a value
                notes.append(f"(row {i}, read_error)")
                continue
            if i == 0:
                header = row
                continue
            if len(rows) >= max_rows:
                break
            rows.append(row)
    rep = inspect_rows(header, rows, allowlist=allowlist)
    rep.parse_notes = notes + rep.parse_notes
    return rep


if __name__ == "__main__":  # pragma: no cover — CLI is gated to cai before real-file use
    import sys
    if len(sys.argv) < 2:
        print("usage: pii_safe_file_inspector.py <file.csv> [allowlist,comma,separated]", file=sys.stderr)
        raise SystemExit(2)
    allow = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else None
    print(inspect_csv(sys.argv[1], allowlist=allow).render())
