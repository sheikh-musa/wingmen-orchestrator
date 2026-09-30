"""Repo-root conftest — PROD-REF REFUSAL GUARD (backlog#68, Nazim #46564/#46566/#46576).

SAFETY-CRITICAL, no opt-out. A pytest session refuses to start if the EFFECTIVE
DATABASE_URL points at any known PRODUCTION store. This fires in BOTH local runs AND
CI: the 2026-09-28/29 substrate pooler lockout was a LOCAL `pytest tests/` run whose
stale-password env hammered the prod substrate, and the CI job had been exporting the
prod DATABASE_URL secret to the whole test run. Tests must only ever touch an
ephemeral/local database; a prod DSN is a hard refuse.

PRECEDENCE (#46576): resolve ENV-VAR FIRST. If DATABASE_URL (or SUPABASE_DB_URL) is
SET in the environment, that is the effective DSN and the .env file is NOT consulted —
so a lane that exports a LOCAL DSN for TDD is ALLOWED even though the fleet .env holds
the prod DSN. ONLY when the env var is unset do we fall back to the .env file value
(the rotation's single push-point, per #214). Net:
  env = local DSN            -> ALLOW (never reads .env)
  env unset + .env = prod    -> REFUSE
  env = prod DSN             -> REFUSE
  env unset + no .env / local -> ALLOW (DB-integration tests skip as before)
"""
import os

# The three production stores (docs/data-store-registry.md). A DSN containing any of
# these project refs is prod — never a test target.
_PROD_REFS = (
    "tscuymavysscrvoberrr",   # orchestrator substrate
    "ceayjeamtmcyzzvqflus",   # ihsanos multi-tenant DB
    "goumlynecruxrlmzlntp",   # irsyad silo (goumlyne)
)


def _env_dsn(environ):
    """The DSN from the ENVIRONMENT (DATABASE_URL wins over SUPABASE_DB_URL), or None."""
    return environ.get("DATABASE_URL") or environ.get("SUPABASE_DB_URL") or None


def _env_file_dsn():
    """The first DATABASE_URL / SUPABASE_DB_URL from the repo's .env file, or None."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith(("DATABASE_URL=", "SUPABASE_DB_URL=")):
                    return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    except (FileNotFoundError, OSError):
        pass
    return None


def effective_dsn(environ=None):
    """The ONE DSN a test run will actually use: ENV-VAR FIRST (DATABASE_URL /
    SUPABASE_DB_URL); only if the env var is UNSET, the .env file. Pure + injectable
    so it's unit-testable. Returns None when neither source has a DSN."""
    environ = os.environ if environ is None else environ
    return _env_dsn(environ) or _env_file_dsn()


def prod_ref(dsn):
    """The prod store ref contained in `dsn`, or None. Never returns the full DSN
    (it carries a password)."""
    if not dsn:
        return None
    for ref in _PROD_REFS:
        if ref in dsn:
            return ref
    return None


def pytest_configure(config):
    ref = prod_ref(effective_dsn())
    if ref:
        import pytest
        pytest.exit(
            f"REFUSED: the effective DATABASE_URL points at the PRODUCTION store "
            f"'{ref}'. Tests must NEVER run against prod (backlog#68 / bus #46566 — a "
            f"local run once tripped the substrate pooler circuit breaker). Export "
            f"DATABASE_URL to a LOCAL/ephemeral Postgres (see scripts/pytest_local.sh) "
            f"and re-run. No opt-out.",
            returncode=2,
        )
