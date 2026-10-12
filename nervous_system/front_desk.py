"""front_desk.py — PURE logic for front desk v1 (op#29128, bus #63359/#63381/
#63383): instant-ack + one narrow whitelist-answer ("live list") in front of
Nazim's nazim-console DMs. No I/O here — front_desk_runner.py is the thin glue
that calls these functions against the live DB/Telegram.

Design, per orch-console's approved 6 changes (bus #63383):
  1. ACK EVERYTHING that isn't the whitelisted "live list" answer. An ack never
     marks the row handled — Nazim still owns and must answer it himself; see
     ACK_TEMPLATE / classify().
  2. NO LLM. The intent match is regex/keywords PLUS the existing cos_triage
     (nervous_system.triage) as a cross-check — never a model call.
  3. irsyad EXCLUSION: operator_asks rows about irsyad/gazzabyte never surface
     in a live-list answer to Musa (standing op#27563 — irsyad status goes to
     Wan/Shuq, not Musa). filter_irsyad() is the single enforcement point.
  4. "live list" = status of EVERYTHING Musa is waiting on, not a literal list:
     render_live_list() uses operator_asks.triage_summary ONLY (never the raw
     `ask` column — matches scripts/asks_daily_digest.py's existing rule), no
     PII, waiting-on-you rows first.
  5/6. Host/kill-switch are front_desk_runner.py concerns, not pure logic.
"""
from __future__ import annotations

import re

from nervous_system import triage

ACK_TEMPLATE = "Got it. Nazim is on it, reply shortly."

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


def is_irsyad_row(row: dict) -> bool:
    """True if an operator_asks row is about irsyad — checked against every
    text field that could carry it, not just one, so a row delegated to an
    irsyad lane but phrased without the keyword in `ask` still gets caught via
    `delegated_to`, and vice versa."""
    for field in ("ask", "triage_summary", "delegated_to"):
        val = row.get(field)
        if val and triage.IRSYAD_DOMAIN_RE.search(val):
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
