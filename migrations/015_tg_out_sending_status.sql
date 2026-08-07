-- 015: tg_out claim state for concurrent drainers.
-- The CLI one-shot drain (tg_out send ...) and the daemon drain run
-- concurrently; without an atomic claim both deliver the same queued row and
-- the operator receives every message twice (observed live 2026-07-03, rows
-- 2021/2022 + 2023/2024). drain_once() now claims rows by flipping them to
-- 'sending' under FOR UPDATE SKIP LOCKED; stuck 'sending' rows (drainer died
-- mid-delivery) re-queue after 120s.

ALTER TABLE tg_out DROP CONSTRAINT tg_out_status_check;
ALTER TABLE tg_out ADD CONSTRAINT tg_out_status_check
  CHECK (status IN ('queued','sending','sent','failed','dead'));
