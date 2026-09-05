"""singleton_liveness — process/tmux DEATH detection for PROTECTED singletons.

WHY (Nazim 35131/35135, 2026-08-28): cai (the governance node) was killed by a botched
switch_singleton_token (kill-then-fail-relaunch) and sat DEAD — no tmux session, no process —
for ~30h UNDETECTED. Two gaps let it hide: (1) with no unread bus rows the wedge watchdog's
Signal-A never fired, and (2) protected_agents are SPARED heartbeat-reaping, so a stale
heartbeat never flagged it either. Nothing asked the one question that matters: is the
process actually alive?

KEY SEMANTIC SPLIT (Nazim #4): 'protected' means DON'T AUTO-REAP/KILL it — it must NOT mean
'exempt from a liveness check'. Protected singletons are exactly the bodies nothing else
watches, so this monitor CHECKS THEM ESPECIALLY.

SEPARATION OF CONCERNS (Nazim #2): liveness = 'is it alive'; wedge = 'alive but stuck'. This
is a SEPARATE monitor. classify_dead() is also intended as a PRECONDITION the wedge watchdog
calls FIRST (check alive → if dead, page+STOP, never nudge a corpse — the gap-2 follow-up).

CONSERVATIVE BY DESIGN:
  - DETECT + PAGE ONLY. NEVER auto-boots (Nazim #3): auto-booting a governance/singleton body
    unattended is where real damage happens. A human boots it (as Nazim did: boot_cai.sh).
  - Dead = NO tmux session AND heartbeat stale past a GENEROUS threshold (Nazim #5). A live
    body's tmux exists; an in-place /clear recycle KEEPS the tmux (verified on 2 recycles), so
    only a real kill (no tmux) past the grace fires — no false-positive on recycle/boot.
  - SAME-HOST only for v1: checks Mini singletons whose tmux is locally readable (cai, the
    console). The hub (cc-orchestrator) is cross-host (VPS) with no local session — its
    liveness is orch_lease-tracked; it's excluded here (needs a VPS-side check later). SELF
    (cc-fleet-health) is excluded — a dead SRE can't run its own monitor (lease-expiry + the
    hub reclaim + sre-liveness cover that).
  - Dead-man's-switch: a probe failure PAGES loud, never silent.

Usage (from ~/wingmen/orchestrator):
  .venv/bin/python -m nervous_system.singleton_liveness            # DRY-RUN (report only)
  .venv/bin/python -m nervous_system.singleton_liveness --page     # page on DEAD
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

# same-host protected singletons to check, agent:tmux-session (env-overridable). Excludes
# self (cc-fleet-health) and the cross-host hub (cc-orchestrator, no local session).
CHECK_SESSIONS = {
    kv.split(":")[0].strip(): kv.split(":")[1].strip()
    for kv in os.environ.get("SINGLETON_LIVENESS_SESSIONS", "cai:cai,orch-console:nazim").split(",")
    if ":" in kv
}
THRESHOLD_S = int(os.environ.get("SINGLETON_LIVENESS_THRESHOLD_S", "1200"))  # 20min, generous
SELF_AGENT = "cc-fleet-health"
# A DEAD body can't read its own page (Nazim follow-up #1): if a CONSOLE is the dead one,
# page the HUB (alive, cross-host, can TG the operator) instead of the dead console.
CONSOLE_AGENTS = {"orch-console", "nazim-console"}
HUB_AGENT = "cc-orchestrator"
DEFAULT_PAGE_TO = "orch-console"
STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "logs", "singleton_liveness_state.json")


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat()} | [singleton-liveness] {msg}", flush=True)


# ---- pure decision -------------------------------------------------------------
def classify_dead(*, tmux_present, hb_age_s, threshold_s):
    """tmux present => ALIVE (ground truth). No tmux + fresh hb => GRACE (booting/blip).
    No tmux + hb stale past threshold => DEAD."""
    if tmux_present:
        return "alive"
    if hb_age_s is None or hb_age_s >= threshold_s:
        return "dead"
    return "grace"


def checked_agents(protected, *, sessions, self_agent):
    """From the protected set, the same-host non-self singletons we can locally death-check:
    those WITH a tmux-session mapping and NOT self. The cross-host hub has no mapping -> skipped."""
    return {a: sessions[a] for a in protected if a != self_agent and a in sessions}


def page_recipient(dead_agent):
    """Route the DEAD-page to a LIVE body that can act. A dead console can't read its own
    inbox, so a console-death pages the HUB (cross-host, alive, can escalate to the operator);
    any other death pages the console (alive to act)."""
    return HUB_AGENT if dead_agent in CONSOLE_AGENTS else DEFAULT_PAGE_TO


# ---- shared with the wedge watchdog (Gap-2: liveness precondition + backstop) --------
def is_covered(agent, sessions=None):
    """True if THIS monitor locally checks `agent` (has a tmux-session mapping). The
    cross-host hub is NOT covered — so the wedge backstop must page for it, not defer."""
    return agent in (CHECK_SESSIONS if sessions is None else sessions)


def backstop_action(agent, *, nudge_ok, sessions=None, lease_fresh=None):
    """The wedge-watchdog rc=255 backstop decision (Nazim 35141 crux). A FAILED singleton
    nudge is DEAD evidence. DEFER the page IFF this monitor covers the agent (it owns the
    single page); otherwise (cross-host hub / no local session) the backstop is the ONLY
    detector on that path. A successful nudge -> nothing.

    lease_fresh gates the UNCOVERED path (Nazim 37448 — the cross-host false-DEAD fix). The
    caller passes the orch_lease.renewed_at freshness for a lease-tracked cross-host body (the
    hub); it is the AUTHORITATIVE cross-host liveness signal, never a Mini-side tmux/nudge one:
      - lease_fresh True  -> 'alive_unreachable': the body is ALIVE on its remote host (still
        renewing its lease); a failed Mini->VPS nudge is unreachable-FROM-HERE, NOT death. The
        caller surfaces an actionable 'wedged-but-alive, needs a cross-host ssh nudge' page —
        NEVER a DEAD/needs-boot page (that would risk a split-brain second boot).
      - lease_fresh False/None -> 'page' (35141 DETECTION PRESERVED: a truly-dead hub stops
        renewing its lease -> stale; unknown/non-lease singleton fails SAFE to page)."""
    if nudge_ok:
        return "none"
    if is_covered(agent, sessions):
        return "defer"
    if lease_fresh is True:
        return "alive_unreachable"
    return "page"


def agent_liveness(agent):
    """Full liveness verdict for one agent, own short-lived conn. 'uncovered' if this monitor
    has no local session for it (the wedge precondition then proceeds normally); else
    classify_dead() on live tmux + heartbeat. Reused as the wedge watchdog's precondition."""
    if not is_covered(agent):
        return "uncovered"
    session = CHECK_SESSIONS[agent]
    try:
        present = _tmux_has_session(session)
    except RuntimeError:
        return "uncovered"        # tmux unreadable -> don't assert dead; let wedge logic run
    import psycopg2
    c = _connect(); cur = c.cursor()
    try:
        hb = _hb_age(cur, agent)
    finally:
        cur.close(); c.close()
    return classify_dead(tmux_present=present, hb_age_s=hb, threshold_s=THRESHOLD_S)


