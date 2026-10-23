# PB-01 — Migration / DDL apply to a prod substrate
`version:` 1  ·  `owner:` cc-quality  ·  `last-reviewed:` 2026-10-22  ·  `risk:` P0

### 1. WHEN THIS FIRES
You are about to run SQL that is **DDL-shaped** against any prod silo — a
top-level `CREATE`/`ALTER`/`DROP`/`TRUNCATE`/`GRANT`/`REVOKE`, OR one of those
hidden inside a dollar-quoted `DO`/function body or a dynamic `EXECUTE format(...)`
string (this is the hiding place the detector exists for). Applying, or
reviewing someone else's apply, of a `migrations/*.sql`.

### 2. THE FAILURE IT PREVENTS
- **Prod damage from an un-rehearsed apply.** A DDL run straight at prod with no
  `ON_ERROR_STOP`, no rollback boundary, no invariant check → a partial apply
  leaves the schema wedged. (`prod-damage-autocommit-append`.)
- **`supabase db push` against prod** — resets/migrates by diff, can DROP; banned
  outright (CLAUDE.md substrate rule). Direct psycopg with `--expect-ref` is the
  only sanctioned path.
- **A body that references a column that doesn't exist on a silo** — `EXECUTE
  'CREATE OR REPLACE FUNCTION …'` does NOT column-validate at apply; it succeeds
  and fails at call time on the silo that's missing the column.
  (`plpgsql-body-not-column-validated-at-apply`, mig441.)
- **Reviewing against the named migration file instead of LIVE** — a fix can land
  in a later migration; the named file is not the live state.
  (`reaudit-diff-against-live-not-named-migration`.)
- **DROP+CREATE silently re-widening an ACL** via `ALTER DEFAULT PRIVILEGES`
  re-firing at CREATE. (`drop-create-function-acl-net-of-default-privileges`, mig468.)

### 3. STEPS
1. **Report before DDL** — the bus `--gate <id>` row that states what the DDL
   does + the silos + the equivalence/deny claim, BEFORE applying. **[hook]** —
   the apply is refused without it. *(Blocks: an unannounced schema change.)*
2. **Scratch-rehearse** — run the exact SQL against a throwaway/ephemeral PG (or a
   `BEGIN … ROLLBACK` on live for a read-shaped probe) first; confirm it applies
   clean and the invariants hold. **[manual]** *(Blocks: discovering a syntax/lock
   error on prod.)*
3. **Equivalence / deny-proof** — for a function/policy change, diff the
   normalized live body (`pg_get_functiondef` / `pg_policies`, comment-stripped,
   whitespace-normalized) vs the new one and prove the *predicate tokens* match
   except the intended change; for a security change, run the **negative control**
   (the thing that must now be refused → prove `42501`/`22P02`/0-rows). **[manual]**
   *(Blocks: a widen/drop masquerading as a no-op — judge the NET ACL, not the GRANT line.)*
4. **Pre-image guard for DROP+CREATE** — pin `md5(prosrc)` of the live body and
   `RAISE EXCEPTION` if it drifted since the RO export; confirm the pinned md5 ==
   live on every target silo. **[manual]** *(Blocks: replacing a body that another
   gated migration already changed.)*
5. **Apply via `apply_migration.py --gate <id> --expect-ref <ref>`** — direct
   psycopg, never `supabase db push`; `--expect-ref` fails closed if pointed at the
   wrong project. **[hook + manual]** *(Blocks: wrong-silo apply; the push footgun.)*
6. **Harden the SQL file** — `-v ON_ERROR_STOP=1`; new statements appended *above*
   the file's final `ROLLBACK`; invariant `SELECT`s after each mutating statement.
   **[manual]** *(Blocks: a partial apply continuing past a failed statement.)*
7. **Verify on LIVE, per silo** — re-pull `pg_get_functiondef`/`proacl`/`pg_policies`
   from each silo and byte/predicate-compare to the intended net; a silo you can't
   reach is "could not measure," never "done." **[manual]** *(Blocks: assuming a
   multi-silo apply landed uniformly.)*

### 4. ENFORCING HOOK
- **Hook:** `scripts/hooks/secrets_transcript_guard.py` → imports
  `scripts/hooks/ddl_detect.py::is_ddl_shaped` (vendored pinned at
  `scripts/lib/exec_broker/ddl_detect.py`) — **EXISTING**. Gate validation in
  `scripts/lib/exec_broker/gate_validation.py`; sanctioned apply tool
  `scripts/apply_migration.py`.
- **What it enforces:** any Bash command whose SQL is DDL-shaped is **refused
  (exit 2 + stderr the model sees)** unless it carries `--gate <bus-id>`; the
  detector is conservative (DDL keyword in a *top-level* comment/string is
  ignored; inside a dollar-quoted body is NOT). *(Verified live 2026-10-22: it
  refused this very session's probe command for lacking a gate.)*
- **What it CANNOT enforce (residual, all [manual] above):** that the rehearsal
  actually ran (step 2), that the equivalence/deny-proof is real (step 3), the
  md5 pre-image pin (step 4), `ON_ERROR_STOP`/rollback-boundary/invariants in the
  file (step 6), and the per-silo live re-verify (step 7). The hook guarantees
  *a gate exists*; it does NOT read the gate's rigor. → **GAP candidate:** a
  lightweight `apply_migration.py` precheck that greps the SQL file for
  `ON_ERROR_STOP=1` + a trailing `ROLLBACK` and refuses without them (mechanical,
  closes step 6; the rest stay human + reviewer).
- **Known false-positive:** DDL keywords in an `echo`/comment string of an
  otherwise read-only command trip the guard — documented workaround is to drop
  the DDL-looking words from echo text.

### 5. DISCOVERY
- Index `README.md` row (risk P0, top).
- CLAUDE.md substrate-rules section already says "never `supabase db push`;
  direct psycopg apply" — add `→ see PB-01`.
- **Load-bearing:** `secrets_transcript_guard`'s refuse message should cite
  `PB-01` (small change: append " — see reports/build-rigor-playbooks/PB-01" to
  the gate-required stderr), so a lane that hits the guard is handed the playbook.

### 6. PROVENANCE
`prod-damage-autocommit-append`, `plpgsql-body-not-column-validated-at-apply`,
`reaudit-diff-against-live-not-named-migration`, `drop-create-function-acl-net-of-default-privileges`,
`revoke-migration-owner-vs-applier-silent-noop`, `goumlyne-migration-ledger-not-source-of-truth`,
`post-assert-sibling-consistency-check`; CLAUDE.md substrate rules; bus #56599/#56607
(exec gate mirror), op#22669 item 3 (apply_migration `--gate`).
