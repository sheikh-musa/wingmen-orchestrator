#!/usr/bin/env python3
"""PreToolUse guard: a lane may only act on the POWERS its client channels grant.

TG2.0 section 3, Phase A (orch-console #86250). The per-client `powers` key on
`bot_channels.group_routing` says what a lane may do for that client. The
default is ["reply_text"], and everything here is FAIL-CLOSED (design
Refinement #1):

  * absent / empty / unparseable powers -> ["reply_text"] only
  * an action class the powers do not name -> REFUSE
  * a client-outbound command with no channel argument -> REFUSE
  * a channel the lane does not serve -> REFUSE
  * the powers cannot be read (DB error) -> REFUSE

WHY PHASE A KEYS ON THE COMMAND AND NOT ON THE LANE ALONE. The design assumed
the guard could derive "which channel this turn serves" from the lane's own
identity, the way console_irsyad_guard derives the console body. That holds
only for a single-charter lane. A lane session can serve several clients
(`cc-coffeemedia` is the inject_target for both `coffeemedia` and `faizul`;
`cc-bariq` is a build lane that also serves `bariq`), and ingest's
agent-session route is nudge-only — `nudge_session` types "N unread on
<channel>" into the pane and never exports the channel into the lane process.
So a hook sees CC_BASE_AGENT_ID (the lane) and NOT the client. The one place
the client IS nameable today is the outbound boundary itself,
`scripts/lane_reply.sh <channel_key> "<text>"`, so Phase A gates exactly that
and the claim is the channel argument.

Phase A also uses the lane-scoped INTERSECTION of the powers of every channel
the lane serves, not the powers of the channel named in the command: the union
would let a turn aimed at one client spend another client's powers.

Deliberately NOT gated here: read-only tools, and Bash that does not invoke
lane_reply.sh. That is why the ordinary Bash path never opens a database
connection — the guard costs nothing until an outbound reply is attempted.

Phase B (a per-turn claim file) is cancelled by orch-console #86250: agents are
moving to headless sessions where each inbound arrives as a real message
carrying its channel, which supersedes it.

Scope / discriminator: a WORKER LANE is CC_BASE_AGENT_ID present and not
'orch-console' (same shape as lane_prod_key_guard / console_irsyad_guard). The
console (ORCH_BODY_ROLE=console, no CC_BASE_AGENT_ID) and CI (no
CC_BASE_AGENT_ID) are unaffected.

Exit 2 + stderr = the tool call is refused and the reason is shown to the model.
"""
import json
import os
import re
import shlex
import sys

DEFAULT_POWERS = ("reply_text",)
ACTION_REPLY_TEXT = "reply_text"
REPLY_SCRIPT = "lane_reply.sh"
# Shell separators that start a new command. Splitting on these (not doing real
# shell parsing) is enough to tell command position from a mere mention.
_SEPARATORS = re.compile(r"&&|\|\||;|\||\n")
# Interpreters that may be used to run the script explicitly.
_INTERPRETERS = ("bash", "sh", "zsh", "python", "python3")


def lane_base_agent_id():
    """The worker lane this hook is running inside, or None."""
    base = os.environ.get("CC_BASE_AGENT_ID")
    if not base or base == "orch-console":
        return None
    return base


def _lane_session(base):
    """The tmux/session name a lane's channels are usually owned by.

    owner_lane in bot_channels is a lane or session name ('coffeemedia',
    'cosem-exams', 'cc-bariq'), so the base id alone is not enough to find the
    channels a lane serves. An explicit override wins (tests and odd launchers);
    then the live tmux session name; then the base id with its 'cc-' prefix
    stripped, which is how the fleet names these lanes.
    """
    override = os.environ.get("CLIENT_POWERS_GUARD_SESSION")
    if override:
        return override
    try:
        import shutil
        import subprocess
        tmux = shutil.which("tmux") or "/opt/homebrew/bin/tmux"
        r = subprocess.run([tmux, "display-message", "-p", "#S"],
                           capture_output=True, text=True, timeout=1.5)
        name = (r.stdout or "").strip()
        if r.returncode == 0 and name:
            return name
    except Exception:
        pass
    return base[3:] if base.startswith("cc-") else base


def powers_for_channel(raw):
    """The powers granted for one channel, fail-closed.

    Absent, empty, or unparseable -> reply_text only: a channel that never
    declared powers gets the minimum, never everything.
    """
    if isinstance(raw, str):
        return {raw.strip()} if raw.strip() else set(DEFAULT_POWERS)
    if isinstance(raw, dict):
        raw = list(raw.keys())
    if not isinstance(raw, (list, tuple, set)):
        return set(DEFAULT_POWERS)
    names = {p.strip() for p in raw if isinstance(p, str) and p.strip()}
    return names or set(DEFAULT_POWERS)


