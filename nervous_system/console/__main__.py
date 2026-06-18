"""`python -m nervous_system.console` — standalone console process.

Runs on its OWN port (CONSOLE_PORT), NEVER inside the orchestrator process
(CAI-RESP-264 condition 5: process isolation). Loads .env for local dev so
CONSOLE_TOKEN / CONSOLE_DB_URL / DATABASE_URL resolve.
"""
from __future__ import annotations

import pathlib

try:
    from dotenv import load_dotenv

    load_dotenv(pathlib.Path(__file__).resolve().parents[2] / ".env")
except Exception:
    # dotenv is optional in container/VPS deploys where env is injected directly.
    pass

from nervous_system.console.app import run

if __name__ == "__main__":
    run()
