"""Tests for the core orchestrator functions in wingmen_orch.py."""

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import mock_supabase_chain

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")



# RALPH_RUNNER_ENABLED gate (added in PR #24): _ralph_runner_enabled() reads
# this env var on every call and short-circuits pick_next_jobs to [] when
# false. Tests of pick_next_jobs need the gate enabled to exercise actual
# claim logic. Force true at module import so all TestPickNextJobs cases
# run regardless of the developer's .env state.
os.environ["RALPH_RUNNER_ENABLED"] = "true"


class TestRalphRunnerEnabledGate:
    """PR #24: RALPH_RUNNER_ENABLED env-flag short-circuits pick_next_jobs."""

    @pytest.mark.asyncio
    async def test_gate_disabled_returns_empty_without_db_call(self):
        from legacy.wingmen_orch import pick_next_jobs
        sb = MagicMock()
        sb.table = MagicMock()  # any DB call would fail this assertion below
        with patch.dict(os.environ, {"RALPH_RUNNER_ENABLED": "false"}, clear=False):
            result = await pick_next_jobs(sb, set(), max_picks=3)
        assert result == []
        sb.table.assert_not_called()  # short-circuit before any DB touch

    @pytest.mark.asyncio
    async def test_gate_enabled_proceeds_to_db_query(self):
        from legacy.wingmen_orch import pick_next_jobs
        sb = mock_supabase_chain([])
        with patch.dict(os.environ, {"RALPH_RUNNER_ENABLED": "true"}, clear=False):
            result = await pick_next_jobs(sb, set(), max_picks=3)
        # Empty result is fine; what matters is that we didn't short-circuit
        assert result == []


class TestPickNextJobs:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_queued_jobs(self):
        from legacy.wingmen_orch import pick_next_jobs

        sb = mock_supabase_chain([])
        result = await pick_next_jobs(sb, set(), max_picks=3)
        assert result == []

    @pytest.mark.asyncio
    async def test_picks_one_job_per_repo(self):
        from legacy.wingmen_orch import pick_next_jobs

        jobs = [
            {"id": 1, "repo_name": "ihsandms", "status": "queued"},
            {"id": 2, "repo_name": "ihsandms", "status": "queued"},
            {"id": 3, "repo_name": "dookana", "status": "queued"},
        ]
        sb = mock_supabase_chain()
        select_result = MagicMock(data=jobs)
        claim_ihsandms = MagicMock(data=[{"id": 1, "repo_name": "ihsandms", "status": "running"}])
        claim_dookana = MagicMock(data=[{"id": 3, "repo_name": "dookana", "status": "running"}])
        sb.execute = AsyncMock(side_effect=[select_result, claim_ihsandms, claim_dookana])

        result = await pick_next_jobs(sb, set(), max_picks=5)
        assert len(result) == 2
        repos = {j["repo_name"] for j in result}
        assert repos == {"ihsandms", "dookana"}

    @pytest.mark.asyncio
    async def test_skips_repos_with_running_jobs(self):
        from legacy.wingmen_orch import pick_next_jobs

        jobs = [
            {"id": 1, "repo_name": "ihsandms", "status": "queued"},
            {"id": 2, "repo_name": "dookana", "status": "queued"},
        ]
        sb = mock_supabase_chain()
        select_result = MagicMock(data=jobs)
        claim_dookana = MagicMock(data=[{"id": 2, "repo_name": "dookana", "status": "running"}])
        sb.execute = AsyncMock(side_effect=[select_result, claim_dookana])

        result = await pick_next_jobs(sb, running_repos={"ihsandms"}, max_picks=5)
        assert len(result) == 1
        assert result[0]["repo_name"] == "dookana"

    @pytest.mark.asyncio
    async def test_respects_max_picks(self):
        from legacy.wingmen_orch import pick_next_jobs

        jobs = [
            {"id": 1, "repo_name": "ihsandms", "status": "queued"},
            {"id": 2, "repo_name": "dookana", "status": "queued"},
            {"id": 3, "repo_name": "hifz-companion", "status": "queued"},
        ]
        sb = mock_supabase_chain()
        select_result = MagicMock(data=jobs)
        claim = MagicMock(data=[{"id": 1, "repo_name": "ihsandms", "status": "running"}])
        sb.execute = AsyncMock(side_effect=[select_result, claim])

        result = await pick_next_jobs(sb, set(), max_picks=1)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_skips_job_when_cas_claim_fails(self):
        from legacy.wingmen_orch import pick_next_jobs

        jobs = [
            {"id": 1, "repo_name": "ihsandms", "status": "queued"},
            {"id": 2, "repo_name": "dookana", "status": "queued"},
        ]
        sb = mock_supabase_chain()
        select_result = MagicMock(data=jobs)
        cas_fail = MagicMock(data=[])  # another instance grabbed it
        cas_ok = MagicMock(data=[{"id": 2, "repo_name": "dookana", "status": "running"}])
        sb.execute = AsyncMock(side_effect=[select_result, cas_fail, cas_ok])

        result = await pick_next_jobs(sb, set(), max_picks=5)
        assert len(result) == 1
        assert result[0]["repo_name"] == "dookana"

    @pytest.mark.asyncio
    async def test_returns_empty_when_max_picks_zero(self):
        from legacy.wingmen_orch import pick_next_jobs

        sb = mock_supabase_chain([])
        result = await pick_next_jobs(sb, set(), max_picks=0)
        assert result == []
        sb.execute.assert_not_called()


