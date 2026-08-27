#!/usr/bin/env python3
"""opus_reprobe_storefront.py — durable re-probe heartbeat for the cc-storefront opus flip.

Owns the deadline Nazim handed cc-fleet-health (#28102 / cai CAI-RESP-1170 addendum):
storefront is finishing its FULL money-path/live-tenant audits on SONNET-5 because a FRESH
opus flip on musa2 currently 429s (cold-probe 4/4). This job re-probes musa2's OPUS window
and:
  * when opus is COMFORTABLY servable (2 consecutive HTTP-200 opus probes) -> bus-nudge
    cc-fleet-health to flip storefront to opus-4-8 + trigger coord's opus confirmation-pass.
  * if the DEADLINE (state.deadline_utc, ~2026-08-19T12:00Z) passes and opus is STILL not
    servable -> ESCALATE (bus to cai + orch-console + cc-fleet-health). Escalate, never
    self-resolve (Nazim's explicit steer).

Durable by design: state + flags live in state/opus_reprobe_storefront.json (NOT any live
memory), so a cc-fleet-health reset does not drop the deadline. Idempotent + deduped via the
state flags. Run by launchd dev.wingmen.opus-reprobe every ~30 min.
"""
from __future__ import annotations
import json, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path

ORCH = Path(__file__).resolve().parent.parent
STATE = ORCH / "state" / "opus_reprobe_storefront.json"
MUSA2_TOKEN = Path.home() / ".wingmen" / "keys" / "musa2-oauth-token"
API = "https://api.anthropic.com/v1/messages"


def _dsn() -> str:
    # .env is sourced by launchd wrapper; fall back to reading it.
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        for ln in (ORCH / ".env").read_text().splitlines():
            if ln.startswith("DATABASE_URL=") or ln.startswith("SUPABASE_DB_URL="):
                dsn = ln.split("=", 1)[1].strip().strip('"').strip("'")
                if dsn:
                    break
    return dsn


def _probe_opus_once() -> int:
    """One 1-token opus probe on musa2. Returns HTTP status (200 = servable, 429 = refused)."""
    tok = MUSA2_TOKEN.read_text().strip()
    body = json.dumps({"model": "claude-opus-4-8", "max_tokens": 1,
                       "messages": [{"role": "user", "content": "hi"}]}).encode()
    req = urllib.request.Request(API, data=body, method="POST", headers={
        "Authorization": f"Bearer {tok}", "anthropic-beta": "oauth-2025-04-20",
        "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0  # transient/network — treat as NOT servable, do not act


def _opus_comfortably_servable() -> bool:
    """2 consecutive HTTP-200 opus probes (spaced) — one lucky 200 amid a burst throttle
    is not 'comfortable'. Any 429/other -> not servable."""
    if _probe_opus_once() != 200:
        return False
    time.sleep(5)
    return _probe_opus_once() == 200


def _post(cur, to_agent: str, subject: str, body: str, priority: str = "P2") -> None:
    cur.execute(
        "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,"
        "requires_response,priority,posted_by_identity) "
        "VALUES ('cc-fleet-health',%s,'update',%s,%s,false,%s,'cc-fleet-health')",
        (to_agent, subject, body, priority))


def main() -> int:
    st = json.loads(STATE.read_text())
    if st.get("status") == "done":
        return 0
    now = datetime.now(timezone.utc)
    deadline = datetime.fromisoformat(st["deadline_utc"].replace("Z", "+00:00"))
    servable = _opus_comfortably_servable()

    import psycopg
    dsn = _dsn()
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
        if servable and st.get("status") != "done":
            # RE-NUDGE every run while opus is servable AND the flip is not yet done.
            # NOT one-shot: the opus-clear wake to cc-fleet-health could ITSELF hit the
            # silent wake-delivery bug (Nazim #28215 — the backstop-sweep fails woke=[]
            # with no page), and a single missed nudge would strand the whole #361
            # opus-confirm + #355 re-audit chain with nobody alarmed. So keep firing P1
            # every 30min until I execute the flip and set status='done'.
            first = st.get("status") != "opus-clear-flip-pending"
            _post(cur, "cc-fleet-health",
                  "OPUS CLEAR on musa2 (2/2 200s) — FLIP cc-storefront to opus-4-8 NOW (CAI-1170)"
                  + ("" if first else " [re-nudge — still pending]"),
                  "musa2 OPUS is comfortably servable (2 consecutive 200s). ACT NOW:\n"
                  "1. Flip cc-storefront live model to opus-4-8 via ROBUST verified-submit: "
                  "scripts/lane_nudge.sh storefront '/model claude-opus-4-8' — pane-verify 'Set model to Opus 4.8'. "
                  "NOT opus-5. NOT the backstop-sweep.\n"
                  "2. WAKE storefront to run the opus pass via ROBUST lane_nudge too — NOT the silently-failing "
                  "sweep (Nazim #28215: the #361 opus-confirm + #355 re-audit RIDE on this nudge LANDING; a silent "
                  "sweep-fail stalls the whole chain unalarmed).\n"
                  "3. Ping coord (run the opus confirmation-pass over the sonnet verdicts) + ping orch-console.\n"
                  "4. Set status='done' in state/opus_reprobe_storefront.json so this STOPS re-nudging.\n"
                  "This P1 RE-FIRES every 30min until step 4 — deliberately, because the opus-clear wake itself "
                  "could hit the silent wake bug.", "P1")
            st["clear_notified"] = True
            st["status"] = "opus-clear-flip-pending"
            conn.commit()
        elif now >= deadline and not servable and not st.get("deadline_escalated"):
            for who in ("cc-fleet-health", "cai", "orch-console"):
                _post(cur, who,
                      "OPUS DEADLINE 12:00Z passed + opus STILL not servable on musa2 — escalating (per Nazim #28102)",
                      "Durable re-probe: the 2026-08-19T12:00Z checkpoint passed and musa2 OPUS is "
                      "still NOT comfortably servable (fresh opus probe still 429/other). Per Nazim "
                      "#28102 this ESCALATES to cai + orch-console (do not self-resolve). The FULL "
                      "money-path/live-tenant audit verdicts remain sonnet-only, awaiting an opus "
                      "confirmation-pass that cannot run until opus capacity exists. Needs a cai/operator "
                      "call: wait longer, find an opus-capable pool, or accept sonnet verdicts.", "P1")
            st["deadline_escalated"] = True
            st["status"] = "deadline-escalated"
            conn.commit()

    st["last_probe_utc"] = now.isoformat()
    st["last_probe_servable"] = servable
    STATE.write_text(json.dumps(st, indent=2) + "\n")
    print(f"{now.isoformat()} | opus_servable={servable} status={st['status']} "
          f"clear_notified={st.get('clear_notified')} deadline_escalated={st.get('deadline_escalated')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
