"""Unit tests for scripts/hooks/live_checkout_edit_guard.py -- the PreToolUse guard that
refuses Edit/Write/NotebookEdit targeting the LIVE ~/wingmen/orchestrator checkout directly
(cc-fleet-health's own memory, live-checkout-edit-slip-20261007: 6+ self-caught recurrences
of the same slip, mechanical fix overdue per that memory's own conclusion).

A malformed/unreadable stdin payload fails OPEN here (unlike console_irsyad_guard, which
fails closed for an identity check it cannot safely skip) -- this guard has no target path
to check against without a readable payload, so there is nothing concrete to refuse."""
import importlib.util
import io
import json
import pathlib

import pytest

_MOD_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "hooks" / "live_checkout_edit_guard.py"
_spec = importlib.util.spec_from_file_location("live_checkout_edit_guard", _MOD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def _run(monkeypatch, tmp_path, *, tool_name, file_path, root=None, console=False):
    monkeypatch.setattr(guard, "LIVE_ORCH_DIR", (root or tmp_path / "orchestrator").resolve())
    if console:
        monkeypatch.setenv("ORCH_BODY_ROLE", "console")
        monkeypatch.delenv("CC_BASE_AGENT_ID", raising=False)
    else:
        monkeypatch.delenv("ORCH_BODY_ROLE", raising=False)
        monkeypatch.delenv("CC_BASE_AGENT_ID", raising=False)
    payload = json.dumps({"tool_name": tool_name, "tool_input": {"file_path": file_path}})
    monkeypatch.setattr(guard.sys, "stdin", io.StringIO(payload))
    return guard.main()


def test_edit_inside_live_checkout_is_blocked(monkeypatch, tmp_path, capsys):
    root = tmp_path / "orchestrator"
    rc = _run(monkeypatch, tmp_path, tool_name="Edit", file_path=str(root / "scripts" / "foo.py"), root=root)
    assert rc == 2
    assert "live_checkout_edit_guard" in capsys.readouterr().err


def test_write_inside_live_checkout_is_blocked(monkeypatch, tmp_path):
    root = tmp_path / "orchestrator"
    rc = _run(monkeypatch, tmp_path, tool_name="Write", file_path=str(root / "scripts" / "new_thing.py"), root=root)
    assert rc == 2


def test_notebook_edit_inside_live_checkout_is_blocked(monkeypatch, tmp_path):
    root = tmp_path / "orchestrator"
    payload = json.dumps({"tool_name": "NotebookEdit",
                           "tool_input": {"notebook_path": str(root / "nb.ipynb")}})
    monkeypatch.setattr(guard, "LIVE_ORCH_DIR", root.resolve())
    monkeypatch.setattr(guard.sys, "stdin", io.StringIO(payload))
    assert guard.main() == 2


def test_edit_in_a_sibling_worktree_is_not_blocked(monkeypatch, tmp_path):
    # orchestrator.wt-foo is a SIBLING, not nested inside orchestrator/ -- a naive substring
    # check (rather than Path.relative_to) would wrongly match this.
    root = tmp_path / "orchestrator"
    sibling = tmp_path / "orchestrator.wt-foo" / "scripts" / "foo.py"
    rc = _run(monkeypatch, tmp_path, tool_name="Edit", file_path=str(sibling), root=root)
    assert rc == 0


def test_edit_in_a_fleet_health_worktree_is_not_blocked(monkeypatch, tmp_path):
    root = tmp_path / "orchestrator"
    wt = tmp_path / "fleet-health" / "wt" / "some-slug" / "scripts" / "foo.py"
    rc = _run(monkeypatch, tmp_path, tool_name="Edit", file_path=str(wt), root=root)
    assert rc == 0


def test_edit_outside_live_checkout_entirely_is_not_blocked(monkeypatch, tmp_path):
    root = tmp_path / "orchestrator"
    rc = _run(monkeypatch, tmp_path, tool_name="Edit", file_path=str(tmp_path / "scratch.py"), root=root)
    assert rc == 0


def test_bash_tool_is_never_matched(monkeypatch, tmp_path):
    # This guard only matches Edit/Write/NotebookEdit -- a Bash call (git fetch/merge, running
    # tests, etc. against the live checkout) is routine and must stay unaffected.
    root = tmp_path / "orchestrator"
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": f"cat {root}/STATUS.md"}})
    monkeypatch.setattr(guard, "LIVE_ORCH_DIR", root.resolve())
    monkeypatch.setattr(guard.sys, "stdin", io.StringIO(payload))
    assert guard.main() == 0


def test_malformed_stdin_fails_open(monkeypatch, tmp_path):
    monkeypatch.setattr(guard, "LIVE_ORCH_DIR", (tmp_path / "orchestrator").resolve())
    monkeypatch.setattr(guard.sys, "stdin", io.StringIO("not json"))
    assert guard.main() == 0


def test_missing_file_path_is_not_blocked(monkeypatch, tmp_path):
    root = tmp_path / "orchestrator"
    payload = json.dumps({"tool_name": "Edit", "tool_input": {}})
    monkeypatch.setattr(guard, "LIVE_ORCH_DIR", root.resolve())
    monkeypatch.setattr(guard.sys, "stdin", io.StringIO(payload))
    assert guard.main() == 0


def test_a_brand_new_untracked_file_path_is_still_blocked(monkeypatch, tmp_path):
    # 2026-10-17 recurrence: a brand-new file, not an edit to an existing tracked one -- the
    # guard must not rely on the path already existing on disk.
    root = tmp_path / "orchestrator"
    never_created = root / "scripts" / "hooks" / "brand_new_watchdog.py"
    assert not never_created.exists()
    rc = _run(monkeypatch, tmp_path, tool_name="Write", file_path=str(never_created), root=root)
    assert rc == 2


def test_console_editing_status_md_is_allowed(monkeypatch, tmp_path):
    root = tmp_path / "orchestrator"
    rc = _run(monkeypatch, tmp_path, tool_name="Edit", file_path=str(root / "STATUS.md"), root=root, console=True)
    assert rc == 0


def test_console_editing_nazim_handoff_is_allowed(monkeypatch, tmp_path):
    root = tmp_path / "orchestrator"
    rc = _run(monkeypatch, tmp_path, tool_name="Write",
              file_path=str(root / "reports" / "nazim-handoff-NOW.md"), root=root, console=True)
    assert rc == 0


def test_console_writing_a_nested_path_under_the_handoff_prefix_is_still_blocked(monkeypatch, tmp_path):
    # cc-quality bus #85297: a bare fnmatch "reports/nazim-handoff*.md" lets `*` cross `/`,
    # over-admitting e.g. reports/nazim-handoff-sub/evil.md. The allowlist covers specific
    # files directly under the live checkout, never a whole subtree.
    root = tmp_path / "orchestrator"
    rc = _run(monkeypatch, tmp_path, tool_name="Write",
              file_path=str(root / "reports" / "nazim-handoff-sub" / "evil.md"), root=root, console=True)
    assert rc == 2


def test_console_editing_any_other_live_checkout_file_is_still_blocked(monkeypatch, tmp_path):
    root = tmp_path / "orchestrator"
    rc = _run(monkeypatch, tmp_path, tool_name="Edit",
              file_path=str(root / "scripts" / "lane_burn_alarm.py"), root=root, console=True)
    assert rc == 2


def test_a_lane_editing_status_md_is_still_blocked(monkeypatch, tmp_path):
    # The allowlist is console-only -- a lane (even with ORCH_BODY_ROLE unset, or a lane that
    # happens to carry CC_BASE_AGENT_ID) gets no exception for STATUS.md.
    root = tmp_path / "orchestrator"
    rc = _run(monkeypatch, tmp_path, tool_name="Edit", file_path=str(root / "STATUS.md"), root=root, console=False)
    assert rc == 2


def test_console_role_with_cc_base_agent_id_is_not_console_and_stays_blocked(monkeypatch, tmp_path):
    # Same discriminator as console_irsyad_guard.is_console(): ORCH_BODY_ROLE=console alone is
    # not enough -- a body carrying CC_BASE_AGENT_ID (lanes, coord, the hub) is never this body.
    root = tmp_path / "orchestrator"
    monkeypatch.setattr(guard, "LIVE_ORCH_DIR", root.resolve())
    monkeypatch.setenv("ORCH_BODY_ROLE", "console")
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-irsyad-coord")
    payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(root / "STATUS.md")}})
    monkeypatch.setattr(guard.sys, "stdin", io.StringIO(payload))
    assert guard.main() == 2


