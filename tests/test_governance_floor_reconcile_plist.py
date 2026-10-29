"""Durability + safety guard for the governance-floor-reconcile launchd config
(console bus #80912/#80915): the scheduled job runs --record ONLY (writes
governance_floor_audit rows via direct psycopg) and must never also run
gen_governance_floors.py -- that script rewrites a tracked file on the live
checkout's branch, and a cron auto-committing/pushing it is exactly what the
no-direct-push-to-trunk rule exists to prevent (#80912 ruled this out
explicitly: option (a), a carve-out for a scheduled job to push, was refused).

Cadence is every 6h (StartInterval=21600s) so a stale-at-2x-interval threshold
(12h) stays well under the 72h floor the reconciler is policing -- console
caught (bus #80915) that a daily cadence with 48h staleness would leave the
check blind for two-thirds of that window.
"""
import plistlib
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_PLIST = _REPO / "launchd" / "dev.wingmen.governance-floor-reconcile.plist"


def _load():
    with _PLIST.open("rb") as f:
        return plistlib.load(f)


def test_plist_runs_reconcile_record_and_nothing_else():
    args = _load()["ProgramArguments"]
    assert any(a.endswith("governance_floor_reconcile.py") for a in args), args
    assert "--record" in args, args
    assert not any("gen_governance_floors" in a for a in args), (
        "the plist must never also regenerate docs/governance-floors.md -- "
        "that would write to the live checkout's tracked file from a cron"
    )


def test_plist_label_matches_filename():
    assert _load()["Label"] == _PLIST.stem


def test_plist_cadence_is_6h():
    # console bus #80915: 6h cadence so STALE (2x interval) trips at 12h, not 48h
    assert _load()["StartInterval"] == 21600


def test_plist_runs_at_load_too():
    assert _load()["RunAtLoad"] is True