def intersection_powers(power_sets):
    """The lane-scoped grant: what EVERY served channel allows.

    Empty input is an empty grant, not an unrestricted one.
    """
    sets = list(power_sets)
    if not sets:
        return set()
    acc = set(sets[0])
    for s in sets[1:]:
        acc &= set(s)
    return acc


def check_action(powers, action):
    return bool(action) and isinstance(action, str) and action in (powers or set())


def _segments(cmd):
    for seg in _SEPARATORS.split(cmd or ""):
        seg = seg.strip()
        if seg:
            yield seg


def _tokens(seg):
    try:
        return shlex.split(seg, posix=True)
    except ValueError:
        return seg.split()


def _reply_argv(cmd):
    """Tokens after the script name when lane_reply.sh is in COMMAND position.

    Returns None when the script is not being run (only mentioned, grepped,
    echoed, committed), and () when it is run with no argument at all.
    """
    for seg in _segments(cmd):
        toks = _tokens(seg)
        if not toks:
            continue
        head = os.path.basename(toks[0])
        if head == REPLY_SCRIPT:
            return tuple(toks[1:])
        if head in _INTERPRETERS and len(toks) > 1 \
                and os.path.basename(toks[1]) == REPLY_SCRIPT:
            return tuple(toks[2:])
    return None


def extract_reply_channel(cmd):
    """The channel_key named at the lane_reply.sh boundary, or None."""
    argv = _reply_argv(cmd)
    if not argv:
        return None
    for tok in argv:
        if tok.startswith("-"):
            continue
        return tok
    return None


def is_reply_invocation(cmd):
    return _reply_argv(cmd) is not None


class PowersUnavailable(Exception):
    """Powers could not be read — the caller must REFUSE, not guess."""


def served_channels(base_agent_id):
    """{channel_key: raw powers} for every channel this lane serves.

    Raises PowersUnavailable when the powers cannot be read: a guard that
    cannot prove the grant must not widen it.

    CLIENT_POWERS_GUARD_POWERS_JSON is a test seam — the hook process env is
    set by the launcher, not by the lane's own Bash commands, so it cannot be
    used to widen powers mid-turn.
    """
    seam = os.environ.get("CLIENT_POWERS_GUARD_POWERS_JSON")
    if seam is not None:
        try:
            table = json.loads(seam)
        except Exception as e:
            raise PowersUnavailable(f"powers seam is not JSON ({e.__class__.__name__})")
        for key in (base_agent_id, _lane_session(base_agent_id)):
            if key in table:
                return dict(table[key] or {})
        return {}

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise PowersUnavailable("DATABASE_URL is not set")
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            rows = conn.execute(
                "SELECT channel_key, group_routing->'powers' FROM bot_channels "
                "WHERE owner_lane = %s OR inject_target = %s",
                (base_agent_id, _lane_session(base_agent_id)),
            ).fetchall()
    except Exception as e:
        raise PowersUnavailable(f"bot_channels unreadable ({e.__class__.__name__})")
    return {channel: powers for channel, powers in rows}


def _deny(reason):
    sys.stderr.write(
        "BLOCKED by client_powers_guard: " + reason + " A lane may only act on the "
        "powers its client channels grant (default ['reply_text']; TG2.0 section 3, "
        "orch-console #86250). If this reply is legitimate, the channel's "
        "group_routing->'powers' must name it — ask the console/coord to grant it; "
        "do not work around this guard.\n")
    return 2


def main():
    base = lane_base_agent_id()
    if not base:
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return _deny("the guard could not read the tool input (fail-closed for a worker lane).")

    if payload.get("tool_name") != "Bash":
        return 0
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if not is_reply_invocation(cmd):
        return 0  # not the outbound boundary: no DB, no opinion

    channel = extract_reply_channel(cmd)
    if not channel:
        return _deny("this lane_reply.sh call names no channel — the channel is the "
                     "claim the powers are checked against.")

    try:
        served = served_channels(base)
    except PowersUnavailable as e:
        return _deny(f"the powers for {base} cannot be verified right now ({e}); refusing "
                     "the outbound rather than assuming a grant.")

    if channel not in served:
        return _deny(f"channel {channel!r} is not one this lane serves "
                     f"(served: {sorted(served) or 'none'}).")

    grant = intersection_powers(powers_for_channel(p) for p in served.values())
    if not check_action(grant, ACTION_REPLY_TEXT):
        return _deny(f"the lane-scoped grant for {base} is {sorted(grant) or 'empty'}, which "
                     f"does not include {ACTION_REPLY_TEXT!r} for channel {channel!r}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
