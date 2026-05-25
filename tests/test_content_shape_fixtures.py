"""Fixture-driven integration tests per CAI-RESP-164 R1.

Positive case (probe_max_throttle) must produce 3-of-3 match → hard_kill.
Negative case (cc-scholar incident) must produce <3-of-3 → monitored.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.content_shape_signals import (
    signal_a_median_size,
    signal_b_cadence_band,
    signal_c_identical_prompts,
)
from nervous_system.long_caller_watchdog import ContentShape, decide_kill

PROBE_DIR = Path(__file__).parent / "fixtures" / "probe_max_throttle"
SCHOLAR_DIR = Path(__file__).parent / "fixtures" / "cc_scholar_2026_05_19_2244"

# Git doesn't preserve file mtimes across checkouts — clobbered to checkout-time.
# signal_b's span calculation needs deterministic mtimes, so restore them from
# the session-NN filename index before any fixture-driven test runs. Mirrors
# what gen.py set when the probe fixture was authored.
#
# The cc-scholar fixture doesn't need this restoration: its desired behavior is
# match=False on signal_b (whether due to mtimes-clobbered span~0s or genuinely
# short span), so the negative test passes either way.
_PROBE_BASE_MTIME = 1700000000.0
_PROBE_CADENCE = 300.0
_SESSION_INDEX_RE = re.compile(r"session-(\d+)\.jsonl$")


@pytest.fixture(scope="module", autouse=True)
def _restore_probe_mtimes():
    if PROBE_DIR.exists():
        for p in PROBE_DIR.glob("session-*.jsonl"):
            m = _SESSION_INDEX_RE.search(p.name)
            if not m:
                continue
            idx = int(m.group(1))
            mt = _PROBE_BASE_MTIME + idx * _PROBE_CADENCE
            os.utime(p, (mt, mt))
    yield


def _jsonls(d: Path) -> list[Path]:
    return sorted(
        [p for p in d.iterdir() if p.is_file() and p.name.endswith(".jsonl") and not p.name.startswith(".")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def _shape(d: Path) -> ContentShape:
    paths = _jsonls(d)
    return ContentShape(
        signal_a=signal_a_median_size(paths),
        signal_b=signal_b_cadence_band(paths),
        signal_c=signal_c_identical_prompts(paths),
    )


def _has_fixtures(d: Path) -> bool:
    return d.exists() and len(list(d.glob("session-*.jsonl"))) >= 10


@pytest.mark.skipif(not _has_fixtures(PROBE_DIR), reason="probe fixture not generated")
def test_probe_fixture_produces_3_of_3_match():
    shape = _shape(PROBE_DIR)
    assert shape.signal_a.match is True, f"signal_a expected burn-pattern: {shape.signal_a}"
    assert shape.signal_b.match is True, f"signal_b expected burn-pattern: {shape.signal_b}"
    assert shape.signal_c.match is True, f"signal_c expected burn-pattern: {shape.signal_c}"
    assert shape.all_match is True


@pytest.mark.skipif(not _has_fixtures(PROBE_DIR), reason="probe fixture not generated")
def test_probe_fixture_triggers_hard_kill():
    d = decide_kill(
        caller_name="cc-test-runaway-probe",
        sessions_24h=300, cadence_seconds=300,
        registered=False, parent_pid=99999,
        content_shape=_shape(PROBE_DIR),
    )
    assert d.action == "hard_kill"
    assert d.reason == "R1_AMENDED_unregistered_3of3_content_shape"


@pytest.mark.skipif(not _has_fixtures(SCHOLAR_DIR), reason="cc-scholar fixture not generated")
def test_scholar_fixture_not_3_of_3():
    shape = _shape(SCHOLAR_DIR)
    assert shape.all_match is False, "cc-scholar must NOT match 3-of-3"


@pytest.mark.skipif(not _has_fixtures(SCHOLAR_DIR), reason="cc-scholar fixture not generated")
def test_scholar_fixture_does_not_hard_kill():
    d = decide_kill(
        caller_name="cc-scholar",
        sessions_24h=351, cadence_seconds=11,
        registered=False, parent_pid=24810,
        content_shape=_shape(SCHOLAR_DIR),
    )
    assert d.action != "hard_kill"
