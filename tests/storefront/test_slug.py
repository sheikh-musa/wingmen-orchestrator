"""URL-safe slug rules for t.me/dookanabot?start=<slug>.

Telegram start_param allows A-Z a-z 0-9 _ and - (max 64 chars). We further
lowercase and require 1-32 chars to keep shop URLs clean and stable.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from storefront.slug import normalize_slug, validate_slug


def test_validate_accepts_simple_slug():
    assert validate_slug("aunty-mariam") is True


def test_validate_accepts_underscores_and_digits():
    assert validate_slug("shop_42") is True


def test_validate_rejects_empty():
    assert validate_slug("") is False


def test_validate_rejects_spaces():
    assert validate_slug("aunty mariam") is False


def test_validate_rejects_uppercase():
    assert validate_slug("AuntyMariam") is False


def test_validate_rejects_slash():
    assert validate_slug("a/b") is False


def test_validate_rejects_too_long():
    assert validate_slug("x" * 33) is False


def test_normalize_lowercases_and_trims():
    assert normalize_slug("  Aunty-Mariam  ") == "aunty-mariam"


def test_normalize_spaces_to_hyphens():
    assert normalize_slug("Aunty Mariam Kitchen") == "aunty-mariam-kitchen"


def test_normalize_strips_invalid_chars():
    assert normalize_slug("café!!bistro") == "cafbistro"
