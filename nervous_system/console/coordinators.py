"""Single source of truth for the fleet's coordinator / always-on brains.

Before this module, the coordinator set was hardcoded in FIVE places across two
files — `db.py` (`build_coordinators_query` VALUES + `build_lanes_query` NOT IN)
and `hosted_view.py` (`_COORD_IDS` tuple + `_clone_lanes` NOT IN + `_clone_coordinators`
VALUES) — with two DIFFERENT column shapes. Adding or renaming a coordinator meant
editing all five and keeping them in sync by hand: a maintenance trap (flagged in a
prior change; cc-quality prove-by-backlog debt (a), CAI-RESP-776/777/778).

This is now the ONE place a coordinator is defined. Each consumer projects only the
columns it needs:
  * db.py coordinators card VALUES  -> 6 cols (adds op_tag + tmux_session for the
    Mini's local pane peek + operator-message liveness LEAST);
  * hosted_view.py clone VALUES     -> 4 cols (VPS has no live pane; peek is off);
  * both lane sections' exclusion   -> the agent_id set (NOT IN / _COORD_IDS).

The values are trusted module constants (never user input); helpers emit them as SQL
literals so a future coordinator is added in exactly ONE place.
"""
from __future__ import annotations

from typing import List, Tuple


class Coordinator:
    """One coordinator record. Field meaning:

    * agent_id     — base_agent_id / cc_identity; the exclusion + join key.
    * short        — display name on the coordinator card.
    * role_label   — role subtitle on the card.
    * op_tag       — operator_messages.tag for the outbound-liveness LEAST (db.py only).
    * tmux_session — live pane name for the local peek (db.py only).
    * host_hint    — static physical-host fallback for the host badge.
    """

    __slots__ = ("agent_id", "short", "role_label", "op_tag", "tmux_session", "host_hint")

    def __init__(self, agent_id, short, role_label, op_tag, tmux_session, host_hint):
        self.agent_id = agent_id
        self.short = short
        self.role_label = role_label
        self.op_tag = op_tag
        self.tmux_session = tmux_session
        self.host_hint = host_hint


# Canonical ordered set. Order mirrors the previous db.py VALUES so the generated
# card query is byte-identical; both card queries `ORDER BY c.agent_id` anyway, and
# NOT IN / any() are order-insensitive, so output is invariant to this ordering.
COORDINATORS: List[Coordinator] = [
    Coordinator("cc-orchestrator", "Hub", "Orchestrates the fleet", "orch-channel", "orch", "VPS"),
    Coordinator("orch-console", "Nazim", "CTO console / 2nd coordinator", "nazim-console", "nazim", "Mini"),
    Coordinator("cai", "cai", "governance / strategic node", "cai", "cai", "Mini"),
    Coordinator("cc-fleet-health", "SRE", "fleet reliability / health", "fleet-health", "fleet-health", "Mini"),
    Coordinator("cc-finance", "Finance", "Head of Revenue", "finance", "finance", "Mini"),
]


def _sql_str(s: str) -> str:
    """Quote a trusted constant as a SQL string literal. These are module
    constants (never user input); single quotes are still escaped defensively."""
    return "'" + s.replace("'", "''") + "'"


def exclusion_ids() -> Tuple[str, ...]:
    """The base_agent_ids that own coordinator cards and are therefore excluded
    from the lane / context-bloat sections. Replaces the old `_COORD_IDS` tuple."""
    return tuple(c.agent_id for c in COORDINATORS)


def exclusion_sql() -> str:
    """Parenthesised SQL list for `base_agent_id NOT IN (...)`. Set-identical to
    the previous hand-written literals (NOT IN is order-insensitive)."""
    return "(" + ", ".join(_sql_str(c.agent_id) for c in COORDINATORS) + ")"


def values_sql(columns: List[str]) -> str:
    """A `VALUES (...), (...)` body projecting only *columns*, in order. The caller
    supplies the matching `AS c(...)` alias. Column names must be Coordinator
    fields."""
    rows = []
    for c in COORDINATORS:
        vals = ", ".join(_sql_str(getattr(c, col)) for col in columns)
        rows.append("(" + vals + ")")
    return "VALUES " + ", ".join(rows)
