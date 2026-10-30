#!/usr/bin/env python3
"""bus_send.py — the ONE sanctioned way to post an agent_messages bus row.

Why this exists (orch-console bus #43651, op#22517 aftermath): every prior
"bus helper" was a per-agent scratch file recreated ad hoc (e.g.
~/wingmen/orchestrator/_bus_tmp.py, scratchpad/bus_send.py) — untracked,
uncovered by tests, and each one lets --priority default or be omitted. On
2026-09-27 (cc-substrate) a hand-written INSERT for a hub action-now ask
omitted the priority column; it silently landed at the table default 'P2',
which is BELOW the hub's P0/P1+requires_response wake floor (CAI-451/786,
memory reference_hub_wake_floor_p1_rr), so the hub never woke on it. A
promise to "remember next time" doesn't survive a context reset — this
script makes --priority a REQUIRED argparse argument so the mistake is no
longer possible to make silently.

Follow-up (bus #43673): the untracked scratch helpers (_bus_tmp.py,
scratchpad/bus_send.py) are now thin shims over send() below, so there is
exactly one INSERT INTO agent_messages in the fleet's ad-hoc-send path.

Follow-up (op#22669): send() used to ALSO write an operator_asks link row
(migration 044) for every fresh console-to-body ask (from_agent='orch-console',
type='decision', --req, no --thread/--reply-to). REVERTED (Musa op#23554, bus
#46353, 2026-09-30): that heuristic couldn't tell a genuine Musa ask from a
pure fleet delegation, and a delegation is NOT an ask OF Musa — it phantom-
appeared on his "Your asks" board (ids 378/381: "OEH: cc-quality review...",
"Musa picked V2 option B..."), and broadcasting the same decision to several
bodies duplicated identically (ids 210/211/212). A delegation is now NEVER a
new ask; --link-ask lets a caller LINK this bus row to an operator_asks row
that already exists because it traces back to something Musa actually said
(migration 084's ask_surface + source_msg_id/waiting_on_operator scope).

Usage:
    scripts/bus_send.py --to cc-orchestrator --type update \\
        --subject "short subject" --priority P1 [--req] \\
        [--thread <uuid-or-prefix>] [--reply-to <id>] [--link-ask <id>] \\
        [--from <agent_id>] [--dry-run] <<'EOF'
    body text goes on stdin (>=40 bytes — the empty-body guard; Nazim shipped
    three blank bus rows on 2026-09-05 by forgetting the heredoc)
    EOF

Identity: --from is resolved automatically if omitted, in this order:
    1. CC_BASE_AGENT_ID   (interactive fleet lane / this Claude Code session)
    2. AGENT_ID           (daemon/watchdog launchers)
    3. ORCH_AGENT_ID, but ONLY when ORCH_BODY_ROLE=console (the one
       documented case where the console's own identity is the right
       fallback — see reference_agent_id_not_orch_agent_id_for_identity;
       ORCH_AGENT_ID is otherwise fleet-wide-exported .env noise and must
       NEVER be trusted blindly)
    Refuses (does not guess) if none of these resolve.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.agent_identity import IdentityError, resolve_agent_id  # noqa: E402
from scripts.lib.bus_pii_redact import redact_pii  # noqa: E402
from scripts.lib.singleton_bases import is_singleton_base  # noqa: E402

_VALID_PRIORITIES = ("P0", "P1", "P2", "P3")
_VALID_TYPES = (
    "review_request", "question", "decision", "agreed",
    "challenge", "update", "blocker", "counter",
)
_MIN_BODY_BYTES = 40
_HUB_AGENT = "cc-orchestrator"

# Real, intentional send targets that are legitimately NEVER wake-eligible by
# is_wake_eligible_recipient's definition (operator / human-opened addresses) --
# never "undeliverable" below, despite having no live wake owner. Mirrors the
# human/musa half of scripts/fleet_health.py's _HUMAN_OPENED / _NEVER_ARCHIVE_ADDRS
# (that set also has "operator" and "substrate", omitted here: "operator" has zero
# rows ever sent to it, and "substrate" is already rejected at the DB layer by
# agent_messages_reject_pseudo_targets regardless of this check -- cc-quality
# #50975 verified both empirically). Small, stable sets kept in sync by hand;
# worth a shared module if either grows.
_HUMAN_OPENED = frozenset({"cto-desktop"})
_UNDELIVERABLE_EXEMPT = frozenset({"musa"}) | _HUMAN_OPENED

# bus #78935/#78954: a test process leaked a real escalation row to the live
# substrate. Fixed, deployment-specific anchor for the "is this actually the
# live checkout" check below -- deliberately NOT Path.home()-based, because a
# test that fakes $HOME to sandbox itself would make a Path.home()-derived
# reference move right along with it, defeating the whole point of the check.
LIVE_ORCH_ROOT = "/Users/sheikhmusa/wingmen/orchestrator"

# bus #78963: console's review of the above found it still misses the
# INHERITED-DSN path -- when no .env FILE exists beside the script (exactly
# the self-relative/test-repo case the (2) fix moves scripts toward), dburl()
# falls through to the passed-in `database_url`, i.e. whatever DATABASE_URL
# the calling shell already had exported. launch_dangerous_cc.sh `set -a`s
# the live .env into every lane shell, so a test that sets
# WINGMEN_TEST_SANDBOX=1 but forgets to override DATABASE_URL itself would
# still silently post to production. This matches on the live Supabase
# pooler's project ref (present in the DSN's username/host) rather than
# string-equality with any particular .env line, since the inherited env var
# and the file's own line are not guaranteed to be formatted identically.
LIVE_SUBSTRATE_REF = "tscuymavysscrvoberrr"


_LIVE_INSTANCE_WINDOW = "30 minutes"  # matches scripts/irsyad_autoscaler.py LIVE_HEARTBEAT_WINDOW


def live_instance_ids(base_to: str, dsn: str | None = None) -> list[str]:
    """bus #49220: agent_status rows whose base_agent_id matches `base_to` but
    whose own agent_id differs (a real NN-suffixed instance, e.g. cc-irsyad-1
    for base cc-irsyad -- not the base address reporting as its own row,
    which singletons/solo coords do), seen live in the last 30 minutes.
    Returns [] for a base id with no fanned-out instances (singletons, solo
    coords) -- that case is a no-op by construction, not a special case."""
    import psycopg2

    conn = psycopg2.connect(dsn or dburl(os.environ.get("DATABASE_URL")))
    cur = conn.cursor()
    cur.execute(
        "SELECT agent_id FROM agent_status "
        "WHERE base_agent_id=%s AND agent_id<>%s "
        "AND status IN ('idle','working') "
        f"AND last_heartbeat > now() - interval '{_LIVE_INSTANCE_WINDOW}' "
        "ORDER BY agent_id",
        (base_to, base_to),
    )
    instances = [r[0] for r in cur.fetchall()]
    conn.close()
    return instances


def _heartbeat_age_str(agent_id: str, dsn: str | None = None) -> str:
    """bus #49675: a suggested instance can go stale between query and read --
    stamp the suggestion with the heartbeat age the DB measured at refusal time
    (server-side now()-last_heartbeat, not a client clock) so a future "it
    suggested a dead instance" report carries its own evidence instead of
    requiring a live-re-query postmortem days later."""
    import psycopg2

    try:
        conn = psycopg2.connect(dsn or dburl(os.environ.get("DATABASE_URL")))
        cur = conn.cursor()
        cur.execute(
            "SELECT status, now() - last_heartbeat FROM agent_status WHERE agent_id=%s",
            (agent_id,),
        )
        row = cur.fetchone()
        conn.close()
        if row is None:
            return "no agent_status row"
        status, age = row
        return f"status={status}, heartbeat {age} ago"
    except (Exception, SystemExit) as exc:  # noqa: BLE001 -- diagnostic text only, never block the refusal
        return f"heartbeat age unavailable ({exc})"


def refuse_if_base_has_live_instances(to: str, to_base: bool, dsn: str | None = None) -> None:
    """Refuse (not just warn) a send to a BASE id when live instance(s) exist,
    unless the caller explicitly opts in via --to-base. Builder lanes poll
    their own instance address, not the base -- orch-console lost 5 rows to
    this on 2026-10-01 even with a memory rule in place (a promise doesn't
    survive a context reset, same lesson as the --priority fix above)."""
    if to_base or is_singleton_base(to):
        return  # a singleton's base address IS the body (orch-console #77922)
    instances = live_instance_ids(to, dsn=dsn)
    if not instances:
        return
    age = _heartbeat_age_str(instances[0], dsn=dsn)
    raise SystemExit(
        f"bus_send: REFUSED — '{to}' is a BASE id with live instance(s) "
        f"({', '.join(instances)}); they poll their own instance address, not "
        f"the base, so this row would sit unread. Did you mean --to {instances[0]} "
        f"[{age}]? Pass --to-base to send to the base address anyway."
    )


def _agent_status_row(agent_id: str, dsn: str | None = None) -> tuple | None:
    """(base_agent_id, is_live) for one agent_status row, or None if it has none.
    `is_live` uses the same floor as live_instance_ids: status IN ('idle','working')
    and a heartbeat inside _LIVE_INSTANCE_WINDOW."""
    import psycopg2

    conn = psycopg2.connect(dsn or dburl(os.environ.get("DATABASE_URL")))
    cur = conn.cursor()
    cur.execute(
        "SELECT base_agent_id, "
        f"(status IN ('idle','working') AND last_heartbeat > now() - interval '{_LIVE_INSTANCE_WINDOW}') "
        "FROM agent_status WHERE agent_id=%s",
        (agent_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def _is_genuine_recipient_reply(sender: str, target_to: str, dsn: str | None = None) -> bool:
    """True iff `sender` is a genuine answerer of a row addressed to
    `target_to` -- exact match, or both resolve to the SAME registry
    base_agent_id via agent_status (the same lookup
    refuse_if_instance_stale_and_base_live already uses, not a fresh guess).
    A name-shape relationship is NEVER by itself proof of same-family and is
    not checked here -- the registry is the only non-exact test.

    bus #83138 (held_commitments#898 regression, found same-day): the prior
    version stripped a trailing -<digits> from BOTH sides with one regex,
    breaking any lane whose BASE NAME itself ends in a digit (cc-cosem-exams-2,
    cc-cosem-adcda-1, cc-cosem-platform-2/4).

    bus #83150/#83152 (same-day re-regression, caught by cc-quality before
    merge, spec correction owned by console): the FIRST fix for #83138 added
    two dash-prefix fast paths (target_to.startswith(sender+"-") and the
    reverse) to catch base<->instance without a DB round-trip. Those are
    UNSOUND in this namespace: a base name can itself be a dash-prefix of a
    totally DIFFERENT base (cc-irsyad < cc-irsyad-coord, cc-cosem-exams <
    cc-cosem-exams-2, cc-console < cc-console-revamp, cc-storefront <
    cc-storefront-c) -- 68 such cross-base pairs exist, 20 with live
    traffic, cc-irsyad<->cc-irsyad-coord alone carrying 609 messages. The
    prefix check short-circuited BEFORE the registry comparison that would
    have correctly rejected those pairs, reintroducing the exact #640
    false-stamp bug (a self-follow-up on cc-irsyad falsely stamping
    cc-irsyad-coord's genuinely unanswered row) for every one of them.
    Dropped entirely -- the registry is authoritative, full stop. A missed
    stamp only over-alerts; a wrong stamp hides a message, so an unresolvable
    name (no agent_status row on either side) falls back to the literal
    string and so, correctly, does NOT stamp unless the strings are
    identical."""
    if not sender or not target_to:
        return False
    if sender == target_to:
        return True
    sender_row = _agent_status_row(sender, dsn=dsn)
    target_row = _agent_status_row(target_to, dsn=dsn)
    sender_base = sender_row[0] if sender_row and sender_row[0] else sender
    target_base = target_row[0] if target_row and target_row[0] else target_to
    return sender_base == target_base


def refuse_if_instance_stale_and_base_live(to: str, dsn: str | None = None) -> None:
    """Mirror of refuse_if_base_has_live_instances, inverted (bus #58614 item 1).
    Motivating case: bus row #58568 (cc-irsyad-3 -> cc-quality-1, an urgent review
    request) was stranded because cc-quality-1 had gone stale while its base
    cc-quality was the live singleton actually polling -- a numbered instance id
    can retire (per the ORCH-TOPOLOGY-001/op#27235 relaunch pattern) without the
    sender knowing. Refuses and points at the live base instead of silently
    forwarding -- same shape as the base-has-live-instances guard, so a sender
    always gets an explicit, correctable address rather than a guess.

    Cheap pre-check: only a to_agent shaped like a numbered instance (ends in
    -<digits>) can possibly be this case -- skips the DB round-trip for every
    other address, including write-only producers (commitment-sweeper, ...)
    that refuse_if_undeliverable already handles with zero DB calls."""
    if not re.search(r"-\d+$", to or ""):
        return
    row = _agent_status_row(to, dsn=dsn)
    if row is None:
        # An instance of a SINGLETON base with no status row at all is a dead address: it
        # used to be waved through here. Console's GO rulings #78373/#78383 went to
        # 'cc-fleet-health-1' (a stale agents row, no status row) and sat unread while the
        # doorbell rang the real body for mail it couldn't see. Not a blanket ban on -N:
        # auditor singletons do run live instances (cc-finance-1 is live), which have rows.
        _base = re.sub(r"-\d+$", "", to)
        if is_singleton_base(_base):
            raise SystemExit(
                f"bus_send: REFUSED — '{to}' has no agent_status row (no live body) and its "
                f"base '{_base}' is a singleton. Use --to {_base} instead."
            )
        return  # not a singleton's instance -- not this guard's concern
    base_agent_id, to_is_live = row
    if not base_agent_id or base_agent_id == to or to_is_live:
        return  # `to` IS the base, or the instance itself is live -- nothing stranded
    base_row = _agent_status_row(base_agent_id, dsn=dsn)
    if base_row is None or not base_row[1]:
        return  # the base isn't live either -- no live address to redirect to
    age = _heartbeat_age_str(to, dsn=dsn)
    raise SystemExit(
        f"bus_send: REFUSED — '{to}' is a retired/stale instance [{age}]; its base "
        f"'{base_agent_id}' is the live singleton now. Use --to {base_agent_id} instead."
    )


_PR_NUM_RE = re.compile(r"\bPR\s*#\s*(\d+)\b", re.IGNORECASE)


def _pr_number_from_subject(subject: str) -> str | None:
    """Extract a PR number from a review-verdict subject ('cosem-adcda PR#852
    ...', 'PR #611 at ...'). None if the subject doesn't name one."""
    m = _PR_NUM_RE.search(subject or "")
    return m.group(1) if m else None


def _repo_from_cwd() -> tuple[str, str] | None:
    """(owner, repo) from the CURRENT worktree's origin remote, or None if this
    process isn't inside a git checkout with a GitHub origin. cc-quality sends
    a verdict from inside the exact worktree it reviewed, so the cwd IS the
    repo the PR number belongs to -- no registry lookup needed for this half."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
    except Exception:
        return None
    m = re.search(r"github\.com[:/]([^/]+)/([^/.]+?)(?:\.git)?$", out)
    return (m.group(1), m.group(2)) if m else None


def _pr_head_branch(owner: str, repo: str, pr_number: str) -> str | None:
    """The PR's current head branch via `gh`, or None on any failure (unknown
    PR, gh not authenticated, network hiccup) -- never blocks a send over an
    API call that isn't itself the thing being verified."""
    import json
    import subprocess

    try:
        out = subprocess.run(
            ["gh", "pr", "view", pr_number, "-R", f"{owner}/{repo}", "--json", "headRefName"],
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout
        return json.loads(out).get("headRefName") or None
    except Exception:
        return None


def _resolve_pr_author_instance(repo: str, branch: str, dsn: str | None = None) -> str | None:
    """The agent_id of the LIVE instance whose worktree currently has `branch`
    checked out, among registered lanes for `repo` -- or None if zero or more
    than one match (ambiguous resolution never blocks a send; it only refuses
    on a CONFIRMED mismatch). Scoped by worktree basename starting with the
    repo name (every observed lane dir for a repo follows this convention:
    cosem-adcda, cosem-adcda-hotfix, cosem-adcda.wt-urgent, ...) rather than
    the stale, hand-maintained REPOS.json (missing cosem-exams entirely)."""
    import subprocess

    import psycopg2

    conn = psycopg2.connect(dsn or dburl(os.environ.get("DATABASE_URL")))
    cur = conn.cursor()
    cur.execute("SELECT lane, worktree_path FROM fleet_lanes")
    rows = cur.fetchall()
    candidates = []
    for lane, worktree_path in rows:
        if not worktree_path or not os.path.basename(worktree_path.rstrip("/")).startswith(repo):
            continue
        try:
            current = subprocess.run(
                ["git", "-C", worktree_path, "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, timeout=10, check=True,
            ).stdout.strip()
        except Exception:
            continue
        if current == branch:
            candidates.append(lane)
    if len(candidates) != 1:
        conn.close()
        return None  # no live worktree has it checked out right now, or it's ambiguous
    cur.execute("SELECT agent_id FROM agent_status WHERE tmux_session=%s", (candidates[0],))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def refuse_if_pr_verdict_misrouted(
    from_agent: str, to: str, subject: str, skip: bool = False, dsn: str | None = None
) -> None:
    """orch-console bus #85014 (incident: cc-quality PASSed cosem-adcda PR#852/
    855/857 to cc-cosem-adcda-1/-3; the actual author, cc-cosem-adcda-2, idled
    ~10h unaware -- Musa caught it by asking). A review verdict from cc-quality
    naming a PR# should reach the instance whose worktree AUTHORED that PR's
    head branch, not whichever instance happened to be addressed by hand.

    Resolution-failure-safe: any inconclusive step (not a cc-quality sender, no
    PR# in the subject, cwd has no GitHub origin, `gh` lookup fails, zero/
    multiple live worktrees hold the branch) is a silent no-op -- this guard
    only ever fires on a POSITIVELY CONFIRMED mismatch, never on an inability
    to verify, so it can't become a new way to strand a legitimate send.

    Cheap pre-checks first (sender shape, subject regex) before any DB round-
    trip or subprocess call, same shape as refuse_if_instance_stale_and_base_
    live's own -<digits> pre-check -- the overwhelming majority of sends are
    neither from cc-quality nor name a PR#, and must never pay for a DB call
    just to find that out."""
    if skip:
        return
    if from_agent != "cc-quality" and not from_agent.startswith("cc-quality-"):
        return
    pr_number = _pr_number_from_subject(subject)
    if not pr_number:
        return
    from_row = _agent_status_row(from_agent, dsn=dsn)
    from_base = from_row[0] if from_row and from_row[0] else from_agent
    if from_base != "cc-quality":
        return
    repo_info = _repo_from_cwd()
    if not repo_info:
        return
    owner, repo = repo_info
    branch = _pr_head_branch(owner, repo, pr_number)
    if not branch:
        return
    correct = _resolve_pr_author_instance(repo, branch, dsn=dsn)
    if not correct or correct == to:
        return
    to_row = _agent_status_row(to, dsn=dsn)
    to_base = to_row[0] if to_row and to_row[0] else to
    correct_row = _agent_status_row(correct, dsn=dsn)
    correct_base = correct_row[0] if correct_row and correct_row[0] else correct
    if to_base == correct_base and to == to_base:
        return  # addressed to the shared base itself -- not a wrong-instance misroute
    age = _heartbeat_age_str(correct, dsn=dsn)
    raise SystemExit(
        f"bus_send: REFUSED — PR#{pr_number}'s head branch '{branch}' is checked out in "
        f"{correct}'s worktree, not '{to}''s; the author instance would never see this "
        f"verdict. Use --to {correct} [{age}]. Pass --skip-pr-author-check to override."
    )


def _undeliverable(to_agent: str | None) -> bool:
    """A to_agent with NO possible live wake owner -- same SSOT definition as
    scripts/fleet_health.py's _undeliverable: not eligible on agent_wake's most
    permissive floor (P0 + requires_response), so a narrow-floor recipient (the
    hub) is never misread as dead. Exempt addresses (operator / human-opened)
    are real, intentional targets that are just never wake-eligible by design,
    not a misroute -- never flagged here."""
    if not to_agent or to_agent in _UNDELIVERABLE_EXEMPT:
        return False
    from nervous_system.agent_wake import is_wake_eligible_recipient

    return not is_wake_eligible_recipient(to_agent, "P0", True)


def refuse_if_undeliverable(
    to: str, reply_to: int | None = None, allow_undeliverable: bool = False, stream=None,
) -> None:
    """Refuse a send to a structurally-undeliverable to_agent -- a write-only
    producer with no live wake owner (commitment-sweeper, programme-stall-guard,
    sla-watchdog, ...). Four real dead-letters (bus #50648/#50649/#50689/#50697)
    were a previous console body "replying" to exactly this class of address --
    a standing rule against it doesn't survive a context reset (orch-console
    #50969), so this makes it structural.

    This refuses regardless of --reply-to -- replying to a row FROM a write-only
    producer must not create a new row TO it either; the correct action is to
    STAMP the original row (a plain UPDATE, which --reply-to already does as a
    side effect of a normal send), never to send anything. When reply_to is
    known, the refusal message points at that exact row to close instead of a
    generic one.

    --allow-undeliverable is the explicit, logged escape hatch for a genuine
    deliberate one-off: downgrades this to a warning and the send proceeds."""
    if not _undeliverable(to):
        return
    close_id = str(reply_to) if reply_to else "<its row id>"
    message = (
        f"'{to}' has no wake owner (write-only producer?). To close one of its "
        f"fire rows: UPDATE agent_messages SET read_at=now(), responded_at=now(), "
        f"response_ref='...' WHERE id={close_id}."
    )
    if allow_undeliverable:
        print(f"bus_send: WARNING — {message} (--allow-undeliverable set, sending anyway)",
              file=stream or sys.stderr)
        return
    raise SystemExit(f"bus_send: REFUSED — {message}")


def warn_if_below_hub_wake_floor(to: str, req: bool, priority: str, stream=None) -> None:
    """bus #44527: --to cc-orchestrator --req at a priority below P1 will NOT wake the
    hub (floor = P0/P1 AND requires_response — reference_hub_wake_floor_p1_rr). This is
    a WARNING, not a refusal: P2-rr is a legitimate way to post a non-urgent item that
    still wants a response eventually."""
    if to == _HUB_AGENT and req and priority not in ("P0", "P1"):
        print(
            f"bus_send: WARNING — --to {_HUB_AGENT} --req at priority {priority} will NOT "
            "wake the hub (floor = P0/P1 + requires_response); use --priority P1 if it must act.",
            file=stream or sys.stderr,
        )


_DATA_SECURITY_KEYWORDS = (
    "pii", "real data", "real client data", "real trainee", "gov-pii",
    "data leak", "data breach", "data exposure", "exposed data",
    "data security", "sensitive data",
)
_PROVENANCE_CITATION_TOKENS = ("data_truth", "data_provenance", "classification:")


def warn_if_missing_provenance_citation(priority: str, subject: str, body: str, stream=None) -> None:
    """op#25626 / bus #51657/#51717 (orch-console gate condition #4, v1 = warn):
    a P0/P1 message that raises a data-security claim must cite a data_truth.py
    classification — the fleet already raised a false data-security P0 over
    generated test data once. WARNING, not a refusal (mirrors
    warn_if_below_hub_wake_floor's shape): a heuristic keyword match can
    false-positive, and a blocked urgent safety message is worse than a missed
    reminder. After a week in production, report firing/false-positive rate to
    orch-console, who will decide whether to harden this to a refusal."""
    if priority not in ("P0", "P1"):
        return
    text = f"{subject}\n{body}".lower()
    if not any(k in text for k in _DATA_SECURITY_KEYWORDS):
        return
    if any(t in text for t in _PROVENANCE_CITATION_TOKENS):
        return
    print(
        "bus_send: WARNING — this message raises a data-security concern at "
        f"priority {priority} without citing a classification. Run "
        "`scripts/data_truth.py classify <project_ref> [org_id]` first and cite "
        "the result (see docs/DATA-PROVENANCE.md) — a slug/name is NOT evidence.",
        file=stream or sys.stderr,
    )


def warn_if_weekday_date_mismatch(subject: str, body: str, stream=None) -> None:
    """2026-10-07 (orch-console bus #57970): flag a weekday paired with the wrong date
    ("Thursday 9 October" when 9 Oct 2026 is a Friday). The send scripts REFUSE on this;
    the bus only WARNS — never blocks, never raises (a guard bug must not stop a bus post)."""
    out = stream or sys.stderr
    try:
        from scripts.lib import date_weekday_guard as _dwg
        for f in _dwg.find_mismatches(f"{subject}\n{body}"):
            print(f"bus_send: WARNING — weekday/date mismatch: {f.message()}", file=out)
    except Exception as e:  # noqa: BLE001 — warn-only path, must never block a bus send
        print(f"bus_send: WARNING — weekday/date guard could not run ({type(e).__name__}); "
              "message not checked.", file=out)


# bus #58159 item 2 (stamp-or-die + evidence flag, WARN mode only -- the REFUSE flip
# is a separate PR once the 48h soak below has a count). Every warn below is logged
# so the soak has real numbers instead of vibes.
WARN_LOG_PATH = os.path.join(
    os.environ.get("ORCH_ROOT", os.path.expanduser("~/wingmen/orchestrator")),
    "logs", "bus_send_warnings.log",
)


def _log_warn(warn_type: str, **fields) -> None:
    """Append one JSONL record per WARN fired below, same shape/posture as
    secrets_output_scanner.py's _log_event: never block or crash the send over a
    logging failure, 600-perm the file on first write."""
    import datetime
    import json
    import stat

    record = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "warn_type": warn_type,
        **fields,
    }
    try:
        log_dir = os.path.dirname(WARN_LOG_PATH)
        os.makedirs(log_dir, exist_ok=True)
        is_new = not os.path.exists(WARN_LOG_PATH)
        with open(WARN_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        if is_new:
            os.chmod(WARN_LOG_PATH, stat.S_IRUSR | stat.S_IWUSR)  # 600
    except OSError:
        pass  # logging must never block or crash a send


def _open_rr_from(to_agent: str, addressed_to: str, dsn: str | None = None) -> tuple[int, str] | None:
    """The most recent open (requires_response=true, responded_at IS NULL) row FROM
    `to_agent` TO `addressed_to` -- the row this send might be silently answering
    without linking via --reply-to. None when there is no such open row."""
    import psycopg2

    conn = psycopg2.connect(dsn or dburl(os.environ.get("DATABASE_URL")))
    cur = conn.cursor()
    cur.execute(
        "SELECT id, now() - created_at FROM agent_messages "
        "WHERE from_agent=%s AND to_agent=%s AND requires_response=true "
        "AND responded_at IS NULL ORDER BY created_at DESC LIMIT 1",
        (to_agent, addressed_to),
    )
    row = cur.fetchone()
    conn.close()
    return (row[0], str(row[1])) if row else None


def warn_if_answering_rr_without_reply_to(
    from_agent: str, to: str, reply_to: int | None, dsn: str | None = None, stream=None,
) -> None:
    """bus #58159 item 2 ("stamp-or-die", WARN mode): a reply sent as a fresh row
    instead of via --reply-to never stamps the original RR's responded_at -- it keeps
    showing as unanswered to triage/asks tooling forever, even though a reply went
    out. Skipped outright when --reply-to is already given (nothing to warn about);
    a DB error degrades to a warning of its own rather than blocking the send (this
    check must never be the reason a real send fails)."""
    if reply_to:
        return
    try:
        found = _open_rr_from(to, from_agent, dsn=dsn)
    except (Exception, SystemExit) as e:  # noqa: BLE001 -- a guard bug must never block a
        # send. dburl() raises bare SystemExit (not Exception) when no DATABASE_URL
        # resolves -- that escaped this catch, crashed the guard, and let pytest's
        # failure introspection dump the live os.environ (incl. real secrets) from
        # dburl()'s frame into the on-disk transcript (bus #64769/#64771, caught by
        # secrets_output_scanner). Same fix shape as line ~132's existing
        # `except (Exception, SystemExit)`.
        print(
            f"bus_send: WARNING — stamp-or-die RR check could not run ({type(e).__name__}); "
            "not checked.",
            file=stream or sys.stderr,
        )
        return
    if not found:
        return
    rr_id, age = found
    print(
        f"bus_send: WARNING — '{to}' has an open requires_response row addressed to "
        f"{from_agent} (id={rr_id}, sent {age} ago) and this send has no --reply-to. "
        f"If this IS your answer, pass --reply-to {rr_id} or it stays marked "
        "unanswered (stamp-or-die soak, bus #58159 item 2).",
        file=stream or sys.stderr,
    )
    _log_warn("rr_without_reply_to", from_agent=from_agent, to=to, open_rr_id=rr_id)


# identical/verified/clean/byte-identical/PASS, word-bounded + case-insensitive so
# "password" never matches "pass" (bus #58159 item 2's own exact keyword list).
_CLAIM_KEYWORDS_RE = re.compile(r"\b(identical|verified|clean|byte-identical|pass)\b", re.IGNORECASE)


def warn_if_unevidenced_claim(mtype: str, subject: str, body: str, evidence: str | None, stream=None) -> None:
    """bus #58159 item 2 (evidence flag, WARN mode): a review_request/update that
    claims identical/verified/clean/byte-identical/PASS without a --evidence
    comparator (sha256:<file> | text:<doc> | probe:<sql|cmd>=<count>) is exactly the
    failure class this fleet keeps getting burned by -- an assertion standing in for
    a check. Only review_request/update carry this kind of claim; --evidence present
    at all satisfies it in WARN mode (format enforcement, if any, is the REFUSE-flip
    PR's call)."""
    if mtype not in ("review_request", "update"):
        return
    if evidence:
        return
    m = _CLAIM_KEYWORDS_RE.search(f"{subject}\n{body}")
    if not m:
        return
    print(
        f"bus_send: WARNING — this {mtype} claims {m.group(1)!r} without "
        "--evidence <comparator> (sha256:<file> | text:<doc> | probe:<sql|cmd>=<count>) "
        "-- an assertion is not a check (bus #58159 item 2, WARN-mode soak).",
        file=stream or sys.stderr,
    )
    _log_warn("unevidenced_claim", mtype=mtype, subject=subject, matched_keyword=m.group(1))


# console ruling #78298 "A" (op#34803 slow-ask): every orch-console delegation subject
# cites the op number, so the slow-ask net can tie a lane's ack to Musa's ask. Two
# delegations without one (#78234, #78246) false-flagged real acks on 10-27. WARN mode
# (soak-logged); whether it should REFUSE is a later call on the soak numbers.
_OP_TOKEN_RE = re.compile(r"(?i)\bop\s*#\s*\d{4,}")


def _is_lane(to: str, dsn: str | None = None) -> bool:
    """True when `to` (base or -N instance) is a launch_dangerous_cc.sh fleet lane."""
    import psycopg2

    base = re.sub(r"-\d+$", "", to)
    conn = psycopg2.connect(dsn or dburl(os.environ.get("DATABASE_URL")))
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM fleet_lanes WHERE launcher='launch_dangerous_cc.sh' "
        "AND base_agent_id IN (%s, %s) LIMIT 1", (to, base))
    hit = cur.fetchone() is not None
    conn.close()
    return hit


def warn_if_delegation_without_op(from_agent: str, to: str, subject: str,
                                  thread: str | None, reply_to: int | None,
                                  stream=None, is_lane=None) -> None:
    """orch-console -> lane, fresh thread (no --thread/--reply-to), subject with no
    `op#N` token: WARN. Replies inside an existing thread are not delegations."""
    if from_agent != "orch-console" or thread or reply_to:
        return
    if _OP_TOKEN_RE.search(subject or ""):
        return
    try:
        if not (is_lane or _is_lane)(to):
            return
    except (Exception, SystemExit) as e:  # noqa: BLE001 -- dburl() exits on no DSN; a warn must never block a send
        print(f"bus_send: WARNING — op#-citation check could not run ({type(e).__name__}); "
              "sending anyway.", file=stream or sys.stderr)
        return
    print(
        f"bus_send: WARNING — delegation to lane '{to}' has no op number in the subject. "
        "Start it with 'op#N' so the slow-ask net can match the lane's ack to Musa's ask "
        "(console ruling #78298 A, WARN-mode soak).",
        file=stream or sys.stderr,
    )
    _log_warn("delegation_without_op", to=to, subject=subject)


# fable-audit E-client-lanes-quality.md item 5 / hc#359 (bus #64729): hash
# Emirates-ID/NRIC/3-token-Arabic-name/non-system-email shapes out of the
# BODY before insert — a bus row is durable and widely SELECT-able, unlike a
# vault page there is no later "generated mirror" step to catch this at, so
# it has to happen here, at send time. --allow-pii <reason> is a logged
# escape, never a silent bypass.
def apply_pii_redaction(
    body: str, allow_pii: str | None, *, from_agent: str, to: str, subject: str, stream=None,
) -> str:
    if allow_pii:
        _log_warn("pii_escape_used", reason=allow_pii, from_agent=from_agent, to=to, subject=subject)
        print(
            f"bus_send: PII escape used (--allow-pii {allow_pii!r}) — body sent unredacted, logged.",
            file=stream or sys.stderr,
        )
        return body
    redacted, counts = redact_pii(body)
    if counts:
        summary = ", ".join(f"{k}:{v}" for k, v in sorted(counts.items()))
        print(
            f"bus_send: redacted {sum(counts.values())} PII value(s) before insert ({summary}) — "
            "pass --allow-pii <reason> to send unredacted (logged).",
            file=stream or sys.stderr,
        )
        _log_warn("pii_redacted", counts=counts, from_agent=from_agent, to=to, subject=subject)
    return redacted


# Re-exported for callers/tests that reach for bus_send.resolve_from_agent /
# bus_send.IdentityError directly — the real logic now lives in
# scripts/lib/agent_identity.py (bus #47221) so asks_triage.py, asks_open.py,
# etc. can reuse the same fail-closed resolver instead of re-deriving it.
resolve_from_agent = resolve_agent_id


def resolve_sub_tag(from_agent: str, env: dict) -> str | None:
    """Per-instance attribution for a multi-instance family (bus #57180/#57183).

    from_agent stays base-only by design (agent_messages.from_agent is the
    FK-enforced family id, per the dual-identity convention in
    scripts/lib/auto_agent_id.py) -- this does NOT change that. But
    launch_dangerous_cc.sh exports CC_AGENT_ID (the full per-instance sub-tag,
    e.g. cc-cosem-platform-4) alongside CC_BASE_AGENT_ID, and bus_send.py was
    the only sanctioned agent_messages writer that never captured it: every
    lane using the default auto-resolve (or an explicit --from) left `sub_tag`
    NULL, so once a family scales to 3+ concurrent instances (cc-cosem-platform
    hit 5 live), nothing in the row said WHICH instance actually sent it --
    orch-console worked around this by hand-passing --from with the full
    instance id instead, which duplicates the id into from_agent rather than
    recording it where the schema already has a column for exactly this.

    Returns CC_AGENT_ID when present, distinct from from_agent, AND in the
    SAME family as from_agent (i.e. it starts with "{from_agent}-"); None
    when absent (daemons/singletons with no sub-tag), equal to from_agent
    (an explicit --from already named the instance, so sub_tag would be
    pure duplication), or from a DIFFERENT family.

    The family check matters because CC_AGENT_ID is the CALLING process's
    own identity, inherited from whatever lane context invoked it -- it has
    no relation to --from's family just because the two values differ. A
    cross-family value isn't just wrong attribution: the DB's
    agent_messages_sub_tag_family_prefix_chk constraint requires sub_tag to
    start with from_agent + "-", so returning one here would get the WHOLE
    row silently rejected, not just the sub_tag (self-caught near-miss, bus
    #78926/#78927).
    """
    sub_tag = env.get("CC_AGENT_ID")
    if not sub_tag or sub_tag == from_agent:
        return None
    if not sub_tag.startswith(f"{from_agent}-"):
        return None
    return sub_tag


def _refuse_if_sandboxed_but_live(env_path: str) -> None:
    """Defense-in-depth backstop (bus #78935/#78954): a test process leaked a
    real escalation row to the live substrate by resolving dburl()'s .env to
    the LIVE checkout despite the test's own sandboxing attempt. A caller
    that sets WINGMEN_TEST_SANDBOX=1 is asserting "I MUST be isolated from
    the live checkout" -- REFUSE loudly here rather than silently connecting
    if the .env dburl() is about to read is still inside LIVE_ORCH_ROOT, so a
    script that forgot to resolve its own path self-relatively (or any future
    script copy-pasting the old hardcoded-$HOME pattern) fails loudly in a
    test instead of quietly posting a real escalation to production.

    Console's ruling explicitly rejected a redirect hook (an env var that
    picks a DIFFERENT database) in favor of this refusal: a redirect's
    failure mode is a stray env var in a LIVE process silently sending REAL
    escalations to a test DB -- escalations vanishing is worse than a fake
    one appearing. This flag can only ever narrow what a sandboxed caller is
    allowed to reach, never redirect where a live one connects.
    """
    if os.environ.get("WINGMEN_TEST_SANDBOX") != "1":
        return
    live_root = os.path.realpath(LIVE_ORCH_ROOT)
    if os.path.realpath(env_path) == live_root or os.path.realpath(env_path).startswith(live_root + os.sep):
        raise SystemExit(
            f"WINGMEN_TEST_SANDBOX=1 but {env_path} resolves inside the LIVE "
            f"checkout ({LIVE_ORCH_ROOT}) -- refusing to connect rather than "
            "silently posting to production (bus #78935/#78954)."
        )


def _refuse_if_sandboxed_dsn_is_live(dsn: str | None) -> None:
    """Gap (1) from bus #78963: the FILE-path check above misses the case where
    NO .env exists beside the script -- dburl() then falls through to the
    INHERITED `database_url` argument, i.e. whatever DATABASE_URL the calling
    shell already had exported. launch_dangerous_cc.sh `set -a`s the live .env
    into every lane shell, so a test that sets WINGMEN_TEST_SANDBOX=1 but
    forgets to override DATABASE_URL itself would still silently connect to
    production with no refusal. Match on the live substrate's project ref
    (present in the DSN's pooler username/host) rather than string-equality
    with any one .env line, since the inherited value and the file's own line
    are not guaranteed to be formatted identically."""
    if os.environ.get("WINGMEN_TEST_SANDBOX") != "1":
        return
    if dsn and LIVE_SUBSTRATE_REF in dsn:
        raise SystemExit(
            "WINGMEN_TEST_SANDBOX=1 but the resolved DSN targets the LIVE "
            f"substrate (ref {LIVE_SUBSTRATE_REF}) -- refusing to connect "
            "rather than silently posting to production (bus #78963)."
        )


def dburl(database_url: str | None) -> str:
    """Resolve the DSN. `database_url` must be the ALREADY-RESOLVED value (e.g.
    `os.environ.get("DATABASE_URL")`) -- NEVER the whole os.environ/env dict.

    WHY (bus #64769/#64771, #67528/#67541, #74596/#74623 -- the same leak class hit
    6+ times): this used to take `env: dict` and every caller passed the whole
    `os.environ` mapping as that argument.
    When dburl() raised (no DATABASE_URL resolved -- a real, expected failure mode,
    e.g. in a deliberately-scrubbed test env), that `env` parameter sat as a LOCAL
    VARIABLE in this function's own stack frame holding the FULL process environment.
    Any traceback-rendering path that prints frame locals -- pytest's own default
    failure renderer is the repeated offender -- then printed every real secret in
    `os.environ` (COSEM_CAAI_BOT_TOKEN, DATABASE_URL itself, etc.) into whatever
    transcript/log captured that traceback. Taking a single resolved string instead
    means no frame anywhere in this call chain can ever hold an environ-shaped local
    again -- the leak vector is closed at the type, not patched at each call site.

    The .env FILE still wins over the inherited environment: a long-running session
    keeps the DATABASE_URL it was launched with, so after a password rotation every
    bus post from it failed auth and kept the pooler circuit breaker tripped
    fleet-wide (2026-09-28 rotation incident). The file is the rotation's single
    push-point (Mini .env; gzb .env -> /dev/shm secrets)."""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    _refuse_if_sandboxed_but_live(env_path)
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    resolved = line.split("=", 1)[1].strip().strip('"')
                    _refuse_if_sandboxed_dsn_is_live(resolved)
                    return resolved
    except FileNotFoundError:
        pass
    if database_url:
        _refuse_if_sandboxed_dsn_is_live(database_url)
        return database_url
    raise SystemExit("no DATABASE_URL: set it in the environment or .env")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--to", required=True, help="to_agent")
    p.add_argument("--type", required=True, choices=_VALID_TYPES, help="message_type")
    p.add_argument("--subject", required=True)
    p.add_argument(
        "--priority", required=True, choices=_VALID_PRIORITIES,
        help="REQUIRED, no default. Hub action-now asks MUST use P1 (or P0) "
             "with --req — see reference_hub_wake_floor_p1_rr.",
    )
    p.add_argument(
        "--body", default=None,
        help="message body as a string (alternative to the default stdin/heredoc). "
             "Mutually exclusive with --body-file AND with piped stdin; same "
             f">= {_MIN_BODY_BYTES}-byte minimum applies.",
    )
    p.add_argument(
        "--body-file", default=None, dest="body_file",
        help="read the message body from this file (alternative to stdin). Mutually "
             "exclusive with --body AND with piped stdin; same minimum applies.",
    )
    p.add_argument("--req", action="store_true", help="requires_response=true")
    p.add_argument("--thread", default=None, help="uuid or prefix of an existing thread")
    p.add_argument("--reply-to", type=int, default=None, help="id of the row being answered")
    p.add_argument(
        "--link-ask", type=int, default=None, dest="link_ask",
        help="operator_asks.id to LINK this row to — never creates a new ask "
             "(Musa op#23554, bus #46353)",
    )
    p.add_argument("--from", dest="from_agent", default=None, help="override identity (default: auto-resolve)")
    p.add_argument(
        "--evidence", default=None,
        help="comparator backing a claim like identical/verified/clean/PASS -- "
             "sha256:<file> | text:<doc> | probe:<sql|cmd>=<count> (bus #58159 item 2, "
             "WARN mode: its absence on a claiming review_request/update warns, does "
             "not refuse)",
    )
    p.add_argument("--dry-run", action="store_true", help="resolve+validate, print the row, do not touch the DB")
    p.add_argument(
        "--to-base", action="store_true",
        help="send to --to literally even if it's a BASE id with live instance(s) "
             "(bus #49220) -- normally refused with the live instance address instead",
    )
    p.add_argument(
        "--skip-pr-author-check", action="store_true", dest="skip_pr_author_check",
        help="send a cc-quality PR verdict to --to literally even if a different live "
             "instance currently holds that PR's head branch (bus #85014) -- normally "
             "refused with the author instance's address instead",
    )
    p.add_argument(
        "--allow-undeliverable", action="store_true", dest="allow_undeliverable",
        help="send anyway to a structurally-undeliverable --to (write-only producer, "
             "no live wake owner) -- logs a warning instead of refusing; for a genuine "
             "deliberate one-off, never the default",
    )
    p.add_argument(
        "--allow-pii", default=None, metavar="REASON", dest="allow_pii",
        help="skip the Emirates-ID/NRIC/Arabic-name/email hash-redaction pass and send the "
             "body as-is -- REQUIRES a reason, logged (never a silent bypass). Default "
             "behavior redacts those shapes before insert (fable-audit E item 5, hc#359).",
    )
    return p


def read_body(stream) -> str:
    body = stream.read()
    if len(body) < _MIN_BODY_BYTES:
        raise SystemExit(
            f"bus_send: REFUSED — body is {len(body)} bytes (<{_MIN_BODY_BYTES}). "
            "Did you forget the heredoc?"
        )
    return body


def _read_body_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as e:
        raise SystemExit(f"bus_send: REFUSED — cannot read --body-file {path!r}: {e}")


def _stdin_has_data(stream) -> bool:
    """True iff piped/redirected stdin actually carries bytes right now. Never blocks:
    a tty has no pending body; otherwise peek via select(timeout=0) and only then read
    (safe — the only caller is about to refuse, so consuming stdin is harmless)."""
    try:
        if stream.isatty():
            return False
    except (OSError, ValueError):
        return False
    try:
        import select
        ready, _, _ = select.select([stream], [], [], 0.0)
    except (OSError, ValueError):
        return False
    if not ready:
        return False
    try:
        return bool(stream.read().strip())
    except Exception:
        return False


def resolve_body(args, stream) -> str:
    """Resolve the message body from EXACTLY ONE source: --body, --body-file, or stdin
    (default/back-compat for existing heredoc callers). More than one source -> refuse.
    The >= _MIN_BODY_BYTES floor applies to every source."""
    if args.body is not None and args.body_file is not None:
        raise SystemExit(
            "bus_send: REFUSED — pass only ONE of --body / --body-file (both were given)."
        )
    flag_body = None
    if args.body is not None:
        flag_body = args.body
    elif args.body_file is not None:
        flag_body = _read_body_file(args.body_file)
    if flag_body is not None:
        if _stdin_has_data(stream):
            raise SystemExit(
                "bus_send: REFUSED — body supplied via flag AND piped stdin; use exactly "
                "ONE source (drop the heredoc, or drop --body/--body-file)."
            )
        if len(flag_body) < _MIN_BODY_BYTES:
            raise SystemExit(
                f"bus_send: REFUSED — body is {len(flag_body)} bytes (<{_MIN_BODY_BYTES}); "
                f"need >= {_MIN_BODY_BYTES}."
            )
        return flag_body
    # No body flag -> read stdin (existing heredoc callers unchanged).
    return read_body(stream)


def send(
    from_agent: str, to: str, mtype: str, subject: str, body: str, priority: str,
    req: bool = False, thread: str | None = None, reply_to: int | None = None,
    link_ask: int | None = None, dsn: str | None = None, allow_undeliverable: bool = False,
    sub_tag: str | None = None, is_test: bool = False, posted_by_identity: str | None = None,
    connect_timeout: int | None = None, statement_timeout_ms: int | None = None,
) -> tuple[int, str]:
    """Do the actual INSERT. The one place the SQL lives — CLI (`main`) and
    every shim (`_bus_tmp.py`, `scratchpad/bus_send.py`) call this so there is
    exactly one INSERT INTO agent_messages in the whole fleet's ad-hoc-send
    path. `priority` has no default here either — callers must pass it.

    `connect_timeout`/`statement_timeout_ms` (bus #80233/#80236, coord's
    PR#591 review; revised after coord's live pooler probe #80453): both
    default to None, which reproduces the pre-existing connect(dsn) call
    exactly -- no behavior change for the ~100 existing callers that don't
    pass them. A caller that runs inside a bounded context (e.g. an EXIT
    trap's SIGALRM backstop) should pass both: SIGALRM cannot interrupt a
    libpq call blocked in C, so it only bounds a connect that fails outright
    (DNS/refused/unroutable) -- a connect that SUCCEEDS and then stalls on a
    slow query is invisible to it. connect_timeout bounds the former,
    straight through to psycopg2.connect.

    statement_timeout_ms bounds the latter via `SET LOCAL statement_timeout`
    as the FIRST statement of this function's own transaction, NOT a
    connection-string `options=` GUC: the real bus DSN goes through a
    Supabase pooler that silently ignores startup options (coord confirmed
    with a live probe -- connect succeeds, but the session's
    statement_timeout stayed at the role default instead of what options=
    asked for). SET LOCAL is pooler-safe because it is an explicit statement
    sent over the already-pooled connection, not a parameter the pooler can
    drop at connection-setup time, and it is scoped to exactly this
    transaction (cleared at this function's own commit(), never leaking into
    whatever the pool hands the next caller)."""
    if priority not in _VALID_PRIORITIES:
        raise ValueError(f"send(): priority must be one of {_VALID_PRIORITIES}, got {priority!r}")
    refuse_if_undeliverable(to, reply_to=reply_to, allow_undeliverable=allow_undeliverable)

    import psycopg2

    connect_kwargs = {}
    if connect_timeout is not None:
        connect_kwargs["connect_timeout"] = connect_timeout
    conn = psycopg2.connect(dsn or dburl(os.environ.get("DATABASE_URL")), **connect_kwargs)
    cur = conn.cursor()
    if statement_timeout_ms is not None:
        cur.execute(f"SET LOCAL statement_timeout = {int(statement_timeout_ms)}")
    cur.execute("SELECT set_config('app.current_agent_id', %s, true)", (from_agent,))

    if reply_to and not thread:
        cur.execute("SELECT thread_id FROM agent_messages WHERE id=%s", (reply_to,))
        row = cur.fetchone()
        thread = str(row[0]) if row else None
    if thread and len(thread) < 36:
        cur.execute(
            "SELECT thread_id FROM agent_messages WHERE thread_id::text LIKE %s "
            "ORDER BY created_at DESC LIMIT 1",
            (thread + "%",),
        )
        row = cur.fetchone()
        if not row:
            raise SystemExit(f"no thread matches prefix {thread}")
        thread = str(row[0])
    if not thread:
        thread = str(uuid.uuid4())

    # backlog#124 step 2/4 (bus #74863): post THROUGH the RPC, never a raw INSERT --
    # this is the fleet-wide cutover that must be live everywhere before migration
    # 124's revoke (direct INSERT -> service_role) can safely apply.
    # Same 9 columns, same order, as the INSERT this replaces -- post_agent_message's
    # signature was audited against this exact call shape (bus #74860) before being
    # written. is_test/posted_by_identity are migration 122's trailing optional params
    # (backlog#124, bus #74939) -- defaults (False/None) reproduce the pre-122 RPC
    # exactly for every caller that doesn't pass them.
    cur.execute(
        "SELECT * FROM post_agent_message(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (from_agent, to, mtype, subject, body, priority, req, thread, sub_tag,
         is_test, posted_by_identity),
    )
    row_id, thread_id = cur.fetchone()

    if link_ask is not None:
        # Musa op#23554 (bus #46353): a delegation is NEVER a new ask — at most
        # it LINKS an existing operator_asks row (one already traceable to a
        # real Musa inbound) to this bus thread, so the "Your asks" board
        # tracks the delegate's live progress. Fails LOUD on a bad/closed id —
        # a caller typo should never silently no-op.
        cur.execute(
            "UPDATE operator_asks SET thread_id=%s, delegated_to=%s "
            "WHERE id=%s AND closed_at IS NULL",
            (thread_id, to, link_ask),
        )
        if cur.rowcount == 0:
            conn.rollback()
            conn.close()
            raise SystemExit(
                f"bus_send: --link-ask {link_ask} does not exist or is already closed"
            )

    if reply_to:
        # orch-console #82607/#82608 (held_commitments#898): this used to stamp
        # read_at/responded_at on the target row for ANY reply referencing it, including a
        # SELF-follow-up (sender == the target row's own from_agent, answering nobody —
        # just adding to its own earlier message). That silently marked genuine unread/
        # unanswered P0/P1s as read+answered: 33 occurrences on orch-console's inbox in
        # 12h, 15 of them P1. Only stamp when the sender of THIS reply is the target row's
        # actual recipient (to_agent) -- i.e. a genuine answer. A self-follow-up's thread_id
        # link is already handled upstream (reply_to backfills `thread` when the caller
        # didn't pass one) -- nothing else to do here for that case.
        #
        # bus #83138 (same-day regression): the first version of this check used a blind
        # -\d+$ strip on BOTH sides to detect base<->instance, which silently broke every
        # numbered-NAME lane family (cc-cosem-exams-2, cc-cosem-adcda-1/2/3,
        # cc-cosem-platform-2/4 -- the digit is the family name, not an instance suffix).
        # bus #83150/#83152 (same-day re-regression): the prefix-matching fast path added
        # for the above was itself unsound (cc-irsyad vs cc-irsyad-coord and 67 other
        # cross-base dash-prefix pairs) and was dropped. _is_genuine_recipient_reply() is
        # now registry-base_agent_id-only (plus the exact-match shortcut) -- never
        # name-shape guessing.
        cur.execute("SELECT from_agent, to_agent FROM agent_messages WHERE id=%s", (reply_to,))
        target_row = cur.fetchone()
        if target_row is not None:
            target_from, target_to = target_row
            if _is_genuine_recipient_reply(from_agent, target_to, dsn=dsn):
                cur.execute(
                    "UPDATE agent_messages SET read_at=coalesce(read_at,now()), "
                    "responded_at=coalesce(responded_at,now()), "
                    "response_ref=coalesce(response_ref,%s) WHERE id=%s",
                    (str(row_id), reply_to),
                )

    conn.commit()
    conn.close()
    return row_id, str(thread_id)


def post_on_conn(
    cur, from_agent: str, to: str, mtype: str, subject: str, body: str, priority: str,
    req: bool = False, thread: str | None = None, sub_tag: str | None = None,
    is_test: bool = False, posted_by_identity: str | None = None,
) -> tuple[int, str]:
    """Like send(), but posts through post_agent_message on a CURSOR the caller
    already has open, instead of opening a new connection -- for the rare call
    site whose bus row must commit (or roll back) together with other writes in
    the same transaction (e.g. irsyad_worker_reaper.py's _reap(): the "REAPED"
    row must not survive if the fleet_lanes/agent_status change it describes
    didn't land). Takes a bare cursor, never a conn, so it structurally cannot
    call commit() -- that boundary stays the caller's."""
    if priority not in _VALID_PRIORITIES:
        raise ValueError(f"post_on_conn(): priority must be one of {_VALID_PRIORITIES}, got {priority!r}")
    if not thread:
        thread = str(uuid.uuid4())
    cur.execute(
        "SELECT * FROM post_agent_message(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (from_agent, to, mtype, subject, body, priority, req, thread, sub_tag,
         is_test, posted_by_identity),
    )
    row_id, thread_id = cur.fetchone()
    return row_id, str(thread_id)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        from_agent = args.from_agent or resolve_from_agent(os.environ)
    except IdentityError as e:
        print(f"bus_send: {e}", file=sys.stderr)
        return 2

    body = resolve_body(args, sys.stdin)
    body = apply_pii_redaction(body, args.allow_pii, from_agent=from_agent, to=args.to, subject=args.subject)

    warn_if_below_hub_wake_floor(args.to, args.req, args.priority)
    warn_if_missing_provenance_citation(args.priority, args.subject, body)
    warn_if_weekday_date_mismatch(args.subject, body)
    warn_if_unevidenced_claim(args.type, args.subject, body, args.evidence)

    if args.dry_run:
        print(f"DRY RUN — would insert: from={from_agent} to={args.to} type={args.type} "
              f"priority={args.priority} req={args.req} subject={args.subject!r} "
              f"body_bytes={len(body)}")
        return 0

    refuse_if_base_has_live_instances(args.to, args.to_base)
    refuse_if_instance_stale_and_base_live(args.to)
    refuse_if_pr_verdict_misrouted(from_agent, args.to, args.subject, skip=args.skip_pr_author_check)
    warn_if_answering_rr_without_reply_to(from_agent, args.to, args.reply_to)

    sub_tag = resolve_sub_tag(from_agent, os.environ)
    row_id, thread_id = send(
        from_agent, args.to, args.type, args.subject, body, args.priority,
        req=args.req, thread=args.thread, reply_to=args.reply_to,
        link_ask=args.link_ask, allow_undeliverable=args.allow_undeliverable,
        sub_tag=sub_tag,
    )
    print(f"SENT id={row_id} thread={thread_id} from={from_agent} to={args.to} "
          f"priority={args.priority} req={args.req}"
          + (f" sub_tag={sub_tag}" if sub_tag else ""))
    # after the row lands: a habit warning must never sit in a refusal's path or delay a send
    warn_if_delegation_without_op(from_agent, args.to, args.subject, args.thread, args.reply_to)
    return 0


if __name__ == "__main__":
    sys.exit(main())
