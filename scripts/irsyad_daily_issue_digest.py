#!/usr/bin/env python3
"""irsyad_daily_issue_digest.py — go-live-window daily check for issues the
client's staff hit but don't raise. Reads the last 24h of ui_events from the
irsyad silo (goumlyne): rage-clicks (frustration), abandoned flows (gave up),
and flow-completion — scoped to the Madrasah Irsyad org — and pushes a short
morning digest to the operator. Sentry app-errors are included when
SENTRY_AUTH_TOKEN is set (else a one-line pointer to the Sentry dashboard).

Tighter cadence than the Sunday weekly UX job, for the active client-testing
period. Runs daily via launchd (dev.wingmen.irsyad-daily-digest).
"""
from __future__ import annotations
import os, sys, subprocess, urllib.request, json
import psycopg
from dotenv import load_dotenv

ORCH = os.path.join(os.path.dirname(__file__), "..")
load_dotenv(os.path.join(ORCH, ".env"))
IRSYAD_ORG = "73339164-7c1f-40ba-a093-33f1f292dd4c"


def _q(cur, sql, args=()):
    return cur.execute(sql, args).fetchall()


def build_digest() -> str:
    dsn = os.environ["GOUMLYNE_DATABASE_URL"]
    lines = []
    with psycopg.connect(dsn, connect_timeout=15) as c, c.cursor() as cur:
        # scope to irsyad-org users (ui_events has no org_id; join via org_members)
        scope = ("and (u.user_id is null or u.user_id in "
                 "(select user_id from org_members where org_id=%s))")
        # rage-clicks by screen
        rc = _q(cur,
            "select coalesce(properties->>'path','?') p, count(*) n "
            "from ui_events u where event_name='rage_click' "
            "and created_at > now() - interval '24 hours' " + scope +
            " group by 1 order by 2 desc", (IRSYAD_ORG,))
        ab = _q(cur,
            "select coalesce(properties->>'path','?') p, count(*) n "
            "from ui_events u where event_name='flow_abandoned' "
            "and created_at > now() - interval '24 hours' " + scope +
            " group by 1 order by 2 desc", (IRSYAD_ORG,))
        started = _q(cur, "select count(*) from ui_events u where event_name='flow_started' "
            "and created_at > now() - interval '24 hours' " + scope, (IRSYAD_ORG,))[0][0]
        completed = _q(cur, "select count(*) from ui_events u where event_name='flow_completed' "
            "and created_at > now() - interval '24 hours' " + scope, (IRSYAD_ORG,))[0][0]
        pv = _q(cur, "select count(*) from ui_events u where event_name='page_view' "
            "and created_at > now() - interval '24 hours' " + scope, (IRSYAD_ORG,))[0][0]
        active = _q(cur, "select count(distinct session_id) from ui_events u "
            "where created_at > now() - interval '24 hours' " + scope, (IRSYAD_ORG,))[0][0]

    quiet = not rc and not ab
    head = "🩺 Irsyad daily check (24h)"
    lines.append(f"{head}\nActive sessions: {active} · page views: {pv} · flows: {started} started/{completed} completed")
    if rc:
        lines.append("\n😖 Rage-clicks (users stuck/frustrated):")
        for p, n in rc:
            lines.append(f"  • {p} — {n}x")
    if ab:
        lines.append("\n🚪 Abandoned flows (started then gave up):")
        for p, n in ab:
            lines.append(f"  • {p} — {n}x")
    if quiet:
        lines.append("\nNo rage-clicks or abandoned flows — nothing users are silently struggling with today.")

    # Sentry errors (needs a token; else point at the dashboard)
    tok = (os.environ.get("SENTRY_AUTH_TOKEN") or "").strip()
    if not tok:
        lines.append("\n🐞 App errors: captured to Sentry (add SENTRY_AUTH_TOKEN to surface counts here).")
    else:
        try:
            org = os.environ.get("SENTRY_ORG", "irsyad")
            proj = os.environ.get("SENTRY_PROJECT", "ihsanos-irsyad")
            req = urllib.request.Request(
                f"https://sentry.io/api/0/projects/{org}/{proj}/issues/?statsPeriod=24h&query=is:unresolved",
                headers={"Authorization": f"Bearer {tok}"})
            with urllib.request.urlopen(req, timeout=15) as r:
                issues = json.load(r)
            if issues:
                lines.append(f"\n🐞 Sentry — {len(issues)} unresolved error(s), 24h:")
                for i in issues[:5]:
                    lines.append(f"  • {i.get('title','?')[:60]} ({i.get('count','?')}x)")
            else:
                lines.append("\n🐞 Sentry: no unresolved errors in 24h.")
        except Exception as e:
            lines.append(f"\n🐞 Sentry query failed ({type(e).__name__}) — check the dashboard.")
    return "\n".join(lines)


def main() -> int:
    msg = build_digest()
    subprocess.run(["bash", os.path.join(ORCH, "scripts", "tg_send.sh"), msg, "irsyad"], check=False)
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
