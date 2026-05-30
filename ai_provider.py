"""Model-agnostic AI provider — shared across all agents and system tasks.

Replaces direct anthropic.messages.create() calls throughout the orchestrator.
Supports CLI-route (claude -p subprocess, Max-covered — DEFAULT), Anthropic
API direct (vision + explicit opt-in), and local models (Ollama).

Configuration via env vars:
  AI_DEFAULT_PROVIDER=cli_route  # cli_route | claude | local | fast
  AI_FAST_PROVIDER=cli_route     # for intent routing, summaries
  AI_VISION_PROVIDER=claude      # vision MUST use claude (Carve-Out 3)
  OLLAMA_BASE_URL=               # empty = not available, fallback to cli_route
  AI_CLI_CONCURRENCY=2           # max concurrent claude -p spawns
  AI_CLI_YIELD_THRESHOLD_SECONDS=300  # yield if interactive < N seconds old
  AI_CLI_YIELD_HARD_CAP_SECONDS=1800  # max time to wait before proceeding

CAI-PROCESS-MAX-FIRST-001: cli_route is the default. Direct API is reserved
for the 5 carve-outs (latency_budget_under_3s, streaming_structured_output,
vision_multimodal, Haiku-via-model-rule, tool_use_with_caller_defined_tools).
The vision case auto-routes to direct API; other carve-outs require explicit
caller opt-in via model='claude_api' or by calling private helpers directly.

═══════════════════════════════════════════════════════════════════════════
REGISTRY OF RATIFIED DIRECT-API CARVE-OUTS (per CAI-RESP-174 + parent rule)
═══════════════════════════════════════════════════════════════════════════
Any callsite that bypasses call_ai() and directly instantiates
anthropic.Anthropic() MUST appear in this registry with cai ratification.
Audit-paths: `grep -rn "anthropic.Anthropic\\|anthropic.AsyncAnthropic"` should
match only ai_provider.py itself + the registered callsites below. Any new
match is a CAI-PROCESS-MAX-FIRST-001 violation requiring either migration
or a new carve-out filing.

  Carve-Out 5 — tool_use_with_caller_defined_tools
    Callsite: nervous_system/council_agent.py
    Ratified: CAI-RESP-174 (parent CAI-PROCESS-MAX-FIRST-001)
    Reason:   CTO council reasoning uses Claude tool-use with 5 caller-
              defined tools (read_file, grep, list_files, git_log, sql)
              dispatched in-process by the council module. CLI `claude -p`
              only exposes Claude's built-in tools (Bash/Read/Edit) and
              cannot inject caller-defined tools with caller-side dispatch.
              Direct API is structurally required, not discretionary spend
              — exempt from the al-Isra 17:26-27 isrāf principle.
    Model:    claude-sonnet-4-20250514

  Carve-Out 3 — vision_multimodal (auto-routed via call_ai, no explicit opt-in)
    Callsite: any call_ai(..., images=[...])
    Ratified: CAI-PROCESS-MAX-FIRST-001 original
    Reason:   cli_route doesn't expose vision; auto-route to direct API
              in call_ai when images are present.

(Carve-Outs 1/2/4 — latency / streaming / Haiku-via-model-rule — currently
have no live callsites in this repo. Future additions land here.)
"""

from __future__ import annotations

import asyncio
import os
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("wingmen.ai_provider")

# ── CLI-route concurrency + yield substrate (CAI-PROCESS-MAX-FIRST-001 (d)) ──

# Mitigation 1 (concurrency cap): single-token-bucket backpressure on
# concurrent `claude -p` spawns. Default 2; soak observation may tighten to 1
# if interactive throttling observed in 7-day window.
_CLI_SEMAPHORE: asyncio.Semaphore | None = None  # lazy init — needs running loop


def _get_cli_semaphore() -> asyncio.Semaphore:
    """Lazy-init the global concurrency semaphore. Bound to the running loop
    on first call so import-time loop-absent doesn't fail."""
    global _CLI_SEMAPHORE
    if _CLI_SEMAPHORE is None:
        cap = int(os.environ.get("AI_CLI_CONCURRENCY", "2"))
        _CLI_SEMAPHORE = asyncio.Semaphore(cap)
    return _CLI_SEMAPHORE


