"""Pure tests for the SHARED inbox id-set helper scripts/lib/inbox_ids.py — the one
source of truth for the base+instance inbox read (bus #51166 / #58271 / #59675)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.lib.inbox_ids import inbox_ids


def test_base_and_instance_when_distinct():
    assert inbox_ids("cc-cosem-adcda", "cc-cosem-adcda-2") == [
        "cc-cosem-adcda", "cc-cosem-adcda-2"]


def test_singleton_instance_equals_base_is_base_only():
    assert inbox_ids("cc-quality", "cc-quality") == ["cc-quality"]


def test_singleton_no_instance_is_base_only():
    assert inbox_ids("cai", None) == ["cai"]


def test_base_first_and_deduped():
    ids = inbox_ids("cc-cosem-platform", "cc-cosem-platform-1")
    assert ids[0] == "cc-cosem-platform"
    assert "cc-cosem-platform-1" in ids
    assert len(ids) == len(set(ids))