def page_dead(agent, hb_age_s=None, dry_run=False):
    """Public wrapper: page the right LIVE recipient that `agent` is DEAD."""
    _page(agent, hb_age_s, dry_run, page_recipient(agent))


# ---- I/O ----------------------------------------------------------------------
def _tmux_bin():
    # Mini lanes/singletons live on the /usr/local/bin/tmux server (two-tmux note).
    return "/usr/local/bin/tmux" if os.path.exists("/usr/local/bin/tmux") else (shutil.which("tmux") or "tmux")


def _tmux_has_session(session):
    try:
        r = subprocess.run([_tmux_bin(), "has-session", "-t", session],
                           capture_output=True, text=True, timeout=15)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError) as e:
        raise RuntimeError(f"tmux has-session {session} failed: {e}") from e  # dead-man: fail LOUD


def _dsn():
    v = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if v:
        return v
    orch = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for line in open(os.path.join(orch, ".env")):
        if line.startswith(("DATABASE_URL=", "SUPABASE_DB_URL=")):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("singleton_liveness: no DATABASE_URL")


# Transient-blip retry for the substrate-pooler connect (port of ad38b99/f326d3e, 2026-09-03
# pooler-blip sweep). A transient DNS failure resolving the Supabase pooler host used to trip
# this dead-man (main() -> "PROBE FAILED") on a blip that self-heals in seconds — and the page's
# OWN connect could then succeed the instant the blip cleared, delivering a FALSE "not watching
# singletons". Retry the connect a few times with short linear backoff so a blip is absorbed; a
# GENUINE persistent failure STILL re-raises -> sweep() raises -> main() pages LOUD (dead-man
# preserved; retries only buy a grace window). Env-tunable.
_DB_ATTEMPTS = int(os.environ.get("SINGLETON_LIVENESS_DB_ATTEMPTS", "3"))
_DB_RETRY_BASE_S = float(os.environ.get("SINGLETON_LIVENESS_DB_RETRY_BASE_S", "1.0"))