class TestMainLoopConcurrency:
    def test_available_slot_calculation(self):
        max_builds = 3

        # No tasks running — all slots free
        running = {}
        assert max_builds - len(running) == 3

        # One task running
        running = {"ihsandms": MagicMock()}
        assert max_builds - len(running) == 2

        # All slots occupied
        running = {"ihsandms": MagicMock(), "dookana": MagicMock(), "hifz-companion": MagicMock()}
        assert max_builds - len(running) == 0

    @pytest.mark.asyncio
    async def test_respects_max_concurrent_builds(self):
        from legacy.wingmen_orch import pick_next_jobs, MAX_CONCURRENT_BUILDS

        sb = mock_supabase_chain()

        # With 2 of MAX slots occupied, only MAX-2 slots should be requested
        running_tasks = {"ihsandms": MagicMock(), "dookana": MagicMock()}
        available = MAX_CONCURRENT_BUILDS - len(running_tasks)
        running_repos = set(running_tasks.keys())

        if available > 0:
            jobs_data = [{"id": 5, "repo_name": "hifz-companion", "status": "queued"}]
            select_result = MagicMock(data=jobs_data)
            claim_ok = MagicMock(data=[{"id": 5, "repo_name": "hifz-companion", "status": "running"}])
            sb.execute = AsyncMock(side_effect=[select_result, claim_ok])

            result = await pick_next_jobs(sb, running_repos, available)
            assert len(result) <= available
        else:
            result = await pick_next_jobs(sb, running_repos, available)
            assert result == []


class TestRecoverStaleJobs:
    @pytest.mark.asyncio
    async def test_requeues_stale_jobs(self):
        from legacy.wingmen_orch import recover_stale_jobs

        stale_jobs = [{"id": 10, "repo_name": "ihsandms"}]
        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.eq.return_value = sb
        sb.lt.return_value = sb
        sb.update.return_value = sb

        find_result = MagicMock(data=stale_jobs)
        update_result = MagicMock(data=[])
        sb.execute = AsyncMock(side_effect=[find_result, update_result])

        await recover_stale_jobs(sb)
        assert sb.update.called
        update_arg = sb.update.call_args[0][0]
        assert update_arg["status"] == "queued"

    @pytest.mark.asyncio
    async def test_no_stale_jobs_no_update(self):
        from legacy.wingmen_orch import recover_stale_jobs

        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.eq.return_value = sb
        sb.lt.return_value = sb
        sb.execute = AsyncMock(return_value=MagicMock(data=[]))

        await recover_stale_jobs(sb)
        sb.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_does_not_crash(self):
        from legacy.wingmen_orch import recover_stale_jobs

        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.eq.return_value = sb
        sb.lt.return_value = sb
        sb.execute = AsyncMock(side_effect=RuntimeError("db down"))

        await recover_stale_jobs(sb)


