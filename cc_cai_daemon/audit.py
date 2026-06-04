"""INV-5 audit logger. HARD SHIP CONDITION per CAI-RESP-185."""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

import psycopg

logger = logging.getLogger("cc_cai.audit")


class AuditLogger:
    """Synchronous INV-5 audit writer.

    Synchronous on purpose: the audit row MUST land before the side effect
    fires. If the audit write fails, the side effect MUST NOT proceed
    (CAI-RESP-185 amanah precondition). Callers wrap any side effect with:
        audit_id = audit.log_tool_call(...)
        if audit_id is None:
            return  # audit failed; do nothing
        # then proceed with side effect
    """

    def __init__(self, dsn: str, session_id: Optional[str] = None):
        self.dsn = dsn
        self.session_id = session_id or f"cc-cai-{uuid.uuid4().hex[:12]}"

    def _insert(self, **fields) -> Optional[int]:
        cols = ["session_id", *fields.keys()]
        vals = [self.session_id, *fields.values()]
        placeholders = ", ".join(["%s"] * len(cols))
        sql = (
            f"INSERT INTO cc_cai_audit_log ({', '.join(cols)}) "
            f"VALUES ({placeholders}) RETURNING id"
        )
        try:
            with psycopg.connect(self.dsn, autocommit=True) as conn, conn.cursor() as cur:
                cur.execute(sql, vals)
                return cur.fetchone()[0]
        except Exception as e:
            logger.error(f"INV-5 audit write FAILED: {e}", exc_info=True)
            return None

    def log_classification(
        self, *, agent_message_id: int, classification: str,
        reason: str, confidence: float,
    ) -> Optional[int]:
        return self._insert(
            event_type="classification",
            agent_message_id=agent_message_id,
            classification=classification,
            classification_reason=reason,
            confidence=confidence,
        )

    def log_tool_call(
        self, *, tool_name: str,
        tool_input: dict, tool_output: dict | None = None,
        agent_message_id: int | None = None,
    ) -> Optional[int]:
        return self._insert(
            event_type="tool_call",
            tool_name=tool_name,
            tool_input_summary=json.dumps(tool_input, default=str),
            tool_output_summary=json.dumps(tool_output, default=str) if tool_output else None,
            agent_message_id=agent_message_id,
        )

    def log_silent_action(
        self, *, agent_message_id: int, action: str,
        classification: str, reason: str,
    ) -> Optional[int]:
        return self._insert(
            event_type="silent_action",
            agent_message_id=agent_message_id,
            classification=classification,
            classification_reason=reason,
            tool_name=action,
        )

    def log_escalation(
        self, *, agent_message_id: int, reason: str,
        telegram_message_id: int | None = None,
    ) -> Optional[int]:
        return self._insert(
            event_type="escalation",
            agent_message_id=agent_message_id,
            classification="escalate",
            classification_reason=reason,
            escalated_to_operator=True,
            telegram_message_id=telegram_message_id,
        )

    def log_kill_switch_trip(self, *, new_state: str, reason: str) -> Optional[int]:
        return self._insert(
            event_type="kill_switch_trip",
            classification_reason=reason,
            kill_switch_state=new_state,
        )
