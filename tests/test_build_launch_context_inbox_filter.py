"""Tests for the inbox .or_() filter in build_launch_context.py.

bus #51166: cc-shipforge and cc-scholar both booted with the inbox reader
filtering only on their BASE agent id, silently missing mail addressed to
their INSTANCE id (e.g. 'cc-shipforge-1'). Fix: match base, instance, and
broadcast (to_agent IS NULL), never base alone.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.build_launch_context import inbox_or_filter


def test_single_instance_lane_matches_base_only_once():
    # No distinct instance id: the filter must not repeat the same clause twice.
    f = inbox_or_filter("cc-ihsanos", None)
    assert f == "to_agent.eq.cc-ihsanos,to_agent.is.null"


def test_single_instance_lane_instance_equals_base():
    f = inbox_or_filter("cc-ihsanos", "cc-ihsanos")
    assert f == "to_agent.eq.cc-ihsanos,to_agent.is.null"


def test_multi_instance_lane_matches_both_base_and_instance():
    f = inbox_or_filter("cc-shipforge", "cc-shipforge-1")
    assert "to_agent.eq.cc-shipforge," in f
    assert "to_agent.eq.cc-shipforge-1" in f
    assert f.endswith("to_agent.is.null")
    # exact bug-regression shape
    assert f == "to_agent.eq.cc-shipforge,to_agent.eq.cc-shipforge-1,to_agent.is.null"


def test_broadcast_clause_always_present():
    for f in (inbox_or_filter("cc-x"), inbox_or_filter("cc-x", "cc-x-2")):
        assert "to_agent.is.null" in f
