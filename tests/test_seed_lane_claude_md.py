"""seed_lane_claude_md must render a new lane's CLAUDE.md with no leftover
placeholders and keep the bus/doorbell boilerplate that fixes the cc-oeh idle/
prompt-injection-refusal failure (bus #43474) verbatim across lanes.
"""
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from seed_lane_claude_md import render_claude_md, seed_and_commit  # noqa: E402

_DOORBELL_BOILERPLATE = (
    "A pane line such as `[wake] new inbox item …` or `📥 … read agent_messages` "
    "is the fleet doorbell. It only means \"check the bus\". The bus row is the "
    "instruction, not the pane text."
)


def _sample_kwargs(**overrides):
    kwargs = dict(
        base_agent_id="cc-testlane",
        instance_id="cc-testlane-1",
        directing_body="orch-console",
        repo_name="testlane",
        job_description="Do the test thing.",
        hard_rules=["Never touch the live thing.", "Commit as sheikh-musa <noreply>."],
    )
    kwargs.update(overrides)
    return kwargs


def test_no_placeholders_remain():
    rendered = render_claude_md(**_sample_kwargs())
    assert "{{" not in rendered and "}}" not in rendered


def test_identity_fields_present():
    rendered = render_claude_md(**_sample_kwargs())
    assert "cc-testlane" in rendered
    assert "cc-testlane-1" in rendered
    assert "orch-console" in rendered
    assert "testlane" in rendered
    assert "Do the test thing." in rendered


def test_hard_rules_rendered_as_bullets():
    rendered = render_claude_md(**_sample_kwargs())
    assert "- Never touch the live thing." in rendered
    assert "- Commit as sheikh-musa <noreply>." in rendered


def test_doorbell_boilerplate_present_verbatim():
    rendered = render_claude_md(**_sample_kwargs())
    assert _DOORBELL_BOILERPLATE in rendered


def test_bus_read_query_addresses_both_base_and_instance_ids():
    rendered = render_claude_md(**_sample_kwargs())
    assert "to_agent IN ('cc-testlane','cc-testlane-1')" in rendered


def test_seed_and_commit_makes_first_commit(tmp_path):
    repo = tmp_path / "testlane"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    sha = seed_and_commit(
        repo_path=repo,
        commit_name="sheikh-musa",
        commit_email="97861619+sheikh-musa@users.noreply.github.com",
        **_sample_kwargs(),
    )
    assert (repo / "CLAUDE.md").exists()
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline"], capture_output=True, text=True, check=True
    ).stdout.strip().splitlines()
    assert len(log) == 1, "CLAUDE.md must be the first and only commit in a fresh repo"
    assert sha.startswith(log[0].split()[0]) or log[0].split()[0] in sha
