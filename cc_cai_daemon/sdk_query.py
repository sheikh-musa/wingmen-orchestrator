"""Thin wrapper around claude_agent_sdk.query().

Phase 1 is RULES-ONLY per CAI-RESP-185 Q4 — the classifier doesn't call
the SDK. This wrapper exists for Phase 2+ when LLM-based reasoning
widens the silent-lane. Kept as a documented placeholder + INV-3
scrub so the contract is ready when needed.

Per CAI-RESP-185 INV-3: ANTHROPIC_API_KEY MUST be scrubbed from env
before SDK invocation so it uses the Max OAuth in ~/.claude/.credentials.json.
Mirrors ai_provider._call_cli_route's scrub pattern from CADENCE-003.
"""
from __future__ import annotations

import os
from typing import Any, AsyncIterator


async def query_max_oauth(prompt: str, **options) -> AsyncIterator[Any]:
    """Yields messages from claude_agent_sdk.query() with INV-3 scrub.

    Phase 1: not yet called by any module. Phase 2+: classifier may use
    this for LLM-based pattern recognition once narrow-lane has its
    1-week zero-misclassification proof.

    Args:
        prompt: the user-side text
        **options: passed through to claude_agent_sdk.query()

    Yields:
        AssistantMessage / ToolUseBlock / ResultMessage etc.

    Raises:
        ImportError if claude_agent_sdk not available (Python 3.10+ only).
        RuntimeError if any unexpected error during query.
    """
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
