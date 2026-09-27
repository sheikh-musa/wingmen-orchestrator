"""launchd StartCalendarInterval jobs that target a specific Abu Dhabi (UAE)
wall-clock time (bus #44286): asks-daily-digest and programme-stall-digest
both target 09:00 Asia/Dubai, but StartCalendarInterval's Hour fires in the
HOST's local time zone, not UTC and not UAE. This Mac Mini's system clock is
Asia/Singapore (UTC+8) -- confirmed live via `date`, and already relied on
elsewhere in this repo (scripts/audit_mac_mini.py's SGT constant).

bus #44286: asks-daily-digest.plist's Hour=5 wrongly assumed a UTC host
clock, so the digest fired at 21:00Z = 01:00 UAE instead of 09:00. This test
converts 09:00 Asia/Dubai into Asia/Singapore via zoneinfo (not hand
arithmetic, so it can't repeat the same kind of manual-conversion mistake)
and fails if either tracked plist's Hour drifts from that conversion again.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ORCH = Path(__file__).resolve().parent.parent
LAUNCHD = ORCH / "launchd"

# This Mac Mini's system clock (confirmed via `date`; matches
# scripts/audit_mac_mini.py's SGT constant). If this host is ever moved to a
# different zone, this is the one line to change -- both assertions below
# recompute from it, they never hardcode an Hour.
HOST_TZ = ZoneInfo("Asia/Singapore")
UAE_TZ = ZoneInfo("Asia/Dubai")


def _expected_host_hour(uae_hour: int) -> int:
    """Converts a target Asia/Dubai wall-clock hour into this host's local
    hour via real zoneinfo arithmetic (neither zone observes DST, but doing
    the conversion this way -- not by hand -- is what makes this test able to
    catch the exact class of mistake bus #44286 was)."""
    anchor = datetime(2026, 1, 5, uae_hour, 0, tzinfo=UAE_TZ)  # arbitrary Monday
    return anchor.astimezone(HOST_TZ).hour


_START_CALENDAR_INTERVAL_RE = re.compile(
    r"<key>StartCalendarInterval</key>\s*<dict>(.*?)</dict>", re.DOTALL
)
_INT_KEY_RE = re.compile(r"<key>(\w+)</key>\s*<integer>(-?\d+)</integer>")


def _load_start_calendar_interval(plist_name: str) -> dict:
    """Extracts the StartCalendarInterval dict's int keys via regex rather
    than plistlib/expat -- several tracked plists in this repo (this one
    included) use a bare '--' inside XML comments elsewhere in the file,
    which real launchd tolerates but expat's strict XML parser rejects as
    malformed. Regexing just the block we need avoids depending on the rest
    of the file being strictly well-formed XML."""
    text = (LAUNCHD / plist_name).read_text()
    block = _START_CALENDAR_INTERVAL_RE.search(text)
    assert block, f"{plist_name}: no StartCalendarInterval dict found"
    return {k: int(v) for k, v in _INT_KEY_RE.findall(block.group(1))}


def test_asks_daily_digest_hour_matches_9am_uae_in_host_tz():
    interval = _load_start_calendar_interval("dev.wingmen.asks-daily-digest.plist")
    expected = _expected_host_hour(9)
    assert interval["Hour"] == expected, (
        f"asks-daily-digest.plist fires at Hour={interval['Hour']} host-local "
        f"({HOST_TZ.key}); 09:00 Asia/Dubai converts to Hour={expected} in "
        f"{HOST_TZ.key} -- this is the bus #44286 regression (fired at "
        "01:00 UAE instead of 09:00)"
    )


def test_programme_stall_digest_hour_matches_9am_uae_in_host_tz():
    interval = _load_start_calendar_interval("dev.wingmen.programme-stall-digest.plist")
    expected = _expected_host_hour(9)
    assert interval["Hour"] == expected, (
        f"programme-stall-digest.plist fires at Hour={interval['Hour']} "
        f"host-local ({HOST_TZ.key}); 09:00 Asia/Dubai converts to "
        f"Hour={expected} in {HOST_TZ.key} (bus #44286 audit item 3)"
    )


def test_programme_stall_digest_still_fires_monday():
    """Weekday is not a TZ-dependent field here, but a Hour fix that silently
    dropped Weekday would defeat the whole 'Monday digest' intent."""
    interval = _load_start_calendar_interval("dev.wingmen.programme-stall-digest.plist")
    assert interval["Weekday"] == 1
