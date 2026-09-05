"""Agent prompt builders and response parsers."""

from legacy.agents.router import build_router_prompt, parse_router_response
from legacy.agents.brainstorm import build_brainstorm_prompt
from legacy.agents.auditor import build_auditor_prompt, parse_auditor_response
from legacy.agents.fixer import build_fixer_prompt

__all__ = [
    "build_router_prompt",
    "parse_router_response",
    "build_brainstorm_prompt",
    "build_auditor_prompt",
    "parse_auditor_response",
    "build_fixer_prompt",
]
