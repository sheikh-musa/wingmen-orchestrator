"""fleet_health SURFACES (never reaps) unread rows whose to_agent has no live wake owner.

WHY (substrate audit #5B). Operator/non-agent addresses like 'musa' and 'substrate' are NOT
wake-eligible recipients (agent_wake never delivers to them), so messages sent there
dead-letter unread forever — and step-4's archive deliberately SPARES them, because reaping
would hide a real misroute. This detector makes them VISIBLE: one coalesced surface to
orch-console per (to_agent) per day, NEVER reaped. Deliverable identities (cc-* lanes, cai,
console, and the hub on its P0/P1 floor) are NOT flagged — a dead-but-once-live cc lane is
step-4's job, not this detector's.

Prod-clean: pure classification only, no DB (importing fleet_health must not load .env under
pytest — the module guards load_dotenv on PYTEST_CURRENT_TEST).
"""
from scripts import fleet_health as fh


def test_operator_and_non_address_are_undeliverable():
    assert fh._undeliverable("musa"), "the operator handle has no live wake owner"
    assert fh._undeliverable("substrate"), "'substrate' is a non-address, no wake owner"


def test_identities_with_a_wake_owner_are_not_flagged():
    assert not fh._undeliverable("cc-quality"), "a worker lane is a deliverable identity"
    assert not fh._undeliverable("cai")
    assert not fh._undeliverable("orch-console")
    # the hub is eligible on its narrow P0/P1 floor — the detector uses that floor, so it is
    # NEVER treated as an undeliverable dead-letter sink.
    assert not fh._undeliverable("cc-orchestrator")


def test_none_target_is_not_flagged():
    # a NULL to_agent is filtered by the query; the predicate must not crash on it either.
    assert not fh._undeliverable(None)


def test_nervous_system_resolves_under_script_invocation(tmp_path):
    """Regression guard for 99de7e3 (#5B): the launchd daemon runs `python3 scripts/fleet_health.py`,
    so sys.path[0] is scripts/ — NOT the repo root. _undeliverable()'s lazy
    `from nervous_system.agent_wake import is_wake_eligible_recipient` then raised
    ModuleNotFoundError and crash-looped the job every 10 min. The other tests here import via
    `from scripts import fleet_health`, which pytest silently masks (repo root already on path),
    so they never caught it. This reproduces the DAEMON's invocation faithfully: a fresh
    interpreter whose sys.path[0] is scripts/ (nothing else), then import fleet_health (which
    must bootstrap the repo root onto sys.path at module load) and resolve nervous_system.

    Prod-clean: PYTEST_CURRENT_TEST is set so fleet_health skips load_dotenv; we only exercise
    the import path, never the DB sweep."""
    import os, subprocess, sys
    orch = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    scripts_dir = os.path.join(orch, "scripts")
    probe = (
        "import sys; "
        f"sys.path.insert(0, {scripts_dir!r}); "      # emulate the daemon: sys.path[0] == scripts/
        "import fleet_health; "                          # module-top bootstrap must add the repo root
        "from nervous_system.agent_wake import is_wake_eligible_recipient; "
        "print('IMPORT_OK')"
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}  # don't let PYTHONPATH mask it
    env["PYTEST_CURRENT_TEST"] = "1"                                  # keep load_dotenv off (prod-clean)
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                       cwd=str(tmp_path), env=env)  # cwd off-repo so the root isn't on path via cwd
    assert r.returncode == 0, f"nervous_system unresolved under script invocation (99de7e3): {r.stderr}"
    assert "IMPORT_OK" in r.stdout
