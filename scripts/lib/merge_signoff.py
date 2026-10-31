#!/usr/bin/env python3
"""merge_signoff.py — the independent-review half of the merge gate (op#36818).

WHY THIS EXISTS. merge_pinned.sh checks that the head is the reviewed head and that
CI is green. Both are properties of the PUSH, not of a REVIEW: a head nobody reviewed
still passes if its checks happen to be green, and on 2026-10-31 a FAILED check was
merged because the check itself was not read. The other failure mode is subtler — a
PASS written for an earlier sha, or a PASS the author wrote for their own work, reads
exactly like a review if nothing compares identities and shas.

WHERE THE SIGN-OFF LIVES. agent_messages, in the line the reviewer already writes:
    VERDICT: PASS | owner/repo | PR#n | <full 40-char sha>
No new table, no migration, no change to reviewer behaviour beyond emitting the FULL
sha. A repo file would be authored by the PR author and forgeable by the same push; a
GitHub approval cannot separate identities while every agent shares one token.

WHAT IS REQUIRED (all of it, otherwise exit 2):
  * from_agent resolves — through agent_status.base_agent_id, the fleet's own lane
    registry — to an agent registered in protected_agents with kind
    'auditor-singleton'. Not a hardcoded name: raising a new auditor registers it.
  * that reviewer family is NOT the PR author's family (derived from the branch).
  * the body carries VERDICT: PASS, the repo, this PR number, and the FULL 40-char
    sha. Prefixes are refused: prefix matching is exactly how a stale review slips
    onto a moved head.
  * the row is not older than the head commit's committer date, and is not a test row.
  * fail CLOSED on the lookup itself: no DB, a malformed date, or two DIFFERENT
    reviewer families on the same sha all refuse.

HONEST LIMIT. This is a process gate, not a cryptographic one. Every agent shares the
substrate DSN, so any agent COULD insert a PASS row. What it makes impossible is the
accident: stale PASS, PASS for another sha, self-review, green-but-never-reviewed.
Real attribution needs separate GitHub identities, which is a fleet-wide change.

Usage (from merge_pinned.sh):
  merge_signoff.py --repo owner/name --pr N --head <full40> --branch <headRefName> \
                   --commit-date <ISO8601>
Exit 0 prints one evidence line; exit 2 prints the refusal reason on stderr.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
VERDICT_RE = re.compile(r"VERDICT:\s*(PASS|FAIL)\b", re.IGNORECASE)
SHA40_RE = re.compile(r"\b[0-9a-f]{40}\b", re.IGNORECASE)
LOOKBACK_DAYS = 14


def _refuse(reason: str) -> int:
    print(f"merge_signoff: REFUSED — {reason}", file=sys.stderr)
    return 2


def dsn() -> Optional[str]:
    """DATABASE_URL from the environment, else a dot-env beside the repo (the live
    checkout first, then this worktree — the gate usually runs from a lane worktree,
    which has no dot-env of its own)."""
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    for env_file in (Path.home() / "wingmen" / "orchestrator" / ".env",
                     REPO_ROOT / ".env"):
        try:
            for line in env_file.read_text(errors="replace").splitlines():
                m = re.match(r"^\s*(?:export\s+)?DATABASE_URL\s*=\s*(.*)$", line)
                if m:
                    return m.group(1).strip().strip("\"'")
        except OSError:
            continue
    return None


def connect(dsn_value: Optional[str]):
    """Separate function so tests stub the whole DB at one seam."""
    import psycopg2  # imported lazily: the CLI must fail closed, not ImportError
    return psycopg2.connect(dsn_value)


def reviewer_base(cur, agent: str) -> str:
    """The agent's lane family, via agent_status.base_agent_id (the fleet registry).

    Falls back to the agent's own id when it has no row: an unregistered sender is
    still only a reviewer if that id itself is a registered auditor.
    """
    cur.execute("SELECT base_agent_id FROM agent_status WHERE agent_id = %s LIMIT 1",
                (agent,))
    row = cur.fetchone()
    return (row[0] if row and row[0] else agent) or agent


def auditor_families(cur) -> set:
    cur.execute("SELECT agent_id FROM protected_agents WHERE kind = 'auditor-singleton'")
    return {r[0] for r in cur.fetchall()}


def author_family(cur, branch: str) -> Optional[str]:
    """The agent a branch belongs to, by its leading segment — the bus's own rule."""
    if not branch or "/" not in branch:
        return None
    cand = branch.split("/", 1)[0].strip()
    if not cand or cand in ("fable", "main", "master"):
        return None
    return reviewer_base(cur, cand)


