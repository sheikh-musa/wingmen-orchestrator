"""Fire-drill harness — 5 end-to-end scenarios through run_job().

Exercises the full pipeline (context load → spec → build → test gate →
deploy → status report) with fully mocked subsystems.

Scenarios
---------
1. Happy path: build succeeds, tests pass, deploy lands.
2. Test gate failure: build succeeds but tests fail → re-queue.
3. Dirty tree rejection: pre-flight fails → crash path.
4. Build crash: Claude CLI throws exception → pause after MAX_FAIL_COUNT.
5. Schema gate block: DDL detected after successful build → early return.
"""

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import mock_supabase_chain

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")



def _drill_mocks(tmp_path):
    """Context manager yielding a namespace of all mocked subsystems.

    Every external call in run_job() is patched.  Individual tests
    override specific mocks before awaiting run_job().
    """
    stack = contextlib.ExitStack()
    ns = {}

    mock_context = {
        "repo_path": str(tmp_path),
        "repo_config": {"github": "https://github.com/test/repo"},
    }

    patches = {
        "ctx": stack.enter_context(patch("wingmen_orch.context_loader")),
        "spec": stack.enter_context(patch("wingmen_orch.spec_generator")),
        "ralph": stack.enter_context(patch("wingmen_orch.ralph_runner")),
        "tgate": stack.enter_context(patch("wingmen_orch.test_gate")),
        "deploy": stack.enter_context(patch("wingmen_orch.deploy_manager")),
        "reporter": stack.enter_context(patch("wingmen_orch.status_reporter")),
        "audit": stack.enter_context(patch("wingmen_orch.build_audit")),
        "ensure": stack.enter_context(patch("wingmen_orch._ensure_repo", new_callable=AsyncMock)),
        "pull": stack.enter_context(patch("wingmen_orch._git_pull", new_callable=AsyncMock)),
        "clean": stack.enter_context(patch("wingmen_orch._check_clean_tree", new_callable=AsyncMock)),
        "push": stack.enter_context(patch("wingmen_orch._git_push", new_callable=AsyncMock)),
        "gitinfo": stack.enter_context(patch("wingmen_orch._capture_git_info", new_callable=AsyncMock)),
        "schema": stack.enter_context(patch("wingmen_orch.schema_gate_check", new_callable=AsyncMock)),
        "set_status": stack.enter_context(patch("wingmen_orch.set_job_status", new_callable=AsyncMock)),
        "write_wo": stack.enter_context(patch("wingmen_orch._write_work_output", new_callable=AsyncMock)),
        "write_ws": stack.enter_context(patch("wingmen_orch._write_work_session", new_callable=AsyncMock)),
        "resolve_cid": stack.enter_context(patch("wingmen_orch._resolve_client_chat_id", new_callable=AsyncMock)),
        "subprocess": stack.enter_context(patch("asyncio.create_subprocess_exec", new_callable=AsyncMock)),
        "verify_wo": None,
    }

    # Wire up defaults — happy-path values
    patches["ctx"].load_context = AsyncMock(return_value=mock_context)
    patches["clean"].return_value = (True, "")
    patches["spec"].generate_spec = AsyncMock(return_value="build spec text")
    patches["spec"].validate_spec = MagicMock(return_value=(True, []))
    patches["ralph"].run_claude = AsyncMock(return_value={"success": True, "summary": "Built widget"})
    patches["tgate"].run_tests = AsyncMock(return_value={"passed": True, "output": "5 passed"})
    patches["push"].return_value = None
    patches["gitinfo"].return_value = {
        "commit_sha": "abc123",
        "files_changed": ["app.py"],
        "diff_summary": "+10 -2",
    }
    patches["schema"].return_value = False
    patches["deploy"].deploy = AsyncMock(return_value={"deployed": True, "url": "https://test.vercel.app"})
    patches["reporter"].notify_progress = AsyncMock()
    patches["reporter"].report = AsyncMock()
    patches["reporter"]._format_elapsed = lambda s: f"{int(s)}s"
    patches["audit"].maybe_audit = AsyncMock()
    patches["audit"].verify_work_output = AsyncMock()
    patches["verify_wo"] = patches["audit"].verify_work_output
    patches["resolve_cid"].return_value = None

    # Subprocess mock for git tag
    proc_mock = MagicMock()
    proc_mock.communicate = AsyncMock(return_value=(b"", b""))
    patches["subprocess"].return_value = proc_mock

    ns.update(patches)
    ns["stack"] = stack
    return ns


def _make_job(**overrides):
    job = {
        "id": 42,
        "repo_name": "ihsandms",
        "description": "Add login page",
        "status": "queued",
        "priority": 1,
        "fail_count": 0,
        "client_id": None,
        "triggered_by": None,
        "created_at": "2026-04-14T00:00:00Z",
        "updated_at": "2026-04-14T00:00:00Z",
        "result_summary": None,
    }
    job.update(overrides)
    return job


