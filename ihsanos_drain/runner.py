"""CADENCE-008 A execute arm — worktree + claude -p + CI-gate orchestration.

The orchestration (`execute_ruling`) is pure/DI-driven so every safety branch is
unit-tested without spawning processes:
  - claude session fails / ambiguous  -> escalate, NO merge
  - diff adds a migration not named in the ruling -> refuse, NO merge, NO CI
  - CI red -> escalate, NO merge
  - CI green + clean -> fast-forward merge, record spend

Going live is gated on DRAIN_EXECUTE_ENABLED + both CADENCE-008 gates (window
close + >=2 clean report-only cycles). The thin subprocess wrappers
(claude -p, git, CI) mirror ralph_runner and are wired only behind those gates.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MIGRATION_PREFIX = "supabase/migrations/"
_SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")


@dataclass(frozen=True)
class ExecOutcome:
    status: str  # merged | escalated_ci_red | escalated_ambiguous | refused_migration
    ruling_ref: str
    detail: str
    tokens_spent: int = 0


def unauthorized_migrations(changed_files, decision_text: str) -> list:
    """Migration files in the diff whose basename is not literally named in the
    ruling. cai #2067: filename mandatory; sha optional-but-binding (future)."""
    text = decision_text or ""
    bad = []
    for path in changed_files:
        if not path.startswith(MIGRATION_PREFIX):
            continue
        basename = path[len(MIGRATION_PREFIX):]
        if basename not in text and path not in text:
            bad.append(path)
    return bad


def build_prompt(ruling: dict) -> str:
    ref = ruling.get("decision_ref", "(unknown)")
    decision = ruling.get("decision", "")
    return (
        f"You are the cc-ihsanos drain worker executing pre-authorized ruling "
        f"{ref} in an isolated git worktree.\n\n"
        f"RULING TEXT:\n{decision}\n\n"
        "HARD RULES (non-negotiable):\n"
        "1. Do ONLY the work this ruling authorizes. Nothing else.\n"
        "2. NEVER create or modify any database migration file that is not named "
        "in the ruling above. If the work seems to require an unnamed migration, "
        "STOP and report instead of acting.\n"
        "3. Commit your work in the worktree. Do not push, do not merge, do not "
        "touch secrets or .env.\n"
        "4. If anything is ambiguous or you are unsure, STOP and report rather "
        "than guess.\n"
    )


def execute_ruling(
    ruling: dict,
    *,
    run_claude,
    changed_files_fn,
    run_ci,
    merge_fn,
) -> ExecOutcome:
    ref = ruling.get("decision_ref", "(unknown)")
    prompt = build_prompt(ruling)

    res = run_claude(prompt) or {}
    tokens = int(res.get("tokens", 0))
    if not res.get("ok"):
        return ExecOutcome(
            "escalated_ambiguous", ref,
            res.get("summary", "claude session failed/ambiguous"), tokens,
        )

    bad = unauthorized_migrations(changed_files_fn(), ruling.get("decision", ""))
    if bad:
        return ExecOutcome(
            "refused_migration", ref, f"unauthorized migrations: {bad}", tokens,
        )

    ci = run_ci() or {}
    if not ci.get("green"):
        return ExecOutcome("escalated_ci_red", ref, ci.get("detail", "ci red"), tokens)

    merge_fn()
    return ExecOutcome("merged", ref, res.get("summary", "merged"), tokens)