def _parse_ts(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        ts = value
    else:
        try:
            ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def names_pr(text: str, pr: int) -> bool:
    """True when the text refers to this PR number — `PR#765`, `PR 765` or `#765`.

    Number-bounded on both sides so PR#76 does not satisfy PR#765.
    """
    if re.search(rf"(?<![\d#])#\s*{pr}(?![\d])", text) is not None:
        return True
    return re.search(rf"\bPR\s*#?\s*{pr}(?![\d])", text, re.IGNORECASE) is not None


def bound_verdict_lines(text: str, leaf: str, pr: int, head: str):
    """(passing, blocking) verdict LINES that name THIS repo, PR number and full sha.

    Bound to one line on purpose. Matching the four facts independently across a whole
    body lets a row that passes PR#768 elsewhere in the text sign off PR#769: reviewers
    do write combined verdicts ("A passes, B is blocked"), so that is an accident
    waiting to happen, not an adversarial-only case. A qualifying line therefore has to
    be a PASS line that itself carries this repo, this PR number and this full sha.

    A FAIL line naming this same PR and sha is a contradiction — the row denies and
    grants in one breath — and blocks it even if another line would have passed.
    """
    passing, blocking = [], []
    for line in text.splitlines():
        m = VERDICT_RE.search(line)
        if not m:
            continue
        if head not in {s.lower() for s in SHA40_RE.findall(line)}:
            continue
        if leaf not in line.lower() or not names_pr(line, pr):
            continue
        if m.group(1).upper() == "FAIL":
            blocking.append(line.strip())
        else:
            passing.append(line.strip())
    return passing, blocking


def find_signoffs(cur, repo: str, pr: int, head: str, after: datetime):
    """(passing rows, self-contradicting row ids, rows that only FAIL this sha).

    Passing rows are (id, from_agent, created_at, text). FAIL-only rows matter because
    one auditor's PASS must not merge past another auditor's FAIL of the same sha.
    """
    leaf = repo.split("/")[-1].lower()
    cur.execute(
        """
        SELECT id, from_agent, created_at, body, subject, is_test
        FROM agent_messages
        WHERE created_at >= now() - make_interval(days => %s)
          AND (subject ILIKE '%%VERDICT:%%' OR body ILIKE '%%VERDICT:%%')
        ORDER BY id
        """, (LOOKBACK_DAYS,))
    out, contradictions, fails = [], [], []
    for row_id, from_agent, created_at, body, subject, is_test in cur.fetchall():
        text = f"{subject or ''}\n{body or ''}"
        if is_test:
            continue
        passing, blocking = bound_verdict_lines(text, leaf, pr, head)
        if blocking:
            if passing:
                contradictions.append(row_id)
            else:
                ts = _parse_ts(created_at)
                if ts is not None and ts >= after:
                    fails.append((row_id, from_agent, ts))
            continue
        if not passing:
            continue
        ts = _parse_ts(created_at)
        if ts is None or ts < after:
            continue
        out.append((row_id, from_agent, ts, text))
    return out, contradictions, fails


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--head", required=True, help="full 40-char head sha")
    ap.add_argument("--branch", default="", help="PR head branch (author is its prefix)")
    ap.add_argument("--commit-date", required=True, help="head commit committer date, ISO")
    args = ap.parse_args(argv)

    if not re.fullmatch(r"[0-9a-f]{40}", args.head.strip(), re.IGNORECASE):
        return _refuse(f"--head must be the FULL 40-char sha, got {len(args.head)} chars. "
                       "A prefix cannot be matched against a verdict body without "
                       "reintroducing the stale-review hole.")
    head = args.head.strip().lower()
    after = _parse_ts(args.commit_date)
    if after is None:
        return _refuse(f"cannot parse --commit-date {args.commit_date!r}")

    dsn_value = dsn()
    try:
        conn = connect(dsn_value)
    except Exception as exc:  # noqa: BLE001 — every failure here is a refusal
        return _refuse(f"cannot reach the substrate to look for a sign-off "
                       f"({type(exc).__name__}). The gate fails closed: no sign-off "
                       "proven, no merge.")
    try:
        with conn:
            cur = conn.cursor()
            families = auditor_families(cur)
            if not families:
                return _refuse("no agent is registered as an auditor "
                               "(protected_agents kind=auditor-singleton) — the registry "
                               "is the only thing that makes a PASS mean a reviewer.")
            rows, contradictions, fails = find_signoffs(cur, args.repo, args.pr, head,
                                                        after)
            if not rows:
                extra = ""
                if contradictions:
                    extra = (" Rows " + ", ".join(str(c) for c in contradictions) +
                             " name this PR and sha as a FAIL in the same row, so they "
                             "cannot sign it off.")
                return _refuse(
                    f"no VERDICT: PASS row names {args.repo} PR#{args.pr} and the FULL "
                    f"sha {head} on ONE verdict line from a row newer than the head "
                    "commit. Ask the reviewer to re-send in the form "
                    "'VERDICT: PASS | owner/repo | PR#n | <full 40-char sha>'." + extra)
            good, ignored = [], []
            author = author_family(cur, args.branch)
            for row_id, from_agent, ts in fails:
                fam = reviewer_base(cur, from_agent)
                if fam in families and fam != author:
                    return _refuse(f"an independent auditor ({from_agent}, row {row_id}) "
                                   f"FAILed this exact sha {head} — a PASS elsewhere "
                                   "cannot merge past it. Resolve the FAIL first.")
            for row_id, from_agent, ts, _text in rows:
                fam = reviewer_base(cur, from_agent)
                if fam not in families:
                    ignored.append(f"{row_id} from {from_agent} (not an auditor)")
                    continue
                if author and fam == author:
                    ignored.append(f"{row_id} from {from_agent} (the PR author's own family)")
                    continue
                good.append((row_id, from_agent, fam, ts))
            if not good:
                detail = "; ".join(ignored) if ignored else "none matched the auditor bar"
                return _refuse(f"no INDEPENDENT sign-off for {args.repo} PR#{args.pr}: {detail}")
            # Two independent auditors passing the same exact sha is corroboration, not
            # ambiguity — the ambiguity that mattered was a PASS we could not tie to a
            # reviewer or a sha, and binding each verdict to one line settles that. A
            # genuine conflict (one auditor FAILs this sha) is refused above.
            distinct = sorted({g[2] for g in good})
            row_id, from_agent, fam, ts = good[-1]
            others = [f for f in distinct if f != fam]
            note = f" (+corroborated by {', '.join(others)})" if others else ""
            print(f"sign-off: PASS id={row_id} by {from_agent} (family {fam}) at "
                  f"{ts.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} "
                  f"for {args.repo} PR#{args.pr} {head[:12]}{note}")
            return 0
    except Exception as exc:  # noqa: BLE001
        return _refuse(f"the sign-off lookup failed ({type(exc).__name__}: {exc}). "
                       "Fail closed: no sign-off proven, no merge.")
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
