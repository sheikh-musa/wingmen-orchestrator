"""Tests for scripts/hooks/client_media_read_guard.py (bus #52461/#52465).

Real incident (2026-10-05): a lane Read a genuine UAE-gov trainee gradebook
screenshot directly out of logs/tg_media, skipping the stager entirely. This
guard refuses a lane's Read of raw inbound client media unless a staging
verdict for that exact file already says CLEAN (export_clean_file writes
reports/client-file-staging/<op_id>/<stem>.md on CLEAN; HOLD writes nothing,
ever -- see stage_client_file.py). The console body is exempt (it runs the
stager against the raw file in the first place).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).parent.parent / "scripts" / "hooks" / "client_media_read_guard.py"


def run_hook(tool_name: str, tool_input: dict, env: dict | None = None) -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
    full_env = dict(os.environ)
    full_env.pop("CC_BASE_AGENT_ID", None)
    full_env.pop("ORCH_BODY_ROLE", None)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload, text=True, capture_output=True, env=full_env,
    )


def assert_blocked(tool_name: str, tool_input: dict, env: dict | None = None):
    r = run_hook(tool_name, tool_input, env=env)
    assert r.returncode == 2, f"expected BLOCK for {tool_input!r}, got exit {r.returncode}, stderr={r.stderr!r}"
    assert "client_media_read_guard" in r.stderr


def assert_allowed(tool_name: str, tool_input: dict, env: dict | None = None):
    r = run_hook(tool_name, tool_input, env=env)
    assert r.returncode == 0, f"expected ALLOW for {tool_input!r}, got exit {r.returncode}, stderr={r.stderr!r}"


def _make_media(tmp_path, name="cosem-exams_1_abc123.jpg", subdir=None):
    media_dir = tmp_path / "logs" / "tg_media"
    if subdir:
        media_dir = media_dir / subdir
    media_dir.mkdir(parents=True)
    f = media_dir / name
    f.write_bytes(b"fake-image-bytes")
    return f


def _stage_clean(tmp_path, stem, op_id="op1"):
    out_dir = tmp_path / "reports" / "client-file-staging" / op_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stem}.md").write_text("verdict: CLEAN\n", encoding="utf-8")


def _stage_structure_only(tmp_path, stem, op_id="op1"):
    out_dir = tmp_path / "reports" / "client-file-staging" / op_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stem}.md").write_text(
        "verdict: CLEAN\n\n(structure only — sensitive channel, content withheld)\n",
        encoding="utf-8",
    )


# ---- must BLOCK: unstaged raw client media, lane body ----------------------

def test_blocks_unstaged_media_read(tmp_path):
    f = _make_media(tmp_path)
    assert_blocked("Read", {"file_path": str(f)})


def test_blocks_unstaged_media_even_with_unrelated_staged_file_present(tmp_path):
    f = _make_media(tmp_path)
    _stage_clean(tmp_path, stem="some-other-file")
    assert_blocked("Read", {"file_path": str(f)})


def test_blocks_unstaged_media_in_nested_project_subdir(tmp_path):
    # cc-quality bus #52593/#52596: logs/tg_media/<project>/<file> is the
    # real, standing layout (op#15741) -- a one-level-only match left all
    # 13 project subdirectories (incl. gazzabyte-irsyad, cosem-adcda)
    # completely unguarded.
    f = _make_media(tmp_path, name="gradebook_2_def456.jpg", subdir="gazzabyte-irsyad")
    assert_blocked("Read", {"file_path": str(f)})


def test_blocks_media_in_nested_subdir_with_structure_only_export(tmp_path):
    # cc-quality bus #52593/#52596, finding 2: a structure-only
    # (sensitive-channel) export must NOT unlock the raw Read -- it exists
    # specifically because content heuristics couldn't be trusted.
    f = _make_media(tmp_path, name="progress_3_ghi789.jpg", subdir="cosem-adcda")
    _stage_structure_only(tmp_path, stem="progress_3_ghi789")
    assert_blocked("Read", {"file_path": str(f)})


def test_blocks_unstaged_media_read_via_symlink_outside_tg_media(tmp_path):
    # cc-storefront opus minors-lens F1 (bus #52650): a symlink whose own
    # path does NOT contain logs/tg_media/ but points INTO it must not evade
    # the regex -- normpath alone doesn't resolve symlinks, realpath does.
    f = _make_media(tmp_path, name="gradebook_5_mno345.jpg", subdir="gazzabyte-irsyad")
    link = tmp_path / "innocuous_link.jpg"
    link.symlink_to(f)
    assert_blocked("Read", {"file_path": str(link)})


# ---- must ALLOW: staged CLEAN, console exemption, unrelated paths ----------

def test_allows_staged_clean_media_read(tmp_path):
    f = _make_media(tmp_path)
    _stage_clean(tmp_path, stem=f.stem)
    assert_allowed("Read", {"file_path": str(f)})


def test_allows_staged_clean_media_read_in_nested_subdir(tmp_path):
    f = _make_media(tmp_path, name="navmap_4_jkl012.jpg", subdir="irsyad")
    _stage_clean(tmp_path, stem=f.stem)
    assert_allowed("Read", {"file_path": str(f)})


def test_console_body_exempt_even_when_unstaged(tmp_path):
    f = _make_media(tmp_path)
    assert_allowed(
        "Read", {"file_path": str(f)},
        env={"ORCH_BODY_ROLE": "console"},
    )


def test_lane_with_cc_base_agent_id_is_not_exempt_even_with_console_role(tmp_path):
    # ORCH_BODY_ROLE=console alone isn't the discriminator -- CC_BASE_AGENT_ID
    # present means this is a lane, not the console (matches
    # console_irsyad_guard's own is_console() shape exactly).
    f = _make_media(tmp_path)
    assert_blocked(
        "Read", {"file_path": str(f)},
        env={"ORCH_BODY_ROLE": "console", "CC_BASE_AGENT_ID": "cc-substrate"},
    )


def test_allows_read_of_file_outside_tg_media(tmp_path):
    f = tmp_path / "README.md"
    f.write_text("hello", encoding="utf-8")
    assert_allowed("Read", {"file_path": str(f)})


def test_allows_non_read_tool_even_against_unstaged_media(tmp_path):
    f = _make_media(tmp_path)
    assert_allowed("Bash", {"command": f"cat {f}"})


def test_allows_when_tool_input_missing_file_path():
    assert_allowed("Read", {})
