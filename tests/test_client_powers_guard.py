"""TG2.0 section 3, Phase A: the per-client powers guard.

Tests scripts/hooks/client_powers_guard.py. A worker LANE may only act on the
powers granted to the client channels it serves; the default is ["reply_text"]
and EVERYTHING is fail-closed (Refinement #1):

  * absent / empty / unparseable powers  -> ["reply_text"] only
  * an action class the powers do not name -> REFUSE
  * a client-outbound command with no channel argument -> REFUSE
  * a channel the lane does not serve -> REFUSE
  * the powers cannot be read (DB unreachable) -> REFUSE

Phase A gates the one outbound boundary that exists today, `lane_reply.sh
<channel_key> "<text>"`, and uses the lane-scoped INTERSECTION of the powers of
every channel the lane serves (the union would let one client's turn spend
another client's powers).

RIGHT-REASON asserts: every DENY asserts exit 2 AND this guard's own marker in
stderr, never merely a non-zero exit (a crash or a missing module would give
that too). Every ALLOW asserts exit 0, so the allow path is proven to run.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).parent.parent / "scripts" / "hooks" / "client_powers_guard.py"
# The refusal marker must be the guard's OWN sentence, not just its filename:
# a missing hook file makes python exit 2 with ".../client_powers_guard.py" in
# stderr, which would satisfy a bare "marker in stderr" assert for the WRONG
# reason. Watched this happen on the first RED run (bus-honest note in the PR).
MARKER = "BLOCKED by client_powers_guard"

SEAM = "CLIENT_POWERS_GUARD_POWERS_JSON"
SESSION_SEAM = "CLIENT_POWERS_GUARD_SESSION"
# A DSN that cannot connect: used to prove the guard refuses when it cannot
# verify powers, and (separately) to prove it does not touch the DB at all on
# the ordinary Bash path.
DEAD_DB = "postgresql://nobody@127.0.0.1:1/none?connect_timeout=1"


def run_hook(tool_name, tool_input, env=None, stdin_override=None):
    payload = stdin_override if stdin_override is not None else json.dumps(
        {"tool_name": tool_name, "tool_input": tool_input})
    full_env = {
        k: v for k, v in os.environ.items()
        if k not in ("CC_BASE_AGENT_ID", "ORCH_BODY_ROLE", SEAM, SESSION_SEAM)
    }
    # Both bus-DSN names are scrubbed from the inherited env so a developer's own
    # shell can never decide what these tests observe (the lane rename op#892 is
    # the whole point of the tests below).
    full_env.pop("DATABASE_URL", None)
    full_env.pop("BUS_DATABASE_URL", None)
    full_env.update(env or {})
    return subprocess.run(
        [sys.executable, str(HOOK)], input=payload, capture_output=True,
        text=True, env=full_env, timeout=120)


def seam(mapping):
    return {SEAM: json.dumps(mapping)}


def reply_cmd(channel="faizul", text="hello"):
    return f"scripts/lane_reply.sh {channel} \"{text}\""


# ── identity scope: only a worker LANE is governed ───────────────────────────

def test_console_is_unaffected():
    r = run_hook("Bash", {"command": reply_cmd()}, env={"ORCH_BODY_ROLE": "console"})
    assert r.returncode == 0, r.stderr
    assert MARKER not in r.stderr


def test_ci_with_no_lane_identity_is_unaffected():
    r = run_hook("Bash", {"command": reply_cmd()}, env={})
    assert r.returncode == 0, r.stderr


# ── the allow path runs ──────────────────────────────────────────────────────

def test_reply_to_a_served_reply_text_channel_is_allowed():
    r = run_hook("Bash", {"command": reply_cmd("faizul")}, env={
        "CC_BASE_AGENT_ID": "cc-coffeemedia",
        **seam({"cc-coffeemedia": {"coffeemedia": ["reply_text"],
                                   "faizul": ["reply_text"]}}),
    })
    assert r.returncode == 0, r.stderr


def test_reply_command_reached_through_cd_and_bash_is_still_recognised():
    for cmd in ('cd scripts && ./lane_reply.sh faizul "hi"',
                'bash scripts/lane_reply.sh faizul "hi"',
                'echo "hi" | scripts/lane_reply.sh faizul'):
        r = run_hook("Bash", {"command": cmd}, env={
            "CC_BASE_AGENT_ID": "cc-coffeemedia",
            **seam({"cc-coffeemedia": {"faizul": ["reply_text"]}}),
        })
        assert r.returncode == 0, (cmd, r.stderr)


# ── fail-closed on the action class ──────────────────────────────────────────

def test_powers_without_reply_text_refuse_the_reply():
    r = run_hook("Bash", {"command": reply_cmd("faizul")}, env={
        "CC_BASE_AGENT_ID": "cc-coffeemedia",
        **seam({"cc-coffeemedia": {"faizul": ["publish_site"]}}),
    })
    assert r.returncode == 2, r.stdout + r.stderr
    assert MARKER in r.stderr


def test_lane_scoped_intersection_refuses_when_a_served_channel_lacks_reply_text():
    """A lane serving two clients where ONE lacks reply_text must not reply —
    the intersection is empty. A union would let the stricter client's turn
    spend the looser client's powers."""
    r = run_hook("Bash", {"command": reply_cmd("faizul")}, env={
        "CC_BASE_AGENT_ID": "cc-coffeemedia",
        **seam({"cc-coffeemedia": {"coffeemedia": ["publish_site", "reply_text"],
                                   "faizul": ["reply_text"]}}),
    })
    # reply_text is in BOTH -> allowed. The interesting case is below.
    assert r.returncode == 0, r.stderr

    r2 = run_hook("Bash", {"command": reply_cmd("coffeemedia")}, env={
        "CC_BASE_AGENT_ID": "cc-coffeemedia",
        **seam({"cc-coffeemedia": {"coffeemedia": ["publish_site"],
                                   "faizul": ["reply_text"]}}),
    })
    assert r2.returncode == 2, r2.stdout + r2.stderr
    assert MARKER in r2.stderr


