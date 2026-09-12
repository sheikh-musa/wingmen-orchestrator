#!/usr/bin/env python3
"""sentry_new_error_watch.py — cc-fleet-health standing Sentry NEW-error watch (Musa op#19859).

"Make sure we are aware of all sentry errors going forward" (Nazim 39189/39191).
Polls the ihsanos Sentry project for unresolved issues, SURFACES each genuinely-new
SERVER/app issue ONCE to the fleet bus (orch-console) for triage, and ESCALATES only
the MATERIAL ones to the operator (paged ONCE per issue; re-escalate only on a real
state change — reopened or a fresh spike — never every tick).

FILTERS client-side browser noise (visitor wallet/extension errors): MetaMask,
"Receiving end does not exist", "<unknown>", chrome/moz-extension. NOISE_PATTERNS is
in-script + easy to extend.

DEDUP (id-based, survives regressions better than a ts-cursor): state file tracks
  reported_ids  — surfaced-once set
  escalated     — {issue_id: count_at_last_escalation} for the page-once/spike logic
Issue ids no longer unresolved are PRUNED, so a resolved→reopened issue re-fires.

FAIL-SOFT / DEAD-MAN: any Sentry API error prints LOUD ("could not poll Sentry: …")
and exits non-zero WITHOUT advancing state — never silently goes dark (charter #1).

DEPLOYS INERT: sends/mutates state only when SENTRY_WATCH_ENABLED=1 (else scans + logs
WOULD-* and touches nothing). Its launchd job ships gated until the arm.

Usage:
  python3 scripts/sentry_new_error_watch.py --selftest   # prove eval logic (no network)
  python3 scripts/sentry_new_error_watch.py --dry-run    # poll + decide, print, no send/state
  python3 scripts/sentry_new_error_watch.py --json       # machine-readable
  python3 scripts/sentry_new_error_watch.py              # live (only sends when ENABLED)
Env: SENTRY_AUTH_TOKEN, SENTRY_ORG, SENTRY_PROJECT (+ DATABASE_URL for sends).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ORCH = Path(__file__).resolve().parent.parent

# ── tunables (env-overridable; patterns extend easily) ───────────────────────
NOISE_PATTERNS = [p.lower() for p in (
    "MetaMask",
    "Receiving end does not exist",
    "Could not establish connection",   # extension messaging
    "<unknown>",
    "chrome-extension",
    "moz-extension",
    "extension context invalidated",
)]
# Material-by-content: server/app failures we DO want the operator to see.
SERVER_ERROR_PATTERNS = [p.lower() for p in (
    "write failed",
    "server components render",
    "internal server error",
    "actionerror",
    "captureaction",
    # OUR generic server/action catch-all (the #686 class): captureActionError surfaces
    # these on a real server throw. Escalate, don't just surface — esp. given the capture
    # gap means these are the signal that a server action actually failed (Nazim 39194 #1).
    "the error has been reported",
    "something went wrong",
    "unhandledrejection",
    "unhandled promise",
    " 500",   # leading space; can over-match ("processed 500 rows") but over-escalation is fail-safe
    "database",
    "psycopg",
    "supabase",
)]
MATERIAL_SPIKE_COUNT = int(os.environ.get("SENTRY_MATERIAL_SPIKE_COUNT", "50"))
STATE_PATH = _ORCH / "state" / "sentry_new_error_watch.json"
SURFACE_PREFIX = "[sentry-new-error]"
ESCALATE_PREFIX = "🔴 [sentry-MATERIAL]"


# ── PURE decision core (unit-tested; no network, no I/O) ─────────────────────
def is_noise(title: str, culprit: str = "") -> bool:
    """True iff this issue is client-side browser noise (not our bug)."""
    hay = f"{title or ''} {culprit or ''}".lower()
    return any(p in hay for p in NOISE_PATTERNS)


def is_material(issue: dict) -> bool:
    """True iff worth paging the operator: fatal, a server/app-error signature, or a
    high-count spike. (Noise is filtered before this is consulted.)"""
    level = (issue.get("level") or "").lower()
    if level == "fatal":
        return True
    hay = f"{issue.get('title') or ''} {issue.get('culprit') or ''}".lower()
    if any(p in hay for p in SERVER_ERROR_PATTERNS):
        return True
    try:
        if int(issue.get("count") or 0) >= MATERIAL_SPIKE_COUNT:
            return True
    except (TypeError, ValueError):
        pass
    return False


def decide(state: dict, issues: list) -> dict:
    """PURE. (prev state, current unresolved issues) -> {surface, escalate, state}.

    surface  = genuinely-NEW non-noise issues (id not seen) — reported ONCE.
    escalate = the MATERIAL subset, paged ONCE per issue; re-escalated only on a
               fresh spike (count grew by >= MATERIAL_SPIKE_COUNT since last page).
    Reopened is handled by PRUNING ids no longer unresolved: a resolved issue drops
    out of state, so its later reappearance re-enters as new (re-surfaces + re-pages).
    """
    current_ids = {str(i.get("id")) for i in issues}
    reported = {i for i in state.get("reported_ids", []) if i in current_ids}   # prune resolved
    escalated = {k: v for k, v in (state.get("escalated") or {}).items() if k in current_ids}

    surface, escalate = [], []
    for it in issues:
        if is_noise(it.get("title"), it.get("culprit")):
            continue                                    # never surface/escalate/track noise
        iid = str(it.get("id"))
        if iid not in reported:
            surface.append(it)
            reported.add(iid)
        if is_material(it):
            prev = escalated.get(iid)
            try:
                cnt = int(it.get("count") or 0)
            except (TypeError, ValueError):
                cnt = 0
            fresh_spike = prev is not None and (cnt - int(prev)) >= MATERIAL_SPIKE_COUNT
            if prev is None or fresh_spike:
                escalate.append(it)
                escalated[iid] = cnt
    return {"surface": surface, "escalate": escalate,
            "state": {"reported_ids": sorted(reported), "escalated": escalated}}


# ── shell: network / bus / state (never reached by the pure tests) ───────────
def _die(msg: str, code: int = 1):
    """Dead-man: fail LOUD, non-zero — a poll that can't run must never look clean."""
    print(f"[sentry-watch] COULD NOT POLL SENTRY: {msg}", file=sys.stderr)
    sys.exit(code)