# -- any-body handoff exception (bus #86146, cc-quality) --------------------

def test_non_console_body_writing_its_own_handoff_is_allowed(monkeypatch, tmp_path):
    root = tmp_path / "orchestrator"
    rc = _run(monkeypatch, tmp_path, tool_name="Write",
              file_path=str(root / "reports" / "cc-quality-handoff-NOW.md"), root=root, console=False)
    assert rc == 0


def test_console_role_writing_a_non_console_handoff_is_also_allowed(monkeypatch, tmp_path):
    # Deliberately NOT identity-gated -- the whole point is that the identity signal
    # (ORCH_BODY_ROLE/CC_BASE_AGENT_ID) is unreliable, so this exception doesn't try to
    # match the caller's own name against the filename.
    root = tmp_path / "orchestrator"
    rc = _run(monkeypatch, tmp_path, tool_name="Write",
              file_path=str(root / "reports" / "cc-fleet-health-handoff-NOW.md"), root=root, console=True)
    assert rc == 0


def test_timestamped_handoff_variant_is_allowed(monkeypatch, tmp_path):
    root = tmp_path / "orchestrator"
    rc = _run(monkeypatch, tmp_path, tool_name="Edit",
              file_path=str(root / "reports" / "cc-fleet-health-handoff-20261031T2130Z.md"),
              root=root, console=False)
    assert rc == 0


