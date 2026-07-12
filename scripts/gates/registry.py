"""registry.py — invariant_registry gate API (CAI-RESP-420).

The bridge between an executable gate and the substrate invariant registry. A gate
imports this and calls mark_asserted() on a GREEN run — that stamps last_asserted_at
and flips the row to COVERED. This is what makes the registry live rather than prose:
cai (or a monitor) can query for COVERED rows whose last_asserted_at has gone stale
and know a gate stopped running. cc-infra owns the gates; cai stewards the rows.

No actuation, no bridge — a single substrate table read/write. DSN defaults to the
substrate (DATABASE_URL); override for tests.
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")


def _dsn(dsn: str | None = None) -> str:
    return dsn or os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def mark_asserted(invariant_ref: str, gate_ref: str, dsn: str | None = None) -> bool:
    """A gate calls this on a GREEN run: stamp last_asserted_at=now(), flip to
    COVERED, and record which gate did it. Returns True if the row existed. Does
    NOT create rows — cai stewards the enumeration; a gate asserting an unknown
    invariant is a wiring error surfaced by the False return, not a silent insert."""
    with psycopg.connect(_dsn(dsn)) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-infra',true)")
        cur.execute(
            "UPDATE invariant_registry "
            "SET last_asserted_at=now(), gate_status='COVERED', gate_ref=%s, updated_at=now() "
            "WHERE invariant_ref=%s",
            (gate_ref, invariant_ref),
        )
        n = cur.rowcount
        conn.commit()
        return n > 0


def mark_failing(invariant_ref: str, dsn: str | None = None) -> bool:
    """A gate calls this when it RAN but the invariant is VIOLATED: leave the row
    non-COVERED (revert to 'pending') and refresh updated_at, so a violated
    invariant can never read COVERED. last_asserted_at is intentionally NOT bumped
    (assertion means 'held', not merely 'checked')."""
    with psycopg.connect(_dsn(dsn)) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-infra',true)")
        cur.execute(
            "UPDATE invariant_registry "
            "SET gate_status='pending', updated_at=now() "
            "WHERE invariant_ref=%s AND gate_status='COVERED'",
            (invariant_ref,),
        )
        n = cur.rowcount
        conn.commit()
        return n > 0


def get(invariant_ref: str, dsn: str | None = None) -> dict | None:
    with psycopg.connect(_dsn(dsn)) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT invariant_ref, domain, statement, gate_ref, gate_status, severity, "
            "origin_incident, last_asserted_at, stewarded_by, seeded_by "
            "FROM invariant_registry WHERE invariant_ref=%s",
            (invariant_ref,),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d.name for d in cur.description]
        return dict(zip(cols, row))


def stale_covered(max_age_hours: int = 48, dsn: str | None = None) -> list:
    """COVERED rows whose gate hasn't asserted within max_age_hours — a gate that
    stopped running. The query a monitor runs to catch a rotted gate (cai's rule)."""
    with psycopg.connect(_dsn(dsn)) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT invariant_ref, gate_ref, last_asserted_at FROM invariant_registry "
            "WHERE gate_status='COVERED' AND (last_asserted_at IS NULL "
            "OR last_asserted_at < now() - make_interval(hours => %s)) "
            "ORDER BY last_asserted_at NULLS FIRST",
            (max_age_hours,),
        )
        return cur.fetchall()
