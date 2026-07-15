#!/usr/bin/env python3
"""lane_watchdog.py — autonomous fleet progress-watchdog (CAI-RESP-284).

THE PROBLEM it solves: the fleet is coordinated through cc-orchestrator (the hub).
When the hub is busy, lanes that stall sit unnoticed until a manual sweep — shipforge
sat idle ~a day on 2026-06-20; storefront/cosem-tdu/mirror repeatedly stalled on
unsent keystrokes. This watchdog runs on a launchd timer INDEPENDENT of the hub's
attention and keeps every lane on track.

WHAT IT DOES per scan (every ~5 min):
  - WORKING ('esc to interrupt')        -> leave alone (NEVER touch a working lane).
  - FROZEN at folder-trust prompt        -> auto-answer (Enter = trust) immediately.
  - IDLE + UNSENT INPUT (the dumb stall) -> verified-submit (Enter, confirm it took;
        retry once; else escalate). Acts ONLY if the SAME unsent text persisted since
        the previous scan (≈5 min) — avoids firing on a lane mid-keystroke.
  - ON A DECISION DIALOG ('Enter to select') -> ESCALATE to the hub (needs judgment;
        never auto-pick an option).
  - IDLE-CLEAN (empty prompt)            -> leave alone (legitimately done/awaiting).

SAFETY (cai HARD RULE 1, CAI-RESP-284): recovery NEVER depends on a bare blind
send-keys — every auto-submit is VERIFIED (re-capture, confirm the pane entered a
working state) and escalates if it can't. Working lanes are never disturbed.

Escalations + actions are logged (logs/lane_watchdog.log) and posted to the bus
(agent_messages -> cc-orchestrator) so the live hub sees them next turn.
State persists in logs/lane_watchdog_state.json (for the cross-scan persistence guard).
"""
from __future__ import annotations
import hashlib, json, os, re, subprocess, sys, time
from pathlib import Path
from datetime import datetime, timezone

ORCH = Path(os.path.expanduser("~/wingmen/orchestrator"))
STATE_FILE = ORCH / "logs" / "lane_watchdog_state.json"
LOG_FILE = ORCH / "logs" / "lane_watchdog.log"

# Watch every live tmux lane by DENYLIST, not a hardcoded allowlist — the
# allowlist silently failed to watch new/worktree sessions (e.g. 'cosem-adcda-2'
# ran 3h dark on 2026-07-02 because it wasn't in the set). Now: watch all live
# sessions EXCEPT the operator-attended hub and transient reviewers.
# NEVER watch the orchestrator BODIES — 'orch'/'orchestrator' (Studio hub) and
# 'nazim' (console/CTO body, ORCH-TOPOLOGY-001). Neither is a build lane; both
# are conversational consoles where a watchdog keystroke is the 2026-07-03
# phantom-injection class (cai was the specimen; nazim is the same identity
# category — an orch body, not a lane). 'reviewer-*' etc. is transient.
NON_LANE_SESSIONS = {"orch", "orchestrator", "nazim"}
NON_LANE_PREFIXES = ("reviewer-", "billingtest", "catest")

# GOVERNANCE / CONVERSATIONAL CONSOLES (CAI-RESP-381): these are watched for
# liveness but must NEVER receive an auto-submit / auto-answer send-keys. On
# 2026-07-03 the watchdog treated 'cai' as a build lane and re-submitted staged
# 'YES PURGE' text into the governance console every 300s (its verifier couldn't
# confirm cai's TUI, so recovered=False forever = infinite replay) — 11 phantom
# authorization claims. An agent injecting fabricated operator input into a
# governance console is the worst identity-doctrine violation possible, so for
# these sessions the ONLY sanctioned action is escalation (bus-notify), never a
# keystroke. cai/orch, any ai-responder persona console, belong here.
# 'support' (cc-support, op#4537) is a client-facing conversational agent — a
# phantom keystroke there could send garbage to the LIVE irsyad/Gazzabyte client,
# the worst-case blast radius. Idle-until-a-client-message is its NORMAL state
# (the empty-REPL placeholder reads as IDLE_UNSENT, a false positive). Watch it
# for liveness, NEVER auto-submit.
GOVERNANCE_CONSOLES = {"cai", "support"}
GOVERNANCE_PREFIXES = ("mamadah", "nutri", "mizan")   # ai-responder persona consoles

