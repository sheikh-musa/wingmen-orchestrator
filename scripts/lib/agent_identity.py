"""agent_identity.py — the ONE place "who am I, for attribution" is resolved.

Extracted from bus_send.py's resolve_from_agent (orch-console bus #47221,
after Nazim caught scripts/asks_triage.py stamping triaged_by from
$ORCH_AGENT_ID — every process that sources the orchestrator .env inherits
ORCH_AGENT_ID=orch-console as fleet-wide-exported noise, so any lane running
asks_triage.py got misattributed to the console. 17 oeh + 2 angullia rows
were corrected after this fix landed. bus_send.py already had the correct,
battle-tested fail-closed resolver; this just factors it out so
asks_triage.py / asks_open.py / anything else that stamps an attribution
column can reuse it instead of re-deriving (and re-breaking) the same logic.
"""
from __future__ import annotations


class IdentityError(RuntimeError):
    pass


def resolve_agent_id(env: dict) -> str:
    """Resolve "who am I" for attribution. Fail closed, never guess.

    Order (matches bus_send.py's resolve_from_agent):
      1. CC_BASE_AGENT_ID — interactive fleet lane / this Claude Code session.
      2. AGENT_ID         — daemon/watchdog launchers.
      3. ORCH_AGENT_ID, but ONLY when ORCH_BODY_ROLE=console (the one
         documented case where the console's own identity is a correct
         fallback — see reference_agent_id_not_orch_agent_id_for_identity).
         ORCH_AGENT_ID is otherwise fleet-wide-exported .env noise and must
         NEVER be trusted blindly.
    Raises IdentityError (never guesses / never defaults to 'orch-console')
    if none of these resolve.
    """
    v = env.get("CC_BASE_AGENT_ID")
    if v:
        return v
    v = env.get("AGENT_ID")
    if v:
        return v
    if env.get("ORCH_BODY_ROLE") == "console":
        v = env.get("ORCH_AGENT_ID")
        if v:
            return v
    raise IdentityError(
        "cannot resolve agent identity: set CC_BASE_AGENT_ID or AGENT_ID in "
        "the environment, or pass an explicit identity override. Refusing "
        "to guess (see reference_agent_id_not_orch_agent_id_for_identity)."
    )