def test_empty_or_absent_powers_default_to_reply_text_only():
    for powers in ([], {}, None):
        r = run_hook("Bash", {"command": reply_cmd("faizul")}, env={
            "CC_BASE_AGENT_ID": "cc-coffeemedia",
            **seam({"cc-coffeemedia": {"faizul": powers}}),
        })
        assert r.returncode == 0, (powers, r.stderr)


# ── fail-closed on the channel claim ────────────────────────────────────────

def test_missing_channel_argument_is_refused():
    for cmd in ("scripts/lane_reply.sh", "scripts/lane_reply.sh --dry-run"):
        r = run_hook("Bash", {"command": cmd}, env={
            "CC_BASE_AGENT_ID": "cc-coffeemedia",
            **seam({"cc-coffeemedia": {"faizul": ["reply_text"]}}),
        })
        assert r.returncode == 2, (cmd, r.stdout + r.stderr)
        assert MARKER in r.stderr


def test_channel_the_lane_does_not_serve_is_refused():
    r = run_hook("Bash", {"command": reply_cmd("alderei")}, env={
        "CC_BASE_AGENT_ID": "cc-coffeemedia",
        **seam({"cc-coffeemedia": {"faizul": ["reply_text"]}}),
    })
    assert r.returncode == 2, r.stdout + r.stderr
    assert MARKER in r.stderr


def test_lane_with_no_channels_at_all_is_refused():
    r = run_hook("Bash", {"command": reply_cmd("faizul")}, env={
        "CC_BASE_AGENT_ID": "cc-unknown-lane", **seam({}),
    })
    assert r.returncode == 2, r.stdout + r.stderr
    assert MARKER in r.stderr


# ── fail-closed when the powers cannot be verified ──────────────────────────

def test_db_unreachable_refuses_the_outbound():
    r = run_hook("Bash", {"command": reply_cmd("faizul")}, env={
        "CC_BASE_AGENT_ID": "cc-coffeemedia",
        "DATABASE_URL": DEAD_DB,
    })
    assert r.returncode == 2, r.stdout + r.stderr
    assert MARKER in r.stderr


def test_a_lane_env_reaches_the_powers_with_bus_database_url_only():
    """orch-console #90166 / op#892: a LANE's scrubbed env carries the bus DSN as
    BUS_DATABASE_URL and NEVER a bare DATABASE_URL (lane_env_allowlist.sh drops it;
    launch_dangerous_cc.sh refuses to boot if one leaks through). The guard used to
    read only DATABASE_URL, so it failed closed on EVERY client-serving lane.

    This is the whole defect reproduced in its real env shape: BUS_DATABASE_URL
    present, DATABASE_URL absent. The DSN here is dead, so the correct outcome is
    still a refusal -- but for the RIGHT reason: an attempted connect that failed,
    not "no DSN". That distinction is what tells us the lane's DSN was found."""
    r = run_hook("Bash", {"command": reply_cmd("faizul")}, env={
        "CC_BASE_AGENT_ID": "cc-cosem-exams",
        "BUS_DATABASE_URL": DEAD_DB,
    })
    assert r.returncode == 2, r.stdout + r.stderr
    assert MARKER in r.stderr
    # the reason must be an unreadable DB, never a missing variable
    assert "no bus DSN in the hook env" not in r.stderr, r.stderr
    assert "bot_channels unreadable" in r.stderr, r.stderr