def _sentry_issues() -> list:
    """GET unresolved issues (last 24h). Raises on any failure -> caller dead-mans."""
    import urllib.request
    import json as _json
    tok = os.environ.get("SENTRY_AUTH_TOKEN")
    org = os.environ.get("SENTRY_ORG")
    proj = os.environ.get("SENTRY_PROJECT")
    if not (tok and org and proj):
        raise RuntimeError("SENTRY_AUTH_TOKEN/ORG/PROJECT not all set")
    url = (f"https://sentry.io/api/0/projects/{org}/{proj}/issues/"
           f"?query=is:unresolved&statsPeriod=24h&limit=50")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (trusted host)
        data = _json.loads(r.read().decode())
    if not isinstance(data, list):
        raise RuntimeError(f"unexpected Sentry response: {str(data)[:200]}")
    out = []
    for it in data:
        out.append({"id": str(it.get("id")), "title": it.get("title"),
                    "culprit": it.get("culprit"), "level": it.get("level"),
                    "count": it.get("count"), "permalink": it.get("permalink"),
                    "firstSeen": it.get("firstSeen"), "lastSeen": it.get("lastSeen")})
    return out


def _load_state() -> dict:
    import json
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        return {"reported_ids": [], "escalated": {}}


def _save_state(st: dict) -> None:
    import json
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st, indent=2, sort_keys=True))
    tmp.replace(STATE_PATH)


def _pg():
    import psycopg2
    return psycopg2.connect(os.environ["DATABASE_URL"])


def _post_bus(to_agent: str, mtype: str, priority: str, subject: str, body: str) -> None:
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,priority,requires_response) "
            "VALUES ('cc-fleet-health',%s,%s,%s,%s,%s,false)",
            (to_agent, mtype, subject, body, priority))
        conn.commit()


def _fmt(issues: list) -> str:
    lines = []
    for it in issues:
        lines.append(f"  - [{it.get('level')}] {str(it.get('title'))[:90]} "
                     f"(culprit={str(it.get('culprit'))[:40]}, count={it.get('count')})\n"
                     f"    {it.get('permalink') or ''}")
    return "\n".join(lines)


