#!/usr/bin/env python3
"""seed_lane_claude_md.py — seed a new lane's repo with a CLAUDE.md from the fleet
onboarding template, committed as (ideally) the FIRST commit in that repo.

WHY (bus #43474, 2026-09-26): cc-oeh booted into an empty repo with zero fleet
context, dismissed the bus doorbell as 'non-functional', then refused an explicit
nudge as a probable prompt injection -- a reasonable instinct with zero identity/
context. orch-console hand-wrote a CLAUDE.md and cold-restarted it to fix it live.
This script makes that fix part of onboarding for every NEW-repo lane going
forward, so a lane never has to bootstrap trust in its own doorbell from nothing.

Renders templates/lane_claude_template.md with the lane's identity/job/hard-rules
and writes <repo>/CLAUDE.md, then commits it. Run this BEFORE handing a freshly
booted lane its first bus task.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE_PATH = _REPO_ROOT / "templates" / "lane_claude_template.md"


def render_claude_md(
    *,
    base_agent_id: str,
    instance_id: str,
    directing_body: str,
    repo_name: str,
    job_description: str,
    hard_rules: list[str],
    template_path: Path = _TEMPLATE_PATH,
) -> str:
    """PURE: render the template with the given params. No I/O beyond reading the template."""
    template = template_path.read_text()
    rules_block = "\n".join(f"- {rule}" for rule in hard_rules)
    return (
        template
        .replace("{{BASE_AGENT_ID}}", base_agent_id)
        .replace("{{INSTANCE_ID}}", instance_id)
        .replace("{{DIRECTING_BODY}}", directing_body)
        .replace("{{REPO_NAME}}", repo_name)
        .replace("{{JOB_DESCRIPTION}}", job_description)
        .replace("{{HARD_RULES}}", rules_block)
    )


def _git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True, text=True,
    )


def _has_commits(repo_path: Path) -> bool:
    r = _git(repo_path, "rev-parse", "--verify", "HEAD")
    return r.returncode == 0


def seed_and_commit(
    *,
    repo_path: Path,
    base_agent_id: str,
    instance_id: str,
    directing_body: str,
    repo_name: str,
    job_description: str,
    hard_rules: list[str],
    commit_name: str,
    commit_email: str,
) -> str:
    """Write CLAUDE.md into repo_path and commit it. Returns the new commit sha.

    If repo_path has zero commits yet, this IS the first commit. If it already has
    commits, this still commits CLAUDE.md but prints a warning -- a fork that already
    scaffolded something before seeding lost the "truly first commit" property, which
    is a should-not-happen ordering bug worth surfacing, not silently swallowing.
    """
    if not (repo_path / ".git").exists():
        raise SystemExit(f"{repo_path} is not a git repo (no .git/) -- init it first")

    rendered = render_claude_md(
        base_agent_id=base_agent_id,
        instance_id=instance_id,
        directing_body=directing_body,
        repo_name=repo_name,
        job_description=job_description,
        hard_rules=hard_rules,
    )
    already_seeded = _has_commits(repo_path)
    (repo_path / "CLAUDE.md").write_text(rendered)

    _git(repo_path, "add", "CLAUDE.md")
    commit = _git(
        repo_path, "-c", f"user.name={commit_name}", "-c", f"user.email={commit_email}",
        "commit", "-m", f"chore: seed fleet onboarding CLAUDE.md for {base_agent_id}",
    )
    if commit.returncode != 0:
        raise SystemExit(f"git commit failed:\n{commit.stdout}\n{commit.stderr}")

    if already_seeded:
        print(
            f"WARNING: {repo_path} already had commits before this one -- CLAUDE.md "
            "was NOT the first commit. Seed it before any other scaffolding next time.",
            file=sys.stderr,
        )

    sha = _git(repo_path, "rev-parse", "HEAD").stdout.strip()
    return sha


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-path", required=True, type=Path)
    ap.add_argument("--base-agent-id", required=True)
    ap.add_argument("--instance-id", required=True)
    ap.add_argument("--directing-body", required=True)
    ap.add_argument("--repo-name", required=True)
    ap.add_argument("--job-description", required=True)
    ap.add_argument("--rule", action="append", dest="hard_rules", default=[],
                     help="a hard-rule bullet; repeat for multiple rules")
    ap.add_argument("--commit-name", default="sheikh-musa")
    ap.add_argument("--commit-email", default="97861619+sheikh-musa@users.noreply.github.com")
    args = ap.parse_args(argv)

    if not args.hard_rules:
        raise SystemExit("--rule is required at least once (e.g. a data/write-access constraint)")

    sha = seed_and_commit(
        repo_path=args.repo_path,
        base_agent_id=args.base_agent_id,
        instance_id=args.instance_id,
        directing_body=args.directing_body,
        repo_name=args.repo_name,
        job_description=args.job_description,
        hard_rules=args.hard_rules,
        commit_name=args.commit_name,
        commit_email=args.commit_email,
    )
    print(f"seeded {args.repo_path}/CLAUDE.md, committed {sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
