#!/usr/bin/env python3
"""op11911_syed_trigger — TEMPORARY armed trigger for the operator token policy.

op#11911 (2026-08-11): run HK PATH-B (cc-ihsanos-1 un-paused) full-tilt UNTIL the
Syed weekly pool crosses 90%, then HARD STOP -> the SRE re-pauses cc-ihsanos + any
other un-paused non-irsyad/non-singleton lane back to the irsyad-only drawdown.

The SRE (cc-fleet-health) OWNS the fire, but is nudge-driven, not a hot loop. This
tiny StartInterval job is the ARMING: every 5 min it reads the Syed 7d% (kept fresh
in public.pool_usage by the weekly-limit monitor) and, the first time Syed >= 90%,
posts a P1 + requires_response bus row to cc-fleet-health so the SRE is WOKEN to
execute the re-pause. Fires ONCE (state file), then goes quiet.

DEAD-MAN'S-SWITCH: if the pool_usage Syed reading is STALE (monitor down) or
unreadable, it does NOT silently assume <90% — it posts a LOUD one-shot alert so a
frozen gauge can't hide a real 90% crossing. Never fails silent.

TEMPORARY: bootout `dev.wingmen.op11911-syed-trigger` after the Syed reset
(~10:00 UTC 2026-08-12); this whole trigger is for that one window.
"""
import os
import sys
import json
import pathlib

THRESHOLD = float(os.environ.get("OP11911_SYED_THRESHOLD", "90"))   # pct_7d
STALE_MAX_MIN = float(os.environ.get("OP11911_STALE_MAX_MIN", "20"))
STATE = pathlib.Path(os.environ.get(
    "OP11911_STATE", os.path.expanduser("~/.wingmen/op11911_syed_trigger.state.json")))
ORCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _dsn() -> str:
    v = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if v:
        return v
    for line in open(os.path.join(ORCH_DIR, ".env")):
        if line.startswith(("DATABASE_URL=", "SUPABASE_DB_URL=")):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("no DATABASE_URL")


def _load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save(d: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d, indent=2))


def _wake_sre(subject: str, body: str, cur) -> None:
    # P1 + requires_response -> lands on the SRE's wake floor (CAI-786).
    cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
    cur.execute(
        "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,"
        " priority,requires_response,created_at) "
        "VALUES ('cc-fleet-health','cc-fleet-health','update',%s,%s,'P1',true,now())",
        (subject[:180], body))


def main() -> int:
    import psycopg
    st = _load()
    with psycopg.connect(_dsn(), connect_timeout=15) as c, c.cursor() as cur:
        cur.execute(
            "SELECT pct_7d, round(extract(epoch from (now()-updated_at))/60) "
            "FROM pool_usage WHERE pool='Syed'")
        row = cur.fetchone()
        if not row or row[0] is None:
            if not st.get("stale_fired"):
                _wake_sre(
                    "op#11911 TRIGGER DEAD-MAN: cannot read Syed pool_usage",
                    "The op#11911 Syed-90% trigger could NOT read a Syed pct_7d from "
                    "pool_usage (row missing/null). The weekly-limit monitor may be "
                    "down. Check Syed usage BY HAND and decide the re-pause manually "
                    "— do not assume <90%.", cur)
                st["stale_fired"] = True
                _save(st)
                c.commit()
            print("[op11911] no Syed pool_usage row — dead-man alerted")
            return 0
        pct, age_min = float(row[0]), float(row[1])
        if age_min > STALE_MAX_MIN:
            if not st.get("stale_fired"):
                _wake_sre(
                    f"op#11911 TRIGGER DEAD-MAN: Syed reading STALE ({age_min:.0f}m old)",
                    f"pool_usage Syed pct_7d is {age_min:.0f} min old (> {STALE_MAX_MIN:.0f}m) "
                    f"= the weekly monitor likely stopped refreshing it. Last value {pct:.0f}%. "
                    f"Check Syed usage BY HAND; a frozen gauge must not hide a 90% crossing.", cur)
                st["stale_fired"] = True
                _save(st)
                c.commit()
            print(f"[op11911] Syed reading stale ({age_min:.0f}m) — dead-man alerted")
            return 0
        st["stale_fired"] = False  # fresh again
        print(f"[op11911] Syed pct_7d={pct:.0f}% (fresh {age_min:.0f}m); threshold={THRESHOLD:.0f}%")
        if pct >= THRESHOLD and not st.get("fired"):
            _wake_sre(
                f"op#11911 ANTI-STALL {pct:.1f}% (>= {THRESHOLD:.1f}%) — FLIP IRSYAD TO SONNET NOW",
                f"Syed hit {pct:.1f}% (>= anti-stall threshold {THRESHOLD:.1f}%) with the ~10:00 UTC reset "
                f"still ahead. EXECUTE the irsyad->Sonnet emergency flip to cap the downside at a Sonnet dip "
                f"(never a 100% hard stall): `scripts/fleet_model.sh sonnet --live` flips the engineer lanes "
                f"(irsyad-prog1/irsyad/irsyad-prog2/irsyad-coord) via verified /model nudge; it excludes "
                f"hub/cai/nazim/fleet-health by design. EXCLUDE cc-irsyad-b (irsyad-import) if it is still "
                f"MID-TABUNG-WRITE (money write, Wan-approved) — flip it LAST once its write is done. Then "
                f"verify the 7d climb slows + report to orch-console. See fleet-health-handoff-NOW.md.", cur)
            st["fired"] = True
            _save(st)
            c.commit()
            print(f"[op11911] FIRED at {pct:.0f}% — SRE woken to re-pause")
    return 0


if __name__ == "__main__":
    sys.exit(main())