def _resolve_claude_bin() -> str:
    """Resolve the absolute path to the `claude` CLI binary.

    launchd-spawned daemons (orch, watchdog) don't inherit interactive shell
    PATH, so bare "claude" fails with FileNotFoundError. Operator sets
    CLAUDE_BIN in .env; we also try a few known macOS install locations.
    Final fallback is bare "claude" (works for interactive operator runs).
    """
    explicit = os.environ.get("CLAUDE_BIN")
    if explicit and os.access(explicit, os.X_OK):
        return explicit
    for candidate in (
        os.path.expanduser("~/.local/bin/claude"),
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
    ):
        if os.access(candidate, os.X_OK):
            return candidate
    return "claude"


# Mitigation 2 (yield mechanism): pause CLI spawns while Musa's interactive
# session is alive. The launcher writes ~/.wingmen/cc_active heartbeat every
# 5 min; if mtime < threshold age, an interactive session is using the Max
# pool and background tasks must wait their turn.
_CC_ACTIVE_MARKER = Path.home() / ".wingmen" / "cc_active"


async def _yield_if_interactive_active() -> None:
    """Block until the interactive-session marker file is stale (or absent),
    or the hard cap elapses (in case the marker is hung from a crashed session).

    Per CAI-STAFF-SPEC-001 §5.2 / CAI-PROCESS-MAX-FIRST-001 (d) Mitigation 2.
    """
    threshold = int(os.environ.get("AI_CLI_YIELD_THRESHOLD_SECONDS", "300"))
    hard_cap = int(os.environ.get("AI_CLI_YIELD_HARD_CAP_SECONDS", "1800"))
    start = time.monotonic()
    poll = 30
    while time.monotonic() - start < hard_cap:
        try:
            age = time.time() - _CC_ACTIVE_MARKER.stat().st_mtime
        except FileNotFoundError:
            return  # No marker = no active interactive session; proceed.
        if age >= threshold:
            return  # Marker is stale; interactive session inactive.
        logger.info(
            "ai_provider: yielding to active interactive session "
            "(marker age=%.0fs, threshold=%ds, elapsed_wait=%.0fs)",
            age, threshold, time.monotonic() - start,
        )
        await asyncio.sleep(poll)
    logger.warning(
        "ai_provider: yield exceeded hard cap (%ds) — proceeding anyway",
        hard_cap,
    )

# ── Provider Config ─────────────────────────────────────────────

def _get_provider(model: str) -> str:
    """Resolve model hint to actual provider.

    Per CAI-PROCESS-MAX-FIRST-001: cli_route is the new default for "auto",
    "claude", and "fast" hints (they all wanted Claude reasoning; CLI route
    is Max-covered, free at the substrate level subject to rate-limit yield).
    Direct API ("claude_api") is the explicit opt-in for the 4 non-Haiku
    carve-outs; vision auto-routes to it via call_ai. "local" still routes
    to Ollama when configured.
    """
    if model == "claude_api":
        return "claude"  # Explicit opt-in to direct API (carve-outs 1/2/5)
    if model == "claude":
        # FLIPPED per MAX-FIRST: was direct API, now cli_route by default.
        # Callers needing direct API for a carve-out must use "claude_api".
        return "cli_route"
    if model == "local":
        if os.environ.get("OLLAMA_BASE_URL"):
            return "ollama"
        logger.info("Ollama not configured, falling back to cli_route")
        return "cli_route"
    if model == "fast":
        fast = os.environ.get("AI_FAST_PROVIDER", "cli_route")
        if fast == "local" and os.environ.get("OLLAMA_BASE_URL"):
            return "ollama"
        return "cli_route"
    if model == "auto":
        default = os.environ.get("AI_DEFAULT_PROVIDER", "cli_route")
        if default == "local" and os.environ.get("OLLAMA_BASE_URL"):
            return "ollama"
        if default == "claude":
            return "cli_route"  # Operator-set default doesn't escape MAX-FIRST.
        return default if default in ("cli_route", "claude") else "cli_route"
    return "cli_route"


# ── Claude Provider ─────────────────────────────────────────────