# Anti-replay cap (CAI-RESP-381): a given unsent input gets at most this many
# verified auto-submit attempts before the watchdog escalates and HOLDS — it
# will not re-fire the same text on subsequent scans until the input changes.
MAX_AUTOSUBMIT_ATTEMPTS = 1
IDLE_NUDGE_THROTTLE = 600   # re-nudge an idle-with-unread-work lane at most once/10min
ORCH_ESC_NUDGE_THROTTLE = 300  # wake the 24/7 orch to ACTION escalations at most once/5min

# PROGRESS-STALL (op #3695): the blind spot behind cc-cosem-platform's 16h stall.
# A lane can look WORKING (stale status='working' + fresh heartbeat) OR sit
# IDLE_CLEAN with an empty composer and make ZERO real progress — evading
# IDLE_UNSENT (needs composer text) and WORKING (never touched). Detect on real
# PROGRESS (a new bus post / pane-scrollback delta / new commit), never on the
# heartbeat that lies. Flag ONLY if the lane ALSO holds unactioned work; escalate-
# only (never a keystroke). Tuned generous so a genuinely-busy long build (which
# streams scrollback / posts / commits) never trips it.
PROGRESS_STALL_SEC = 1200     # no progress this long = candidate stall (20 min)
STALL_REESCALATE_SEC = 1800   # re-escalate a persisting stall at most once/30min
_escalation_count = 0       # escalations posted THIS scan (escalate() increments)

def is_governance_console(sess: str) -> bool:
    return sess in GOVERNANCE_CONSOLES or sess.startswith(GOVERNANCE_PREFIXES)


def unread_bus_work(sess: str) -> int:
    """Count UNREAD agent_messages addressed to this lane's base agent. An
    IDLE-CLEAN lane with unread dispatched work has stalled without picking it
    up — the overnight-stall class. Maps tmux session → fleet_lanes.base_agent_id."""
    try:
        sys.path.insert(0, str(ORCH))
        from dotenv import load_dotenv
        import psycopg
        load_dotenv(str(ORCH / ".env"))
        dsn = os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL")
        with psycopg.connect(dsn, connect_timeout=10) as c, c.cursor() as cur:
            cur.execute("SELECT base_agent_id FROM fleet_lanes WHERE lane=%s", (sess,))
            row = cur.fetchone()
            if not row or not row[0]:
                return 0
            # RECENT + ACTIONABLE unread only. Lanes rarely mark_read, so their
            # unread backlog is huge (97+) regardless of priority — counting all of
            # it would nudge every idle lane forever. The stall class is: fresh work
            # dispatched, lane idled without picking it up. Catching it within the
            # window (watchdog runs every 5min) prevents a 30min blip becoming a 7h
            # stall, without re-nudging on stale backlog.
            cur.execute(
                "SELECT count(*) FROM agent_messages WHERE to_agent=%s AND read_at IS NULL "
                "AND (requires_response OR priority='P1') "
                "AND created_at > now() - interval '45 minutes'", (row[0],))
            return cur.fetchone()[0]
    except Exception as e:
        log(f"unread-bus-work-failed {sess}: {e}")
        return 0

def scrollback_digest(cap: str) -> str:
    """Hash the pane content ABOVE the composer glyph line, so the animated input
    widget + footer (spinner, elapsed timer, 'esc to interrupt (Ns · ↑ tokens)')
    do NOT read as progress — only real streamed conversation output (new
    scrollback) moves the digest. An idle or wedged pane has a static scrollback →
    static digest; a genuinely-working lane streams new lines → digest changes."""
    lines = cap.splitlines()
    cut = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].lstrip().startswith("❯"):   # ❯ composer glyph
            cut = i
            break
    if cut is None:
        cut = max(0, len(lines) - 3)                 # no composer found: drop the volatile tail
    body = "\n".join(l.rstrip() for l in lines[:cut]).strip()
    return hashlib.sha1(body.encode("utf-8", "ignore")).hexdigest()[:16]