def test_neither_bus_dsn_name_present_still_refuses():
    r = run_hook("Bash", {"command": reply_cmd("faizul")}, env={
        "CC_BASE_AGENT_ID": "cc-cosem-exams",
    })
    assert r.returncode == 2, r.stdout + r.stderr
    assert MARKER in r.stderr
    assert "no bus DSN in the hook env" in r.stderr, r.stderr


def test_a_bare_database_url_still_works_for_orchestrator_side_invocation():
    """The fallback keeps direct (unscrubbed) invocation working -- bus_send.py's
    precedence, deliberately mirrored."""
    r = run_hook("Bash", {"command": reply_cmd("faizul")}, env={
        "CC_BASE_AGENT_ID": "cc-cosem-exams",
        "DATABASE_URL": DEAD_DB,
    })
    assert r.returncode == 2, r.stdout + r.stderr
    assert "bot_channels unreadable" in r.stderr, r.stderr


def test_lane_names_covers_both_spellings_and_the_session():
    """`bot_channels.owner_lane` does not use one spelling (the module docstring
    names 'coffeemedia', 'cosem-exams', 'cc-bariq' side by side). The old query
    compared it to the base id alone and inject_target to the session alone, so
    `cc-cosem-exams` (owner_lane 'cosem-exams', inject_target 'exams') matched
    NEITHER and served zero channels even with a working DSN -- the second half
    of #90166. Every spelling the column actually uses must be a candidate."""
    g = _load_module()
    for base, alias in (("cc-cosem-exams", "cosem-exams"), ("bariq", "cc-bariq")):
        names = g._lane_names(base)
        assert base in names, names
        assert alias in names, names
        assert g._lane_session(base) in names, names


def test_unreadable_tool_input_is_refused_for_a_worker_lane():
    r = run_hook(None, None, env={"CC_BASE_AGENT_ID": "cc-coffeemedia"},
                 stdin_override="{not json")
    assert r.returncode == 2
    assert MARKER in r.stderr


# ── no cost and no false positives on the ordinary path ─────────────────────

def test_ordinary_bash_never_touches_the_database():
    """The guard must not add a DB round-trip to every Bash call: with a dead
    DSN a non-outbound command still passes."""
    for cmd in ("git status", "npm run build", "ls -la"):
        r = run_hook("Bash", {"command": cmd}, env={
            "CC_BASE_AGENT_ID": "cc-coffeemedia", "DATABASE_URL": DEAD_DB,
        })
        assert r.returncode == 0, (cmd, r.stderr)


def test_mentioning_the_script_outside_command_position_is_not_gated():
    for cmd in ('echo "lane_reply.sh"',
                'grep -n lane_reply.sh scripts/lane_reply.sh',
                'git log --oneline -- scripts/lane_reply.sh'):
        r = run_hook("Bash", {"command": cmd}, env={
            "CC_BASE_AGENT_ID": "cc-coffeemedia", "DATABASE_URL": DEAD_DB,
        })
        assert r.returncode == 0, (cmd, r.stderr)


def test_non_bash_tools_are_out_of_scope():
    for tool in ("Read", "Write", "Edit", "WebFetch"):
        r = run_hook(tool, {"command": reply_cmd()},
                     env={"CC_BASE_AGENT_ID": "cc-coffeemedia"})
        assert r.returncode == 0, (tool, r.stderr)


# ── module-level behaviour (no subprocess, no DB) ───────────────────────────

