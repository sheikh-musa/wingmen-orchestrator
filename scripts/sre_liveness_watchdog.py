#!/usr/bin/env python3
"""sre_liveness_watchdog.py — the watcher for the watchman.

WHY (2026-08-11, op#-flagged by the operator "is sre asleep or what"): cc-fleet-health
(the SRE) recycles every OTHER bloated/wedged body, but nothing recovers IT. It sat
idle-composer-WEDGED ~1h — its own next step ('run the self-heal diagnosis') staged in
the composer but never submitted — while its 5-min heartbeat kept renewing the
fleet_health_lease and thus kept it reading 'active' on every gauge. The existing
lane_wedge_watchdog can't cover this case: its recovery actions are fleet_health_lease-
gated to the SRE, so when the SRE is the wedged body the watchdog IS the wedged body.

This closes the 'who watches the watchmen' hole with the one property that matters:
INDEPENDENCE. It runs as a standalone launchd job (dev.wingmen.sre-liveness), NOT the
SRE's interactive agent and NOT tied to its heartbeat loop (the heartbeat is exactly
what MASKED the wedge — it is not trusted here as liveness).

WEDGE SIGNATURE for the SRE (tmux session 'fleet-health'), per cc-fleet-health's spec
(18101), evaluated via scripts/lib/composer_capture.sh — the fleet's ONE dim-ghost-vs-
real definition, reused by shell-out, never reimplemented:
  (a) composer holds REAL staged/queued text (CC_EMPTY!=1, non-dim) OR the pane shows a
      queued-message / nav-menu footer, AND the pane is NOT busy ('esc to interrupt'
      absent) — i.e. work typed but never submitted; OR
  (b) pane idle + composer empty, BUT unread agent_messages to_agent='cc-fleet-health'
      exist older than UNREAD_MIN_AGE_SEC — i.e. work waiting, the SRE not moving.
A busy pane ('esc to interrupt') is a LIVE turn => never a wedge. An empty composer with
an empty inbox is healthy-idle => never a wedge. A GRACE window (same wedge seen on 2
consecutive scans) keeps a normal between-turns pause from ever tripping it.

RECOVERY (escalating, phantom-safe — never a raw send-keys into the SRE's composer):
  - NUDGE: post a P1 requires_response bus row to cc-fleet-health from this watcher's own
    id. The Mini realtime auto-wake fires on that NEW arrival — the exact path that
    recovered the SRE by hand. Posting a row never clobbers staged text (unlike a
    send-keys append), so it is safe even under signature (a). Up to NUDGE_MAX per
    episode, NUDGE_INTERVAL_SEC apart.
  - ESCALATE: if still wedged after NUDGE_MAX nudges, a self-wedge the nudge can't clear
    needs a human (same dead-man doctrine as the reset hardening). Post a LOUD P1 rr row
    to orch-console AND page the operator directly via nazim_send — a safety page is
    never silenced by assuming a relay is alive.
  - SESSION GONE: if tmux 'fleet-health' does not exist, the SRE is DOWN (not wedged) —
    that needs a reboot (operator/hub infra), so escalate immediately.

DEAD-MAN'S-SWITCH (feedback_monitors_need_deadmans_switch): every scan updates a state
file; any unhandled exception is logged loudly and pages the operator via the dependency-
free nazim_send path — a blind watcher must SAY so, never fail silent.

STATUS: this watcher is REVIEWED by cc-fleet-health (CAI-779 symmetry — it reviews the
body-reset changes that touch it; it reviews this one that nudges it) before the launchd
job is loaded. Detection is always safe; the nudge/escalate actions ship behind --arm.
"""
from __future__ import annotations
import json, os, subprocess, sys, time, pathlib

ORCH = pathlib.Path.home() / "wingmen" / "orchestrator"
COMPOSER_LIB = ORCH / "scripts" / "lib" / "composer_capture.sh"
NAZIM_SEND = ORCH / "scripts" / "nazim_send.sh"
STATE = ORCH / "logs" / "sre_liveness.state"

SESSION = "fleet-health"
AGENT = "cc-fleet-health"
WATCHER_ID = "sre-liveness"
# agent_messages.from_agent FKs to agents.id, and 'sre-liveness' is NOT a registered
# agent (unlike sla-watchdog) — so its bus posts used to FK-fail silently, breaking the
# escalation path (detect worked, act no-op'd). Post under the SRE's own registered id
# instead; the [sre-liveness] tag in subject/body preserves attribution. Ops-not-governance:
# no agents-registry write (that stays a cai/operator-gated change). Fix: Nazim op#30671.
POST_FROM = AGENT  # 'cc-fleet-health' — registered; satisfies the from_agent FK
TMUX = "/usr/local/bin/tmux" if os.path.exists("/usr/local/bin/tmux") else "tmux"