def lane_progress_state(sess: str):
    """(last_bus_id, last_commit_sha, unactioned_directives) for the lane's base
    agent. Real progress = a NEW bus post OR a NEW commit. 'unactioned' = a
    requires_response directive to this lane still UNANSWERED — WIDER than the
    45min recent-unread window (unactioned_bus_work), because the 16h stall was
    work that had aged out of that window. DB failure => unactioned=0 (fail toward
    NOT crying wolf: never manufacture a stall from a DB blip)."""
    try:
        sys.path.insert(0, str(ORCH))
        from dotenv import load_dotenv
        import psycopg
        load_dotenv(str(ORCH / ".env"))
        dsn = os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL")
        with psycopg.connect(dsn, connect_timeout=10) as c, c.cursor() as cur:
            cur.execute("SELECT base_agent_id FROM fleet_lanes WHERE lane=%s", (sess,))
            row = cur.fetchone()
            if not row or not row[0]:
                return ("", "", 0)
            aid = row[0]
            cur.execute("SELECT coalesce(max(id),0) FROM agent_messages WHERE from_agent=%s", (aid,))
            last_bus = cur.fetchone()[0]
            cur.execute("SELECT last_commit_sha FROM agent_status WHERE agent_id=%s OR base_agent_id=%s "
                        "ORDER BY last_heartbeat DESC NULLS LAST LIMIT 1", (aid, aid))
            r = cur.fetchone()
            last_commit = (r[0] if r else "") or ""
            cur.execute("SELECT count(*) FROM agent_messages WHERE to_agent=%s AND requires_response "
                        "AND responded_at IS NULL AND skipped_at IS NULL "
                        "AND created_at > now() - interval '24 hours'", (aid,))
            unactioned = cur.fetchone()[0]
            return (str(last_bus), last_commit, unactioned)
    except Exception as e:
        log(f"progress-probe-failed {sess}: {e}")
        return ("", "", 0)


_TOK_RE = re.compile(r"([\d][\d.,]*\s*k?)\s*tokens", re.IGNORECASE)


def footer_tokens(cap: str) -> str:
    """The pane's live token counter(s) (e.g. '↑ 1.2k tokens') from the footer —
    it RISES as the model GENERATES (real work), so a busy build advances it every
    scan. Deliberately NOT the elapsed timer (which ticks regardless of work). A
    frozen counter = not generating; an idle pane has no counter (empty)."""
    tail = "\n".join(cap.splitlines()[-4:])
    return ",".join(m.replace(" ", "") for m in _TOK_RE.findall(tail))


def progress_fingerprint(sess: str, cap: str):
    """(fingerprint, unactioned). Progress = advance in ANY of FOUR independent
    signals — a genuinely-busy lane moves at least one every scan, so it can't be
    a false positive: pane scrollback (streamed output), pane TOKEN-DELTA (model
    generating), latest bus post id, last commit sha. Never the heartbeat."""
    last_bus, last_commit, unactioned = lane_progress_state(sess)
    return f"{scrollback_digest(cap)}|{footer_tokens(cap)}|{last_bus}|{last_commit}", unactioned


def progress_stalled(stalled_sec: float, unactioned: int) -> bool:
    """Pure decision: a lane is stalled only when it has made no progress for the
    window AND is holding work it should be doing (so an idle-and-DONE lane with no
    pending directive is never flagged)."""
    return stalled_sec > PROGRESS_STALL_SEC and unactioned > 0


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} | {msg}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def tmux(*args: str) -> str:
    try:
        return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return ""

def live_sessions() -> list[str]:
    out = tmux("list-sessions", "-F", "#{session_name}")
    lanes = []
    for s in out.splitlines():
        if not s or s in NON_LANE_SESSIONS or s.startswith(NON_LANE_PREFIXES):
            continue
        lanes.append(s)
    return lanes

def capture(sess: str) -> str:
    return tmux("capture-pane", "-t", sess, "-p")

def input_line(cap: str) -> str:
    # the Claude-Code TUI input line starts with the prompt glyph
    for ln in reversed(cap.splitlines()):
        s = ln.strip()
        if s.startswith("❯"):           # ❯
            return s[1:].strip()
    return ""

def classify(cap: str) -> str:
    low = cap.lower()
    footer = "\n".join(l for l in cap.splitlines() if l.strip())[-400:].lower()
    # token-window wall signal (the real indicator we're hitting the Max limit) —
    # check FIRST, it can appear over a 'working' or idle pane.
    if ("usage limit" in low or "limit will reset" in low
            or "reached your usage" in low or "approaching your usage" in low):
        return "USAGE_LIMIT"
    if "esc to interrupt" in footer:
        return "WORKING"
    if "trust this folder" in low or "do you trust" in low or "yes, i trust" in low:
        return "TRUST_PROMPT"
    if "enter to select" in footer:
        return "ON_DIALOG"
    if "for agents" in footer:                 # idle footer
        return "IDLE_UNSENT" if input_line(cap) else "IDLE_CLEAN"
    return "UNKNOWN"