def _load_module():
    spec = importlib.util.spec_from_file_location("client_powers_guard", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_powers_for_channel_is_fail_closed():
    g = _load_module()
    for absent in (None, [], {}, ""):
        assert g.powers_for_channel(absent) == {"reply_text"}, absent
    assert g.powers_for_channel(["reply_text"]) == {"reply_text"}
    assert g.powers_for_channel(["publish_site"]) == {"publish_site"}
    # non-string junk is not a power
    assert g.powers_for_channel(["reply_text", 5, None]) == {"reply_text"}
    assert g.powers_for_channel("reply_text") == {"reply_text"}


def test_intersection_powers_is_the_lane_scoped_grant():
    g = _load_module()
    assert g.intersection_powers([{"reply_text"}, {"reply_text", "publish_site"}]) == {"reply_text"}
    assert g.intersection_powers([{"reply_text"}, {"publish_site"}]) == set()
    assert g.intersection_powers([]) == set()


def test_check_action_refuses_anything_not_named():
    g = _load_module()
    assert g.check_action({"reply_text"}, "reply_text") is True
    assert g.check_action({"reply_text"}, "publish_site") is False
    assert g.check_action({"reply_text"}, None) is False
    assert g.check_action({"reply_text"}, "") is False


def test_extract_reply_channel_parses_the_boundary():
    g = _load_module()
    assert g.extract_reply_channel('scripts/lane_reply.sh faizul "hi"') == "faizul"
    assert g.extract_reply_channel('cd scripts && ./lane_reply.sh faizul hi') == "faizul"
    assert g.extract_reply_channel('echo hi | /Users/x/scripts/lane_reply.sh bariq') == "bariq"
    assert g.extract_reply_channel('bash scripts/lane_reply.sh faizul hi') == "faizul"
    assert g.extract_reply_channel("scripts/lane_reply.sh") is None
    assert g.extract_reply_channel('echo "lane_reply.sh"') is None
    assert g.extract_reply_channel("git status") is None


# ── recognition must not be bypassable by ordinary shell prefixes ───────────
# cc-quality PR #725 review (bus #86290): the DECISION was fail-closed but the
# RECOGNITION was not — a leading VAR=val, a common wrapper (env/command/
# timeout/nohup/nice/stdbuf/xargs/sudo/exec/time), or the interpreter `-c`
# form all reached the send path ungated. Same class as PR#322's fleet fix.

_BYPASS_SHAPES = [
    'FOO=bar scripts/lane_reply.sh faizul "hi"',
    'FOO=bar BAZ=1 scripts/lane_reply.sh faizul "hi"',
    'env scripts/lane_reply.sh faizul "hi"',
    'env FOO=bar scripts/lane_reply.sh faizul "hi"',
    'command scripts/lane_reply.sh faizul "hi"',
    'timeout 5 scripts/lane_reply.sh faizul "hi"',
    'nice -n 5 scripts/lane_reply.sh faizul "hi"',
    'nohup scripts/lane_reply.sh faizul "hi"',
    'stdbuf -oL scripts/lane_reply.sh faizul "hi"',
    'sudo scripts/lane_reply.sh faizul "hi"',
    'exec scripts/lane_reply.sh faizul "hi"',
    'bash -c "scripts/lane_reply.sh faizul hi"',
    'sh -c "scripts/lane_reply.sh faizul hi"',
    'FOO=bar bash -c "scripts/lane_reply.sh faizul hi"',
]


def test_prefixed_and_wrapped_invocations_are_still_gated():
    """A lane whose grant lacks reply_text must be REFUSED through every one of
    these shapes, not just the bare one."""
    for cmd in _BYPASS_SHAPES:
        r = run_hook("Bash", {"command": cmd}, env={
            "CC_BASE_AGENT_ID": "cc-coffeemedia",
            **seam({"cc-coffeemedia": {"faizul": ["publish_site"]}}),
        })
        assert r.returncode == 2, (cmd, r.stdout + r.stderr)
        assert MARKER in r.stderr, cmd


def test_prefixed_and_wrapped_invocations_still_allow_a_granted_lane():
    """The same shapes must not over-refuse when the grant DOES include reply_text."""
    for cmd in _BYPASS_SHAPES:
        r = run_hook("Bash", {"command": cmd}, env={
            "CC_BASE_AGENT_ID": "cc-coffeemedia",
            **seam({"cc-coffeemedia": {"faizul": ["reply_text"]}}),
        })
        assert r.returncode == 0, (cmd, r.stderr)


def test_wrapped_forms_still_yield_the_channel():
    g = _load_module()
    for cmd in _BYPASS_SHAPES:
        assert g.extract_reply_channel(cmd) == "faizul", cmd
    assert g.extract_reply_channel('timeout 5 scripts/lane_reply.sh') is None
    assert g.extract_reply_channel('bash -c "git status"') is None
