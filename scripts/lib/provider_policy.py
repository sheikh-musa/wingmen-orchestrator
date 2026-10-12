"""provider_policy — per-client model-provider policy resolver (Musa op#29321/#29324).

Answers "for this client_key, which providers are allowed and is text_only
(vision/image MCP off) required?" from `provider_policy` rows. The gate the
GLM/non-Anthropic routing reads before placing a lane on z.ai.

SAFETY (fail-closed, orch-console residency posture): never default to allowing
a non-Anthropic provider. An unknown client_key resolves to Anthropic-only.
"""
from __future__ import annotations

ANTHROPIC_ONLY = {"allowed_providers": ["anthropic"], "text_only": False}


def resolve_policy(rows, client_key):
    """PURE. `rows` = provider_policy rows (dicts: client_key, allowed_providers,
    text_only). Returns {allowed_providers, text_only} for client_key, or the
    fail-closed Anthropic-only default when no row matches."""
    for r in rows:
        if r.get("client_key") == client_key:
            providers = list(r.get("allowed_providers") or ["anthropic"])
            text_only = bool(r.get("text_only"))
            # HARD invariant (Musa op#29321/#29324): a non-Anthropic provider is
            # permitted ONLY with text_only=true. A row that allows one without it
            # is a misconfiguration — fail closed rather than leak images to z.ai.
            if any(p != "anthropic" for p in providers) and not text_only:
                return dict(ANTHROPIC_ONLY)
            return {"allowed_providers": providers, "text_only": text_only}
    return dict(ANTHROPIC_ONLY)