def run(dry: bool = False) -> dict:
    """Poll -> decide -> (surface to bus, escalate material) when ENABLED+not-dry.
    Returns the decision for --json/logging. Dead-mans loud on any API failure."""
    try:
        issues = _sentry_issues()
    except Exception as e:  # noqa: BLE001 — fail-soft LOUD, never silent
        _die(f"{type(e).__name__}: {e}")
    state = _load_state()
    d = decide(state, issues)
    # Read the arm flag at CALL-TIME (after load_dotenv), not module-import, so a direct
    # manual live-run honors a .env-only SENTRY_WATCH_ENABLED too (Nazim 39194 #2) — a
    # co-verify run without the wrapper is then not silently SCAN+LOG.
    enabled = os.environ.get("SENTRY_WATCH_ENABLED") == "1"
    send_live = enabled and not dry
    print(f"sentry-new-error-watch — {'LIVE' if send_live else 'SCAN+LOG'} — "
          f"{len(issues)} unresolved, {len(d['surface'])} new to surface, "
          f"{len(d['escalate'])} material to escalate — enabled={enabled} dry={dry}")
    for it in d["surface"]:
        print(f"  {'SURFACE' if send_live else 'WOULD-SURFACE'} {it['id']} {str(it['title'])[:70]}")
    for it in d["escalate"]:
        print(f"  {'ESCALATE' if send_live else 'WOULD-ESCALATE'} {it['id']} {str(it['title'])[:70]}")
    if send_live:
        if d["surface"]:
            _post_bus("orch-console", "update", "P3",
                      f"{SURFACE_PREFIX} {len(d['surface'])} new Sentry issue(s) for triage",
                      "TL;DR: new unresolved Sentry issue(s) (client-noise filtered) — triage:\n"
                      + _fmt(d["surface"]))
        for it in d["escalate"]:
            _post_bus("orch-console", "blocker", "P2",
                      f"{ESCALATE_PREFIX} {str(it.get('title'))[:70]}",
                      "ELI5 (relay to operator): a MATERIAL Sentry error is live — "
                      f"{str(it.get('title'))[:120]} on {str(it.get('culprit'))[:60]} "
                      f"(count={it.get('count')}, level={it.get('level')}). "
                      f"{it.get('permalink') or ''}\nWhat: a real server/app error (not client noise). "
                      "Why it matters: user-impacting / server-side. What to do: triage the linked issue.")
        _save_state(d["state"])
    print(f"done — {'sent + state saved' if send_live else 'scan+log, no send/state'}.")
    return d


def _selftest() -> int:
    """Prove the pure core without network (parity with deploy_provenance_watch)."""
    ok = True

    def chk(cond, label):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")

    chk(is_noise("i: Failed to connect to MetaMask", "/x"), "noise: metamask")
    chk(is_noise("Could not establish connection. Receiving end does not exist.", "/x"), "noise: extension")
    chk(is_noise("<unknown>", "/x"), "noise: unknown")
    chk(not is_noise("Error: audit-log write failed", "GET /x"), "not-noise: server error")
    chk(is_material({"level": "fatal", "title": "aborted", "count": 1}), "material: fatal")
    chk(is_material({"level": "error", "title": "audit-log write failed", "count": 1}), "material: server pattern")
    chk(is_material({"level": "error", "title": "x", "count": MATERIAL_SPIKE_COUNT + 1}), "material: spike")
    chk(not is_material({"level": "error", "title": "minor render warning", "count": 1}), "not-material: benign")
    # decide: surface-once, escalate-material, dedup, spike, prune-reopened
    iss = [{"id": "10", "title": "audit-log write failed", "culprit": "/x", "level": "error", "count": 3},
           {"id": "11", "title": "Failed to connect to MetaMask", "culprit": "/y", "level": "error", "count": 9}]
    d = decide({"reported_ids": [], "escalated": {}}, iss)
    chk([i["id"] for i in d["surface"]] == ["10"], "decide: noise excluded from surface")
    chk([i["id"] for i in d["escalate"]] == ["10"], "decide: material escalated")
    d2 = decide(d["state"], iss)
    chk(d2["surface"] == [] and d2["escalate"] == [], "decide: dedup (no re-surface/re-escalate)")
    iss3 = [{"id": "10", "title": "audit-log write failed", "culprit": "/x", "level": "error",
             "count": 3 + MATERIAL_SPIKE_COUNT}]
    d3 = decide(d["state"], iss3)
    chk([i["id"] for i in d3["escalate"]] == ["10"], "decide: re-escalate on fresh spike")
    d4 = decide(d["state"], [])   # 10 resolved -> pruned
    chk("10" not in d4["state"]["reported_ids"] and "10" not in d4["state"]["escalated"], "decide: prune reopened")
    print("SELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(_ORCH / ".env")
    args = sys.argv[1:]
    if "--selftest" in args:
        return _selftest()
    if "--json" in args:
        import json
        d = run(dry=True)
        print(json.dumps({"surface": [i["id"] for i in d["surface"]],
                          "escalate": [i["id"] for i in d["escalate"]],
                          "state": d["state"]}, indent=2))
        return 0
    run(dry="--dry-run" in args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
