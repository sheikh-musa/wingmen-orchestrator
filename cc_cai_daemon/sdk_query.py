"""Thin wrapper around claude_agent_sdk.query().

Phase 1 is RULES-ONLY per CAI-RESP-185 Q4 — the classifier doesn't call
the SDK. This wrapper exists for Phase 2+ when LLM-based reasoning
widens the silent-lane. Kept as a documented placeholder + INV-3
scrub so the contract is ready when needed.

Per CAI-RESP-185 INV-3: ANTHROPIC_API_KEY MUST be scrubbed from env
before SDK invocation so it uses the Max OAuth in ~/.claude/.credentials.json.
Mirrors ai_provider._call_cli_route's scrub pattern from CADENCE-003.

Per CAI-RESP-192 Q2: SDK call must pin an explicit model ID — the SDK
does NOT auto-alias deprecated strings, so an opaque default is unsafe
across deprecation events (e.g. claude-sonnet-4-20250514 retires 06-15).
Haiku 4.5 is the cost-conscious floor for the classifier shape signals.
"""
from __future__ import annotations

import os
from typing import Any, AsyncIterator

# Pinned per CAI-RESP-192 Q2. Phase 1 classifier is rules-only and won't
# invoke this; Phase 2+ widening will inherit the pin unless an LLM caller
# explicitly overrides via options['model'].
MODEL_DEFAULT = "claude-haiku-4-5-20251001"


async def query_max_oauth(prompt: str, **options) -> AsyncIterator[Any]:
    """Yields messages from claude_agent_sdk.query() with INV-3 scrub.

    Phase 1: not yet called by any module. Phase 2+: classifier may use
    this for LLM-based pattern recognition once narrow-lane has its
    1-week zero-misclassification proof.

    Args:
        prompt: the user-side text
        **options: passed through to claude_agent_sdk.query(). If 'model'
            is not provided, MODEL_DEFAULT is injected.

    Yields:
        AssistantMessage / ToolUseBlock / ResultMessage etc.

    Raises:
        ImportError if claude_agent_sdk not available (Python 3.10+ only).
        RuntimeError if any unexpected error during query.
    """
    options.setdefault("model", MODEL_DEFAULT)
    # INV-3 scrub: never let SDK see ANTHROPIC_API_KEY
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        # Imported lazily so Python 3.9 modules importing this don't blow up
        # at import time; only blows up if someone actually calls the function.
        from claude_agent_sdk import query

        async for msg in query(prompt=prompt, **options):
            yield msg
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved
