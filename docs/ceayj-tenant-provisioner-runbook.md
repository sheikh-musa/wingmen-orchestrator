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
mentions **the store ref (or its alias), the client_key, the VERB
(provision or revert — a GO for one never authorizes the other; the sha
alone cannot separate them because it is always the FORWARD artifact's),
and the full 64-hex sha256 of the bundle as rendered by the code at request
time**. The sha bind
is anti-drift: a GO written before a spec/template change silently stops
matching the re-rendered artifact — re-approve or revert the change. A gate
is spent once any attempt for it reaches `started` that didn't end in
`error`; retries are only possible after a failed attempt. The partial
unique index on `(gate_id, verb) WHERE status='ok'` enforces the success half
at the DB level.

## Install (orch-console, after gate + merge — never from a build lane)

ONE sanctioned entrypoint (op#35106 / bus #79277), run by orch-console under
console_irsyad_guard's second allow-exception (a fail-closed exception for
exactly this invocation and nothing else):

```
.venv/bin/python3 scripts/install_ceayj_provisioner.py            # dry-run (default)
.venv/bin/python3 scripts/install_ceayj_provisioner.py --apply    # execute
.venv/bin/python3 scripts/install_ceayj_provisioner.py --apply --step 3
```

Prerequisite: orch-console posts a bus **decision** row naming the migration
draft's sha12 — the script discovers the gate row by that sha12 and refuses
without it. Output is key names, shas, counts and PIDs only.

What the four steps do (each idempotent; re-runs report already-applied):

1. Numbers `migrations/drafts/ceayj_provisioner_access_log.sql` after the
   current top, discovers the gate row by sha12, applies via
   apply_migration.py (DSNs via env `DATABASE_URL` only, never on argv).
2. Mints `provisioner_log_role`'s LOGIN password, grants SELECT on
   agent_messages (SELECT only) and INSERT/UPDATE/SELECT on
   provisioner_access_log, writes `.secrets/provisioner_log_role.dsn` (0600),
   and proves the credential with a rolled-back write roundtrip AS the role.
3. Creates the DEDICATED `fleet_provisioner` store role — never postgres —
   with EXACTLY the bundle privileges (CREATEROLE; CREATE on public; USAGE
   WITH GRANT OPTION for editor re-grants; INSERT/DELETE/REFERENCES +
   SELECT(id)-only on organizations), writes its DSN to
   `.secrets/ceayj_admin_write.dsn` (0600, the plist's
   PROVISIONER_STORE_DSN_FILE), and proves the surface with a transactional
   canary (always rolled back) + negative probes (org-wide SELECT denied,
   superuser-grant denied). The admin credential is read from the console's
   private credentials file inside the process only.
   PG17-harness-proven: a full forward+revert bundle lifecycle runs as this
   role under exactly these grants (tests/provisioner/
   test_install_store_role_pg17.py).

   The credentials file is a sourceable shell script; its write-DSN line can
   sit inside an `if [ "$ALLOW_WRITE_DSN" = "1" ]; then ... fi` block — a
   tripwire so an interactive `source` of the file doesn't export the write
   DSN into a whole shell session by accident. The installer does **not**
   require that flag to be set in its own environment (orch-console bus
   #79852/#79884 decision — option D, chosen over widening the guard or
   adding a sentinel-file ceremony step): the flag's purpose is to stop
   *accidental* shell exposure, and the installer is already a stronger
   control than that flag — console_irsyad_guard's exception #2 admits only
   this exact entrypoint/argv, `read_admin_dsn` reads one exact key and
   never evaluates the file as shell, and the ihsanos-residency ref
   (TENANT-RESIDENCY-001) is still asserted on the DSN it reads regardless
   of whether the line was gated. When the matched key is inside a
   conditional, the installer prints one line naming the required flag(s)
   and stating the condition is intentionally not enforced here, then
   proceeds straight to the residency assert — it never silently skips that
   assert on this path.
4. `launchctl load` of `launchd/dev.wingmen.ceayj-provisioner.plist` (skip if
   loaded; no credential ever lives in the 0644 plist), then an UNGATED
   socket request must be refused (GATE_INVALID) — the daemon is up AND
   gated. The self-check lands one `status='error'` audit row
   (`gate_id='0'`).

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
  store `migration_ledger` (what applied where). The caller-supplied
  `reason` free-text lands in the ledger VERBATIM — callers must not put
  tenant data or credentials in it (cc-quality Minor c).
- **Posture note (cc-quality review, PR#566)**: with
  PROVISIONER_ALLOWED_UIDS=501, every fleet process on the Mini passes the
  peer-uid ACL — the effective authorization boundary is the GO gate alone.
  If ops wants a real second boundary, run the daemon under a dedicated
  service user and set the allowlist to that uid (orch-console/ops call).

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
