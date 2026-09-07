-- 017_console_memory_backup.sql — Nazim (console body) memory persistence.
-- The console body's distilled lessons live in a machine-local memory dir;
-- per session-mortal-state doctrine, state that must outlive a machine lives
-- in the substrate. Nightly delta snapshots; restore = latest row per file.
-- Apply via scripts/apply_migration.py 017 --silo tscuymavysscrvoberrr (historical
-- applier: apply_console_memory_backup.py, deleted 2026-09-05 PR #88; decision-962: no db push).

CREATE TABLE IF NOT EXISTS console_memory_backup (
    id             bigserial PRIMARY KEY,
    file_name      text        NOT NULL,
    content        text        NOT NULL,
    content_sha256 text        NOT NULL,
    backed_up_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS console_memory_backup_file_idx
    ON console_memory_backup (file_name, backed_up_at DESC);

ALTER TABLE console_memory_backup ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON console_memory_backup FROM anon, authenticated;