def verified_enter(sess: str) -> bool:
    """Bare Enter (for the folder-trust prompt, where the 'Yes' option is
    pre-highlighted). Confirm the pane left the prompt; retry once."""
    for _ in range(2):
        tmux("send-keys", "-t", sess, "Enter")
        time.sleep(4)
        if "esc to interrupt" in capture(sess).lower():
            return True
    return False

def verified_resubmit(sess: str, text: str) -> bool:
    """Reliably (re)submit a lane's own unsent prompt. Bare Enter is unreliable
    (proven 2026-06-20) — use scripts/lane_nudge.sh (clear → retype → Enter →
    VERIFY it entered a working state; retries; exit 0 = confirmed submitted)."""
    if not text:
        return verified_enter(sess)
    try:
        r = subprocess.run([str(ORCH / "scripts" / "lane_nudge.sh"), sess, text],
                           capture_output=True, text=True, timeout=90)
        return r.returncode == 0
    except Exception:
        return False

def escalate(sess: str, state: str, detail: str) -> None:
    global _escalation_count
    _escalation_count += 1
    log(f"ESCALATE {sess}: {state} — {detail}")
    try:
        sys.path.insert(0, str(ORCH))
        from dotenv import load_dotenv
        import psycopg
        load_dotenv(str(ORCH / ".env"))
        dsn = os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL")
        with psycopg.connect(dsn, connect_timeout=10) as c, c.cursor() as cur:
            cur.execute(
                """insert into agent_messages (from_agent,to_agent,message_type,subject,body,requires_response,priority)
                   values ('cc-orchestrator','cc-orchestrator','update',%s,%s,false,'P2')""",
                (f"[watchdog] lane '{sess}' needs attention: {state}",
                 f"lane_watchdog: '{sess}' is {state}. {detail} (Auto-recovery not applicable / exhausted — hub judgment needed.)"))
            c.commit()
    except Exception as e:
        log(f"escalate-bus-failed {sess}: {e}")


def nudge_orch_escalations(n: int) -> bool:
    """Wake the ALWAYS-ON Studio orch to ACTION accumulated lane escalations
    (operator directive 2026-07-04: the 24/7 orch owns escalation-action, not the
    laptop-bound direct session). Clean count-nudge ONLY — C-u clears any stuck
    draft first (phantom-injection-safe, no text-submit), fired only when the orch
    is IDLE so it never interrupts real work. Best-effort: the escalations are
    already durable on the bus; this just wakes the actioner."""
    line = (f"\U0001F4E5 {n} lane escalation(s) need action — drain your agent_messages "
            f"(watchdog 'needs attention' items) and act on / route each. You own these now.")
    try:
        if subprocess.run(["tmux", "has-session", "-t", "=orch"],
                          capture_output=True, timeout=5).returncode != 0:
            return False
        tmux("send-keys", "-t", "=orch:0.0", "C-u")     # clear any stuck draft
        time.sleep(0.3)
        tmux("send-keys", "-t", "=orch:0.0", "-l", line)
        time.sleep(0.5)
        tmux("send-keys", "-t", "=orch:0.0", "Enter")
        return True
    except Exception:
        return False