class TestCleanupZombieJobs:
    @pytest.mark.asyncio
    async def test_marks_zombie_jobs_failed(self):
        from legacy.wingmen_orch import cleanup_zombie_jobs

        zombie_jobs = [{"id": 5, "repo_name": "ihsandms"}]
        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.eq.return_value = sb
        sb.update.return_value = sb

        find_result = MagicMock(data=zombie_jobs)
        update_result = MagicMock(data=[])
        sb.execute = AsyncMock(side_effect=[find_result, update_result])

        count = await cleanup_zombie_jobs(sb)
        assert count == 1
        assert sb.update.called
        update_arg = sb.update.call_args[0][0]
        assert update_arg["status"] == "failed"
        assert update_arg["result_summary"].startswith("Zombie:")

    @pytest.mark.asyncio
    async def test_no_zombies_no_update(self):
        from legacy.wingmen_orch import cleanup_zombie_jobs

        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.eq.return_value = sb
        sb.execute = AsyncMock(return_value=MagicMock(data=[]))

        count = await cleanup_zombie_jobs(sb)
        assert count == 0
        sb.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_does_not_crash(self):
        from legacy.wingmen_orch import cleanup_zombie_jobs

        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.eq.return_value = sb
        sb.execute = AsyncMock(side_effect=RuntimeError("db down"))

        count = await cleanup_zombie_jobs(sb)
        assert count == 0

    @pytest.mark.asyncio
    async def test_bumps_fail_count_on_zombie(self):
        """TASK-033 spec deviation #3 fix (per CC-UPDATE-015).
        Without fail_count bumps, a repeatedly-zombied job never hits
        the 3-fail pause threshold — flaps forever."""
        from legacy.wingmen_orch import cleanup_zombie_jobs

        zombie_jobs = [{"id": 5, "repo_name": "ihsandms", "fail_count": 2}]
        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.eq.return_value = sb
        sb.update.return_value = sb
        sb.execute = AsyncMock(side_effect=[MagicMock(data=zombie_jobs), MagicMock(data=[])])

        count = await cleanup_zombie_jobs(sb)
        assert count == 1
        update_arg = sb.update.call_args[0][0]
        assert update_arg.get("fail_count") == 3, (
            "fail_count must increment so zombied jobs eventually pause"
        )


class TestSetJobStatus:
    @pytest.mark.asyncio
    async def test_sets_status_with_extras(self):
        from legacy.wingmen_orch import set_job_status

        sb = mock_supabase_chain([])
        await set_job_status(sb, job_id=42, status="completed", result_summary="OK")

        sb.update.assert_called_once()
        update_arg = sb.update.call_args[0][0]
        assert update_arg["status"] == "completed"
        assert update_arg["updated_at"] == "now()"
        assert update_arg["result_summary"] == "OK"


class TestEnsureRepo:
    @pytest.mark.asyncio
    async def test_skips_when_path_exists(self, tmp_path):
        from legacy.wingmen_orch import _ensure_repo

        repo_dir = tmp_path / "myrepo"
        repo_dir.mkdir()

        with patch("asyncio.create_subprocess_exec") as mock_proc:
            await _ensure_repo(str(repo_dir), "https://github.com/test/repo")
            mock_proc.assert_not_called()

    @pytest.mark.asyncio
    async def test_clones_when_missing(self, tmp_path):
        from legacy.wingmen_orch import _ensure_repo

        repo_dir = tmp_path / "newrepo"

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await _ensure_repo(str(repo_dir), "https://github.com/test/repo")
            mock_exec.assert_called_once()
            args = mock_exec.call_args[0]
            assert "gh" in args
            assert "clone" in args


class TestGitPull:
    @pytest.mark.asyncio
    async def test_skips_when_no_git_dir(self, tmp_path):
        from legacy.wingmen_orch import _git_pull

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            await _git_pull(str(tmp_path))
            mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_pulls_when_git_exists(self, tmp_path):
        from legacy.wingmen_orch import _git_pull

        (tmp_path / ".git").mkdir()

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Already up to date.", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await _git_pull(str(tmp_path))
            mock_exec.assert_called_once()
            args = mock_exec.call_args[0]
            assert "git" in args
            assert "pull" in args
            assert "--ff-only" in args


