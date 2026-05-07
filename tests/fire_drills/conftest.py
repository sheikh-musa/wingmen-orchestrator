"""Force subprocess transport cleanup while the event loop is still alive.

pytest-asyncio (auto mode) creates a fresh event loop per test. Drills spawn
live `git` / `claude` subprocesses via `asyncio.create_subprocess_exec`, which
register transports with the loop. If a transport gets garbage-collected
*after* the loop has closed, its `__del__` calls into the dead loop and
raises `RuntimeError('Event loop is closed')` — which pytest's unraisable
hook then escalates into `RuntimeError: Failed to process unraisable
exception` on the *next* test's setup.

Reproduces on Python 3.9 / Ubuntu CI when collection ordering happens to
schedule a subprocess-spawning live test immediately before its dry-run
sibling. Local macOS runs don't always trigger it because gc timing differs.

Fix: run `gc.collect()` inside the fixture teardown (loop still alive),
which flushes the transports cleanly. No-op for tests that don't spawn
subprocesses.
"""
from __future__ import annotations

import gc

import pytest_asyncio


@pytest_asyncio.fixture(autouse=True)
async def _flush_subprocess_transports():
    yield
    gc.collect()