class TestFireDrill:
    """End-to-end fire-drill scenarios through run_job()."""

    @pytest.mark.asyncio
    async def test_drill_happy_path(self, tmp_path):
        from legacy.wingmen_orch import run_job

        ns = _drill_mocks(tmp_path)
        with ns["stack"]:
            sb = mock_supabase_chain()
            job = _make_job()

            await run_job(sb, job)

            # Job marked completed
            ns["set_status"].assert_any_call(
                sb, 42, "completed", result_summary="Built widget",
            )
            # Work output written with success=True
            ns["write_wo"].assert_called_once()
            wo_kwargs = ns["write_wo"].call_args
            assert wo_kwargs.kwargs.get("success") is True or \
                (len(wo_kwargs.args) > 2 and True in wo_kwargs.args) or \
                _kw_from_call(wo_kwargs, "success") is True
            # Work session written with outcome=success
            ns["write_ws"].assert_called_once()
            assert _kw_from_call(ns["write_ws"].call_args, "outcome") == "success"
            # Status reporter called
            ns["reporter"].report.assert_called_once()
            # Deploy was called
            ns["deploy"].deploy.assert_called_once()

    @pytest.mark.asyncio
    async def test_drill_test_gate_failure(self, tmp_path):
        from legacy.wingmen_orch import run_job

        ns = _drill_mocks(tmp_path)
        with ns["stack"]:
            ns["tgate"].run_tests = AsyncMock(
                return_value={"passed": False, "output": "FAILED test_login"}
            )

            sb = mock_supabase_chain()
            job = _make_job()

            await run_job(sb, job)

            # Re-queued (fail_count 0 → 1, under MAX_FAIL_COUNT)
            ns["set_status"].assert_any_call(
                sb, 42, "queued",
                fail_count=1,
                result_summary="Tests failed: FAILED test_login",
            )
            # Work output with success=False
            ns["write_wo"].assert_called_once()
            assert _kw_from_call(ns["write_wo"].call_args, "success") is False
            # Work session with outcome=failed
            ns["write_ws"].assert_called_once()
            assert _kw_from_call(ns["write_ws"].call_args, "outcome") == "failed"
            # Deploy never called
            ns["deploy"].deploy.assert_not_called()

    @pytest.mark.asyncio
    async def test_drill_dirty_tree_auto_stashes_and_proceeds(self, tmp_path):
        """BUG-019: dirty tree must be auto-stashed, NOT rejected.

        The old rejection-on-dirty-tree behavior was deliberately replaced
        (see wingmen_orch.py:1059-1086). The build continues in an isolated
        worktree (ralph_runner._create_worktree) so pre-existing dirt can't
        contaminate the build, and the stash preserves the files for
        recovery via `git stash list`.

        This test verifies the drill reaches the new code path. For a
        recoverability check against a real repo (stash actually preserves
        the file), see tests/test_auto_stash_recovery.py (STRONG).
        """
        from legacy.wingmen_orch import run_job

        ns = _drill_mocks(tmp_path)
        with ns["stack"]:
            ns["clean"].return_value = (False, "M untracked.txt")

            sb = mock_supabase_chain()
            job = _make_job()

            await run_job(sb, job)

            # Stash subprocess was invoked with the BUG-019 marker.
            stash_calls = [
                c for c in ns["subprocess"].call_args_list
                if len(c.args) >= 4 and c.args[:4] == ("git", "stash", "push", "-u")
            ]
            assert len(stash_calls) == 1, (
                f"expected exactly one auto-stash subprocess call, "
                f"got {len(stash_calls)}: {ns['subprocess'].call_args_list}"
            )
            stash_msg = stash_calls[0].args[5]  # position of -m argument
            assert "BUG-019 auto-stash before job #42" in stash_msg

            # Claude DID run — dirty tree no longer blocks the build.
            ns["ralph"].run_claude.assert_called_once()

            # Job progressed normally (completed, not early-returned to queued).
            ns["set_status"].assert_any_call(
                sb, 42, "completed", result_summary="Built widget",
            )

    @pytest.mark.asyncio
    async def test_drill_build_crash_pauses(self, tmp_path):
        from legacy.wingmen_orch import run_job

        ns = _drill_mocks(tmp_path)
        with ns["stack"]:
            ns["ralph"].run_claude = AsyncMock(
                side_effect=RuntimeError("Claude CLI timeout")
            )

            sb = mock_supabase_chain()
            job = _make_job(fail_count=2)

            await run_job(sb, job)

            # Paused (fail_count 2 → 3 ≥ MAX_FAIL_COUNT)
            ns["set_status"].assert_called_once()
            call_args = ns["set_status"].call_args
            assert call_args.args[2] == "paused"
            assert call_args.kwargs["fail_count"] == 3
            # Work output with success=False
            ns["write_wo"].assert_called_once()
            assert _kw_from_call(ns["write_wo"].call_args, "success") is False
            # Work session with outcome=crashed
            ns["write_ws"].assert_called_once()
            assert _kw_from_call(ns["write_ws"].call_args, "outcome") == "crashed"

    @pytest.mark.asyncio
    async def test_drill_schema_gate_blocks(self, tmp_path):
        from legacy.wingmen_orch import run_job

        ns = _drill_mocks(tmp_path)
        with ns["stack"]:
            ns["schema"].return_value = True  # DDL detected → blocked

            sb = mock_supabase_chain()
            job = _make_job()

            await run_job(sb, job)

            # Deploy never called (schema gate fires before deploy)
            ns["deploy"].deploy.assert_not_called()
            # Job NOT marked completed
            for call in ns["set_status"].call_args_list:
                assert call.args[2] != "completed"
            # notify_progress called with "paused" phase
            paused_calls = [
                c for c in ns["reporter"].notify_progress.call_args_list
                if len(c.args) >= 3 and c.args[2] == "paused"
            ]
            assert len(paused_calls) >= 1
            assert "Schema migration pending" in paused_calls[0].args[3]


def _kw_from_call(call_obj, key):
    """Extract a keyword arg from a mock call, checking both kwargs and positional."""
    if key in call_obj.kwargs:
        return call_obj.kwargs[key]
    return None