UNREAD_MIN_AGE_SEC = 20 * 60      # signal (b): work waiting this long = not moving
GRACE_SCANS = 8                   # same wedge on N consecutive scans before acting.
                                  # TEMP bumped 2->4->8 (2026-08-21): the wedge-staged verdict
                                  # currently false-positives on cc-fleet-health's own idle
                                  # OUTPUT text (capture-pane -p strips the dim marker) — see
                                  # memory sre-liveness-nudge-fk-fail. Extra grace cuts the
                                  # false self-wake burn without masking a persistent real
                                  # wedge (the 2026-08-11 case was ~1h). REVERT to 2 once the
                                  # composer_capture output-vs-staged discrimination is fixed.
NUDGE_MAX = 2                     # nudges per episode before escalating to a human
NUDGE_INTERVAL_SEC = 10 * 60      # min gap between nudges (and detection cadence budget)
ESCALATE_REPAGE_SEC = 60 * 60     # don't re-page the operator more than hourly per episode


def _log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}", flush=True)


def _dsn():
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL") \
        or os.environ.get("ORCHESTRATOR_DATABASE_URL")


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    st["last_scan"] = int(time.time())
    STATE.write_text(json.dumps(st))


def _session_exists() -> bool:
    try:
        subprocess.run([TMUX, "has-session", "-t", SESSION], check=True,
                       capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def _observe() -> dict:
    """Return {busy, empty, n, partial, menu, flat} for the SRE pane, via the shared
    composer_capture.sh definition + a queued/nav-menu footer grep. Read-miss fails
    safe to 'unreadable' so we never assert a wedge we could not actually see."""
    snippet = (
        '. "$1" || exit 9; composer_parse_pane "$2" "$3" >/dev/null 2>&1; '
        'pane_busy "$2" "$3" >/dev/null 2>&1; '
        'menu=0; "$2" capture-pane -t "$3" -p 2>/dev/null | tail -8 | '
        'LC_ALL=C grep -qiE "press up to edit queued|to navigate|esc to cancel|enter to select" && menu=1; '
        'printf "RESULT %s %s %s %s %s %s\\n" "${CC_EMPTY:-x}" "${CC_N:-x}" "${CC_PARTIAL:-x}" "${CC_BUSY:-0}" "$menu" "${CC_PH_BASIS:-x}"; '
        'printf "FLAT %s\\n" "${CC_FLAT:-}"'
    )
    try:
        r = subprocess.run(["bash", "-c", snippet, "_", str(COMPOSER_LIB), TMUX, f"{SESSION}:0.0"],
                           capture_output=True, text=True, timeout=20)
    except Exception as e:
        return {"readable": False, "err": str(e)[:120]}
    empty = n = partial = "x"
    busy = menu = "0"
    ph_basis = "x"
    flat = ""
    for ln in (r.stdout or "").splitlines():
        if ln.startswith("RESULT "):
            p = ln.split()
            if len(p) >= 6:
                empty, n, partial, busy, menu = p[1], p[2], p[3], p[4], p[5]
            if len(p) >= 7:
                ph_basis = p[6]  # CC_PH_BASIS: no-content|dim-sgr|literal-fallback(+(real-text))
        elif ln.startswith("FLAT "):
            flat = ln[5:]
    return {"readable": partial != "noprompt" and empty != "x",
            "busy": busy == "1", "empty": empty == "1", "menu": menu == "1",
            "n": n, "partial": partial, "flat": flat, "ph_basis": ph_basis}


def _oldest_unread_age_sec() -> "float | None":
    dsn = _dsn()
    if not dsn:
        return None
    try:
        import psycopg
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT EXTRACT(EPOCH FROM now()-min(created_at)) FROM agent_messages "
                "WHERE to_agent=%s AND read_at IS NULL", (AGENT,))
            v = cur.fetchone()[0]
            return float(v) if v is not None else None
    except Exception as e:
        _log(f"WARN oldest-unread query failed: {e}")
        return None


