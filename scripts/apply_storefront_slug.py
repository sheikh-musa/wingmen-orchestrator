"""Apply the clients.storefront_slug column + unique index via psycopg.

CLAUDE.md forbids `supabase db push` against production (shadow-diff strips
view arms). Use the orch's direct psycopg-apply pattern instead.
"""
from __future__ import annotations

import os

import psycopg
from dotenv import load_dotenv

DDL = """
alter table clients add column if not exists storefront_slug text;
create unique index if not exists clients_storefront_slug_key
  on clients (storefront_slug)
  where storefront_slug is not null;
"""


def main() -> int:
    load_dotenv()
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL / SUPABASE_DB_URL not set")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()
    print("applied: clients.storefront_slug + clients_storefront_slug_key")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
