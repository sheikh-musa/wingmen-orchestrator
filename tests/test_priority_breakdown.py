"""Pure-logic tests for scripts/lib/priority_breakdown.py's breakdown_line()."""
import importlib.util
import os
import sys

_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "lib", "priority_breakdown.py")
_spec = importlib.util.spec_from_file_location("priority_breakdown", _PATH)
priority_breakdown = importlib.util.module_from_spec(_spec)
sys.modules["priority_breakdown"] = priority_breakdown
_spec.loader.exec_module(priority_breakdown)


def test_breakdown_line_from_iterable_sorts_p0_first():
    assert priority_breakdown.breakdown_line(["P3", "P1", "P3", "P3"]) == "P1:1 P3:3"


def test_breakdown_line_from_dict():
    assert priority_breakdown.breakdown_line({"P2": 2, "P0": 1}) == "P0:1 P2:2"


def test_breakdown_line_empty():
    assert priority_breakdown.breakdown_line([]) == ""


def test_breakdown_line_unknown_priority_sorts_last():
    assert priority_breakdown.breakdown_line(["P1", "WEIRD"]) == "P1:1 WEIRD:1"
