"""dsn_at_use — read the substrate DSN from the live env file at the moment of use.

WHY. The 5-min heartbeat loops in boot_nazim.sh, boot_cai.sh and boot_fleet_health.sh
read DATABASE_URL from their own process environment, which was inherited from the
boot shell. When the substrate password is rotated, .env is rewritten but those
processes keep the DEAD value in memory, so every later beat fails — and every failing
beat is wrapped in `|| true`, so the failure is silent and only its EFFECT shows up
five minutes later as a fleet that believes its singletons are dead. The lanes do not
have this problem: launch_dangerous_cc.sh calls load_dotenv() on every beat, so it
picks the new value up by itself.

So this is the same rule the lanes already follow, made available to the singletons:
resolve the DSN from the FILE each time it is used, and only fall back to the process
environment when no file can be read. A rotated .env is then live within one beat,
with no restart and no kill of the heartbeat subshell (killing the subshell would
silence the beat entirely, which is worse than the problem).

Hosts differ: the Mini keeps it at ~/wingmen/orchestrator/.env, the VPS hub at
/home/wingmen/wingmen/orchestrator/.env. ORCH_ENV_FILE overrides both.
"""
from __future__ import annotations

import os
import re

CANDIDATES = (
    os.path.expanduser("~/wingmen/orchestrator/.env"),
    "/home/wingmen/wingmen/orchestrator/.env",
)
LINE_RE = re.compile(r"^\s*(?:export\s+)?(DATABASE_URL|SUPABASE_DB_URL)\s*=\s*(.*)$")


def from_file(env_file: "str | None" = None) -> "str | None":
    """The first of DATABASE_URL / SUPABASE_DB_URL in the env file, unquoted.

    An explicit path (or ORCH_ENV_FILE) REPLACES the candidate list rather than
    prepending to it: a caller that names a file must never silently get another
    host's file instead.
    """
    if env_file:
        paths = [env_file]
    elif os.environ.get("ORCH_ENV_FILE"):
        paths = [os.environ["ORCH_ENV_FILE"]]
    else:
        paths = list(CANDIDATES)
    for path in paths:
        found = {}
        try:
            with open(path, "r", errors="replace") as fh:
                for line in fh:
                    m = LINE_RE.match(line)
                    if m:
                        found.setdefault(m.group(1), m.group(2).strip().strip("\"'"))
        except OSError:
            continue
        # Precedence is the same as the environment form this replaces:
        # DATABASE_URL first, SUPABASE_DB_URL only as its fallback — never
        # whichever line happens to come first in the file.
        for key in ("DATABASE_URL", "SUPABASE_DB_URL"):
            if found.get(key):
                return found[key]
    return None


def dsn(env_file: "str | None" = None) -> "str | None":
    """The DSN to use NOW: the file wins over a possibly-stale inherited env var."""
    return from_file(env_file) or (os.environ.get("DATABASE_URL")
                                   or os.environ.get("SUPABASE_DB_URL"))
