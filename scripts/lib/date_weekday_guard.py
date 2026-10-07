"""date_weekday_guard.py — refuse outbound text that pairs a weekday with the wrong date.

WHY (2026-10-07, approved orch-console bus #57970): operator/client messages that week said
"Thursday 9 October", "Mon 13 Oct", "Friday 17 October" — in 2026 those dates are a Friday,
a Tuesday and a Saturday. One reached a client. A model writing a date "from feel" gets the
weekday wrong; this enforces it in code at the send chokepoints instead of in memory.

Recognised shapes (English only, case-insensitive, full or 3-letter names, "Sept",
optional ordinal suffix, optional trailing year 19xx/20xx):
    <weekday> <day> <month>          Thursday 9 October, Fri 9th Oct 2026
    <weekday>, <day> <month>         Friday, 9th October 2026
    <weekday> the <day> of <month>   Friday the 9th of October
    <day> <month> (<weekday>)        9 October (Friday)
    <weekday> <month> <day>          Friday October 9, Fri, Oct 9th, 2026
Dates without a weekday, non-Latin text (Arabic etc.) and impossible dates are ignored.

Year resolution when absent: the next occurrence on/after today (Asia/Dubai) — EXCEPT a
date in the last RECENT_PAST_DAYS days, which resolves to that just-gone date (a status
update saying "we shipped Monday 5 October" on the 7th means this year's 5th, not next
year's).

API:  find_mismatches(text, today=None) -> list[Finding]
CLI:  python3 -m scripts.lib.date_weekday_guard [--warn-only] [--text TEXT]   (else stdin)
      exit 0 clean / 1 mismatch (REFUSED) / 3 guard crashed. --warn-only: always 0.
      DATE_WEEKDAY_GUARD_TODAY=YYYY-MM-DD overrides "today" (tests only).
Shell: scripts/lib/date_weekday_guard.sh  (_date_weekday_guard "$TEXT" || exit 6)
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta

TZ_NAME = "Asia/Dubai"
RECENT_PAST_DAYS = 14

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_WD_ALIASES = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tues": 1, "tue": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thurs": 3, "thur": 3, "thu": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")
_MON_ALIASES = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}


def _alt(names):
    return "|".join(sorted(names, key=len, reverse=True))


_WD = rf"(?P<wd>{_alt(_WD_ALIASES)})\.?"
_MON = rf"(?P<mon>{_alt(_MON_ALIASES)})\.?"
# [0-9] not \d: \d matches Arabic-Indic digits, which we deliberately ignore.
_DAY = r"(?P<day>[0-9]{1,2})(?:st|nd|rd|th)?"
_YEAR = r"(?:,?\s+(?P<year>(?:19|20)[0-9]{2}))?"
_END = r"(?![A-Za-z0-9])"

_PATTERNS = [
    # Thursday 9 October / Friday, 9th October 2026 / Friday the 9th of October
    re.compile(rf"\b{_WD},?\s+(?:the\s+)?{_DAY}\s+(?:of\s+)?{_MON}{_YEAR}{_END}", re.I),
    # 9 October (Friday)
    re.compile(rf"\b{_DAY}\s+(?:of\s+)?{_MON}{_YEAR}\s*\(\s*{_WD}\s*\)", re.I),
    # Friday October 9 / Fri, Oct 9th, 2026
    re.compile(rf"\b{_WD},?\s+{_MON}\s+{_DAY}{_YEAR}{_END}", re.I),
]


@dataclass(frozen=True)
class Finding:
    matched: str
    claimed_weekday: str
    real_weekday: str
    resolved_date: date

    def message(self) -> str:
        d = self.resolved_date
        return (f"'{self.matched}' — {d.day} {_MONTHS[d.month - 1]} {d.year} "
                f"is a {self.real_weekday}, not a {self.claimed_weekday}")


def _today_dubai() -> date:
    override = os.environ.get("DATE_WEEKDAY_GUARD_TODAY")
    if override:
        return date.fromisoformat(override)
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(TZ_NAME)).date()


def _resolve(day: int, month: int, year: int | None, today: date) -> date | None:
    """Concrete date for day/month(/year), or None if impossible."""
    if year is not None:
        try:
            return date(year, month, day)
        except ValueError:
            return None
    try:
        this_year = date(today.year, month, day)
        if timedelta(0) < today - this_year <= timedelta(days=RECENT_PAST_DAYS):
            return this_year
    except ValueError:
        pass
    # Next occurrence on/after today; 29 Feb may be up to 8 years out.
    for y in range(today.year, today.year + 9):
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        if d >= today:
            return d
    return None


def find_mismatches(text: str, today: date | None = None) -> list[Finding]:
    """Every weekday+date pair in `text` whose weekday is wrong, in text order."""
    if not text:
        return []
    today = today or _today_dubai()
    hits = []
    for pat in _PATTERNS:
        for m in pat.finditer(text):
            hits.append(m)
    hits.sort(key=lambda m: (m.start(), -(m.end() - m.start())))
    findings: list[Finding] = []
    taken_until = -1
    for m in hits:
        if m.start() < taken_until:      # overlaps a pair already evaluated
            continue
        taken_until = m.end()
        wd = _WD_ALIASES[m.group("wd").lower()]
        mon = _MON_ALIASES[m.group("mon").lower()]
        year = int(m.group("year")) if m.group("year") else None
        resolved = _resolve(int(m.group("day")), mon, year, today)
        if resolved is None or resolved.weekday() == wd:
            continue
        findings.append(Finding(m.group(0).strip(), _WEEKDAYS[wd],
                                _WEEKDAYS[resolved.weekday()], resolved))
    return findings


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Refuse text pairing a weekday with the wrong date.")
    p.add_argument("--warn-only", action="store_true", help="print warnings but exit 0")
    p.add_argument("--text", default=None, help="message text (default: read stdin)")
    args = p.parse_args(argv)
    try:
        text = args.text if args.text is not None else sys.stdin.read()
        findings = find_mismatches(text)
    except Exception as e:  # noqa: BLE001 — a guard crash must be distinguishable from a refusal
        print(f"date_weekday_guard: CRASHED ({type(e).__name__}: {e})", file=sys.stderr)
        return 0 if args.warn_only else 3
    if not findings:
        return 0
    label = "WARNING" if args.warn_only else "REFUSED"
    for f in findings:
        print(f"{label}: {f.message()}", file=sys.stderr)
    if args.warn_only:
        return 0
    print("Weekday/date mismatch — fix the weekday or the date, then resend. Nothing was sent.",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
