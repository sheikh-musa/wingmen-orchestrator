"""cleanup_zombie_jobs, extracted from legacy/wingmen_orch.py into
scripts/fire_drills/zombie_cleanup.py (Musa op#34845, console's ruling): this
is the real logic scripts/fire_drills/drill_sigkill.py exercises, now a live
module instead of a dependency on the retired legacy/ cluster. Ported
verbatim from the equivalent (skip-marked, now orphaned) TestCleanupZombieJobs
cases in tests/legacy/test_wingmen_orch.py -- same behavior, same assertions,
new home.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.fire_drills.zombie_cleanup import cleanup_zombie_jobs


@pytest.mark.asyncio
async def test_marks_zombie_jobs_failed():
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
async def test_no_zombies_no_update():
    sb = MagicMock()
    sb.table.return_value = sb
    sb.select.return_value = sb
    sb.eq.return_value = sb
    sb.execute = AsyncMock(return_value=MagicMock(data=[]))

    count = await cleanup_zombie_jobs(sb)
    assert count == 0
    sb.update.assert_not_called()


@pytest.mark.asyncio
async def test_exception_does_not_crash():
    sb = MagicMock()
    sb.table.return_value = sb
    sb.select.return_value = sb
    sb.eq.return_value = sb
    sb.execute = AsyncMock(side_effect=RuntimeError("db down"))

    count = await cleanup_zombie_jobs(sb)
    assert count == 0


@pytest.mark.asyncio
async def test_bumps_fail_count_on_zombie():
    """TASK-033 spec deviation #3 fix (per CC-UPDATE-015). Without fail_count
    bumps, a repeatedly-zombied job never hits the 3-fail pause threshold --
    flaps forever."""
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