import re as _re
# The grey autocomplete/hint placeholders Claude Code renders in an EMPTY composer.
# composer_capture.sh classifies dim via SGR codes, but `capture-pane -p` strips
# color, so it falls back to literal matching and these read as REAL staged text —
# a false wedge. Treat a bare bracketed hint as empty. (Flagged to cc-fleet-health
# for the shared lib; guarded here so the watcher never nudges a healthy idle SRE.)
_PLACEHOLDER_RE = _re.compile(r"^<[^>]{0,48}>$")
_PLACEHOLDER_EXACT = {"<no suggestion>", "<none>", ""}


def _is_placeholder(flat: str) -> bool:
    f = (flat or "").strip()
    return f in _PLACEHOLDER_EXACT or bool(_PLACEHOLDER_RE.match(f))


def _is_cosmetic_dim_idle(ph_basis: str, piling: bool) -> bool:
    """Arm-gating (Nazim #31259): a DIM-ghost-suspect composer with NOTHING piling in the
    inbox is cosmetic idle-with-a-ghost, NOT a wedge worth a self-wake — lane_nudge's ACTIVE
    probe clears such a ghost on the next real wake. Suppress the self-nudge ONLY on BOTH
    signals (dim AND no unread piling). A NOT-dim composer (possible real stuck — the ENOSPC
    composer-corruption class) or a piling inbox is NOT cosmetic and still nudges/escalates.
    Never trusts dim to mean empty: a real dim wedge that MATTERS surfaces when work piles
    (-> wedge-inbox). Note CC_PH_BASIS carries 'real-text(dim)' vs 'real-text(not-dim)', so
    the not-dim marker must be excluded explicitly (a bare 'dim' substring matches both)."""
    ph = ph_basis or ""
    is_dim = ("dim" in ph) and ("not-dim" not in ph)
    return is_dim and not piling


def _classify() -> "tuple[str, str]":
    """Return (verdict, detail). verdict in
    {healthy, busy, unreadable, gone, wedge-staged, wedge-inbox}."""
    if not _session_exists():
        return "gone", "tmux session 'fleet-health' absent — SRE is DOWN, not wedged"
    obs = _observe()
    if not obs.get("readable", False):
        return "unreadable", f"pane read miss ({obs.get('partial') or obs.get('err')}) — fail-safe, no assert"
    if obs["busy"]:
        return "busy", "live turn ('esc to interrupt') — healthy"
    # (a) a queued-message footer, OR real staged text (not a grey placeholder), pane
    #     not busy = work typed but never submitted.
    if obs["menu"]:
        return "wedge-staged", "queued-message / nav-menu footer, not busy"
    if (not obs["empty"]) and not _is_placeholder(obs.get("flat", "")):
        # Arm-gating: don't self-wake on a cosmetic dim-ghost when nothing is piling.
        _age = _oldest_unread_age_sec()
        _piling = _age is not None and _age >= UNREAD_MIN_AGE_SEC
        if _is_cosmetic_dim_idle(obs.get("ph_basis", ""), _piling):
            return "healthy", (f"dim-ghost composer + no inbox piling — cosmetic idle-with-ghost, "
                               f"not a wedge worth a wake (flat='{obs['flat'][:50]}' "
                               f"ph_basis={obs.get('ph_basis','x')})")
        return "wedge-staged", (f"real staged composer, not busy (flat='{obs['flat'][:80]}' "
                                f"ph_basis={obs.get('ph_basis','x')} n={obs.get('n','x')})")
    # (b) composer empty + inbox piling
    age = _oldest_unread_age_sec()
    if age is not None and age >= UNREAD_MIN_AGE_SEC:
        return "wedge-inbox", f"empty composer but unread inbox {int(age//60)}m old (>= {UNREAD_MIN_AGE_SEC//60}m)"
    return "healthy", "empty composer, no piling inbox — healthy-idle"


def _nudge(episode: str, verdict: str) -> bool:
    dsn = _dsn()
    if not dsn:
        _log("cannot nudge — no DSN")
        return False
    body = (f"[sre-liveness] You look WEDGED ({verdict}) — this row triggers your realtime "
            f"auto-wake (the path that recovered you on 2026-08-11). Drain agent_messages "
            f"(to_agent='cc-fleet-health') + resume. If this row IS the thing you were stuck "
            f"unable to submit, ignore it and carry on. Episode {episode}.")
    try:
        import psycopg
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,priority,requires_response)"
                " VALUES (%s,%s,'update',%s,%s,'P1',true)",
                (POST_FROM, AGENT, "[sre-liveness] wedge nudge — auto-wake trigger", body))
            conn.commit()
        _log(f"NUDGE posted to {AGENT} (episode {episode}, verdict {verdict})")
        return True
    except Exception as e:
        _log(f"nudge post failed: {e}")
        return False