def test_handoff_nested_under_a_nested_handoff_dir_is_still_blocked(monkeypatch, tmp_path):
    # Same [^/]*-never-crosses-/ discipline as the console allowlist's own regression test.
    root = tmp_path / "orchestrator"
    rc = _run(monkeypatch, tmp_path, tool_name="Write",
              file_path=str(root / "reports" / "cc-quality-handoff-sub" / "evil.md"),
              root=root, console=False)
    assert rc == 2


def test_handoff_filename_must_live_directly_under_reports(monkeypatch, tmp_path):
    root = tmp_path / "orchestrator"
    rc = _run(monkeypatch, tmp_path, tool_name="Write",
              file_path=str(root / "cc-quality-handoff-NOW.md"), root=root, console=False)
    assert rc == 2


def test_arbitrary_md_under_reports_is_not_allowed_by_the_handoff_exception(monkeypatch, tmp_path):
    # Only the *-handoff*.md shape is allowlisted -- an unrelated reports/ doc is still blocked.
    root = tmp_path / "orchestrator"
    rc = _run(monkeypatch, tmp_path, tool_name="Write",
              file_path=str(root / "reports" / "some-other-note.md"), root=root, console=False)
    assert rc == 2


def test_is_console_pure_helper(monkeypatch):
    monkeypatch.setenv("ORCH_BODY_ROLE", "console")
    monkeypatch.delenv("CC_BASE_AGENT_ID", raising=False)
    assert guard.is_console() is True
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-irsyad-coord")
    assert guard.is_console() is False
    monkeypatch.delenv("ORCH_BODY_ROLE", raising=False)
    monkeypatch.delenv("CC_BASE_AGENT_ID", raising=False)
    assert guard.is_console() is False


def test_is_inside_live_checkout_pure_helper(monkeypatch, tmp_path):
    root = tmp_path / "orchestrator"
    monkeypatch.setattr(guard, "LIVE_ORCH_DIR", root.resolve())
    assert guard.is_inside_live_checkout((root / "scripts" / "x.py").resolve()) is True
    assert guard.is_inside_live_checkout((tmp_path / "orchestrator.wt-foo" / "x.py").resolve()) is False
    assert guard.is_inside_live_checkout(root.resolve()) is True  # the root itself
