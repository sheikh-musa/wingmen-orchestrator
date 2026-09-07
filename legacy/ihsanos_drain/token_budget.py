"""CADENCE-008 A daily token-budget enforcement over drain_token_ledger."""
from __future__ import annotations

from typing import Optional


def within_budget(spent_today: int, cap: Optional[int]) -> bool:
    """True if another cycle is allowed. cap=None => unbounded."""
    if cap is None:
        return True
    return spent_today < cap


def spent_today_sql(caller_name: str) -> tuple[str, tuple]:
    """SELECT total tokens spent by caller since UTC midnight."""
    return (
        "SELECT COALESCE(SUM(tokens_spent), 0) FROM drain_token_ledger "
        "WHERE caller_name = %s AND cycle_at >= date_trunc('day', now())",
        (caller_name,),
    )


def record_spend_sql(caller_name: str, tokens: int, note: str) -> tuple[str, tuple]:
    return (
        "INSERT INTO drain_token_ledger (caller_name, tokens_spent, note) "
        "VALUES (%s, %s, %s)",
        (caller_name, tokens, note),
    )