def main() -> int:
    global _escalation_count
    _escalation_count = 0
    try:
        state = json.loads(STATE_FILE.read_text())
    except Exception:
        state = {}
    new_state = {}
    for sess in live_sessions():
        cap = capture(sess)
        st = classify(cap)
        inp = input_line(cap)
        prev = state.get(sess, {})
        # carry the per-input auto-submit attempt counter + held-escalation flag
        # forward while the unsent text is unchanged (anti-replay); a changed
        # input resets both so a genuinely new prompt gets a fresh nudge.
        same_input = prev.get("input") == inp
        attempts = prev.get("attempts", 0) if same_input else 0
        held_escalated = prev.get("held_escalated", False) if same_input else False
        new_state[sess] = {"state": st, "input": inp, "ts": time.time(),
                           "attempts": attempts, "held_escalated": held_escalated}

        # ── PROGRESS-STALL (op #3695) — runs for EVERY watched lane, incl. WORKING
        #    and IDLE_CLEAN (the two the classify-path skips). Progress is measured
        #    by real advance (bus post / pane scrollback / commit), NOT the heartbeat
        #    that can lie 'working' for 16h. Escalate-only; never a keystroke.
        #    Governance consoles are excluded (not build lanes; escalate-only class).
        if not is_governance_console(sess):
            fp, unactioned = progress_fingerprint(sess, cap)
            progressed = fp != prev.get("progress_fp")
            prog_ts = time.time() if progressed else prev.get("progress_ts", time.time())
            new_state[sess]["progress_fp"] = fp
            new_state[sess]["progress_ts"] = prog_ts
            stalled_sec = time.time() - prog_ts
            stall_esc_ts = prev.get("stall_esc_ts", 0)
            if progress_stalled(stalled_sec, unactioned):
                if time.time() - stall_esc_ts > STALL_REESCALATE_SEC:
                    escalate(sess, "PROGRESS_STALL",
                             f"no real progress (bus/pane/commit) for {int(stalled_sec/60)}min while "
                             f"holding {unactioned} unactioned directive(s) — status/heartbeat may be "
                             f"lying (looks working or idle-clean, empty composer, the 16h-stall class). "
                             f"Verify the lane + kick or reassign; do NOT trust status='working'.")
                    stall_esc_ts = time.time()
                new_state[sess]["stall_esc_ts"] = stall_esc_ts
            else:
                new_state[sess]["stall_esc_ts"] = 0

        if st == "USAGE_LIMIT":
            # the token-window wall — escalate every scan (it's the one thing the
            # operator explicitly wants caught before a forced reset-wait)
            escalate(sess, st, "Claude usage-limit warning visible — pace/throttle the fleet NOW (pause speculative lanes, protect revenue + time-sensitive).")
            continue
        if st in ("WORKING", "UNKNOWN"):
            continue

        # GOVERNANCE / CONVERSATIONAL CONSOLES (CAI-RESP-381): escalate ONLY,
        # never a keystroke — no auto-submit, no auto-answer. This is the hard
        # exclusion that makes the 2026-07-03 phantom-injection class impossible.
        if is_governance_console(sess):
            if st not in ("IDLE_CLEAN",) and prev.get("state") != st:  # idle gov = fine, no noise
                escalate(sess, st, f"governance console in state {st} — NOT auto-actioned (keystroke injection forbidden here); hub judgment only")
            continue

        if st == "IDLE_CLEAN":
            # Legitimately idle — BUT if the lane has UNREAD dispatched work on its
            # bus, it stalled without picking it up (the overnight-stall class,
            # 2026-07-04: lanes idled for 7h on queued work while the hub was away).
            # Nudge it to drain, throttled so an ignored message isn't spammed. This
            # is what keeps the fleet moving without live babysitting.
            new_state[sess]["idle_nudge_ts"] = prev.get("idle_nudge_ts", 0)
            n = unread_bus_work(sess)
            if n > 0 and (time.time() - prev.get("idle_nudge_ts", 0)) > IDLE_NUDGE_THROTTLE:
                ok = verified_resubmit(sess, f"\U0001F4E5 {n} unread agent_messages addressed to you — drain your inbox and act on them (mark read).")
                new_state[sess]["idle_nudge_ts"] = time.time()
                log(f"{sess}: IDLE_CLEAN + {n} unread bus msgs — nudged to drain (recovered={ok})")
            continue

        if st == "TRUST_PROMPT":
            ok = verified_enter(sess)   # answering trust → returns to working/ready
            log(f"{sess}: TRUST_PROMPT auto-answered (recovered={ok})")
            if not ok:
                escalate(sess, st, "folder-trust prompt would not clear")
            continue
        if st == "ON_DIALOG":
            # needs a human/hub decision — escalate, but only once it's persisted a scan
            if prev.get("state") == "ON_DIALOG":
                escalate(sess, st, "sitting on a decision dialog across ≥2 scans — pick an option")
            continue
        if st == "IDLE_UNSENT":
            # NEVER auto-submit unsent text. Pressing Enter on a lane's own draft
            # feeds it text the lane then reads as an OPERATOR directive it never
            # received — the phantom-injection class, TWICE now via this exact path:
            #   07-03: auto-resubmit into the cai console ("YES PURGE").
            #   07-04: the mirror/cc-ihsanos lane's unsent 'override the 084 gate,
            #          apply now' was auto-submitted -> a MONEY migration applied to
            #          production on a fabricated operator override.
            # The old attempt-cap only bounded infinite REPLAY; the SINGLE submit is
            # the injury. There is no safe number of auto-submits of unsent text.
            # So: ESCALATE ONLY — a human/hub reads the draft and decides. The
            # watchdog never keystrokes unsent text into any lane, ever.
            if prev.get("state") == "IDLE_UNSENT" and prev.get("input") == inp and inp:
                if not held_escalated:
                    escalate(sess, st, f"unsent prompt sitting ≥2 scans — NOT auto-submitted (phantom-injection guard, 07-04 084 incident); hub/operator must read + decide: {inp[:100]!r}")
                    new_state[sess]["held_escalated"] = True
                else:
                    log(f"{sess}: IDLE_UNSENT held (escalated once, NEVER auto-submitted) input={inp[:60]!r}")
            else:
                log(f"{sess}: IDLE_UNSENT (first sight — will escalate next scan, never auto-submit) input={inp[:60]!r}")

    # Wake the ALWAYS-ON orch to ACTION escalations (operator directive 07-04): the
    # watchdog DETECTS stalls but only ACTIONS them by waking the 24/7 orch — so an
    # escalation no longer sits unread until the laptop-bound session happens to look.
    # Fired only when the orch is idle + throttled; a persistent stall re-escalates
    # each scan so the nudge recurs until the orch clears it.
    last_orch_nudge = state.get("_orch_esc_nudge_ts", 0)
    new_state["_orch_esc_nudge_ts"] = last_orch_nudge
    if _escalation_count > 0 and (time.time() - last_orch_nudge) > ORCH_ESC_NUDGE_THROTTLE:
        try:
            orch_cap = capture("orch")
            if orch_cap and "esc to interrupt" not in orch_cap.lower():   # orch idle
                if nudge_orch_escalations(_escalation_count):
                    new_state["_orch_esc_nudge_ts"] = time.time()
                    log(f"orch: {_escalation_count} escalation(s) this scan — nudged the 24/7 orch to action")
        except Exception as e:
            log(f"orch-escalation-nudge-failed: {e}")

    reap_ghosts()
    log_burn()
    try:
        STATE_FILE.write_text(json.dumps(new_state))
    except Exception as e:
        log(f"state-write-failed: {e}")
    return 0