class TestGitPush:
    @pytest.mark.asyncio
    async def test_skips_when_no_changes(self):
        from legacy.wingmen_orch import _git_push

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await _git_push("/fake/path", job_id=1, description="test")
            assert mock_exec.call_count == 1
            args = mock_exec.call_args[0]
            assert "status" in args

    @pytest.mark.asyncio
    async def test_commits_and_pushes_changes(self):
        from legacy.wingmen_orch import _git_push

        status_proc = AsyncMock()
        status_proc.returncode = 0
        status_proc.communicate = AsyncMock(return_value=(b" M src/main.py", b""))

        add_proc = AsyncMock()
        add_proc.returncode = 0
        add_proc.communicate = AsyncMock(return_value=(b"", b""))

        commit_proc = AsyncMock()
        commit_proc.returncode = 0
        commit_proc.communicate = AsyncMock(return_value=(b"committed", b""))

        push_proc = AsyncMock()
        push_proc.returncode = 0
        push_proc.communicate = AsyncMock(return_value=(b"pushed", b""))

        procs = [status_proc, add_proc, commit_proc, push_proc]
        call_idx = {"i": 0}

        def side_effect(*args, **kwargs):
            p = procs[call_idx["i"]]
            call_idx["i"] += 1
            return p

        with patch("asyncio.create_subprocess_exec", side_effect=side_effect) as mock_exec:
            await _git_push("/fake/path", job_id=1, description="Add feature")
            assert mock_exec.call_count == 4


class TestRunJob:
    @pytest.mark.asyncio
    async def test_successful_job_full_pipeline(self, sample_job):
        from legacy.wingmen_orch import run_job

        sb = mock_supabase_chain([])

        mock_context = {
            "repo_path": "/tmp/test-repo",
            "repo_config": {"github": "https://github.com/test/repo", "vercel_project": "test"},
        }
        mock_result = {"success": True, "summary": "All good"}
        mock_deploy = {"deployed": True, "url": "https://test.vercel.app"}

        with patch("wingmen_orch.context_loader") as ctx, \
             patch("wingmen_orch.spec_generator") as spec, \
             patch("wingmen_orch.ralph_runner") as ralph, \
             patch("wingmen_orch.deploy_manager") as deploy, \
             patch("wingmen_orch.status_reporter") as reporter, \
             patch("wingmen_orch.test_gate") as tg, \
             patch("wingmen_orch.build_audit") as audit, \
             patch("wingmen_orch._ensure_repo", new_callable=AsyncMock), \
             patch("wingmen_orch._git_pull", new_callable=AsyncMock), \
             patch("wingmen_orch._git_push", new_callable=AsyncMock), \
             patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:

            mock_proc.return_value.communicate = AsyncMock(return_value=(b"", b""))
            ctx.load_context = AsyncMock(return_value=mock_context)
            spec.generate_spec = AsyncMock(return_value="build this")
            spec.validate_spec = MagicMock(return_value=(True, []))
            ralph.run_claude = AsyncMock(return_value=mock_result)
            deploy.deploy = AsyncMock(return_value=mock_deploy)
            tg.run_tests = AsyncMock(return_value={"passed": True, "output": "ok"})
            audit.maybe_audit = AsyncMock()
            audit.verify_work_output = AsyncMock()
            reporter.notify_progress = AsyncMock()
            reporter.report = AsyncMock()
            reporter._format_elapsed = lambda s: f"{int(s)}s"

            await run_job(sb, sample_job)

            ctx.load_context.assert_called_once()
            ralph.run_claude.assert_called_once()
            reporter.report.assert_called_once()
            sb.update.assert_called()
            update_arg = sb.update.call_args[0][0]
            assert update_arg["status"] == "completed"

    @pytest.mark.asyncio
    async def test_failed_job_requeues(self, sample_job):
        from legacy.wingmen_orch import run_job

        sb = mock_supabase_chain([])
        sample_job["fail_count"] = 0

        mock_context = {
            "repo_path": "/tmp/test-repo",
            "repo_config": {"github": "https://github.com/test/repo"},
        }
        mock_result = {"success": False, "summary": "Build failed"}

        with patch("wingmen_orch.context_loader") as ctx, \
             patch("wingmen_orch.spec_generator") as spec, \
             patch("wingmen_orch.ralph_runner") as ralph, \
             patch("wingmen_orch.status_reporter") as reporter, \
             patch("wingmen_orch._ensure_repo", new_callable=AsyncMock), \
             patch("wingmen_orch._git_pull", new_callable=AsyncMock), \
             patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:

            mock_proc.return_value.communicate = AsyncMock(return_value=(b"", b""))
            ctx.load_context = AsyncMock(return_value=mock_context)
            spec.generate_spec = AsyncMock(return_value="build this")
            ralph.run_claude = AsyncMock(return_value=mock_result)
            reporter.notify_progress = AsyncMock()
            reporter._format_elapsed = lambda s: f"{int(s)}s"

            await run_job(sb, sample_job)

            last_update = sb.update.call_args[0][0]
            assert last_update["status"] == "queued"
            assert last_update["fail_count"] == 1

    @pytest.mark.asyncio
    async def test_failed_job_pauses_after_max_failures(self, sample_job):
        from legacy.wingmen_orch import run_job

        sb = mock_supabase_chain([])
        sample_job["fail_count"] = 2

        mock_context = {
            "repo_path": "/tmp/test-repo",
            "repo_config": {"github": "https://github.com/test/repo"},
        }
        mock_result = {"success": False, "summary": "Still failing"}

        with patch("wingmen_orch.context_loader") as ctx, \
             patch("wingmen_orch.spec_generator") as spec, \
             patch("wingmen_orch.ralph_runner") as ralph, \
             patch("wingmen_orch.status_reporter") as reporter, \
             patch("wingmen_orch._ensure_repo", new_callable=AsyncMock), \
             patch("wingmen_orch._git_pull", new_callable=AsyncMock), \
             patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:

            mock_proc.return_value.communicate = AsyncMock(return_value=(b"", b""))
            ctx.load_context = AsyncMock(return_value=mock_context)
            spec.generate_spec = AsyncMock(return_value="build this")
            ralph.run_claude = AsyncMock(return_value=mock_result)
            reporter.notify_progress = AsyncMock()
            reporter.report = AsyncMock()
            reporter._format_elapsed = lambda s: f"{int(s)}s"

            await run_job(sb, sample_job)

            update_calls = [c[0][0] for c in sb.update.call_args_list]
            paused_call = [c for c in update_calls if c.get("status") == "paused"]
            assert len(paused_call) == 1
            assert paused_call[0]["fail_count"] == 3

    @pytest.mark.asyncio
    async def test_job_crash_sets_failure_status(self, sample_job):
        from legacy.wingmen_orch import run_job

        sb = mock_supabase_chain([])
        sample_job["fail_count"] = 0

        with patch("wingmen_orch.context_loader") as ctx, \
             patch("wingmen_orch.status_reporter") as reporter:
            ctx.load_context = AsyncMock(side_effect=RuntimeError("boom"))
            reporter.notify_progress = AsyncMock()

            await run_job(sb, sample_job)

            last_update = sb.update.call_args[0][0]
            assert last_update["status"] == "queued"
            assert last_update["fail_count"] == 1
            assert "boom" in last_update["result_summary"]


