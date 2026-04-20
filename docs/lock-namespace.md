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

## Identity invariants

The advisory lock at key `1001` (`AGENT_ID_ALLOC`) serializes allocation of
*agent sub-tags* (e.g. `cc-ihsanos` → `cc-ihsanos-42`). The allocator uses
string-prefix matching to find the next free suffix under a base tag.

**Invariant (CAI-RESP-054):** No two registered base agent ids may stand in a
prefix+hyphen relationship. That is, if `cc-foo` is a registered base tag,
then `cc-foo-bar` MUST NOT also be a registered base tag — only an allocated
sub-tag under `cc-foo`.

**Why:** if both `cc-foo` and `cc-foo-bar` existed as independent base tags,
a `LIKE 'cc-foo-%'` scan under the `1001` lock would conflate sub-tags of
`cc-foo` with base tags of `cc-foo-bar`, producing either double-allocation
or false collisions. The allocator cannot distinguish the two cases from the
stored string alone.

**How enforced:** today, socially — by convention and code review. Agent
base tags are declared in a short list (orchestrator config). A mechanical
check is tracked as a future hardening; until then, reviewers must reject
a new base tag that prefixes an existing one.

## History

- **2026-04-20** — Registry created (CAI-RESP-053 A1). Migrated
  `auto_agent_id.py` from `hashtext('cc-agent-id-alloc')` to `1001`.
- **2026-04-20** — Identity invariants section added (CAI-RESP-054
  documented-limitation). Prefix-collision rule made explicit pending
  mechanical enforcement.
