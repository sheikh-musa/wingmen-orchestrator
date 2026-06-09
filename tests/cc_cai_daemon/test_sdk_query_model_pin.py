"""Verify sdk_query injects the haiku pin per CAI-RESP-192 Q2.

Without an explicit model pin, the Agent SDK falls back to its own
default — which is opaque and may resolve to a deprecated string
(e.g. claude-sonnet-4-20250514 retires 2026-06-15). The wrapper must
default model='claude-haiku-4-5-20251001' when caller doesn't override.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cc_cai_daemon import sdk_query


def test_model_default_constant_pinned_to_haiku():
    assert sdk_query.MODEL_DEFAULT == "claude-haiku-4-5-20251001"


def test_query_max_oauth_setdefault_pattern_in_source():
    """The wrapper must call options.setdefault('model', MODEL_DEFAULT) so
    callers that pass model=... can still override."""
    source = inspect.getsource(sdk_query.query_max_oauth)
    assert "options.setdefault(\"model\", MODEL_DEFAULT)" in source, (
        "sdk_query.query_max_oauth must inject MODEL_DEFAULT via "
        "options.setdefault so the SDK never falls back to its own default"
    )
