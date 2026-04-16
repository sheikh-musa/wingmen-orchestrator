"""Tests for _cancel_pending_tasks shutdown helper (BUG-015, Job #82)."""

import asyncio
import warnings
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from wingmen_orch import _cancel_pending_tasks


def test_cancel_pending_tasks_cancels_background_task():
    """A long-running task is cancelled and awaited before loop close,
    with no 'Task was destroyed but it is pending' warning emitted."""
    loop = asyncio.new_event_loop()
    try:
        async def _spin():
            while True:
                await asyncio.sleep(0.05)

        task = loop.create_task(_spin())
        loop.run_until_complete(asyncio.sleep(0.01))

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _cancel_pending_tasks(loop)

        assert task.done()
        assert task.cancelled()
        leak_msgs = [
            str(w.message) for w in caught
            if "Task was destroyed but it is pending" in str(w.message)
        ]
        assert leak_msgs == [], f"Unexpected leak warnings: {leak_msgs}"
    finally:
        loop.close()


def test_cancel_pending_tasks_swallows_exceptions():
    """A background task that raises does not propagate out of the helper."""
    loop = asyncio.new_event_loop()
    try:
        async def _boom():
            await asyncio.sleep(0)
            raise RuntimeError("boom")

        task = loop.create_task(_boom())
        loop.run_until_complete(asyncio.sleep(0.01))

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _cancel_pending_tasks(loop)

        assert task.done()
        leak_msgs = [
            str(w.message) for w in caught
            if "Task was destroyed but it is pending" in str(w.message)
        ]
        assert leak_msgs == [], f"Unexpected leak warnings: {leak_msgs}"
    finally:
        loop.close()


def test_cancel_pending_tasks_noop_when_no_pending():
    """Helper returns cleanly when there is nothing to cancel."""
    loop = asyncio.new_event_loop()
    try:
        _cancel_pending_tasks(loop)
    finally:
        loop.close()
