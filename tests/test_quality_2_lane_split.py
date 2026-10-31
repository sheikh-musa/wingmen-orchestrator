"""The two reviewer lanes must not collapse onto one pointer.

orch-console #87390 split the review queue across two reviewers because the
single reviewer became the bottleneck of the build-review loop once builders
moved to DeepSeek: cc-quality-2 takes cosem-adcda + cosem-platform, cc-quality
keeps orchestrator + everything else + any secrets-P0.

That split is held up by FOUR separate one-word differences, each of which
fails SILENTLY if it regresses — the lane boots, looks healthy, and quietly
becomes the other reviewer (same model pointer, same token, same tmux name, or
the same fleet_lanes routing key). None of them throws. Hence this test.

  1. model pointer   .quality_2_model          not .quality_model
  2. token pointer   .quality_2_default_token  not .quality_default_token
  3. tmux session    quality-2                 not quality
  4. fleet_lanes key cc-quality-2              not cc-quality

(4) is the subtlest: priority_sla_watchdog.lane_map and idle_with_work_watchdog
build a base_agent_id -> ONE-lane dict. Keying this lane on 'cc-quality' would
collide with the existing 'quality' lane and could route a cc-quality-2 nudge
into cc-quality's pane, while still APPEARING to resolve.

Review independence is the reason cc-quality-2 is on an Anthropic model at all,
so the launcher's refusal to boot on a non-Anthropic model is also pinned here.
"""
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO_ROOT / "scripts"
_BOOT = _SCRIPTS / "boot_quality_2.sh"
_LAUNCHD = _SCRIPTS / "boot_quality_2_launchd.sh"
_REGISTER = _SCRIPTS / "register_quality_2.py"


def _code_lines(path: Path):
    """Non-comment, non-blank lines. Comment lines are prose ABOUT the code and
    legitimately name the things the code must not use (e.g. 'NEVER Musa's')."""
    out = []
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(line)
    return out


def _code_text(path: Path) -> str:
    return "\n".join(_code_lines(path))


# --- existence -------------------------------------------------------------

def test_quality_2_files_are_tracked_in_the_repo():
    """They previously lived only in ~/wingmen/quality-2 — untracked and one disk
    failure from gone, unlike their cc-quality siblings."""
    for p in (_BOOT, _LAUNCHD, _REGISTER):
        assert p.is_file(), "missing from the repo: %s" % p


# --- 1. model pointer ------------------------------------------------------

def test_boot_reads_its_own_model_pointer():
    code = _code_text(_BOOT)
    assert ".quality_2_model" in code, "boot_quality_2.sh must read .quality_2_model"
    assert not re.search(r"\.quality_model\b", code), (
        "boot_quality_2.sh must NOT read .quality_model — that is cc-quality's pin; "
        "sharing it silently collapses both reviewers onto one model"
    )


def test_boot_refuses_a_non_anthropic_model():
    """Review independence is the point of this lane: it reviews DeepSeek builders."""
    code = _code_text(_BOOT)
    assert "deepseek" in code and "glm" in code, (
        "boot_quality_2.sh must refuse non-Anthropic models"
    )
    assert re.search(r"\bexit 1\b", code), "the refusal must actually stop the boot"


# --- 2. token pointer ------------------------------------------------------

def test_boot_reads_its_own_token_pointer():
    code = _code_text(_BOOT)
    assert ".quality_2_default_token" in code, (
        "boot_quality_2.sh must read .quality_2_default_token (Console #87390: Syed token, "
        "never Musa's)"
    )
    assert not re.search(r"\.quality_default_token\b", code), (
        "boot_quality_2.sh must NOT fall back to cc-quality's token pointer — that is the "
        "mislabel that would put the second reviewer on the wrong account"
    )


def test_boot_runs_the_name_fingerprint_guard():
    """A mislabelled key file is refused, not run on."""
    code = _code_text(_BOOT)
    assert "token_file_guard" in code, (
        "the token must be verified against the name->fingerprint map, not trusted"
    )


# --- 3. tmux session + agent_status constraint -----------------------------

def test_boot_defaults_to_its_own_tmux_session():
    code = _code_text(_BOOT)
    assert "quality-2" in code
    assert not re.search(r'Q2_TMUX_SESSION:-quality"', code), (
        "the default session must be quality-2, not quality"
    )


def test_agent_status_uses_the_constraint_forced_base_id():
    """agent_status CHECK: base_agent_id = agent_id with a trailing -N stripped.
    'cc-quality-2' therefore MUST carry base_agent_id='cc-quality'. Getting this
    wrong is a CheckViolation at boot — loud, but it must not be 'fixed' by
    weakening the constraint."""
    code = _code_text(_BOOT)
    assert re.search(r"INSERT INTO agent_status", code)
    assert "'cc-quality-2','cc-quality'" in code.replace(" ", ""), (
        "agent_status must be (agent_id='cc-quality-2', base_agent_id='cc-quality')"
    )


# --- 4. fleet_lanes routing key -------------------------------------------

def _fleet_lanes_tuple_values():
    """The literal VALUES tuple of FLEET_LANES_SQL, in column order.

    Deliberately parsed rather than substring-matched: 'cc-quality-2' also appears
    in AGENTS_SQL and the registration check, so `assert "'cc-quality-2'" in code`
    still passes when the routing key itself is regressed to 'cc-quality'. That
    false-pass was caught by mutation-testing this file — do not undo the parsing.
    """
    code = _REGISTER.read_text()
    m = re.search(
        r"INSERT INTO fleet_lanes\s*\([^)]*\)\s*VALUES\s*\((.*?)\)\s*\n\s*ON CONFLICT",
        code,
        re.S,
    )
    assert m, "could not locate the fleet_lanes VALUES tuple in register_quality_2.py"
    # None of the first six values contain a comma, so a plain split is exact here.
    return [v.strip() for v in m.group(1).split(",")]


def test_register_keys_fleet_lanes_on_the_full_instance_id():
    vals = _fleet_lanes_tuple_values()
    # columns: lane, worktree_path, branch, model, launcher, base_agent_id, ...
    assert vals[0] == "'quality-2'", "fleet_lanes.lane must be quality-2, got %r" % vals[0]
    assert vals[5] == "'cc-quality-2'", (
        "fleet_lanes.base_agent_id must be 'cc-quality-2', got %r. lane_map is a "
        "base->ONE-lane dict, so keying on 'cc-quality' would collide with the "
        "'quality' lane and could route a cc-quality-2 nudge into cc-quality's pane"
        % vals[5]
    )


def test_register_launcher_allowlist_keeps_every_prior_entry():
    """The widening must be ADDITIVE. Dropping an entry would break another lane's
    boot the moment it is re-registered."""
    code = _code_text(_REGISTER)
    for prior in (
        "launch_dangerous_cc.sh",
        "boot_cai.sh",
        "boot_fleet_health.sh",
        "boot_quality.sh",
        "boot_quality_2.sh",
    ):
        assert "'%s'" % prior in code, (
            "launcher allow-list lost %r — widening must keep every prior entry" % prior
        )
