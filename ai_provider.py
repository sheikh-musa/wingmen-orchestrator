"""Model-agnostic AI provider — shared across all agents and system tasks.

Replaces direct anthropic.messages.create() calls throughout the orchestrator.
Supports Claude (Anthropic API), local models (Ollama), and fast models (Gemini).

Configuration via env vars:
  AI_DEFAULT_PROVIDER=claude     # claude | local | fast
  AI_FAST_PROVIDER=claude        # for intent routing, summaries
  AI_VISION_PROVIDER=claude      # for screenshot analysis
  OLLAMA_BASE_URL=               # empty = not available, fallback to claude
"""

from __future__ import annotations

import os
import json
import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger("wingmen.ai_provider")

# ── Provider Config ─────────────────────────────────────────────

def _get_provider(model: str) -> str:
    """Resolve model hint to actual provider."""
    if model == "claude":
        return "claude"
    if model == "local":
        if os.environ.get("OLLAMA_BASE_URL"):
            return "ollama"
        logger.info("Ollama not configured, falling back to Claude")
        return "claude"
    if model == "fast":
        fast = os.environ.get("AI_FAST_PROVIDER", "claude")
        if fast == "local" and os.environ.get("OLLAMA_BASE_URL"):
            return "ollama"
        return "claude"
    if model == "auto":
        default = os.environ.get("AI_DEFAULT_PROVIDER", "claude")
        if default == "local" and os.environ.get("OLLAMA_BASE_URL"):
            return "ollama"
        return "claude"
    return "claude"


# ── Claude Provider ─────────────────────────────────────────────

async def _call_claude(
    prompt: str,
    system: str = "",
    images: list[str] | None = None,
    max_tokens: int = 4096,
    model_name: str = "claude-sonnet-4-20250514",
) -> str:
    """Call Anthropic Claude API."""
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Build messages
    content = []
    if images:
        for img in images:
            if img.startswith("data:"):
                # Base64 image
                media_type = img.split(";")[0].split(":")[1]
                data = img.split(",", 1)[1]
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": data}
                })
            elif img.startswith("http"):
                # URL image
                content.append({
                    "type": "image",
                    "source": {"type": "url", "url": img}
                })
    content.append({"type": "text", "text": prompt})

    kwargs = {
        "model": model_name,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": content}],
    }
    if system:
        kwargs["system"] = system

    response = await client.messages.create(**kwargs)
    return response.content[0].text


# ── Ollama Provider ─────────────────────────────────────────────

async def _call_ollama(
    prompt: str,
    system: str = "",
    images: list[str] | None = None,
    max_tokens: int = 4096,
    model_name: str = "gemma2:9b",
) -> str:
    """Call local Ollama model."""
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": max_tokens},
    }
    if system:
        payload["system"] = system
    if images:
        # Ollama accepts base64 images
        payload["images"] = [
            img.split(",", 1)[1] if "," in img else img
            for img in images
        ]

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(f"{base_url}/api/generate", json=payload)
        resp.raise_for_status()
        return resp.json().get("response", "")


# ── Public API ──────────────────────────────────────────────────

async def call_ai(
    prompt: str,
    *,
    system: str = "",
    images: list[str] | None = None,
    model: str = "auto",
    max_tokens: int = 4096,
    json_mode: bool = False,
) -> str:
    """Route AI call to the appropriate provider.

    Args:
        prompt: The user/task prompt
        system: System prompt (optional)
        images: List of image URLs or base64 data URIs (for vision tasks)
        model: "auto" | "claude" | "local" | "fast"
        max_tokens: Max response tokens
        json_mode: If True, append "Respond with valid JSON only." to system prompt

    Returns:
        The model's text response
    """
    # Vision tasks always use the vision provider
    if images:
        vision_provider = os.environ.get("AI_VISION_PROVIDER", "claude")
        if vision_provider != "claude" and vision_provider == "local":
            provider = "ollama" if os.environ.get("OLLAMA_BASE_URL") else "claude"
        else:
            provider = "claude"
    else:
        provider = _get_provider(model)

    if json_mode:
        system = (system + "\n\nRespond with valid JSON only. No markdown, no explanation.").strip()

    logger.info(f"call_ai: provider={provider}, model_hint={model}, has_images={bool(images)}, max_tokens={max_tokens}")

    try:
        if provider == "claude":
            return await _call_claude(prompt, system=system, images=images, max_tokens=max_tokens)
        elif provider == "ollama":
            return await _call_ollama(prompt, system=system, images=images, max_tokens=max_tokens)
        else:
            # Fallback
            return await _call_claude(prompt, system=system, images=images, max_tokens=max_tokens)
    except Exception as e:
        # If local fails, try Claude as fallback
        if provider == "ollama":
            logger.warning(f"Ollama failed ({e}), falling back to Claude")
            return await _call_claude(prompt, system=system, images=images, max_tokens=max_tokens)
        raise


def extract_json(text: str) -> dict | list | None:
    """Extract JSON from AI response (handles markdown code blocks)."""
    # Try direct parse first
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # Try extracting from code blocks
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass

    # Try finding JSON object/array in text
    for pattern in [r"\{.*\}", r"\[.*\]"]:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except (json.JSONDecodeError, TypeError):
                pass

    return None