async def _call_claude(
    prompt: str,
    system: str = "",
    images: list[str] | None = None,
    max_tokens: int = 4096,
    model_name: str = "claude-sonnet-4-20250514",
) -> str:
    """Call Anthropic Claude API.

    Post CAI-PROCESS-MAX-FIRST-001: this is the carve-out gateway —
    routed via call_ai when (a) images present (Carve-Out 3 vision_multimodal),
    or (b) caller explicitly opts in via model='claude_api' for Carve-Outs
    1/2/5 (latency_budget_under_3s, streaming_structured_output,
    tool_use_with_caller_defined_tools). All other text paths default to
    cli_route now.

    The Audit 5 token below covers the load-bearing reason this path must
    exist (vision); the other carve-outs are caller-side opt-ins where
    the caller takes responsibility for the carve-out justification.
    """
    # llm_route_exempt: vision_multimodal
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


# ── CLI Route Provider (Max-covered) ────────────────────────────

async def _call_cli_route(
    prompt: str,
    system: str = "",
    max_tokens: int = 4096,  # advisory — CLI doesn't enforce
    model_name: str = "claude-sonnet-4-6",
) -> str:
    """Spawn `claude -p` subprocess. Routes through Musa's Max plan
    subscription (no API charge subject to rate-limit yield).

    Constraints (CAI-PROCESS-MAX-FIRST-001 (d)):
      1. Concurrency cap via _CLI_SEMAPHORE (default 2)
      2. Yields to active interactive session via _CC_ACTIVE_MARKER
      3. No vision/image support — caller must route to _call_claude
      4. No streaming — claude -p returns full response on completion
      5. 5-min timeout per spawn

    Args:
        prompt: User-side text.
        system: Optional system prompt; concatenated into the user prompt
                (claude -p doesn't have a separate --system flag).
        max_tokens: Advisory only.
        model_name: Passed via --model.
    """
    await _yield_if_interactive_active()
    sem = _get_cli_semaphore()
    async with sem:
        # Combine system + prompt — CLI -p takes a single string.
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        claude_bin = _resolve_claude_bin()
        # CADENCE-003 INV-3: claude -p must use Max OAuth, never the API path.
        # When ANTHROPIC_API_KEY is present in env (set in .env for council_agent's
        # Carve-Out 5), the CLI prefers the API and falls back to depleted credits.
        # Scrub it from the subprocess env so the CLI uses ~/.claude/.credentials.json
        # (Max OAuth) instead.
        clean_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        proc = await asyncio.create_subprocess_exec(
            claude_bin, "-p", full_prompt,
            "--model", model_name,
            "--dangerously-skip-permissions",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=clean_env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=300
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError("ai_provider.cli_route: claude -p timed out (5min)")
        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()[:500]
            raise RuntimeError(
                f"ai_provider.cli_route: claude -p exit {proc.returncode}: {err}"
            )
        return stdout.decode(errors="replace").strip()


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
    # Vision tasks always use a vision-capable provider.
    # Per CAI-PROCESS-MAX-FIRST-001 Carve-Out 3: cli_route doesn't expose
    # vision; auto-route to direct API ("claude") in that case.
    if images:
        vision_provider = os.environ.get("AI_VISION_PROVIDER", "claude")
        if vision_provider == "local" and os.environ.get("OLLAMA_BASE_URL"):
            provider = "ollama"
        else:
            provider = "claude"  # Direct API — Carve-Out 3 vision_multimodal
    else:
        provider = _get_provider(model)

    if json_mode:
        system = (system + "\n\nRespond with valid JSON only. No markdown, no explanation.").strip()

    logger.info(
        "call_ai: provider=%s, model_hint=%s, has_images=%s, max_tokens=%s",
        provider, model, bool(images), max_tokens,
    )

    try:
        if provider == "cli_route":
            return await _call_cli_route(prompt, system=system, max_tokens=max_tokens)
        elif provider == "claude":
            return await _call_claude(prompt, system=system, images=images, max_tokens=max_tokens)
        elif provider == "ollama":
            return await _call_ollama(prompt, system=system, images=images, max_tokens=max_tokens)
        else:
            # Default fallback — cli_route (Max-covered)
            return await _call_cli_route(prompt, system=system, max_tokens=max_tokens)
    except Exception as e:
        # Provider-specific fallbacks. Note: cli_route → claude fallback is
        # NOT automatic per CAI-PROCESS-MAX-FIRST-001 — caller must explicitly
        # opt into direct API via model='claude_api' if they need a carve-out.
        # Silent fallback would defeat the audit signal.
        if provider == "ollama":
            logger.warning("Ollama failed (%s), falling back to cli_route", e)
            return await _call_cli_route(prompt, system=system, max_tokens=max_tokens)
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