class TestAutoccPollGate:
    def test_default_enabled(self, monkeypatch):
        from legacy.wingmen_orch import _autocc_poll_enabled
        monkeypatch.delenv("AUTOCC_POLL_ENABLED", raising=False)
        assert _autocc_poll_enabled() is True

    def test_explicit_true(self, monkeypatch):
        from legacy.wingmen_orch import _autocc_poll_enabled
        monkeypatch.setenv("AUTOCC_POLL_ENABLED", "true")
        assert _autocc_poll_enabled() is True

    def test_disabled_via_false(self, monkeypatch):
        from legacy.wingmen_orch import _autocc_poll_enabled
        monkeypatch.setenv("AUTOCC_POLL_ENABLED", "false")
        assert _autocc_poll_enabled() is False

    def test_disabled_via_zero(self, monkeypatch):
        from legacy.wingmen_orch import _autocc_poll_enabled
        monkeypatch.setenv("AUTOCC_POLL_ENABLED", "0")
        assert _autocc_poll_enabled() is False

    def test_disabled_via_off(self, monkeypatch):
        from legacy.wingmen_orch import _autocc_poll_enabled
        monkeypatch.setenv("AUTOCC_POLL_ENABLED", "OFF")
        assert _autocc_poll_enabled() is False

    def test_case_insensitive(self, monkeypatch):
        from legacy.wingmen_orch import _autocc_poll_enabled
        monkeypatch.setenv("AUTOCC_POLL_ENABLED", "False")
        assert _autocc_poll_enabled() is False

    def test_unrecognized_value_treats_as_enabled(self, monkeypatch):
        """Default-safe: unknown values keep the legacy behaviour."""
        from legacy.wingmen_orch import _autocc_poll_enabled
        monkeypatch.setenv("AUTOCC_POLL_ENABLED", "maybe")
        assert _autocc_poll_enabled() is True
