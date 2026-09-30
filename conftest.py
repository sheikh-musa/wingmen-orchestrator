"""Repo-root conftest — PROD-REF REFUSAL GUARD (backlog#68, Nazim #46564/#46566).

SAFETY-CRITICAL, no opt-out. A pytest session refuses to start if the resolved
DATABASE_URL (env var OR the .env file) points at any known PRODUCTION store. This
fires in BOTH local runs AND CI: the 2026-09-28/29 substrate pooler lockout was a
LOCAL `pytest tests/` run whose stale-password env hammered the prod substrate, and
the CI job had been exporting the prod DATABASE_URL secret to the whole test run.
Tests must only ever touch an ephemeral/local database; a prod DSN is a hard refuse.

Resolution mirrors the sources a test actually reads (os.environ DATABASE_URL /
SUPABASE_DB_URL) PLUS the .env file (the rotation's single push-point, per #214's
bus_send.dburl), so neither an inherited env var nor a stale .env slips through.
"""
import os

# The three production stores (docs/data-store-registry.md). A DSN containing any of
# these project refs is prod — never a test target.
_PROD_REFS = (
    "tscuymavysscrvoberrr",   # orchestrator substrate
    "ceayjeamtmcyzzvqflus",   # ihsanos multi-tenant DB
    "goumlynecruxrlmzlntp",   # irsyad silo (goumlyne)
)


def _env_file_dsns():
    """DATABASE_URL / SUPABASE_DB_URL lines from the repo's .env file (if present)."""
    out = []
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith(("DATABASE_URL=", "SUPABASE_DB_URL=")):
                    out.append(line.split("=", 1)[1].strip().strip('"').strip("'"))
    except (FileNotFoundError, OSError):
        pass
    return out


def resolve_dsns(environ=None, include_env_file=True):
    """Every DSN string a test could connect with: env DATABASE_URL/SUPABASE_DB_URL
    plus the .env file's. Pure + injectable so it's unit-testable."""
    environ = os.environ if environ is None else environ
    dsns = [environ.get("DATABASE_URL"), environ.get("SUPABASE_DB_URL")]
    if include_env_file:
        dsns += _env_file_dsns()
    return [d for d in dsns if d]


def prod_ref_in(dsns):
    """Return (dsn_snippet, prod_ref) for the first DSN that names a prod store, else
    None. Pure — the unit test drives this directly."""
    for dsn in dsns:
        for ref in _PROD_REFS:
            if ref in dsn:
                # never return the full DSN (it carries a password) — just the ref.
                return ref
    return None


def pytest_configure(config):
    ref = prod_ref_in(resolve_dsns())
    if ref:
        import pytest
        pytest.exit(
            f"REFUSED: a resolved DATABASE_URL/SUPABASE_DB_URL points at the PRODUCTION "
            f"store '{ref}'. Tests must NEVER run against prod (backlog#68 / bus #46566 — "
            f"a local run once tripped the substrate pooler circuit breaker). Point "
            f"DATABASE_URL at an ephemeral/local Postgres and re-run. No opt-out.",
            returncode=2,
        )
