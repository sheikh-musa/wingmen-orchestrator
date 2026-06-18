-- 004_console_readonly.sql — Fleet Console v1 read-only DB role (CAI-RESP-264 hardening #2).
-- The console connects with a SELECT-only Postgres role, NEVER the service key, so a
-- bug/injection structurally cannot write. Grants are the MINIMUM the two read endpoints
-- need: the bus feed + the lane-status join. Nothing else — no other tables, no DML.
-- Apply via direct psycopg (decision-962: never `supabase db push`). Password injected at
-- apply time from a generated secret, never committed here.

-- :pw is bound by the apply script (psycopg parameter), not interpolated in this file.
CREATE ROLE console_readonly WITH LOGIN PASSWORD :'pw' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;

GRANT CONNECT ON DATABASE postgres TO console_readonly;
GRANT USAGE ON SCHEMA public TO console_readonly;

-- Exactly the 4 coordination tables the console reads. No INSERT/UPDATE/DELETE anywhere.
GRANT SELECT ON public.agent_messages TO console_readonly;
GRANT SELECT ON public.agents         TO console_readonly;
GRANT SELECT ON public.agent_status   TO console_readonly;
GRANT SELECT ON public.fleet_lanes    TO console_readonly;

-- Defensively ensure no default-privilege creep grants it anything future.
ALTER ROLE console_readonly SET statement_timeout = '10s';
ALTER ROLE console_readonly SET default_transaction_read_only = on;