def reap_ghosts() -> None:
    """Audited periodic reap of stale agent_status ghosts (CAI-RESP-292).

    Instances that die uncleanly never run their clean-exit trap, so they linger
    as status='working' forever. This calls the identity-respecting SECURITY
    DEFINER sweeper to offline any non-offline row whose heartbeat is older than
    the 8h freshness bound, stamping reaped_by='lane_watchdog' (honest, attributed
    — never an anonymous forge, never disables the identity trigger, never touches
    a live lane)."""
    try:
        sys.path.insert(0, str(ORCH))
        from dotenv import load_dotenv
        import psycopg
        load_dotenv(str(ORCH / ".env"))
        dsn = os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL")
        with psycopg.connect(dsn, connect_timeout=10) as c, c.cursor() as cur:
            cur.execute("select reaped_agent_id, stale_hours "
                        "from sweep_stale_agents(interval '8 hours', 'lane_watchdog')")
            reaped = cur.fetchall()
            c.commit()
        if reaped:
            log("REAPED stale ghosts: " + ", ".join(f"{a}({h}h)" for a, h in reaped))
    except Exception as e:
        log(f"reap-ghosts-failed: {e}")

def log_burn() -> None:
    """Log the fleet's 5h OUTPUT-token burn (the limited resource) for trend
    visibility. No precise Max cap is queryable, so we track the trend + rely on
    the USAGE_LIMIT pane signal for the actual wall."""
    try:
        sys.path.insert(0, str(ORCH))
        from dotenv import load_dotenv
        import psycopg
        load_dotenv(str(ORCH / ".env"))
        dsn = os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL")
        with psycopg.connect(dsn, connect_timeout=10) as c, c.cursor() as cur:
            cur.execute("select coalesce(sum(coalesce(output_tokens,0)),0) "
                        "from cc_session_costs where created_at > now() - interval '5 hours'")
            out5h = float(cur.fetchone()[0] or 0)
        log(f"BURN 5h-output={out5h/1e6:.2f}M tokens")
    except Exception as e:
        log(f"burn-log-failed: {e}")

if __name__ == "__main__":
    raise SystemExit(main())
