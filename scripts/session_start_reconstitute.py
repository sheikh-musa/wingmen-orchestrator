#!/usr/bin/env python3
"""session_start_reconstitute.py — SessionStart hook that force-reconstitutes the
Nazim console body on a fresh (re)launch.

WHY THIS EXISTS (op#10721): the console boots via `boot_nazim.sh` → a bare
`exec claude ...` — a fresh session with NO reconstitution step. It depended on
Nazim *remembering* to read his handoff, which repeatedly failed: the operator
kept catching "you reset yourself but did not reconstitute." That breaks the
lane-management tool end-to-end, because every relaunch the tool triggers comes
back amnesiac. This hook makes reconstitution structural, not a matter of memory:
on any fresh startup/clear of the CONSOLE body it injects the newest handoff +
the standing inbox-reconcile directive straight into the session's context.

SCOPING (fail-closed to console): only fires for ORCH_BODY_ROLE=console. Any
other body (hub, a lane in an orchestrator worktree) gets NOTHING — the hub reads
its own handoff, lanes have their own boot. On `resume`/`compact` it stays silent:
context is retained there, so re-injecting the handoff would only bloat the window.

LANE EXEMPTION (bus #63378, op#29128 mixup): this hook fires for ANY session
started inside an orchestrator checkout, worktrees included — a lane booted in
`orchestrator.wt-*` inherits the SAME SessionStart hook. `_body_role()` falls back
to reading `.env`'s ORCH_BODY_ROLE when the env var isn't set in-process, and this
repo's `.env` carries `ORCH_BODY_ROLE=console` as the live checkout's default — so
a lane that never set ORCH_BODY_ROLE itself fell through to that default and got
told "You are Nazim / console body." Worse: `console_irsyad_guard.py` exempts any
session WITH `CC_BASE_AGENT_ID` set (the lane discriminator — see
reference_lane_vs_console_discriminator_is_cc_base_agent_id), so a lane that
*believed* it was the console wasn't covered by the guard that stops the console
from posting irsyad gate rulings. cc-substrate hit exactly this: answered
orch-console's inbox and posted a bogus irsyad floor-gate "approval" (#63372,
retracted #63375). Fix: `CC_BASE_AGENT_ID` presence is checked FIRST, before the
`.env` fallback even runs — a lane always gets a short identity notice instead of
the console handoff, regardless of what `.env` says.

OUTPUT: SessionStart additionalContext JSON (per the hooks contract). Fail-safe —
any error prints nothing and exits 0 so a hook fault never blocks a boot.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import sys

sys.path.insert(0, os.environ.get("WINGMEN_ORCH_DIR") or os.path.expanduser("~/wingmen/orchestrator"))

# Normally the live checkout. WINGMEN_ORCH_DIR lets a worktree's copy of this
# hook (and its tests) resolve against that worktree instead of the live repo.
ORCH_DIR = os.environ.get("WINGMEN_ORCH_DIR") or os.path.expanduser("~/wingmen/orchestrator")
# Fresh-context events only. resume/compact retain context — don't re-inject.
RECONSTITUTE_SOURCES = {"startup", "clear"}
_HANDOFF_GLOB = os.path.join(ORCH_DIR, "reports", "nazim-handoff-*.md")
_MAX_HANDOFF_CHARS = 24000  # bound the injection; the newest handoff is ~6KB

# Memory 2.0 Part 2 PR B (op#36768). The boot read has a hard budget: the
# whole hook must stay under 3s, so DB work gets 1.5s (libpq's connect
# timeout is whole seconds, so 1s connect + 1.5s statement). A boot that
# waits on memory is worse than a boot without memory.
_FACTS_TIMEOUT_S = 1.5
_FACTS_MAX_CHARS = 900  # ~250 tokens; boot context is not a dump


def _facts_block() -> str:
    """Facts already on record that this session should not re-ask about.

    Shared with the global hook (scripts/session_start_facts.py) so there is
    one budget, one cap and one fail-safe, not two that can drift.
    """
    try:
        from scripts.lib import boot_facts
        return boot_facts.facts_block()
    except Exception:
        return ""


def _cc_base_agent_id() -> str:
    """The lane discriminator (reference_lane_vs_console_discriminator_is_cc_base_agent_id,
    same signal console_irsyad_guard.py's is_console() uses). Present => this
    process is a lane (builder, coord, cc-substrate, ...), never the console's
    own boot (boot_nazim.sh never sets this)."""
    return os.environ.get("CC_BASE_AGENT_ID", "").strip()


def _lane_identity_notice(base_agent_id: str, instance_agent_id: str) -> str:
    """Pure: the short identity-correction notice a lane session gets instead of
    the console handoff. `instance_agent_id` empty or equal to `base_agent_id`
    means no distinct instance (a singleton/solo lane) — mirrors my_inbox.py's
    my_ids()."""
    ids = f"'{base_agent_id}'"
    if instance_agent_id and instance_agent_id != base_agent_id:
        ids = f"'{base_agent_id}' or '{instance_agent_id}'"
    who = f"lane `{base_agent_id}`"
    if instance_agent_id and instance_agent_id != base_agent_id:
        who += f" (instance `{instance_agent_id}`)"
    return (
        f"ℹ️ SessionStart identity: this session is {who}. "
        "It is NOT the console/Nazim body, regardless of any other boot text you "
        "see (the .env default ORCH_BODY_ROLE=console is for the live checkout's "
        "own boot, not for lanes). Your own inbox is agent_messages where "
        f"to_agent = {ids} — read it with `python3 scripts/lib/my_inbox.py`, "
        "never by querying `to_agent='orch-console'`. Do not reply via "
        "scripts/nazim_send.sh, do not reconcile operator_log/operator_messages "
        "as if you were the operator's console, and do not post gate rulings "
        "reserved for orch-console/cai (op#29128 mixup, bus #63372/#63375/#63378)."
    )


def _body_role() -> str:
    """Resolve ORCH_BODY_ROLE from the live env, falling back to .env."""
    role = os.environ.get("ORCH_BODY_ROLE")
    if role:
        return role.strip()
    env_path = os.path.join(ORCH_DIR, ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("ORCH_BODY_ROLE="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


def _expected_token_fp() -> str | None:
    """The fp the running console SHOULD carry, resolved the SAME way boot_nazim.sh
    resolves the OAuth token — precedence: CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE >
    .nazim_default_token (a durable per-body pointer holding a PATH to a token file;
    op#20537 fleet flip to syed) > .env's CLAUDE_CODE_OAUTH_TOKEN. Never hardcoded.
    Matches the boot-string method (`printf '%s' "$tok" | shasum | cut -c1-12`):
    SHA-1 of the token bytes, first 12 hex chars. Honoring the pointer means a
    durable pool move can't leave this expecting the stale .env default and
    false-alarm every recycle (the 2026-08-07 07ed/6814 slip; op#20537 syed move).
    """
    def _fp(tok: str) -> str | None:
        tok = tok.strip().strip("'\"")
        return hashlib.sha1(tok.encode()).hexdigest()[:12] if tok else None

    # 1) explicit live override (a switch_singleton-style re-token)
    ov = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE", "").strip()
    if ov:
        return _fp(ov)

    # 2) durable per-body pointer: .nazim_default_token holds a PATH to a token file
    ptr = os.path.join(ORCH_DIR, ".nazim_default_token")
    try:
        with open(ptr, "r", encoding="utf-8") as fh:
            tok_path = os.path.expanduser(fh.read().strip())
        if tok_path:
            with open(tok_path, "r", encoding="utf-8") as tf:
                fp = _fp(tf.read())
                if fp:
                    return fp
    except OSError:
        pass

    # 3) fall through to .env's default
    env_path = os.path.join(ORCH_DIR, ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("CLAUDE_CODE_OAUTH_TOKEN="):
                    return _fp(line.split("=", 1)[1])
    except OSError:
        pass
    return None


def _open_asks_block() -> str:
    """op#22669: surface standing operator_asks delegated to this body (console
    = 'orch-console') at boot, so a fresh reconstitution can't lose sight of a
    live ask the way it could lose an unread message before Option B existed.
    Fail-safe — any error (DB down, migration 072 not yet applied, etc.) yields
    an empty block, never a boot-blocking exception (see main()'s outer guard)."""
    try:
        from nervous_system import operator_log
        rows = operator_log.open_asks_for("orch-console")
    except Exception:
        return ""
    if not rows:
        return ""
    lines = ["", "📋 **Open asks delegated to you (orch-console)** — op#22669 ledger:"]
    for (rid, ask, delegated_to, created_at, waiting_on_operator, chase_by) in rows:
        flag = " [WAITING ON MUSA]" if waiting_on_operator else ""
        chase = f" (chase by {chase_by})" if chase_by else ""
        lines.append(f"  - #{rid} ({created_at}){flag}{chase}: {ask}")
    return "\n".join(lines) + "\n"


def _newest_handoff() -> str | None:
    paths = glob.glob(_HANDOFF_GLOB)
    if not paths:
        return None
    # Filenames are date-stamped (nazim-handoff-YYYYMMDD[-suffix].md); mtime is the
    # robust "newest" signal even when a same-day evening file supersedes a morning one.
    return max(paths, key=lambda p: os.path.getmtime(p))


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        payload = {}

    source = (payload.get("source") or "startup").strip()

    if source not in RECONSTITUTE_SOURCES:
        return 0

    # Lane check FIRST, before the console .env-fallback even runs (bus #63378):
    # CC_BASE_AGENT_ID present means this is a lane, full stop, regardless of
    # what ORCH_BODY_ROLE resolves to.
    base_agent_id = _cc_base_agent_id()
    if base_agent_id:
        instance_agent_id = os.environ.get("CC_AGENT_ID", "").strip()
        notice = _lane_identity_notice(base_agent_id, instance_agent_id)
        facts = _facts_block()
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": notice + (("\n" + facts) if facts else ""),
            }
        }))
        return 0

    # Fail-closed scoping: console body only, past this point.
    if _body_role() != "console":
        return 0

    exp_fp = _expected_token_fp()
    fp_clause = (
        f"expected fp from .env = {exp_fp}" if exp_fp
        else "compare against .env's CLAUDE_CODE_OAUTH_TOKEN"
    )

    asks_block = _open_asks_block()

    handoff_path = _newest_handoff()
    if not handoff_path:
        # No handoff on disk — still nudge to reconcile inboxes rather than stay dark.
        context = (
            "You are **Nazim / console body** and just (re)booted with an empty "
            "context. No nazim-handoff file was found under reports/. Before acting: "
            "reconcile BOTH `operator_log.unprocessed()` AND `agent_messages` "
            "(to_agent='orch-console'); verify your OWN running OAuth token off your "
            f"live process ({fp_clause}); reply to the operator ONLY via "
            "`scripts/nazim_send.sh`."
            + asks_block
        )
    else:
        try:
            with open(handoff_path, "r", encoding="utf-8") as fh:
                handoff = fh.read()
        except OSError:
            return 0
        if len(handoff) > _MAX_HANDOFF_CHARS:
            handoff = handoff[:_MAX_HANDOFF_CHARS] + "\n\n…[handoff truncated — read the full file for the rest]"
        rel = os.path.relpath(handoff_path, ORCH_DIR)
        context = (
            "🔁 **CONSOLE RECONSTITUTION (auto-injected on fresh boot — op#10721)**\n\n"
            "You are **Nazim / console body** and just (re)launched with an EMPTY "
            "context. Do NOT act on any operator/bus message until you have absorbed "
            "the handoff below. Then, BEFORE your first outbound action:\n"
            "  1. Verify your OWN running OAuth token off your live process "
            "(walk your shell → claude pid → `ps eww` → CLAUDE_CODE_OAUTH_TOKEN → shasum; "
            f"{fp_clause}). Do not claim a token you have not fingerprinted.\n"
            "  2. Reconcile BOTH `operator_log.unprocessed()` AND `agent_messages` "
            "(to_agent='orch-console'); answer, then stamp EXACTLY the ids you "
            "answered: `operator_log.mark_handled([ids])` (never a high-water stamp "
            "past rows you did not read — Fable audit 2026-09-30 B-2).\n"
            "  3. Reply to the operator ONLY via `scripts/nazim_send.sh` (a terminal "
            "reply never reaches his phone).\n\n"
            "**KNOWN-OPS ONE-LINERS (canon → don't re-derive; full docs in `docs/runbooks/`):**\n"
            "  • GLM spent / 'move lanes to musa' → `.venv/bin/python3 scripts/glm_overflow_switch.py` "
            "(read-only state) then `--apply` to move all glm lanes → Musa. `glm lanes=0` = already done. "
            "Full: `docs/runbooks/glm-playbook.md`.\n\n"
            f"--- newest handoff ({rel}) ---\n\n{handoff}"
            + asks_block
        )

    console_facts = _facts_block()
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context + (("\n" + console_facts) if console_facts else ""),
        }
    }))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Never let a hook fault block a boot.
        sys.exit(0)
