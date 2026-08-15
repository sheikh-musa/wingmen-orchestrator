#!/usr/bin/env python3
"""gazzabyte_board_autopublish.py — keep share.wingmen.dev/r/gazzabyte-status
never-stale WITHOUT a human in the loop (operator directive op#11626, 2026-08-11:
"always ensure the status share page is never stale ... after every task or step,
update it").

The hub (cc-orchestrator) drives the Irsyad build and pushes refreshed boards to
origin/share/gazzabyte-platform-docs frequently. Previously each refresh needed a
manual publish on the Mini via orch-console (Nazim) — a bottleneck that went stale
whenever Nazim was mid-task or recycled (exactly the operator's complaint). This
watcher removes Nazim from that loop: on every new board blob it checks out the file
and republishes via scripts/publish_share.sh.

DETECTION + MECHANICAL PUBLISH only — it never edits board CONTENT (that's the hub's,
on its branch). Read-only on the branch except the single-file checkout of the board.

Dead-man's-switch (feedback_monitors_need_deadmans_switch): any failure to fetch,
resolve, checkout or publish is logged LOUDLY and posted as a P1 DEGRADE bus row to
orch-console, never silently swallowed — and state is NOT advanced, so it retries next
tick. A watcher that can't publish must SAY so, not look done.

Run manually once to establish/confirm baseline; schedule via launchd
(dev.wingmen.gazzabyte-autopublish) for ongoing publishing.
"""
from __future__ import annotations
import os, subprocess, sys, pathlib, datetime

ORCH = pathlib.Path.home() / "wingmen" / "orchestrator"
# op#13342: this watcher used to track reports/gazzabyte-status-board.html on
# share/gazzabyte-platform-docs — a source the hub stopped writing on 2026-08-11, while the
# board actually serving the client became reports/gazzabyte-status-combined.html. The
# watcher was therefore armed to overwrite the live board with four-day-old content the
# moment anyone touched that branch. Repointed at the real source.
BRANCH = os.environ.get("GAZZABYTE_BOARD_BRANCH", "fable/substrate-safe-fixes")
BOARD = os.environ.get("GAZZABYTE_BOARD_FILE", "reports/gazzabyte-status-combined.html")
SLUG = "gazzabyte-status"
TITLE = "Irsyad — Status"
TENANT = "gazzabyte"
# The board's team panel is rendered live per request by the share host's dynamic route
# (wingmen-share app/r/gazzabyte-status). These markers are where that panel goes. A board
# published without them silently reverts the page to a baked snapshot — the exact bug
# op#13342 fixed — so the watcher REFUSES to publish one and says why.
REQUIRED_MARKERS = ("<!--WINGMEN:LIVE-TEAM-PANEL-->", "<!--WINGMEN:LIVE-UPDATED-->")
STATE = ORCH / "logs" / "gazzabyte_autopublish.state"
GIT = "/usr/bin/git" if os.path.exists("/usr/bin/git") else "git"
# launchd strips PATH; publish_share.sh needs vercel/git/node on it
# (reference_launchd_strips_path_reset_scripts_tmux).
RUN_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def _log(msg: str) -> None:
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"[{stamp}] {msg}", flush=True)


def _git(*args: str) -> str:
    return subprocess.check_output([GIT, *args], cwd=str(ORCH), text=True,
                                   stderr=subprocess.STDOUT).strip()


def _degrade(reason: str) -> None:
    """LOUD dead-man's-switch: log + post a P1 bus row to orch-console. A failure to
    post the row must not crash the watcher (best-effort on top of the log)."""
    _log(f"DEGRADE: {reason}")
    try:
        sys.path.insert(0, str(ORCH))
        import psycopg
        from nervous_system.nazim_bus_notify import _dsn
        body = (f"gazzabyte board auto-publisher DEGRADED — {reason}. "
                f"share.wingmen.dev/r/{SLUG} may be STALE (op#11626 wants it never-stale). "
                f"Publish manually until fixed: git fetch origin && "
                f"git checkout origin/{BRANCH} -- {BOARD} && "
                f"scripts/publish_share.sh {BOARD} {SLUG} \"{TITLE}\".")
        with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,priority,requires_response)"
                " VALUES ('gazzabyte-autopublish','orch-console','blocker',%s,%s,'P1',true)",
                (f"auto-publisher DEGRADED — gazzabyte-status may be stale ({reason[:60]})", body))
            conn.commit()
    except Exception as e:  # never let the alarm-bell itself take down the watcher
        _log(f"WARN: could not post degrade bus row: {e}")


def main() -> int:
    try:
        _git("fetch", "origin", BRANCH)
    except subprocess.CalledProcessError as e:
        _degrade(f"git fetch failed: {e.output.strip() if e.output else e}")
        return 1

    try:
        blob = _git("rev-parse", f"origin/{BRANCH}:{BOARD}")
    except subprocess.CalledProcessError as e:
        _degrade(f"cannot resolve board blob on origin/{BRANCH}: {e.output.strip() if e.output else e}")
        return 1

    last = STATE.read_text().strip() if STATE.exists() else ""
    if blob == last:
        _log(f"no change (blob {blob[:12]}) — nothing to publish")
        return 0

    _log(f"new board blob {blob[:12]} (was {last[:12] or '<none>'}) — publishing")
    try:
        _git("checkout", f"origin/{BRANCH}", "--", BOARD)
    except subprocess.CalledProcessError as e:
        _degrade(f"checkout of {BOARD} failed: {e.output.strip() if e.output else e}")
        return 1

    # Fail-closed on a board that would re-bake the team panel (op#13342). State is NOT
    # advanced, so a corrected board publishes on the next tick without manual intervention.
    try:
        content = (ORCH / BOARD).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        _degrade(f"cannot read {BOARD} after checkout: {e}")
        return 1
    missing = [m for m in REQUIRED_MARKERS if m not in content]
    if missing:
        _degrade(f"REFUSING to publish {BOARD}: live-panel marker(s) {missing} absent — "
                 f"publishing it would replace the per-request live team panel with a baked "
                 f"snapshot. Restore the markers in the board source, then re-push.")
        return 1

    env = dict(os.environ, PATH=RUN_PATH)
    proc = subprocess.run(
        # TENANT is positional arg 5 and is what binds this slug to the gazzabyte credential
        # in the manifest — omitting it (as this call used to) republishes the page UNMAPPED,
        # which fails closed the moment the per-tenant gate is switched on.
        [str(ORCH / "scripts" / "publish_share.sh"), BOARD, SLUG, TITLE, "", TENANT],
        cwd=str(ORCH), env=env, text=True, capture_output=True)
    if proc.returncode != 0:
        _degrade(f"publish_share.sh exit {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:300]}")
        return 1

    _log(f"published: {proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else 'ok'}")
    STATE.write_text(blob + "\n")  # advance ONLY after a confirmed publish
    return 0


if __name__ == "__main__":
    sys.exit(main())
