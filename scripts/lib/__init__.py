"""Launcher-side helpers. Kept separate from scripts/ top-level so pytest
can import `scripts.lib.auto_agent_id` cleanly without tripping on scripts/
that invoke sys.exit at import time."""
