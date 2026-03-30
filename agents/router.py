"""Router Agent — classifies user messages into intents."""

from __future__ import annotations

import json
import re

VALID_INTENTS = {"chat", "audit", "fix", "build", "data"}


def build_router_prompt(user_msg: str, repos: list[str], history: list[dict], *, role: str = "admin") -> str:
    """Build the Router Agent prompt with minimal context."""
    repo_list = ", ".join(repos)

    context_lines = ""
    recent = history[-3:]
    for msg in recent[:-1]:
        role_label = "User" if msg["role"] == "user" else "Assistant"
        content = msg["content"][:200]
        context_lines += f"{role_label}: {content}\n"

    role_rules = ""
    if role == "client":
        role_rules = """
IMPORTANT — this user is a non-technical client:
- Never classify as "fix" — use "chat" instead (the advisor will confirm intent first)
- "audit" is allowed
"""

    return f"""You are a message classifier. Given a user message and recent context, return a JSON object.

Intents:
- "chat": questions, brainstorming, discussion, status checks, planning, follow-ups to conversation
- "audit": requests to check ALL pages, crawl the entire site, or do a full review. Only use for broad site-wide requests like "check if all pages work" or "audit the whole site"
- "fix": request to fix a specific issue — a screenshot showing a problem, a single page bug, a UI element that looks wrong. This is for targeted fixes, not full audits
- "build": request to create a new feature or page (will go through build pipeline)
- "data": request to update data (prices, text, toggles) in the database

IMPORTANT: If the message contains a photo/screenshot description showing a specific issue, classify as "fix" NOT "audit". Photos are targeted — the user is showing you exactly what to fix.
{role_rules}
Repos: {repo_list}

Recent conversation:
{context_lines}
Current message: {user_msg}

Return ONLY valid JSON: {{"intent": "...", "repo": "...", "detail": "..."}}
"repo" should be null if no specific repo is mentioned or inferrable.
"detail" is a brief summary of what the user wants."""


def parse_router_response(raw: str) -> dict:
    """Parse router JSON output, with fallback to chat intent."""
    try:
        result = json.loads(raw.strip())
    except json.JSONDecodeError:
        match = re.search(r'\{[^}]+\}', raw)
        if match:
            try:
                result = json.loads(match.group())
            except json.JSONDecodeError:
                return {"intent": "chat", "repo": None, "detail": ""}
        else:
            return {"intent": "chat", "repo": None, "detail": ""}

    if result.get("intent") not in VALID_INTENTS:
        result["intent"] = "chat"

    result.setdefault("repo", None)
    result.setdefault("detail", "")

    return result
