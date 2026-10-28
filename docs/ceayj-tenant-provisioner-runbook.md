# ceayj tenant-provisioner — runbook

Op#34565 option b. Build gate bus #78463; plan at
`reports/ceayj-tenant-provisioner-plan-20261027.md` (§3 carries the verified
peer-uid mechanism; §8 the three findings; §9 the contract digest).

## What this is

A unix-socket daemon on the Mini that provisions (and reverts) per-client
tenants on the shared ihsanos store (`docs/data-store-registry.md`, alias
`ihsanos multi-tenant DB`). A client is a spec file
(`scripts/lib/provisioner/specs/<client_key>.json`); the bundle of SQL a verb
applies is RENDERED server-side from that spec (`render.py`) — nothing
caller-supplied ever crosses the store wire. Every verb needs a real
orch-console/cai GO on the fleet bus, bound to the store ref + client_key +
the rendered bundle's sha256, and each gate drives at most ONE artifact.

## The wire

One newline-JSON request per connection, one newline-JSON response:

```
{"verb":"provision","client_key":"bariq","gate_id":"<agent_messages id>",
 "reason":"...","caller":"orch-console"}
```

Response `{"status":"ok","client_key":...,"verb":...,"bundle_sha256":...}` or
`{"status":"error","error":{"code":...,"message":...}}`. Codes: ACL_DENIED,
BAD_VERB, SPEC_INVALID, GATE_INVALID, UPSTREAM_ERROR, INTERNAL_ERROR.

Send it (as an allowlisted uid):

```
printf '%s\n' "$REQ" | socat - UNIX-CONNECT:~/wingmen/orchestrator/.secrets/provisioner.sock
```

## The GO contract (what a gate row must contain)

`gate_id` must name an agent_messages row: from orch-console or cai,
message_type decision/agreed, created in the past, whose subject+body
mentions **the store ref (or its alias), the client_key, and the full 64-hex
sha256 of the bundle as rendered by the code at request time**. The sha bind
is anti-drift: a GO written before a spec/template change silently stops
matching the re-rendered artifact — re-approve or revert the change. A gate
is spent once any attempt for it reaches `started` that didn't end in
`error`; retries are only possible after a failed attempt. The partial
unique index on `(gate_id, verb) WHERE status='ok'` enforces the success half
at the DB level.

## Install (orch-console/ops, after gate + merge — never from a build lane)

1. Apply `migrations/drafts/ceayj_provisioner_access_log.sql` via
   apply_migration.py (assert headers carry the REVOKE proof).
2. Mint `provisioner_log_role`'s LOGIN password; grant it SELECT on
   agent_messages (SELECT only) and INSERT/UPDATE/SELECT on
   provisioner_access_log.
3. Mint the store admin-write DSN into
   `.secrets/ceayj_admin_write.dsn` (0600).
4. `launchctl load launchd/dev.wingmen.ceayj-provisioner.plist`
   (env vars inside; no credential ever lives in the 0644 plist).

## Operations

- Logs: `logs/ceayj-provisioner.out.log`. Socket:
  `.secrets/provisioner.sock` (0700 dir, peer-uid ACL via LOCAL_PEERCRED —
  see peer.py's header for the two falsified folklore details).
- Stuck-started reconciliation (a crash mid-apply leaves status='started';
  the gate stays spent until resolved):
  `SELECT id, verb, client_key, ts_start FROM provisioner_access_log WHERE status='started' AND ts_start < now() - interval '1 hour';`
- Revert: `{"verb":"revert",...}` with a FRESH GO (same binding rules; the
  revert twin keeps migration_ledger — it is store infra, not the client's).
- Audit trail: substrate `provisioner_access_log` (who/what/gate/verdict) +
  store `migration_ledger` (what applied where).

## Ruled by orch-console (bus #79050, 2026-10-28) — no longer open

1. **mint-editor-dsn = its own gated verb** (never a step inside provision):
   minting a credential is its own security event — its own GO, its own
   ledger row, independently refusable/revocable. Provision succeeds without
   minting anything (v1 already conforms: the bundle only creates the
   NOLOGIN role; the mint verb lands separately).
2. **audit-ledger home = substrate**, on condition the table holds OPS
   METADATA ONLY (tenant slug/id, verb, artifact sha, gate id, actor,
   timestamps, outcome) — no tenant data rows, no credentials or DSNs, ever
   (TENANT-RESIDENCY-001). Pinned by
   `test_ledger_columns_are_ops_metadata_only_exact_set`: the INSERT and DDL
   column sets are asserted EXACT, so a residency-drift column fails CI by
   name.

## Verified posture (tests/provisioner/, 59 green + 1 CI-only)

- registry: ref loaded from the registry doc, z-variant structurally
  impossible, no ref literal anywhere in the package (finding 1).
- spec: lookup-free org-uuid pin, derived role/table names (no cross-wire),
  filename==client_key, alias resolves at VALIDATION time.
- render: every policy org-pinned at render time (a rogue template refuses);
  editor gets SELECT/UPDATE only; revert twin in dependency order; PG17
  harness proves apply/reapply/revert, editor roundtrip AS the role
  (#77536), and CREATEROLE cannot escalate on 17 (finding 2).
- server: full ladder incl. audit invariant (no store connection before a
  committed started row), server-major >=16 floor, DSN never echoed.
