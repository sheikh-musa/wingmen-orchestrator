# PB-05 — Secrets & transcript hygiene
`version:` 1  ·  `owner:` cc-quality  ·  `last-reviewed:` 2026-10-22  ·  `risk:` P1

### 1. WHEN THIS FIRES
You are about to run a command that could surface a secret: reading/echoing a token
or DSN, dumping env, connecting with a DSN on the command line, or reviewing code
that handles secrets. (This playbook mostly DOCUMENTS the contract the existing
hooks already enforce — it's 🟢, included so a lane knows WHY the guard refused it.)

### 2. THE FAILURE IT PREVENTS
- **A real secret rendered into the transcript/tool-output** (then cached/indexed by
  anything that ingests transcripts). During review, fake or hash the value — never
  display real secret output. (CLAUDE.md; `never-fetch-real-secrets-during-review`.)
- **A DSN passed as a psql positional arg** → the full connection string (with
  password) lands in the command line + process list. Use `PG*` env vars parsed from
  the DSN instead.
- **An env dump with no hash/length sink** → bulk secret exposure.

### 3. STEPS
1. **Never display a real secret** — fake it, or emit `sha256[:12]` / a length, never
   the value. **[hook: secrets_output_scanner (post)] + [manual]**
2. **No DSN as a positional arg** — parse the DSN → `PGHOST/PGUSER/PGPASSWORD/…` env,
   then `psql -X -Aqt -c …` with no connstring on the line. **[hook:
   secrets_transcript_guard (pre)]** *(Blocks: connstring-in-cmdline.)*
3. **Env dump only with a hash/length sink** — `env | … | sha256`/count, never raw.
   **[hook: secrets_transcript_guard]**
4. **Known false-positive to work around, not defeat:** the guard also trips on DDL
   keywords (`CREATE FUNCTION`, `GRANT`, `REVOKE`, the literal "DDL") appearing in an
   `echo`/comment string of an otherwise read-only command — drop the DDL-looking
   words from the echo text; do NOT restructure to evade the real secret checks.
   **[manual]**

### 4. ENFORCING HOOK
- **Hooks (EXISTING, 🟢):** `scripts/hooks/secrets_transcript_guard.py` (PreToolUse —
  refuses DSN-as-arg / unhashed-env-dump, and is also the DDL `--gate` enforcer),
  `scripts/hooks/secrets_output_scanner.py` (PostToolUse — scans tool output for
  secret shapes), `scripts/hooks/secret_shape_patterns.py` (shared patterns),
  `scripts/secret_hash_sweep.py`. Registered GLOBALLY (`~/.claude/settings.json`) →
  fire in every session, fleet-wide.
- **What it CANNOT enforce (residual):** a secret a reviewer chooses to paste into a
  *narrative* (not tool output) — human discipline; and the DDL false-positive above.
- **No new hook needed** — this row is already enforced; the playbook's job is to
  explain the contract + the false-positive workaround so the guard teaches rather
  than merely blocks.

### 5. DISCOVERY
Index row; `secrets_transcript_guard`/`secrets_output_scanner` refuse messages cite
`PB-05`; CLAUDE.md secret rule → `see PB-05`.

### 6. PROVENANCE
`never-fetch-real-secrets-during-review`, CLAUDE.md substrate/security rules,
`security-hook-regex-adversarial-probe`, `rule-g-heredoc-and-env-assignment-bypass`.
