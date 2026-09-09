#!/usr/bin/env python3
"""supervised_draft_deadman.py — periodic backstop for orphaned client replies.

A supervised lane files its client reply as a DRAFT for a reviewer to SEND (lane_reply.sh).
self_recycle.sh GATE 3 catches the case where the reviewer RECYCLES with a draft pending; this
catches the OTHER shape — the reviewer stays alive but never releases it (distracted, or simply
gone quiet). On 2026-09-09 the coord's answers to Wan sat unreleased ~11h and only the operator
caught it. This posts a durable bus page to the reviewer console so that either the live Nazim
acts on it, or — if Nazim has died/recycled — the NEXT Nazim reconciles the page at boot. The
page is the safety signal that was missing that day.

DESIGN:
- Detection via the shared scripts/lib/unreleased_client_drafts (one definition, no drift), with
  an SLA so a just-filed draft isn't paged before a human could plausibly release it.
- PAGE, never auto-release: supervision exists for a reason; the deadman surfaces, a human sends.
- DEDUP: page a given draft once, then re-page (escalate) only after RE_PAGE_S, so a standing
  orphan nags on a sane cadence instead of every run. State in state/supervised_draft_deadman_state.json.
- FAIL-CLOSED: a could-not-measure (DB error) pages LOUD — a safety check that cannot read must
  never read green (dead-man's switch), same shape as the irsyad PII monitor.
- UNGATED: detection + the page are never gated by a lease — a safety page must never be silenced.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.lib.unreleased_client_drafts import find_unreleased_drafts, CouldNotMeasure  # noqa: E402

import psycopg2  # noqa: E402

ORCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(ORCH, "state", "supervised_draft_deadman_state.json")
SLA_S = int(os.environ.get("DRAFT_DEADMAN_SLA_S", "1800"))       # page a draft older than this
RE_PAGE_S = int(os.environ.get("DRAFT_DEADMAN_REPAGE_S", "3600"))  # re-page cadence for a standing orphan
TO_AGENT = "orch-console"   # the human-in-loop releaser (Nazim); a fresh Nazim reconciles at boot


def _dsn() -> str:
    env = os.environ.get("DATABASE_URL")
    if env:
        return env
    m = re.search(r"^DATABASE_URL=(.+)$", open(os.path.join(ORCH, ".env")).read(), re.M)
    if not m:
        raise SystemExit("DATABASE_URL not found")
    return m.group(1).strip()


def _load_state() -> dict:
    try:
        return json.load(open(STATE_PATH))
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    json.dump(st, open(tmp, "w"))
    os.replace(tmp, STATE_PATH)


def _page(dsn: str, subject: str, body: str) -> None:
    with psycopg2.connect(dsn) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, "
            "priority, requires_response) VALUES ('supervised-draft-deadman',%s,'blocker',%s,%s,'P1',true)",
            (TO_AGENT, subject[:200], body),
        )
        c.commit()


def main() -> int:
    dsn = _dsn()
    now = time.time()
    try:
        rows = find_unreleased_drafts(min_age_s=SLA_S, dsn=dsn)
    except CouldNotMeasure as e:
        # Dead-man: cannot measure -> page LOUD, do not exit green.
        _page(dsn, "DRAFT-DEADMAN could-not-measure (fail-closed)",
              f"The supervised-draft deadman could NOT run its check: {e}\n"
              "Treat as a possible orphaned client reply and verify manually via "
              "`python -m scripts.lib.unreleased_client_drafts`.")
        print("could-not-measure -> paged", file=sys.stderr)
        return 2

    state = _load_state()
    live_ids = {str(r["draft_id"]) for r in rows}
    # prune released drafts from state so a future re-orphan re-pages cleanly
    state = {k: v for k, v in state.items() if k in live_ids}

    paged = 0
    for r in rows:
        key = str(r["draft_id"])
        last = state.get(key, 0)
        if now - last < RE_PAGE_S:
            continue  # already paged recently
        mins = r["age_s"] // 60
        _page(
            dsn,
            f"ORPHANED CLIENT DRAFT: {r['channel']} op#{r['draft_id']} unreleased {mins}m",
            f"A supervised client reply has been drafted but NOT released for {mins} minutes — the "
            f"client is waiting and hears nothing (the 2026-09-09 Wan strand shape).\n\n"
            f"  channel:    {r['channel']}\n"
            f"  draft:      operator_messages op#{r['draft_id']} (delivered=false)\n"
            f"  drafted_by: {r['drafted_by']}\n"
            f"  reviewer:   {r['reviewer']}\n\n"
            f"RELEASE it (review the draft text first): `scripts/reviewer_send.sh {r['channel']} \"<text>\"` "
            f"— or, if it should not go, clear it. This page repeats every "
            f"{RE_PAGE_S//60}m until the draft is released.",
        )
        state[key] = now
        paged += 1

    _save_state(state)
    print(f"unreleased drafts >= {SLA_S}s: {len(rows)}; paged this run: {paged}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
