"""Permission System — role-based access control for bot intents.

Maps (role, capability) -> allowed intents.
"""

from __future__ import annotations

# Intent constants
INTENTS = {
    "chat", "help", "place_order", "track_order", "book_qurban",
    "site_edit", "bug_report", "manage_team", "view_orders", "analytics",
}

# Base permissions by role (always allowed regardless of capabilities)
ROLE_BASE_PERMISSIONS: dict[str, set[str]] = {
    "owner": {"chat", "help", "site_edit", "bug_report", "manage_team", "view_orders", "analytics"},
    "manager": {"chat", "help", "view_orders"},
    "staff": {"chat", "help", "view_orders"},
    "customer": {"chat", "help"},
}

# Capability -> additional intents unlocked
CAPABILITY_INTENTS: dict[str, set[str]] = {
    "storefront": {"place_order", "track_order"},
    "qurban": {"book_qurban"},
    "orders": {"view_orders"},
    "bug_report": {"bug_report"},
    "support": {"chat", "help"},
}

# Role restrictions: some intents only available to certain roles even if capability unlocked
ROLE_CAPABILITY_FILTER: dict[str, set[str]] = {
    "owner": INTENTS,  # owner can do everything
    "manager": INTENTS - {"site_edit", "bug_report", "manage_team", "analytics"},
    "staff": {"chat", "help", "view_orders", "track_order"},
    "customer": {"chat", "help", "place_order", "track_order", "book_qurban"},
}


def get_allowed_intents(role: str, capabilities: list[str]) -> set[str]:
    """Get the set of allowed intents for a role + capability combination."""
    # Start with base permissions
    allowed = ROLE_BASE_PERMISSIONS.get(role, {"chat", "help"}).copy()

    # Add capability-based intents
    for cap in capabilities:
        allowed |= CAPABILITY_INTENTS.get(cap, set())

    # Filter by role restrictions
    role_filter = ROLE_CAPABILITY_FILTER.get(role, {"chat", "help"})
    allowed &= role_filter

    return allowed


def can_do(role: str, capabilities: list[str], intent: str) -> bool:
    """Check if a role with given capabilities can perform an intent."""
    return intent in get_allowed_intents(role, capabilities)
