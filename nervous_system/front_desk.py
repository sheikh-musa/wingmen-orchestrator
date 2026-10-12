"""front_desk.py — PURE logic for front desk v1 (op#29128, bus #63359/#63381/
#63383, orch-console gate fixes bus #63427): one narrow whitelist-answer
("live list") in front of Nazim's nazim-console DMs. No I/O here —
front_desk_runner.py is the thin glue that calls these functions against the
live DB/Telegram.

Design, per orch-console's approved 6 changes (bus #63383) as corrected by
the PR #374 gate review (bus #63427):
  1. ACK EVERYTHING that isn't the whitelisted "live list" answer — but the
     ack itself is NOT sent from here any more (#63427 item 5): ingest.py
     already sends its own "📨 Got your message" ack on every nazim-console
     inbound row (nazim-console is not in SUPPRESS_CLIENT_ACK_TAGS), so a
     second, front-desk-authored ack was a duplicate. front_desk_runner.py's
     ACK branch now only stamps an audit column; classify() still decides
     ACK vs ANSWER, it just no longer implies "and send this text".
  2. NO LLM. The intent match is regex/keywords PLUS the existing cos_triage
     (nervous_system.triage) as a cross-check — never a model call.
  3. irsyad EXCLUSION: operator_asks rows about irsyad/gazzabyte never surface
     in a live-list answer to Musa (standing op#27563 — irsyad status goes to
     Wan/Shuq, not Musa). filter_irsyad() is the single enforcement point,
     strengthened (#63427 item 6) with a structural delegated_to-prefix check
     plus Wan/Shuq/goumlyne/[dms]/[sms] coverage — see is_irsyad_row().
  4. "live list" = status of EVERYTHING Musa is waiting on, not a literal list:
     render_live_list() uses operator_asks.triage_summary ONLY (never the raw
     `ask` column — matches scripts/asks_daily_digest.py's existing rule), no
     PII, waiting-on-you rows first.
  5/6. Host/kill-switch/sender-identity are front_desk_runner.py concerns, not
     pure logic.
"""
from __future__ import annotations

import re

from nervous_system import triage

# "live list" is Musa's fixed command phrase (bus #63383 item 4); a couple of
# close variants are accepted so a slightly different phrasing doesn't silently
# fall through to a plain ack.
LIVE_LIST_RE = re.compile(
    r"\blive[\s-]*list\b|\bwhat'?s?\s+pending\s+on\s+me\b", re.IGNORECASE
)

ANSWER = "answer"
ACK = "ack"


def classify(text: str | None, cos_triage: dict | None = None) -> str:
    """ANSWER only when the regex matches AND cos_triage agrees it's safe to
    answer directly (category == direct-answer) AND it isn't irsyad domain —
    the double-guard from the design doc. Anything else is ACK (the safe
    default — never answer on a maybe)."""
    if not text or not LIVE_LIST_RE.search(text):
        return ACK
    t = cos_triage or {}
    if t.get("category") != triage.DIRECT_ANSWER:
        return ACK
    if t.get("domain") == "irsyad":
        return ACK
    return ANSWER


# Additional irsyad-adjacent signals beyond triage.IRSYAD_DOMAIN_RE's bare
# domain keywords (#63427 item 6): the client-relationship people/identifiers
# that show up in an ask about irsyad status WITHOUT ever saying "irsyad" or
# "gazzabyte" literally, plus the requirements-tracking tags from the
# standing doctrine that irsyad status never goes to Musa (see
# feedback_no_irsyad_status_to_musa_update_wan_shuq_directly /
# feedback_track_irsyad_requirements_religiously_dms_sms_tags).
_IRSYAD_ADJACENT_RE = re.compile(r"\b(wan|shuq|goumlyne)\b|\[dms\]|\[sms\]", re.IGNORECASE)

# Structural signal: every irsyad-owning lane identity in this fleet shares
# this literal prefix (cc-irsyad, cc-irsyad-coord, cc-irsyad-receipt, ...) —
# checked directly on delegated_to as an identity match, not just a free-text
# keyword search, so the lane assignment alone is enough even if the ask text
# itself never mentions irsyad/gazzabyte/Wan/Shuq by name.
_IRSYAD_LANE_PREFIX = "cc-irsyad"


def is_irsyad_row(row: dict) -> bool:
    """True if an operator_asks row is about irsyad — checked structurally
    (delegated_to's lane-identity prefix) AND against every text field that
    could carry it, not just one, so a row delegated to an irsyad lane but
    phrased without a keyword in `ask` still gets caught via `delegated_to`,
    and vice versa."""
    delegated_to = row.get("delegated_to") or ""
    if delegated_to.startswith(_IRSYAD_LANE_PREFIX):
        return True
    for field in ("ask", "triage_summary", "delegated_to"):
        val = row.get(field)
        if val and (triage.IRSYAD_DOMAIN_RE.search(val) or _IRSYAD_ADJACENT_RE.search(val)):
            return True
    return False


def filter_irsyad(rows: list[dict]) -> list[dict]:
    """Drop every irsyad-domain row before it ever reaches render_live_list()."""
    return [r for r in rows if not is_irsyad_row(r)]


def render_live_list(rows: list[dict]) -> str:
    """Pure formatting, mirrors scripts/asks_daily_digest.py's render_digest()
    shape (waiting-on-you first) but condensed for a DM reply. `rows` must
    already be irsyad-filtered (filter_irsyad()) — this function does not
    re-filter, so a caller skipping that step is a bug at the call site, not
    silently patched here."""
    if not rows:
        return "Nothing open on your ledger right now."
    waiting = [r for r in rows if r.get("waiting_on_operator")]
    others = [r for r in rows if not r.get("waiting_on_operator")]
    lines = [f"📋 {len(rows)} open ({len(waiting)} waiting on you):"]
    for r in waiting:
        lines.append(f"  #{r['id']} [waiting on you] ({r.get('delegated_to') or '?'}): {r['triage_summary']}")
    for r in others:
        lines.append(f"  #{r['id']} ({r.get('delegated_to') or 'unassigned'}): {r['triage_summary']}")
    return "\n".join(lines)
