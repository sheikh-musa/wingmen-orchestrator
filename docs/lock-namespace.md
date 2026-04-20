# Advisory Lock Namespace Registry

Postgres advisory locks use a shared 64-bit integer key space across the
database. To prevent collisions, every advisory-lock key used in this
codebase MUST be registered here before use.

## Reserved ranges

| Range      | Purpose                       | Status  |
|------------|-------------------------------|---------|
| 1000–1099  | Identity & scheduling         | active  |
| 1100–1199  | Migrations & schema evolution | reserved|
| 1200–1299  | Build/deploy coordination     | reserved|
| 1300+      | Future use                    | reserved|

## Active registrations

| Key ID | Constant name     | Owner                                    | Purpose                                  |
|--------|-------------------|------------------------------------------|------------------------------------------|
| 1001   | AGENT_ID_ALLOC    | `scripts/lib/auto_agent_id.py`           | Sub-tag allocator critical section.      |

## Rules

1. Pick the next available integer in the appropriate range.
2. Add a row to the "Active registrations" table in the same commit
   that introduces the lock. A PR that adds a `pg_*_advisory_*_lock`
   call without a registry entry must be rejected in review.
3. Never use `hashtext('some-string')` — it's a 32-bit hash and the
   registry loses meaning. Always use an explicit bigint literal.
4. Document in the owner file:
   ```python
   # See docs/lock-namespace.md — registered as AGENT_ID_ALLOC.
   _ALLOC_LOCK_ID = 1001
   ```

## History

- **2026-04-20** — Registry created (CAI-RESP-053 A1). Migrated
  `auto_agent_id.py` from `hashtext('cc-agent-id-alloc')` to `1001`.