def _sleep(seconds: float) -> None:
    """Indirection over time.sleep so tests can suppress real backoff delay."""
    time.sleep(seconds)


def _retry(op, *, attempts: int, base_delay_s: float, retry_on, sleep=_sleep):
    """Call op(); on a `retry_on` exception retry up to `attempts` TOTAL tries with linear
    backoff, then re-raise the last exception (a persistent failure still surfaces to the
    dead-man). Non-`retry_on` exceptions propagate immediately."""
    for attempt in range(1, attempts + 1):
        try:
            return op()
        except retry_on:
            if attempt == attempts:
                raise
            sleep(base_delay_s * attempt)


def _connect():
    """psycopg2.connect(_dsn()) with a bounded retry on a transient pooler-DNS blip
    (OperationalError). Re-raises on persistent failure so the dead-man still fires."""
    import psycopg2
    return _retry(lambda: psycopg2.connect(_dsn()),
                  attempts=_DB_ATTEMPTS, base_delay_s=_DB_RETRY_BASE_S,
                  retry_on=psycopg2.OperationalError)


def _load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except OSError:
        pass


def _page(agent, hb_age_s, dry_run, recipient):
    body = (f"TL;DR: {agent} (protected singleton) appears DEAD — no tmux session and heartbeat "
            f"stale {int(hb_age_s) if hb_age_s else '?'}s. A nudge can't drain a dead node; it needs "
            f"a BOOT (its sanctioned boot script). I do NOT auto-boot a singleton — flagging you "
            f"(a LIVE body) to relaunch it. If this is a false positive (mid-boot), it self-clears "
            f"when the tmux session reappears.")
    banner = f"🔴 SINGLETON DEAD: {agent} — no tmux/process, needs boot (paging {recipient})"
    print(banner, file=sys.stderr, flush=True)
    if dry_run:
        log(f"DRY-RUN would page {recipient}: 🔴 SINGLETON DEAD: {agent} — needs boot")
        return
    try:
        import psycopg2
        c = _connect(); cur = c.cursor()
        cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
        cur.execute("""INSERT INTO agent_messages (from_agent,to_agent,message_type,priority,subject,body)
                       VALUES ('cc-fleet-health',%s,'blocker','P1',%s,%s)""",
                    (recipient, f"🔴 SINGLETON DEAD: {agent} — no tmux/process, needs boot", body))
        c.commit(); cur.close(); c.close()
        log(f"paged {recipient}: {agent} DEAD")
    except Exception as e:  # noqa: BLE001 — page best-effort; stderr banner already fired
        log(f"WARN page failed ({e}); stderr banner stands")


ORCH_LEASE_KEY = "orch-hub"


