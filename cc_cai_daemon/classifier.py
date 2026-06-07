"""cc-cai Phase 1 classifier — rules-only narrow silent-lane.

Per CAI-RESP-185 Q4 + CADENCE-004 INV-1/INV-6:

  Silent-lane authority is STRICTLY:
    - mark_read_fyi: pure FYI / digest pings (notification_log echoes,
      completion acks, accepted_by_timeout ratifications)
    - ack_fyi: routine consensus on patterns operator previously approved
      (tripwire reports, watchdog clean digests)

  Everything else → escalate, including ALL 8 reaches-operator categories
  from CADENCE-004. When in doubt, escalate (INV-6 default HOLD).

Phase 1 is RULES-ONLY. No LLM calls. Pure-Python function so it's testable
without DB/SDK. The daemon's main loop calls audit.log_classification()
AFTER classify() returns + BEFORE any handler dispatches.

Phase 2 may add SDK-based reasoning only after the narrow lane has its
≥1 week operational evidence + INV-5 audit-log review showing zero
misclassifications (per CAI-RESP-185).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional


# ---------- INV-1 enumerated categories (CADENCE-004) ----------

REACHES_OPERATOR_CATEGORIES: frozenset = frozenset({
    "halal_riba",                 # revenue/finance, lending, interest, BNPL, pricing
    "zakat",                      # zakat handling/transparency
    "client_data_amanah",         # PII, tenant isolation, RLS, encryption, breach
    "client_facing_commitment",   # scope promises, contracts, SLA, marketing claims
    "ip_legal_contract",          # IP, legal, contracts, licensing
    "money_above_threshold",      # spend above operator-set threshold
    "irreversible_destructive",   # DROP TABLE, force-push to main, decommission
    "novel_low_confidence",       # INV-6 floor: unknown shape / low confidence
})


# ---------- Classification result ----------

LabelType = Literal["mark_read_fyi", "ack_fyi", "escalate"]


@dataclass(frozen=True)
class Classification:
    label: LabelType
    reason: str
    confidence: float
    escalation_category: Optional[str] = None


# ---------- Layer 1: INV-1 reaches-operator keyword patterns ----------
# CALIBRATION: extend during Phase 1 observation window

_RIBA_PATTERNS = (
    "interest", "loan", "credit-line", "bnpl", "buy-now-pay-later",
    "stripe radar", "underwriting", "finance-revenue", "fee-model",
    "pricing tier", "subscription billing", "card fee", "transaction fee",
    "lending", "credit card",
)

# CALIBRATION: extend during Phase 1 observation window
_ZAKAT_PATTERNS = ("zakat",)

# CALIBRATION: extend during Phase 1 observation window.
# 'rls' / 'row level security' removed per CAI-RESP-188 (2026-06-05) —
# substrate-engineering vocab, not a client_data_amanah signal.
_CLIENT_DATA_PATTERNS = (
    "pii", "personally identifiable", "tenant isolation",
    "encryption at rest", "field encryption",
    "data breach", "gdpr", "pdpa", "exfiltration",
)

# CALIBRATION: extend during Phase 1 observation window
_CLIENT_FACING_PATTERNS = (
    "scope promise", "customer commitment", "client representation",
    "sla", "uptime guarantee", "marketing claim",
)

# CALIBRATION: extend during Phase 1 observation window
# 2026-06-05 dry-run audit removed bare "ip " (40% of escalations were
# technical IP-address / IP-rotation / TCP-IP / brainstorm mentions).
# Use specific IP-rights phrases instead. "contract" remains broad and
# may need to drop to "service contract" / "vendor contract" if it
# false-positives on "API contract" / "Telegram contract" patterns —
# observe in week 1.
_IP_LEGAL_PATTERNS = (
    "intellectual property", "ip rights", "patent", "copyright",
    "trademark", "contract", "legal review", "open source license",
    "gpl ", "agpl", "proprietary code", "license terms",
)

# CALIBRATION: extend during Phase 1 observation window
_MONEY_PATTERNS = (
    "approve spend", "purchase", "subscription upgrade", "wire transfer",
    "payment", "deposit", "credit card charge", "domain renewal",
    "above threshold",
)

# CALIBRATION: extend during Phase 1 observation window
_IRREVERSIBLE_PATTERNS = (
    "drop table", "drop database", "force push", "rm -rf",
    "decommission", "delete production", "wipe", "factory reset",
    "permanent delete", "truncate",
)


# Ordered: (category, patterns). Order matters because some patterns
# may overlap conceptually; the FIRST match wins. Riba comes first
# because "credit card charge" (money) overlaps with "credit card" (riba)
# and lending mechanisms are the higher-amanah concern.
_KEYWORD_LAYERS = (
    ("halal_riba", _RIBA_PATTERNS),
    ("zakat", _ZAKAT_PATTERNS),
    ("client_data_amanah", _CLIENT_DATA_PATTERNS),
    ("client_facing_commitment", _CLIENT_FACING_PATTERNS),
    ("ip_legal_contract", _IP_LEGAL_PATTERNS),
    ("money_above_threshold", _MONEY_PATTERNS),
    ("irreversible_destructive", _IRREVERSIBLE_PATTERNS),
)


# ---------- Layer 2 sets ----------

_HIGH_PRIORITIES = frozenset({"P0", "P1"})
_ESCALATE_TYPES = frozenset({"challenge", "blocker", "counter"})
_LOW_PRIORITIES = frozenset({"P2", "P3", "P4"})


# ---------- Subject pattern regexes for Layer 3 ----------

_TRIPWIRE_RE = re.compile(r"tripwire\s*\+?\s*\d+\s*h", re.IGNORECASE)
_CALIBRATION_RE = re.compile(r"calibration.*summary", re.IGNORECASE)
_FINDINGS_RE = re.compile(r"findings", re.IGNORECASE)


def _safe_str(v) -> str:
    if v is None:
        return ""
    return str(v)


def classify(msg: dict) -> Classification:
    """Classify a cai-addressed agent_messages row. Pure, never raises.

    Layer order (first-match wins):
      Layer 1: INV-1 reaches-operator keyword triggers (highest priority)
      Layer 2: P0/P1 priority OR challenge/blocker/counter message_type
      Layer 3: recognized routine FYI patterns (silent-lane targets)
      Layer 4: INV-6 default HOLD (escalate, novel_low_confidence, 0.4)
    """
    try:
        subject = _safe_str(msg.get("subject", "")) if isinstance(msg, dict) else ""
        body = _safe_str(msg.get("body", "")) if isinstance(msg, dict) else ""
        priority = _safe_str(msg.get("priority", "")) if isinstance(msg, dict) else ""
        message_type = _safe_str(msg.get("message_type", "")) if isinstance(msg, dict) else ""
        requires_response = bool(msg.get("requires_response", False)) if isinstance(msg, dict) else False

        haystack = (subject + " " + body).lower()
        subj_lower = subject.lower()

        # ----- Layer 1: INV-1 keyword triggers -----
        for category, patterns in _KEYWORD_LAYERS:
            for pat in patterns:
                if pat in haystack:
                    return Classification(
                        label="escalate",
                        reason=f"INV-1 keyword match: {category!r} pattern {pat!r}",
                        confidence=1.0,
                        escalation_category=category,
                    )

        # ----- Layer 2: priority / type escalation -----
        if priority in _HIGH_PRIORITIES:
            return Classification(
                label="escalate",
                reason=f"high priority {priority}",
                confidence=0.95,
                escalation_category="novel_low_confidence",
            )
        if message_type in _ESCALATE_TYPES:
            return Classification(
                label="escalate",
                reason=f"message_type={message_type!r} requires substantive review",
                confidence=0.95,
                escalation_category="novel_low_confidence",
            )

        # ----- Layer 3: recognized routine patterns -----

        # message_type='update' AND no req-resp AND P2-P4
        if (
            message_type == "update"
            and not requires_response
            and priority in _LOW_PRIORITIES
        ):
            if _TRIPWIRE_RE.search(subj_lower):
                return Classification(
                    label="ack_fyi",
                    reason="tripwire report",
                    confidence=0.9,
                )
            if _CALIBRATION_RE.search(subj_lower) or _FINDINGS_RE.search(subj_lower):
                return Classification(
                    label="ack_fyi",
                    reason="calibration summary / findings",
                    confidence=0.85,
                )
            if "accepted_by_timeout" in subj_lower:
                return Classification(
                    label="mark_read_fyi",
                    reason="accepted_by_timeout ratification",
                    confidence=0.95,
                )
            if "digest" in subj_lower or "status.md" in subj_lower or "session_digest" in subj_lower:
                return Classification(
                    label="mark_read_fyi",
                    reason="digest / status echo",
                    confidence=0.9,
                )
            # Generic FYI update — still within narrow-lane (update + no req-resp + P2-P4)
            return Classification(
                label="mark_read_fyi",
                reason="generic update FYI in narrow lane",
                confidence=0.7,
            )

        # message_type='agreed' AND no req-resp
        if message_type == "agreed" and not requires_response:
            return Classification(
                label="mark_read_fyi",
                reason="agreed FYI",
                confidence=0.9,
            )

        # message_type='digest'
        if message_type == "digest":
            return Classification(
                label="mark_read_fyi",
                reason="digest message type",
                confidence=0.95,
            )

        # message_type='decision' AND no req-resp
        if message_type == "decision" and not requires_response:
            if "accepted_by_timeout" in subj_lower:
                return Classification(
                    label="mark_read_fyi",
                    reason="decision accepted_by_timeout",
                    confidence=0.95,
                )
            return Classification(
                label="escalate",
                reason="decision needs substantive read even if FYI-flagged",
                confidence=0.8,
                escalation_category="novel_low_confidence",
            )

        # ----- Layer 4: INV-6 default HOLD -----
        return Classification(
            label="escalate",
            reason=(
                f"INV-6 default HOLD: unrecognized shape "
                f"(message_type={message_type!r}, priority={priority!r}, "
                f"requires_response={requires_response})"
            ),
            confidence=0.4,
            escalation_category="novel_low_confidence",
        )

    except Exception as e:
        # Pure function MUST NOT raise — INV-6 floor on any error path.
        return Classification(
            label="escalate",
            reason=f"INV-6 default HOLD: classifier exception {e!r}",
            confidence=0.4,
            escalation_category="novel_low_confidence",
        )