def _escalate(episode: str, verdict: str, nudges: int) -> None:
    dsn = _dsn()
    human = (f"SRE (cc-fleet-health) appears WEDGED and {nudges} auto-wake nudge(s) did NOT "
             f"clear it — needs a human. Verdict: {verdict}. It recycles everyone else but "
             f"cannot recover itself; a nudge-proof self-wedge is the reset-hardening dead-man "
             f"class. Check tmux 'fleet-health' on the Mini (may need a clean relaunch). Episode {episode}.")
    # Loud bus row to orch-console (I add context + relay)...
    if dsn:
        try:
            import psycopg
            with psycopg.connect(dsn) as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,priority,requires_response)"
                    " VALUES (%s,'orch-console','blocker',%s,%s,'P1',true)",
                    (POST_FROM, "SRE nudge-proof WEDGE — needs human relaunch", human))
                conn.commit()
        except Exception as e:
            _log(f"escalate bus row failed: {e}")
    # ...AND page the operator directly — a safety page is never silenced by assuming a relay is alive.
    try:
        subprocess.run([str(NAZIM_SEND), human, "sre-liveness-escalate"],
                       cwd=str(ORCH), capture_output=True, timeout=30)
    except Exception as e:
        _log(f"operator page failed: {e}")
    _log(f"ESCALATED (episode {episode}, verdict {verdict}, nudges {nudges})")


def main() -> int:
    armed = "--arm" in sys.argv          # detect-only unless armed (ships safe)
    st = _load_state()
    now = int(time.time())
    verdict, detail = _classify()
    _log(f"{verdict}: {detail}")

    if verdict in ("healthy", "busy", "unreadable"):
        # any clean/uncertain read ends the episode — never carry a stale wedge count
        if st.get("episode"):
            _log(f"episode {st['episode']} cleared ({verdict})")
        st = {}
        _save_state(st)
        return 0

    # verdict is a wedge (or 'gone'). GRACE: require the same wedge on consecutive scans.
    ep = st.get("episode") or f"{now}"
    if st.get("last_verdict") == verdict:
        streak = int(st.get("streak", 0)) + 1
    else:
        streak = 1
    st.update(episode=ep, last_verdict=verdict, streak=streak)

    # GRACE applies to 'gone' too (cc-fleet-health review 18115): a single tmux
    # has-session miss can be a transient blip, so require it sustained before we
    # page 'SRE is down'. 'gone' still skips the nudge leg below (can't nudge a
    # dead session) and escalates once confirmed.
    if streak < GRACE_SCANS:
        _log(f"grace: {verdict} streak {streak}/{GRACE_SCANS} — waiting to confirm sustained")
        _save_state(st)
        return 0

    if not armed:
        _log(f"DETECT-ONLY (unarmed): would act on {verdict} (episode {ep})")
        _save_state(st)
        return 0

    nudges = int(st.get("nudges", 0))
    last_nudge = int(st.get("last_nudge_at", 0))
    if verdict != "gone" and nudges < NUDGE_MAX and (now - last_nudge) >= NUDGE_INTERVAL_SEC:
        if _nudge(ep, verdict):
            st["nudges"] = nudges + 1
            st["last_nudge_at"] = now
        _save_state(st)
        return 0

    # escalate: nudges exhausted (or SESSION GONE), still wedged — page a human (rate-limited)
    last_esc = int(st.get("last_escalate_at", 0))
    if (now - last_esc) >= ESCALATE_REPAGE_SEC:
        _escalate(ep, verdict, nudges)
        st["last_escalate_at"] = now
    else:
        _log(f"still wedged ({verdict}) but re-page cooldown active — logged only")
    _save_state(st)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        _log(f"FATAL: {e}")
        try:
            subprocess.run([str(NAZIM_SEND),
                            f"sre-liveness watchdog CRASHED ({str(e)[:100]}) — the SRE watcher is BLIND; check it.",
                            "sre-liveness-deadman"], cwd=str(ORCH), capture_output=True, timeout=30)
        except Exception:
            pass
        sys.exit(1)