def lease_fresh_from_row(row, now):
    """PURE: True=fresh, False=expired, None=indeterminate. Mirrors orch_lease._is_expired
    (fresh == NOT expired). None (missing row / null fields) => the caller fails SAFE to page."""
    if not row:
        return None
    ra = row.get("renewed_at")
    ttl = row.get("ttl_seconds")
    if ra is None or ttl is None:
        return None
    try:
        return not (now > ra + timedelta(seconds=int(ttl)))
    except Exception:  # noqa: BLE001 — any arithmetic/type surprise => indeterminate
        return None


def hub_lease_fresh(now=None):
    """Read the orch-hub lease and return its freshness (True/False/None). This is the
    AUTHORITATIVE cross-host hub-liveness signal — the VPS hub renews orch_lease every
    heartbeat, so a FRESH lease means ALIVE regardless of any Mini-side tmux/nudge result
    (Nazim 37448). None on any read failure => caller fails SAFE to page (dead-man)."""
    now = now or datetime.now(timezone.utc)
    try:
        c = _connect()
        cur = c.cursor()
        cur.execute("SELECT renewed_at, ttl_seconds FROM orch_lease WHERE lease_key=%s",
                    (ORCH_LEASE_KEY,))
        r = cur.fetchone()
        cur.close()
        c.close()
    except Exception as e:  # noqa: BLE001 — never let a lease-read failure crash the monitor
        log(f"hub_lease_fresh read failed ({e}) -> None (fail-safe page)")
        return None
    if not r:
        return None
    return lease_fresh_from_row({"renewed_at": r[0], "ttl_seconds": r[1]}, now)


def hub_alive_evidence(now=None):
    """Is the cross-host hub's HOST alive per its orch_lease?

    HONEST SCOPE (Nazim 37683): the lease is renewed by a systemd TIMER on the VPS
    (wingmen-orch-lease-renew.timer -> orch_lease.py renew), INDEPENDENT of the hub's claude
    session — so a FRESH lease proves the hub HOST is up and the timer runs, NOT that the hub
    session is alive or reading its inbox (observed: lease renewed every ~3 min while the hub
    session sat idle with unread piling for 60+ min). This is still strictly BETTER than the
    signal it replaces (a Mini-side console heartbeat that meant nothing about the hub at
    all), and it is the right answer to the sweeper's question — 'is cc-orchestrator a
    reachable address that will drain?' — but it is HOST-alive, not SESSION-alive. SESSION
    evidence lives elsewhere (hub-ctx-publish freshness in cc_session_costs; the
    hub-bus-currency wedge monitor) and belongs in Phase-1 liveness.py's evidence ladder
    (tmux -> lease(HOST) -> bus/pane activity(SESSION) -> heartbeat). Single shared signal
    for the Mini hub-liveness consumers — no per-consumer copies.
      True  — lease FRESH (host alive) OR indeterminate/unreadable (FAIL-SAFE: a read hiccup
              must never drop a live hub from liveness — that is the harm this guards; logged).
      False — lease DEFINITIVELY expired: host genuinely down, the normal path proceeds.
    Only a positively-expired lease is dead; everything else is treated alive."""
    fresh = hub_lease_fresh(now)  # True / False / None (already catches read errors -> None)
    if fresh is None:
        log("hub_alive_evidence: hub lease indeterminate/unreadable -> fail-safe ALIVE "
            "(do not drop the hub from liveness)")
        return True
    return bool(fresh)


