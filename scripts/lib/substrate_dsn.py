"""substrate_dsn — the fleet's single file-first DATABASE_URL resolver (op#24342).

After a DB-password rotation, a long-lived process keeps the ``DATABASE_URL`` it
was launched with (the inherited environment), so every connect re-tries the OLD
password. Dozens of such processes (lane heartbeats, daemons) then hammer the
pooler with failing auth and Supabase trips ``ECIRCUITBREAKER``, blocking *new*
connections fleet-wide — the 2026-09-30 and 2026-10-01 incidents.

The ``.env`` FILE is the rotation's single push-point, so the FILE value must WIN
over the inherited environment at connect time. This is the one owner for that
rule (bus #48240/#48278); it generalizes the pattern proven in
``scripts/bus_send.py:dburl()``. The name states what it does — ``dsn_from_env_file``
— so no later edit "fixes" it back to an env-first resolver (which is the bug).

Callers must fail fast + LOUD on a missing DSN (this raises), and on a psycopg
auth failure at the connect site they must stop + log loud, never retry-hammer.
"""
import os

# scripts/lib/substrate_dsn.py -> ../../ = the orchestrator repo root, where .env lives.
_DEFAULT_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env")


def _dsn_from_file(env_path):
    """Return the DATABASE_URL defined in the .env FILE, or None if absent/unreadable.

    Matches a ``DATABASE_URL=...`` (optionally ``export``-prefixed) line, strips
    surrounding quotes. First match wins, mirroring ``set -a; . .env`` semantics.
    A missing/unreadable file is not an error here — the caller falls back to env.
    """
    try:
        with open(env_path) as f:
            for raw in f:
                line = raw.strip()
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                if line.startswith("DATABASE_URL="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    return val or None
    except OSError:
        return None
    return None


def dsn_from_env_file(env_path=None, env=None):
    """Resolve ``DATABASE_URL`` reading the .env FILE FIRST, the environment only as fallback.

    The FILE wins so a stale inherited ``DATABASE_URL`` (a pre-rotation password
    held by a long-lived process) can never be used to connect.

    :param env_path: path to the .env file; defaults to the orchestrator repo .env.
    :param env: environment mapping to fall back to; defaults to ``os.environ``.
    :returns: the DATABASE_URL string.
    :raises RuntimeError: LOUD, when neither the file nor the environment has a
        non-empty DATABASE_URL — the caller must fail fast, never hammer the pooler.
    """
    path = env_path if env_path is not None else _DEFAULT_ENV_PATH
    env = env if env is not None else os.environ

    from_file = _dsn_from_file(path)
    if from_file:
        return from_file

    from_env = env.get("DATABASE_URL")
    if from_env:
        return from_env

    raise RuntimeError(
        "DATABASE_URL not found in the .env file (%s) or the environment — "
        "refusing to connect with a missing credential (op#24342 fail-fast)." % path
    )