def page_wedged_alive(agent, hb_age_s=None, dry_run=False, recipient=None):
    """Actionable page for a cross-host body WEDGED but ALIVE (orch_lease fresh): its Mini-side
    nudge can't land (cross-host) yet it is NOT dead. Surfaces the wedge (so a live body unwedges
    it) AND names the remedy — an ssh verified-submit to the VPS orch session — so the responder
    ssh-nudges instead of hunting or (dangerously) booting a second hub. Replaces the false
    DEAD-page on the lease-fresh uncovered path (Nazim 37448 conditions 1+2)."""
    recipient = recipient or DEFAULT_PAGE_TO
    subject = f"🟡 HUB WEDGED (alive): {agent} — needs a cross-host ssh nudge to orch@VPS"
    body = (f"TL;DR: {agent} is WEDGED (idle + staged-unsubmitted) but ALIVE — its orch_lease is "
            f"FRESH (still renewing from the VPS), so it is NOT dead and must NOT be booted (a "
            f"second boot = orch_lease split-brain). A Mini-side nudge can't reach it (cross-host). "
            f"REMEDY: ssh verified-submit to the VPS 'orch' session (91.107.235.77, tmux 'orch', "
            f"user wingmen) — e.g. lane_nudge over ssh — to submit its staged input and drain it.")
    print(subject, file=sys.stderr, flush=True)
    if dry_run:
        log(f"DRY-RUN would page {recipient}: {subject}")
        return
    try:
        c = _connect()
        cur = c.cursor()
        cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
        cur.execute("""INSERT INTO agent_messages (from_agent,to_agent,message_type,priority,subject,body)
                       VALUES ('cc-fleet-health',%s,'blocker','P2',%s,%s)""",
                    (recipient, subject, body))
        c.commit()
        cur.close()
        c.close()
        log(f"paged {recipient}: {agent} WEDGED-alive (needs cross-host ssh nudge)")
    except Exception as e:  # noqa: BLE001 — page best-effort; stderr banner already fired
        log(f"WARN wedged-alive page failed ({e}); stderr banner stands")


def _hb_age(cur, agent):
    cur.execute("SELECT extract(epoch from (now()-last_heartbeat)) FROM agent_status WHERE agent_id=%s", (agent,))
    r = cur.fetchone()
    return float(r[0]) if r and r[0] is not None else None


def sweep(*, dry_run=True):
    import psycopg2
    c = _connect(); cur = c.cursor()
    cur.execute("SELECT agent_id FROM protected_agents")
    protected = [r[0] for r in cur.fetchall()]
    checked = checked_agents(protected, sessions=CHECK_SESSIONS, self_agent=SELF_AGENT)
    state = _load_state()
    results = []
    for agent, session in checked.items():
        present = _tmux_has_session(session)          # raises on tmux failure => dead-man
        hb_age = _hb_age(cur, agent)
        verdict = classify_dead(tmux_present=present, hb_age_s=hb_age, threshold_s=THRESHOLD_S)
        results.append({"agent": agent, "session": session, "verdict": verdict, "hb_age_s": hb_age})
        already = state.get(agent, {}).get("paged_dead", False)
        if verdict == "dead" and not already:
            _page(agent, hb_age, dry_run, page_recipient(agent))
            state.setdefault(agent, {})["paged_dead"] = True
        elif verdict == "alive":
            if already:
                log(f"{agent} back ALIVE — re-arming")
            state.setdefault(agent, {})["paged_dead"] = False
        log(f"{agent} (session={session}): {verdict} (tmux={'up' if present else 'GONE'}, "
            f"hb_age={int(hb_age) if hb_age else '?'}s)")
    if not dry_run:
        _save_state(state)
    cur.close(); c.close()
    return results


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    dry_run = "--page" not in argv
    mode = "PAGE" if not dry_run else "DRY-RUN"
    try:
        results = sweep(dry_run=dry_run)
    except Exception as e:  # dead-man's-switch: a probe failure PAGES loud, never silent
        print(f"🔴 SINGLETON-LIVENESS PROBE FAILED ({type(e).__name__}): {e}", file=sys.stderr, flush=True)
        try:
            import psycopg2
            c = _connect(); cur = c.cursor()
            cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
            cur.execute("""INSERT INTO agent_messages (from_agent,to_agent,message_type,priority,subject,body)
                           VALUES ('cc-fleet-health','orch-console','blocker','P2',
                           'singleton-liveness monitor PROBE FAILED — not watching singletons',%s)""",
                        (f"{type(e).__name__}: {e}",))
            c.commit(); cur.close(); c.close()
        except Exception:
            pass
        return 1
    dead = [r["agent"] for r in results if r["verdict"] == "dead"]
    log(f"[{mode}] checked={len(results)} dead={dead or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
